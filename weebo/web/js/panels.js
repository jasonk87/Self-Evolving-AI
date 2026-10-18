// Drawer panels: Agents, Evolution, Memory, Schedule, Activity, Settings.
import { api } from "./api.js";
import { icon } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import { el, btn, toast, timeAgo, dateTime, duration, renderDiff, modal, confirmDialog, debounce, escapeHtml, copyText } from "./ui.js";
import { listVoices, speak, voiceSupport } from "./voice.js";
import { renderPendingApproval, TIER_LABEL } from "./items.js";

const GRANTABLE = ["remember", "recall", "search_history", "set_reminder", "list_reminders", "cancel_reminder",
  "use_integration", "start_agent", "agent_status", "message_agent", "stop_agent", "save_skill", "propose_improvement",
  "offer_replies", "day_sheet"];
const AUTONOMY = ["read", "draft", "organize", "act", "send", "spend", "contact_people", "delete"];
const EFFORTS = ["minimal", "low", "medium", "high", "xhigh"];
const BUCKETS = [["interrupt", "Tell me now"], ["brief", "In the brief"], ["log", "Just log it"]];

const PANELS = {
  ideas: { title: "Ideas", icon: "sparkles" },
  bots: { title: "Bots", icon: "users" },
  capabilities: { title: "Capabilities", icon: "plug" },
  agents: { title: "Agents", icon: "bot" },
  evolution: { title: "Evolution", icon: "dna" },
  memory: { title: "Memory", icon: "brain" },
  facts: { title: "Source-backed facts", icon: "brain" },
  schedule: { title: "Schedule", icon: "clock" },
  activity: { title: "Activity", icon: "pulse" },
  settings: { title: "Settings", icon: "gear" },
  more: { title: "More", icon: "menu" },
  archive: { title: "Older chats", icon: "inbox" },
};

const STATUS_LABEL = {
  queued: "Queued", running: "Running", completed: "Done", failed: "Failed", cancelled: "Stopped", interrupted: "Interrupted",
  proposed: "Idea", building: "Building", checking: "Verifying", ready: "Ready to merge", merging: "Merging", merged: "Merged",
  rejected: "Rejected", discarded: "Discarded", conflict: "Conflict", rolled_back: "Rolled back",
  vetting: "Council", declined: "Declined",
};

/** Where a self-improvement is right now, in words: the Council, then build → test → review per round. */
function proposalStep(p) {
  const meta = p.meta || {};
  const council = meta.council || {};
  if (p.status === "vetting") return "The Council is deciding whether it's worth building…";
  if (p.status === "declined") return `Council declined: ${council.reason || "no reason given"}`;
  if (["building", "checking"].includes(p.status)) {
    const stage = { building: "Building", testing: "Testing", reviewing: "Code review", evaluating: "Rehearsing real conversations" }[meta.stage] || (p.status === "building" ? "Building" : "Verifying");
    return `${stage} · round ${meta.round || 1} of 3`;
  }
  if (p.status === "proposed" && council.approved === true) return `Council approved: ${council.reason || ""}`;
  if (p.status === "proposed" && council.approved === null && council.reason) return council.reason;
  return "";
}

const BUILD_STEPS = [["council", "Council"], ["building", "Build"], ["testing", "Test"], ["reviewing", "Review"], ["evaluating", "Rehearse"], ["ready", "Ready"]];

function stepper(p) {
  const meta = p.meta || {};
  const skipCouncil = !(meta.council && "reason" in meta.council); // only ideas the (new, up-front) Council vetted
  // Only changes to Weebo's brain or memory are rehearsed against real conversations.
  const rehearsed = meta.stage === "evaluating" || (p.gate_report || "").includes("Behavior evals");
  const current = p.status === "vetting" || p.status === "declined" ? "council"
    : ["building", "checking"].includes(p.status) ? (meta.stage || "building")
    : ["ready", "merging", "merged"].includes(p.status) ? "ready" : "";
  if (!current) return null;
  const at = BUILD_STEPS.findIndex(([key]) => key === current);
  const steps = BUILD_STEPS.filter(([key]) => !(key === "council" && skipCouncil) && !(key === "evaluating" && !rehearsed));
  return el("ol", { class: "stepper", "aria-label": "Build progress" }, steps.map(([key, label]) => {
    const i = BUILD_STEPS.findIndex(([k]) => k === key);
    const state = p.status === "declined" && key === "council" ? "fail" : i < at ? "done" : i === at ? (current === "ready" ? "done" : "now") : "todo";
    return el("li", { class: `step ${state}` }, el("span", { class: "step-dot", html: state === "done" ? icon("check", 12) : state === "fail" ? icon("x", 12) : "" }), el("span", { text: label }));
  }));
}

/** After a merge: did the recorded failures this upgrade claimed to fix stay fixed? */
function proposalOutcome(p) {
  const meta = p.meta || {};
  const addresses = meta.addresses || [];
  const outcome = meta.outcome || {};
  if (!addresses.length) return p.status === "merged" ? el("p", { class: "muted small", text: "Not measured: this upgrade didn't name a recorded failure it fixes." }) : null;
  const n = `${addresses.length} recorded failure${addresses.length > 1 ? "s" : ""}`;
  if (outcome.result === "held") return el("div", { class: "check pass" }, el("span", { html: icon("check", 14) }), el("span", { text: `The fix held: none of the ${n} it targeted came back.` }));
  if (outcome.result === "regressed") {
    return el("details", { class: "check fail", open: true },
      el("summary", { html: `${icon("x", 14)}<span>The failure came back after merging. Weebo's next self-audit will look at it again.</span>` }),
      el("pre", { class: "cmd-out", text: (outcome.issues || []).join("\n") }));
  }
  if (p.status === "merged" && meta.awaiting_live) return el("p", { class: "muted small", text: `Goes live when Weebo restarts; then it watches ${n} for a few days to see whether the fix holds.` });
  if (p.status === "merged") return el("p", { class: "muted small", text: `Watching ${n} for a few days to see whether the fix holds.` });
  return el("p", { class: "muted small", text: `Targets ${n}; Weebo checks whether it stays fixed after merging.` });
}

function pill(status) {
  return el("span", { class: `pill st-${status}`, text: STATUS_LABEL[status] || status });
}

function section(title, ...children) {
  return el("section", { class: "panel-section" }, title ? el("h4", { text: title }) : null, ...children);
}

/** One clean line for a finished task: audits report JSON, agents report markdown. */
function taskPreview(task) {
  let text = task.summary || task.error || "";
  if (task.kind === "audit") {
    try { text = JSON.parse(text).summary || text; } catch { /* not JSON */ }
  }
  return text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")   // [label](link) -> label
    .replace(/[`*#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 160);
}

function empty(text, ic = "sparkles") {
  return el("div", { class: "empty" }, el("span", { html: icon(ic, 22) }), el("p", { text }));
}

export class Panels {
  constructor(app) {
    this.app = app;
    this.drawer = document.getElementById("drawer");
    this.title = document.getElementById("drawer-title");
    this.body = document.getElementById("drawer-body");
    this.current = null;
    this.opts = {};
    document.getElementById("drawer-close").addEventListener("click", () => this.close());
    // Live updates are debounced; editing is checked again when they fire, not only when they're scheduled.
    this._refresh = debounce(() => this.refresh({ live: true }), 250);
    this.pending = false;
    // Live updates wait while you're typing in a panel, so drafts and focus survive; they catch up after.
    this.body.addEventListener("focusout", () => setTimeout(() => {
      if (this.pending && !this.editing()) { this.pending = false; this._refresh(); }
    }, 200));
  }

  editing() {
    const active = document.activeElement;
    if (active && this.body.contains(active) && active.matches("input, textarea, select")) return true;
    return [...this.body.querySelectorAll("form:not([hidden]) input, form:not([hidden]) textarea")]
      .some((field) => field.type !== "checkbox" && field.type !== "radio" && field.value.trim());
  }

  open(name, opts = {}) {
    if (!PANELS[name]) return;
    this.app.setProposalRoute(name === "evolution" ? opts.proposal : null);
    this.current = name;
    this.opts = opts;
    this.title.innerHTML = `${icon(PANELS[name].icon, 18)}<span>${PANELS[name].title}</span>`;
    this.drawer.classList.add("open");
    document.body.classList.add("drawer-open");
    document.querySelectorAll("[data-panel]").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
    this.refresh();
  }

  close() {
    this.app.setProposalRoute(null);
    this.current = null;
    this.drawer.classList.remove("open");
    document.body.classList.remove("drawer-open");
    document.querySelectorAll("[data-panel]").forEach((b) => b.classList.remove("active"));
  }

  toggle(name) { if (this.current === name) this.close(); else this.open(name); }

  // Called by main.js on live events; refreshes only the visible panel.
  notify(type) {
    if (!this.current) return;
    const interest = {
      agents: ["task.", "interaction."], evolution: ["evolution.", "skills.", "evals."], memory: ["memory."], schedule: ["reminder."],
      activity: ["weebo.activity", "notify"], settings: ["engine.", "settings."],
      bots: ["bot.", "policy.", "conv.created"], ideas: ["ideas.", "daysheet."],
      capabilities: ["connector.", "capabilities.", "bot.", "skills."],
    }[this.current] || [];
    if (interest.some((p) => type.startsWith(p))) {
      if (this.current === "agents" && type === "task.progress" && !this.opts.task) return this.patchProgress();
      if (this.editing()) { this.pending = true; return; }
      this._refresh();
    }
  }

  /** Re-render the open panel. A live update (`live`) never replaces a form you're using: it waits until you
   *  leave it, even if you started typing while the update was loading. Refreshes you trigger always run. */
  async refresh({ live = false } = {}) {
    const name = this.current;
    if (!name) return;
    if (live && this.editing()) { this.pending = true; return; }
    const opts = this.opts;
    const scrollTop = this.body.scrollTop;
    try {
      const content = await this[`render_${name}`](opts);
      if (this.current !== name || this.opts !== opts) return;
      if (live && this.editing()) { this.pending = true; return; }
      this.body.replaceChildren(content);
      this.body.scrollTop = scrollTop;
    } catch (err) {
      if (this.current !== name || this.opts !== opts) return;
      this.body.replaceChildren(empty(`Couldn't load: ${err.message}`, "alert"));
    }
  }

  // ================================================================== AGENTS
  async render_agents(opts) {
    if (opts.task) return this.renderTask(opts.task);
    const { tasks, live } = await api.get("/api/tasks");
    const wrap = el("div", { class: "panel" });
    wrap.append(el("p", { class: "panel-intro", text: "Background Codex agents work in parallel while you keep chatting. Weebo launches them for big jobs, or you can start one here." }));
    const form = el("form", { class: "stack form-card", hidden: true });
    const title = el("input", { class: "input", placeholder: "Title, e.g. Build a snake game", required: true });
    const instructions = el("textarea", { class: "input", rows: 4, placeholder: "What should the agent do? Be specific about the goal and where to work.", required: true });
    const cwd = el("input", { class: "input", placeholder: "Folder (optional, defaults to Weebo's workspace)" });
    const effort = el("select", { class: "input" }, ["", "low", "medium", "high", "xhigh"].map((e) => el("option", { value: e, text: e ? `Effort: ${e}` : "Effort: default" })));
    form.append(title, instructions, el("div", { class: "row" }, cwd, effort),
      el("div", { class: "row end" }, btn("Cancel", { onClick: () => { form.hidden = true; } }), btn("Launch agent", { kind: "primary", icon: "play", onClick: () => form.requestSubmit() })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const { task } = await api.post("/api/tasks", { title: title.value, instructions: instructions.value, cwd: cwd.value, effort: effort.value, conversation_id: this.app.mainId || this.app.currentConvId });
        toast("Agent launched", task.title, { kind: "success" });
        this.opts = { task: task.id };
        this.refresh();
      } catch (err) { toast("Couldn't launch agent", err.message, { kind: "error" }); }
    });
    wrap.append(el("div", { class: "row" }, btn("New agent", { kind: "primary", icon: "plus", onClick: () => { form.hidden = false; title.focus(); } })), form);

    const kinds = { agent: "Agent", audit: "Self-audit", evolution: "Evolution build" };
    const active = tasks.filter((t) => ["queued", "running"].includes(t.status));
    const recent = tasks.filter((t) => !["queued", "running"].includes(t.status));
    const card = (t) => {
      const elapsed = (t.finished_at || Date.now() / 1000) - (t.started_at || t.created_at);
      return el("button", { class: `task-card st-${t.status}`, type: "button", dataset: { task: t.id }, onclick: () => { this.opts = { task: t.id }; this.refresh(); } },
        el("div", { class: "task-top" }, el("span", { html: icon(t.kind === "evolution" ? "dna" : t.kind === "audit" ? "search" : "bot", 16) }),
          el("strong", { text: t.title }), pill(t.status)),
        el("div", { class: "task-progress", text: t.status === "running" ? (live?.[t.id]?.progress || this.app.taskProgress.get(t.id) || "Working…") : taskPreview(t) }),
        el("div", { class: "task-meta", text: `${kinds[t.kind] || t.kind} · ${t.status === "queued" ? "waiting" : duration(elapsed)} · ${timeAgo(t.created_at)}` }));
    };
    wrap.append(section(`Running (${active.length})`, active.length ? el("div", { class: "stack" }, active.map(card)) : empty("No agents running right now.", "bot")));
    if (recent.length) wrap.append(section("Recent", el("div", { class: "stack" }, recent.slice(0, 40).map(card))));
    return wrap;
  }

  patchProgress() {
    for (const node of this.body.querySelectorAll(".task-card.st-running")) {
      const text = this.app.taskProgress.get(node.dataset.task);
      if (text) node.querySelector(".task-progress").textContent = text;
    }
  }

  async renderTask(taskId) {
    const { task, events, live } = await api.get(`/api/tasks/${encodeURIComponent(taskId)}`);
    const wrap = el("div", { class: "panel" });
    wrap.append(btn("All agents", { kind: "link", icon: "chevronLeft", onClick: () => { this.opts = {}; this.refresh(); } }));
    wrap.append(el("div", { class: "task-title" }, el("h3", { text: task.title }), pill(task.status)));
    const meta = [`${task.cwd}`, `started ${timeAgo(task.started_at || task.created_at)}`];
    if (task.finished_at) meta.push(`took ${duration(task.finished_at - (task.started_at || task.created_at))}`);
    wrap.append(el("div", { class: "task-meta", text: meta.join(" · ") }));
    if (task.status === "running") {
      const { pending } = await api.get("/api/interactions");
      for (const ask of pending.filter((p) => p.task_id === task.id)) {
        wrap.append(renderPendingApproval(ask, async (id, decision) => { await this.app.resolveInteraction(id, decision); this.refresh(); }));
      }
      const steer = el("input", { class: "input", placeholder: "Send the agent extra instructions…" });
      wrap.append(el("div", { class: "live-progress", text: live.progress || "Working…" }),
        el("div", { class: "row" }, steer,
          btn("Send", { kind: "soft", icon: "send", onClick: async () => {
            if (!steer.value.trim()) return;
            try { await api.post(`/api/tasks/${task.id}/message`, { text: steer.value }); steer.value = ""; toast("Sent to agent", "", { kind: "success" }); }
            catch (err) { toast("Couldn't message agent", err.message, { kind: "error" }); }
          } }),
          btn("Stop", { kind: "danger-soft", icon: "stop", onClick: async () => { await api.post(`/api/tasks/${task.id}/stop`); this.refresh(); } })));
    }
    const prompt = el("details", { class: "fold" }, el("summary", { text: "Instructions" }), el("div", { class: "md small", html: renderMarkdown(task.prompt) }));
    wrap.append(prompt);
    if (task.summary || task.error) {
      wrap.append(section("Report", el("div", { class: "md", html: renderMarkdown(task.summary || `**Error:** ${task.error}`) })));
    }
    const kindIcon = { message: "sparkles", command: "terminal", file_change: "edit", tool: "wrench", web_search: "globe", plan: "list", approval: "hand", steer: "send", finished: "check", created: "plus", subagent: "layers" };
    const timeline = el("ol", { class: "timeline" }, events.map((e) => {
      const item = el("li", { class: `tl-${e.kind}` }, el("span", { class: "tl-ic", html: icon(kindIcon[e.kind] || "dot", 14) }));
      const body = el("div", { class: "tl-body" });
      if (e.kind === "message") body.append(el("div", { class: "md small", html: renderMarkdown(e.content) }));
      else if (e.kind === "command") {
        body.append(el("code", { class: "mono", text: e.content }));
        if (e.data?.exitCode !== undefined && e.data?.exitCode !== null) body.append(el("span", { class: e.data.exitCode === 0 ? "ok small" : "bad small", text: ` exit ${e.data.exitCode}` }));
        if (e.data?.output) body.append(el("details", { class: "fold" }, el("summary", { text: "output" }), el("pre", { class: "cmd-out", text: e.data.output })));
      } else body.append(el("span", { text: e.content }));
      body.append(el("span", { class: "tl-time", text: timeAgo(e.created_at) }));
      item.append(body);
      return item;
    }));
    wrap.append(section("Timeline", events.length ? timeline : empty("Nothing yet.")));
    return wrap;
  }

  // ================================================================== EVOLUTION
  async render_evolution(opts) {
    if (opts.proposal) return this.renderProposal(opts.proposal);
    const [{ proposals, mode, current }, { skills }, evals] = await Promise.all([api.get("/api/proposals"), api.get("/api/skills"), api.get("/api/evals")]);
    const wrap = el("div", { class: "panel" });
    const modeText = {
      off: "Self-evolution is off. Ideas are saved but nothing is built.",
      propose: "Weebo proposes upgrades and waits for your go-ahead before building.",
      build: "Weebo builds and verifies upgrades on its own in an isolated worktree. You approve every merge.",
      auto_merge: "Weebo builds, verifies and merges low-risk upgrades (UI, tests, docs, skills) by itself. Core changes still wait for you.",
    }[mode];
    wrap.append(el("div", { class: "mode-banner" }, el("span", { html: icon("dna", 18) }), el("div", {}, el("strong", { text: `Mode: ${mode.replace("_", "-")}` }), el("p", { text: modeText })),
      btn("Change", { kind: "link", size: "sm", onClick: () => this.open("settings", { focus: "evolution" }) })));

    const form = el("form", { class: "stack form-card", hidden: true });
    const t = el("input", { class: "input", placeholder: "What should Weebo change about itself?", required: true });
    const d = el("textarea", { class: "input", rows: 4, placeholder: "Describe the change and how to tell it works.", required: true });
    form.append(t, d, el("div", { class: "row end" }, btn("Cancel", { onClick: () => { form.hidden = true; } }), btn("Propose", { kind: "primary", icon: "sparkles", onClick: () => form.requestSubmit() })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { const { proposal } = await api.post("/api/proposals", { title: t.value, description: d.value }); this.open("evolution", { proposal: proposal.id }); }
      catch (err) { toast("Couldn't propose", err.message, { kind: "error" }); }
    });
    wrap.append(el("div", { class: "row" }, btn("Suggest an upgrade", { kind: "primary", icon: "plus", onClick: () => { form.hidden = false; t.focus(); } })), form);

    const needs = proposals.filter((p) => ["ready", "conflict"].includes(p.status) || (p.status === "proposed" && mode !== "off"));
    const progress = proposals.filter((p) => ["vetting", "queued", "building", "checking", "merging"].includes(p.status));
    const history = proposals.filter((p) => !needs.includes(p) && !progress.includes(p));
    const card = (p) => {
      const step = proposalStep(p);
      const result = ((p.meta || {}).outcome || {}).result;
      const outcome = result === "held" ? " · fix held" : result === "regressed" ? " · failure came back" : "";
      return el("button", { class: `proposal-card st-${p.status}`, type: "button", onclick: () => this.open("evolution", { proposal: p.id }) },
        el("div", { class: "task-top" }, el("span", { html: icon(p.id === current ? "gear" : "dna", 16, p.id === current ? "spinning" : "") }), el("strong", { text: p.title }), pill(p.status)),
        step ? el("div", { class: "task-progress", text: step }) : null,
        el("div", { class: "task-meta", text: `${p.source === "user" ? "you asked" : p.source} · ${timeAgo(p.created_at)}${p.diff_stat ? " · " + p.diff_stat.split("\n").pop().trim() : ""}${outcome}` }));
    };
    wrap.append(section("Needs you", needs.length ? el("div", { class: "stack" }, needs.map(card)) : empty("Nothing waiting for review.", "check")));
    if (progress.length) wrap.append(section("In progress", el("div", { class: "stack" }, progress.map(card))));
    if (history.length) wrap.append(section("History", el("div", { class: "stack" }, history.slice(0, 30).map(card))));

    wrap.append(this.renderEvals(evals));

    const used = (s) => (s.uses ? `Used ${s.uses} time${s.uses > 1 ? "s" : ""}, last ${timeAgo(s.last_used_at)}` : "Not used yet");
    const skillList = skills.length ? el("div", { class: "stack" }, skills.map((s) => el("div", { class: "skill-row" },
      el("div", {}, el("strong", { text: s.name }), el("p", { class: "muted small", text: s.description }), el("p", { class: "muted small", text: used(s) })),
      el("div", { class: "row" },
        btn("", { icon: "eye", title: "View", size: "sm", onClick: () => modal(s.name, el("div", { class: "md", html: renderMarkdown(s.preview) }), { wide: true }) }),
        btn("", { icon: "trash", title: "Forget skill", size: "sm", onClick: async () => {
          if (await confirmDialog("Forget skill?", `Weebo will no longer know "${s.name}".`, "Forget")) { await api.del(`/api/skills/${s.name}`); this.refresh(); }
        } })))))
      : empty("No learned skills yet. Weebo saves reusable procedures here as it figures them out.", "sparkles");
    wrap.append(section("Learned skills", el("p", { class: "muted small", text: "Skills Weebo learned on its own while dreaming are archived after a stretch of not being used (Settings → Memory & skills). Skills saved in a chat stay." }), skillList));
    return wrap;
  }

  /** Behavior checks: real moments Weebo got wrong, replayed so upgrades to its brain can't make it worse. */
  renderEvals({ cases, stats, enabled }) {
    const active = cases.filter((c) => c.status === "active");
    const retired = cases.filter((c) => c.status !== "active");
    const last = stats.last_run ? `last run ${timeAgo(stats.last_run)}` : "not run yet";
    const intro = enabled
      ? `${active.length} check${active.length === 1 ? "" : "s"} · ${stats.passing}/${stats.checked} passing on the running Weebo · ${last}. They come from your corrections and from dreams; any upgrade to Weebo's brain or memory must pass at least as many.`
      : "Behavior checks are off (Settings → Self-evolution).";
    const run = btn("Run checks now", { kind: "soft", size: "sm", icon: "play", onClick: async () => {
      try { await api.post("/api/autonomy/eval"); toast("Running behavior checks", "Results land here when they finish.", { kind: "success" }); }
      catch (err) { toast("Couldn't start", err.message, { kind: "error" }); }
    } });
    const form = el("form", { class: "stack form-card", hidden: true });
    const prompt = el("textarea", { class: "input", rows: 2, placeholder: "What you'd say to Weebo, e.g. \"Book my usual table for Friday\"", required: true });
    const rubric = el("textarea", { class: "input", rows: 2, placeholder: "What a good reply must do, e.g. \"Uses the restaurant I always pick and asks only for the time\"", required: true });
    form.append(prompt, rubric, el("div", { class: "row end" }, btn("Cancel", { onClick: () => { form.hidden = true; } }), btn("Add check", { kind: "primary", onClick: () => form.requestSubmit() })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api.post("/api/evals", { prompt: prompt.value, rubric: rubric.value }); toast("Check added", "", { kind: "success" }); this.refresh(); }
      catch (err) { toast("Couldn't add", err.message, { kind: "error" }); }
    });
    const add = btn("Add a check", { kind: "ghost", size: "sm", icon: "plus", onClick: () => { form.hidden = false; prompt.focus(); } });
    const row = (c) => {
      const result = (c.meta || {}).last;
      const mark = !result ? icon("dot", 14) : result.passed ? icon("check", 14, "ok") : icon("x", 14, "bad");
      const status = !result ? "Not checked yet" : `${result.passed ? "Passes" : "Fails"}: ${result.reason || ""}`;
      const act = async (action) => {
        try { await api.post(`/api/evals/${c.id}/${action}`); this.refresh(); } catch (err) { toast("Couldn't update", err.message, { kind: "error" }); }
      };
      return el("div", { class: `eval-row${c.status === "active" ? "" : " retired"}` },
        el("span", { html: mark, title: status }),
        el("div", {}, el("strong", { text: c.title }), el("p", { class: "muted small", text: c.rubric }),
          el("p", { class: "muted small", text: `${c.source === "correction" ? "from your correction" : c.source === "dream" ? "noticed while dreaming" : "added by you"} · ${status}` })),
        c.status === "active"
          ? btn("", { icon: "x", title: "Retire this check", size: "sm", onClick: () => act("retire") })
          : btn("", { icon: "undo", title: "Use this check again", size: "sm", onClick: () => act("restore") }));
    };
    const list = active.length ? el("div", {}, active.map(row)) : empty("No behavior checks yet. When you correct Weebo, that moment becomes one.", "shield");
    const old = retired.length ? el("details", { class: "fold" }, el("summary", { text: `Retired (${retired.length})` }), el("div", {}, retired.map(row))) : null;
    return section("Behavior checks", el("p", { class: "muted small", text: intro }), el("div", { class: "row" }, run, add), form, list, old);
  }

  async renderProposal(id) {
    const { proposal: p } = await api.get(`/api/proposals/${encodeURIComponent(id)}`);
    const meta = p.meta || {};
    const wrap = el("div", { class: "panel" });
    wrap.append(btn("All upgrades", { kind: "link", icon: "chevronLeft", onClick: () => this.open("evolution") }));
    wrap.append(el("div", { class: "task-title" }, el("h3", { text: p.title }), pill(p.status)));
    wrap.append(el("div", { class: "task-meta", text: `from ${p.source === "user" ? "you" : p.source} · ${timeAgo(p.created_at)}${p.branch ? " · " + p.branch : ""}` }));
    const steps = stepper(p);
    if (steps) wrap.append(steps);
    const step = proposalStep(p);
    if (step && ["vetting", "building", "checking"].includes(p.status)) wrap.append(el("div", { class: "live-progress", text: step }));

    const act = async (action, confirmText) => {
      if (confirmText && !(await confirmDialog("Are you sure?", confirmText, action[0].toUpperCase() + action.slice(1), "danger"))) return;
      try {
        await api.post(`/api/proposals/${p.id}/${action}`);
        toast({ approve: "Building it", merge: "Merging upgrade", reject: "Rejected", rebuild: "Rebuilding", rollback: "Rolling back", discard: "Discarded" }[action] || "Done", p.title, { kind: "success" });
        this.refresh();
      } catch (err) { toast("Couldn't do that", err.message, { kind: "error" }); }
    };
    const actions = el("div", { class: "row wrap" });
    if (p.status === "proposed") actions.append(btn("Build it", { kind: "primary", icon: "play", onClick: () => act("approve") }), btn("Reject", { kind: "danger-soft", onClick: () => act("reject") }));
    if (p.status === "vetting") actions.append(btn("Build now", { kind: "soft", icon: "play", title: "Skip the Council and build it", onClick: () => act("approve") }), btn("Reject", { kind: "danger-soft", onClick: () => act("reject") }));
    if (p.status === "declined") actions.append(btn("Build anyway", { kind: "soft", icon: "play", onClick: () => act("approve") }), btn("Discard", { kind: "danger-soft", onClick: () => act("discard") }));
    if (p.status === "ready") actions.append(btn("Merge & restart", { kind: "primary", icon: "branch", onClick: () => act("merge") }), btn("Rebuild", { kind: "soft", icon: "refresh", onClick: () => act("rebuild") }), btn("Reject", { kind: "danger-soft", onClick: () => act("reject") }));
    if (["failed", "conflict", "rolled_back"].includes(p.status)) actions.append(btn("Rebuild", { kind: "primary", icon: "refresh", onClick: () => act("rebuild") }), btn("Discard", { kind: "danger-soft", onClick: () => act("discard") }));
    if (p.status === "merged") actions.append(btn("Roll back", { kind: "danger-soft", icon: "undo", onClick: () => act("rollback", "Revert this upgrade with a new git commit and restart Weebo?") }));
    if (p.task_id) actions.append(btn("Build log", { kind: "soft", icon: "bot", onClick: () => this.open("agents", { task: p.task_id }) }));
    if (actions.childElementCount) wrap.append(actions);

    if (meta.blocked && p.status === "proposed") {
      wrap.append(el("div", { class: "mode-banner warn" }, el("span", { html: icon("alert", 18) }), el("div", {}, el("strong", { text: "Waiting for a commit" }), el("p", { text: meta.blocked }))));
    }
    wrap.append(section("What", el("div", { class: "md", html: renderMarkdown(p.description) })));
    if (p.rationale) wrap.append(section("Why", el("div", { class: "md small", html: renderMarkdown(p.rationale) })));

    let gates = {};
    try { gates = JSON.parse(p.gate_report || "{}"); } catch { gates = {}; }
    const checks = el("div", { class: "checks" });
    for (const r of gates.results || []) {
      // Advisory checks (the original versions of rewritten tests) inform your review; they don't fail the build.
      const state = r.skipped ? "skip" : r.ok ? "pass" : r.advisory ? "warn" : "fail";
      const note = r.skipped ? "skipped" : `${r.advisory ? (r.ok ? "for review · " : "needs your eyes · ") : ""}${r.seconds.toFixed(1)}s`;
      const row = el("details", { class: `check ${state}`, open: r.advisory && !r.ok },
        el("summary", { html: `${icon(r.skipped ? "dot" : r.ok ? "check" : r.advisory ? "alert" : "x", 14)}<span>${escapeHtml(r.name)}</span><span class="muted small">${note}</span>` }),
        el("pre", { class: "cmd-out", text: r.output || "" }));
      checks.append(row);
    }
    if (gates.error) checks.append(el("div", { class: "check fail" }, el("span", { text: gates.error })));
    const council = meta.council || {};
    if (council.reason) { // older builds stored an end-of-build verdict ("reasoning"); that step no longer exists
      const ok = council.approved;
      const verdict = ok === false ? "declined" : ok ? `approved${council.value ? " · " + council.value + " value" : ""}` : "didn't vote";
      const body = el("div", { class: "md small", html: renderMarkdown(`**Judge:** ${council.reason}`) });
      if (council.skeptic) body.append(el("details", { class: "fold" }, el("summary", { text: "The Skeptic's case against it" }), el("div", { class: "md", html: renderMarkdown(council.skeptic) })));
      wrap.append(section("The Council", el("details", { class: `check ${ok === false ? "fail" : ok ? "pass" : "skip"}`, open: ok !== true },
        el("summary", { html: `${icon(ok === false ? "x" : ok ? "check" : "dot", 14)}<span>Is this worth building?</span><span class="muted small">${verdict}</span>` }), body)));
    }
    if (p.review_report) {
      checks.append(el("details", { class: `check ${meta.review_blocking ? "fail" : "pass"}` },
        el("summary", { html: `${icon(meta.review_blocking ? "x" : "shield", 14)}<span>Codex code review</span><span class="muted small">${meta.review_blocking ? "blocking findings" : "no blockers"}</span>` }),
        el("div", { class: "md small", html: renderMarkdown(p.review_report) })));
    }
    if (checks.childElementCount) wrap.append(section("Verification", checks));

    if (meta.governance && meta.governance.files) {
      const gov = meta.governance;
      const summary = gov.autonomous ? "Low-risk zones only: eligible for automatic merge." : (gov.why || "Touches protected zones: needs your approval to merge.");
      wrap.append(section("Governance", el("p", { class: "muted small", text: summary }),
        el("table", { class: "gov" }, gov.files.map((f) => el("tr", { title: f.reason || "" }, el("td", { class: "mono", text: f.path }), el("td", { text: f.zone.replace("_", " ") }), el("td", {}, el("span", { class: `pill tier-${f.tier}`, text: f.tier.replace("_", " ") })))))));
    }
    const outcome = proposalOutcome(p);
    if (outcome) wrap.append(section("Did it help?", outcome));
    if (meta.diff) {
      wrap.append(section(`Changes ${p.diff_stat ? "· " + p.diff_stat.split("\n").pop().trim() : ""}`,
        btn("View full diff", { kind: "soft", icon: "diff", onClick: () => modal(p.title, renderDiff(meta.diff), { wide: true }) })));
    }
    if ((meta.rounds || []).length > 1) {
      wrap.append(section("Revision rounds", el("ol", { class: "rounds" }, meta.rounds.map((r) => el("li", { text: `Round ${r.round}: tests ${r.gates_ok ? "passed" : "failed"}, review ${r.review_blocking ? "found blocking issues" : "clear"}` })))));
    }
    return wrap;
  }

  // ================================================================== MEMORY
  async render_facts(opts) {
    const entity = opts.entity || "events";
    const offset = opts.offset || 0;
    const result = await api.get(`/api/facts/${encodeURIComponent(entity)}?offset=${offset}`);
    const wrap = el("div", { class: "panel facts-panel" });
    wrap.append(el("p", { class: "panel-intro", text: "Facts are looked up from their sources, not remembered from chat. Disconnected sources are shown as unverified." }));
    wrap.append(el("div", { class: "chips" }, ["events", "threads", "commitments", "people"].map(key => btn(key, {
      kind: key === entity ? "primary" : "soft", onClick: () => { this.opts = { entity: key }; this.refresh(); }
    }))));
    for (const source of result.sources || []) {
      wrap.append(el("p", { class: "muted small", text: source.ok
        ? `${source.source} · checked ${dateTime(source.fetched_at)}`
        : `${source.source || entity}: ${source.error}` }));
    }
    if (result.unverified?.length) wrap.append(el("p", { role: "status", text: `${result.unverified.length} stored facts are unverified. Reconnect the source to check them.` }));
    if (!result.facts.length) wrap.append(empty(result.complete ? "No matching facts in this slice." : "Current facts could not be verified.", "brain"));
    for (const fact of result.facts) {
      const p = fact.provenance || {};
      const details = [fact.status, fact.start != null ? `${new Date(fact.start * 1000).toLocaleString(undefined, { timeZone: fact.tz })} · ${fact.tz}` : "",
        fact.due != null ? `Due ${dateTime(fact.due)}` : ""].filter(Boolean).join(" · ");
      wrap.append(section(fact.title || fact.subject || fact.text || fact.name,
        el("p", { text: details }),
        el("p", { class: "muted small", text: `Source: ${p.source} · checked ${dateTime(p.fetched_at)} · ${fact.id}` })));
    }
    if (offset > 0) wrap.append(btn("Previous facts", { kind: "soft", onClick: () => { this.opts = { entity, offset: Math.max(0, offset - 50) }; this.refresh(); } }));
    if (result.next_offset != null) wrap.append(btn("More facts", { kind: "soft", onClick: () => { this.opts = { entity, offset: result.next_offset }; this.refresh(); } }));
    return wrap;
  }

  async render_memory(opts) {
    const q = opts.q || "";
    const kind = opts.kind || "";
    const { memories, stats } = await api.get(`/api/memories?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}`);
    const wrap = el("div", { class: "panel" });
    wrap.append(btn("Source-backed facts", { kind: "soft", icon: "brain", onClick: () => this.open("facts") }));
    wrap.append(el("p", { class: "panel-intro", text: `Everything Weebo remembers about you and your world: ${stats.total} memories. Weebo adds to this as you talk and tidies it up while it dreams.` }));
    const search = el("input", { class: "input", type: "search", placeholder: "Search memories…", value: q });
    search.addEventListener("input", debounce(() => { this.opts = { ...this.opts, q: search.value }; this.refresh(); }, 300));
    wrap.append(search);
    const kinds = ["", "preference", "fact", "person", "goal", "project", "lesson", "insight", "episode"];
    wrap.append(el("div", { class: "chips" }, kinds.map((k) => el("button", { class: `chip${k === kind ? " on" : ""}`, type: "button",
      onclick: () => { this.opts = { ...this.opts, kind: k }; this.refresh(); } }, k ? `${k} ${stats.by_kind[k] ? "· " + stats.by_kind[k] : ""}` : "all"))));

    const form = el("form", { class: "row form-inline" });
    const text = el("input", { class: "input", placeholder: "Teach Weebo something…", required: true });
    const k = el("select", { class: "input narrow" }, kinds.slice(1, 7).map((x) => el("option", { value: x, text: x })));
    form.append(text, k, btn("", { icon: "plus", title: "Remember", kind: "primary", onClick: () => form.requestSubmit() }));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { const { action } = await api.post("/api/memories", { text: text.value, kind: k.value, importance: 4 }); toast(action === "added" ? "Remembered" : "Updated an existing memory", text.value, { kind: "success" }); text.value = ""; this.refresh(); }
      catch (err) { toast("Couldn't save", err.message, { kind: "error" }); }
    });
    wrap.append(form);

    if (!memories.length) {
      wrap.append(empty(q ? "No memories match that." : "No memories yet. Tell Weebo about yourself!", "brain"));
      if (!q) wrap.append(btn("Import memories from Weebo 1.x", { kind: "soft", icon: "inbox", onClick: async () => { const r = await api.post("/api/memories/import-legacy"); toast("Imported", `${r.imported.facts} facts, ${r.imported.episodes} episodes`, { kind: "success" }); this.refresh(); } }));
      return wrap;
    }
    const list = el("div", { class: "memory-list" });
    for (const m of memories) {
      const textNode = el("div", { class: "mem-text", text: m.text, title: "Click to edit", tabindex: 0 });
      textNode.addEventListener("click", () => {
        const area = el("textarea", { class: "input", rows: 3 }, m.text);
        const save = async () => {
          if (area.value.trim() && area.value.trim() !== m.text) {
            try { await api.patch(`/api/memories/${m.id}`, { text: area.value.trim() }); } catch (err) { toast("Couldn't save", err.message, { kind: "error" }); }
          }
          this.refresh();
        };
        area.addEventListener("blur", save);
        area.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); area.blur(); } if (e.key === "Escape") this.refresh(); });
        textNode.replaceWith(area);
        area.focus();
      });
      const stars = el("span", { class: "stars", title: "Importance" });
      for (let i = 1; i <= 5; i++) {
        stars.append(el("button", { class: `star${i <= m.importance ? " on" : ""}`, type: "button", "aria-label": `Importance ${i}`,
          onclick: async () => { await api.patch(`/api/memories/${m.id}`, { importance: i }); this.refresh(); } }));
      }
      list.append(el("div", { class: `memory${m.pinned ? " pinned" : ""}` }, textNode,
        el("div", { class: "mem-meta" },
          el("span", { class: `kind k-${m.kind}`, text: m.kind }), stars,
          el("span", { class: "muted small", text: `${m.source ? m.source.split(":")[0] + " · " : ""}${timeAgo(m.updated_at)}` }),
          el("span", { class: "grow" }),
          btn("", { icon: "pin", title: m.pinned ? "Unpin" : "Pin (always in context)", size: "sm", onClick: async () => { await api.patch(`/api/memories/${m.id}`, { pinned: !m.pinned }); this.refresh(); } }),
          btn("", { icon: "trash", title: "Forget", size: "sm", onClick: async () => { await api.del(`/api/memories/${m.id}`); this.refresh(); } }))));
    }
    wrap.append(list);
    return wrap;
  }

  // ================================================================== MORE (the tab bar's overflow)
  async render_more() {
    const wrap = el("div", { class: "panel" });
    const go = (name, label, ic, hint) => el("button", { class: "more-row", type: "button", onclick: () => this.open(name) },
      el("span", { class: "more-ic", html: icon(ic, 18) }),
      el("span", { class: "grow" }, el("div", { text: label }), el("div", { class: "muted small", text: hint })),
      el("span", { html: icon("send", 14) }));
    wrap.append(el("div", { class: "stack" },
      go("agents", `Agents${this.app.runningTasks ? ` (${this.app.runningTasks} running)` : ""}`, "bot", "One-off background jobs"),
      go("memory", "Memory", "brain", "What Weebo remembers, and the source-backed facts"),
      go("capabilities", "Capabilities", "plug", "Connectors, apps, skills, workflows and what's missing"),
      go("schedule", "Schedule", "clock", "Reminders and routines"),
      go("activity", "Activity", "pulse", "What Weebo did on its own, and its notifications"),
      go("settings", "Settings", "gear", "Or just tell Weebo what to change"),
      go("archive", "Older chats", "inbox", "Conversations from before there was one chat")));
    return wrap;
  }

  // ================================================================== BOTS (the team)
  async render_bots(opts) {
    if (opts.bot) return this.renderBot(opts.bot);
    const [{ bots: all, changes }, policy] = await Promise.all([api.get("/api/bots?all=1"), api.get("/api/policy")]);
    const bots = all.filter((b) => b.status !== "archived");
    this.app.setBots(bots);
    const wrap = el("div", { class: "panel" });
    wrap.append(el("p", { class: "panel-intro", text: "Your team: specialist bots that work for Weebo, each in its own chat with its own model, tools and budget. Weebo proposes new ones; nothing joins the team without your yes." }));
    const live = bots.filter((b) => b.status !== "proposed");
    wrap.append(live.length ? el("div", { class: "bot-strip", role: "list" }, live.map((b) => this.botChip(b)))
      : empty("No bots yet. Ask Weebo for one, or approve its proposals.", "users"));
    const proposed = bots.filter((b) => b.status === "proposed");
    if (proposed.length) {
      wrap.append(section(`Proposed (${proposed.length})`, el("div", { class: "stack" }, proposed.map((b) => el("div", { class: "cap-row" },
        el("div", { class: "row" }, el("span", { class: "bot-face", text: b.name.slice(0, 1) }), el("strong", { text: b.name }),
          el("span", { class: "muted small", text: TIER_LABEL[b.data.tier] || b.data.tier })),
        el("p", { class: "small", text: b.data.job }),
        el("div", { class: "row" },
          btn("Approve", { kind: "primary", size: "sm", icon: "check", onClick: () => this.botAction(b.id, "approve") }),
          btn("Details", { kind: "soft", size: "sm", onClick: () => { this.opts = { bot: b.id }; this.refresh(); } }),
          btn("Not now", { kind: "ghost", size: "sm", onClick: () => this.botAction(b.id, "reject") })))))));
    }
    if (policy.needs_you.length) {
      wrap.append(section(`Waiting on you (${policy.needs_you.length})`, el("div", { class: "stack" }, policy.needs_you.map((item) => el("div", { class: "cap-row" },
        el("span", { text: item.data.summary }),
        el("div", { class: "row" },
          btn("Allow once", { kind: "primary", size: "sm", onClick: () => this.post(`/api/needs-you/${item.id}/approve`) }),
          btn("Always allow", { kind: "soft", size: "sm", onClick: () => this.post(`/api/needs-you/${item.id}/always`) }),
          btn("Deny", { kind: "ghost", size: "sm", onClick: () => this.post(`/api/needs-you/${item.id}/deny`) })))))));
    }
    const asks = changes.filter((c) => c.status === "proposed");
    if (asks.length) wrap.append(section("Changes waiting for your yes", el("div", { class: "stack" }, asks.map((c) => el("div", { class: "cap-row" },
      el("span", { text: `${(bots.find((b) => b.id === c.bot_id) || {}).name || "A bot"}: ${(c.data.reasons || []).join("; ")}` }),
      el("div", { class: "row" },
        btn("Approve", { kind: "primary", size: "sm", onClick: () => this.post(`/api/bot-changes/${c.id}/approve`) }),
        btn("Reject", { kind: "ghost", size: "sm", onClick: () => this.post(`/api/bot-changes/${c.id}/reject`) })))))));
    wrap.append(this.reportRules(policy.report_rules, bots));
    wrap.append(section("Standing permissions", policy.standing_rules.length ? el("div", { class: "stack" }, policy.standing_rules.map((r) =>
      el("div", { class: "row" }, el("span", { class: "grow small", text: `${r.effect === "deny" ? "Never" : "Always"} ${r.action} ${r.target || "(anything)"} · ${r.scope === "*" ? "every bot" : (bots.find((b) => b.id === r.scope) || {}).name || r.scope}${r.data.once ? " · once" : ""}` }),
        btn("Revoke", { kind: "ghost", size: "sm", icon: "x", onClick: () => this.del(`/api/policy/standing/${r.id}`) }))))
      : el("p", { class: "muted small", text: "None yet. “Always allow” on a request saves one here." })));
    const retired = all.filter((b) => b.status === "archived");
    if (retired.length) wrap.append(section("Retired", el("div", { class: "stack" }, retired.map((b) => el("button", { class: "more-row", type: "button",
      onclick: () => { this.close(); this.app.openConversation(b.conversation_id); } },
    el("span", { class: "bot-face", text: b.name.slice(0, 1) }),
    el("span", { class: "grow" }, el("div", { text: b.name }), el("div", { class: "muted small", text: "Its chat, kept to read" })))))));
    return wrap;
  }

  botChip(b) {
    const busy = this.app.busyConvs.has(b.conversation_id);
    const chip = el("button", { class: `bot-chip st-${b.status}${busy ? " busy" : ""}`, type: "button", role: "listitem",
      onclick: () => { this.opts = { bot: b.id }; this.refresh(); } },
    el("span", { class: "bot-face", text: b.name.slice(0, 1) }), el("span", { text: b.name }),
    el("span", { class: "muted small", text: b.status === "paused" ? "paused" : busy ? "working" : `${b.usage_today}/${b.budget}` }));
    if (b.unread) chip.append(el("b", { class: "badge", text: "•" }));
    return chip;
  }

  reportRules(rules, bots) {
    const name = (scope) => scope === "*" ? "Every bot" : (bots.find((b) => b.id === scope) || {}).name || scope;
    const scope = el("select", { class: "input narrow" }, [el("option", { value: "*", text: "Every bot" }), ...bots.filter((b) => b.status !== "proposed").map((b) => el("option", { value: b.id, text: b.name }))]);
    const match = el("input", { class: "input", placeholder: "About… e.g. nightly PR fixes" });
    const bucket = el("select", { class: "input narrow" }, BUCKETS.map(([v, l]) => el("option", { value: v, text: l })));
    const form = el("form", { class: "row wrap" }, scope, match, bucket, btn("Add", { kind: "soft", size: "sm", onClick: () => form.requestSubmit() }));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!match.value.trim()) return;
      await this.post("/api/policy/report", { scope: scope.value, match: match.value, bucket: bucket.value });
      match.value = "";
    });
    return section("What reaches you", el("p", { class: "muted small", text: "Reports go to you now, into the morning brief, or only into the log. Trouble always reaches at least the brief." }),
      rules.length ? el("div", { class: "stack" }, rules.map((r) => el("div", { class: "row" },
        el("span", { class: "grow small", text: `${name(r.scope)} · “${r.match}” → ${(BUCKETS.find(([v]) => v === r.bucket) || [, r.bucket])[1]}` }),
        btn("Remove", { kind: "ghost", size: "sm", icon: "x", onClick: () => this.del(`/api/policy/report/${r.id}`) })))) : null,
      form);
  }

  async renderBot(botId) {
    const { bot, jobs, reports, usage, report_rules: rules } = await api.get(`/api/bots/${encodeURIComponent(botId)}`);
    const p = bot.data;
    const wrap = el("div", { class: "panel" });
    wrap.append(btn("All bots", { kind: "link", icon: "chevronLeft", onClick: () => { this.opts = {}; this.refresh(); } }));
    wrap.append(el("div", { class: "task-title" }, el("span", { class: "bot-face", text: bot.name.slice(0, 1) }), el("h3", { text: bot.name }), pill(bot.status)));
    const actions = el("div", { class: "row wrap" });
    if (bot.conversation_id) actions.append(btn("Open its chat", { kind: "primary", size: "sm", icon: "chat", onClick: () => { this.close(); this.app.openConversation(bot.conversation_id); } }));
    if (bot.status === "proposed") actions.append(btn("Approve", { kind: "primary", size: "sm", icon: "check", onClick: () => this.botAction(bot.id, "approve") }));
    if (bot.status === "active") actions.append(btn("Pause", { kind: "soft", size: "sm", onClick: () => this.botAction(bot.id, "pause") }));
    if (bot.status === "paused") actions.append(btn("Resume", { kind: "soft", size: "sm", onClick: () => this.botAction(bot.id, "resume") }));
    if (["active", "paused"].includes(bot.status)) {
      actions.append(btn("Retire", { kind: "ghost", size: "sm", onClick: async () => {
        if (await confirmDialog(`Retire ${bot.name}?`, "Its chat stays readable; its routines stop and its Codex thread is put away.", "Retire")) this.botAction(bot.id, "archive");
      } }),
      btn("Delete", { kind: "danger-soft", size: "sm", onClick: async () => {
        if (await confirmDialog(`Delete ${bot.name}?`, "This removes the bot, its chat and its history for good.", "Delete")) this.botAction(bot.id, "delete");
      } }));
    }
    wrap.append(actions);
    wrap.append(el("div", { class: "task-meta", text: `${TIER_LABEL[p.tier] || p.tier} · ${bot.model || "model picked at runtime"} · ${bot.usage_today}/${bot.budget} turns today` }));

    // Profile: your edits apply directly (you are the one who can say yes).
    const save = async (changes) => {
      try { await api.patch(`/api/bots/${bot.id}`, changes); toast("Saved", "", { kind: "success", timeout: 1200 }); this.refresh(); }
      catch (err) { toast("Couldn't save", err.message, { kind: "error" }); }
    };
    const job = el("textarea", { class: "input", rows: 3, text: p.job || "" });  // a textarea's value is its text
    const goals = el("textarea", { class: "input", rows: 3, text: (p.goals || []).join("\n"), placeholder: "One goal per line" });
    const tier = el("select", { class: "input narrow" }, Object.entries(TIER_LABEL).map(([v, l]) => el("option", { value: v, text: l, selected: v === p.tier })));
    const effort = el("select", { class: "input narrow" }, EFFORTS.map((v) => el("option", { value: v, text: v, selected: v === p.effort })));
    const turns = el("input", { class: "input narrow", type: "number", min: 1, max: 500, value: p.turns_per_day });
    const report = el("select", { class: "input narrow" }, BUCKETS.map(([v, l]) => el("option", { value: v, text: l, selected: v === p.report_default })));
    const checks = (all, chosen) => el("div", { class: "chip-row" }, all.map((name) => el("label", { class: "chip" },
      el("input", { type: "checkbox", value: name, checked: (chosen || []).includes(name) }), el("span", { text: name }))));
    const tools = checks(GRANTABLE, p.tools);
    const autonomy = checks(AUTONOMY, p.autonomy);
    const picked = (box) => [...box.querySelectorAll("input:checked")].map((i) => i.value);
    const form = el("form", { class: "stack form-card" },
      el("label", { class: "field" }, el("span", { class: "field-label", text: "Job" }), job),
      el("label", { class: "field" }, el("span", { class: "field-label", text: "Goals" }), goals),
      el("div", { class: "row wrap" },
        el("label", { class: "field" }, el("span", { class: "field-label", text: "Model tier" }), tier),
        el("label", { class: "field" }, el("span", { class: "field-label", text: "Effort" }), effort),
        el("label", { class: "field" }, el("span", { class: "field-label", text: "Turns a day" }), turns),
        el("label", { class: "field" }, el("span", { class: "field-label", text: "Reports" }), report)),
      el("div", { class: "field" }, el("span", { class: "field-label", text: "Tools" }), tools),
      el("div", { class: "field" }, el("span", { class: "field-label", text: "May do without asking" }), autonomy),
      el("div", { class: "row end" }, btn("Save profile", { kind: "primary", size: "sm", onClick: () => form.requestSubmit() })));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      save({ job: job.value, goals: goals.value.split("\n").map((g) => g.trim()).filter(Boolean), tier: tier.value,
        effort: effort.value, turns_per_day: Number(turns.value), report_default: report.value,
        tools: picked(tools), autonomy: picked(autonomy) });
    });
    wrap.append(section("Profile", form));

    const ruleInput = el("input", { class: "input", placeholder: "Add a standing rule, e.g. never archive newsletters" });
    wrap.append(section("Standing rules", el("div", { class: "stack" }, (p.rules || []).map((r) => el("div", { class: "row" },
      el("span", { class: "grow small", text: `${r.text}${r.added_by === "user" ? "" : ` (from ${r.added_by})`}` }),
      btn("Remove", { kind: "ghost", size: "sm", icon: "x", onClick: () => save({ rules_remove: r.text }) })))),
    el("div", { class: "row" }, ruleInput, btn("Add", { kind: "soft", size: "sm", onClick: () => { if (ruleInput.value.trim()) save({ rules_add: ruleInput.value.trim() }); } }))));
    if ((p.routines || []).length) wrap.append(section("Routines", el("ul", { class: "bullets" }, p.routines.map((r) => el("li", { text: `${r.text} · ${r.recurrence} from ${r.when}` })))));

    const jobInput = el("textarea", { class: "input", rows: 2, placeholder: `Give ${bot.name} a job…` });
    wrap.append(section("Jobs", bot.status === "active" ? el("div", { class: "row" }, jobInput, btn("Assign", { kind: "soft", size: "sm", icon: "send", onClick: async () => {
      if (!jobInput.value.trim()) return;
      try { await api.post(`/api/bots/${bot.id}/jobs`, { instructions: jobInput.value }); jobInput.value = ""; toast("Assigned", "", { kind: "success" }); this.refresh(); }
      catch (err) { toast("Couldn't assign", err.message, { kind: "error" }); }
    } })) : null,
    jobs.length ? el("div", { class: "stack" }, jobs.slice(0, 12).map((j) => el("div", { class: "cap-row" },
      el("div", { class: "row" }, pill(j.status), el("span", { class: "small grow", text: j.data.instructions.slice(0, 140) }), el("span", { class: "muted small", text: timeAgo(j.created_at) })),
      j.data.result_summary ? el("p", { class: "small muted report-summary", text: j.data.result_summary }) : null))) : el("p", { class: "muted small", text: "No jobs yet." })));
    wrap.append(section("Reports", reports.length ? el("div", { class: "stack" }, reports.slice(0, 15).map((r) => el("div", { class: "row" },
      el("span", { class: "pill", text: r.bucket }), el("span", { class: "small grow report-summary", text: r.data.summary }), el("span", { class: "muted small", text: timeAgo(r.created_at) }))))
      : el("p", { class: "muted small", text: "No reports yet." })));
    wrap.append(section("Usage (last 14 days)", el("p", { class: "small", text: usage.map((u) => `${u.day.slice(5)}: ${u.turns}${u.failures ? ` (${u.failures} failed)` : ""}`).join(" · ") || "No turns yet." })));
    if (rules.length) wrap.append(section("Report rules for this bot", el("ul", { class: "bullets" }, rules.map((r) => el("li", { text: `“${r.match}” → ${r.bucket}` })))));
    return wrap;
  }

  async botAction(id, action) {
    try { await api.post(`/api/bots/${encodeURIComponent(id)}/${action}`, {}); this.refresh(); }
    catch (err) { toast("That didn't work", err.message, { kind: "error" }); }
  }

  async post(path, body) {
    try { await api.post(path, body || {}); this.refresh(); }
    catch (err) { toast("That didn't work", err.message, { kind: "error" }); }
  }

  async del(path) {
    try { await api.del(path); this.refresh(); }
    catch (err) { toast("That didn't work", err.message, { kind: "error" }); }
  }

  // ================================================================== IDEAS (and the day sheet)
  async render_ideas() {
    const [{ ideas }, sheet] = await Promise.all([api.get("/api/ideas"), api.get("/api/daysheet")]);
    this.app.setIdeasCount(ideas.length);
    const wrap = el("div", { class: "panel" });
    wrap.append(el("p", { class: "panel-intro", text: "Specific suggestions from your calendar, email and commitments, each with the facts behind it. Do it only runs safe things (reminders, drafts); anything involving other people goes through Weebo and your OK." }));
    wrap.append(ideas.length ? el("div", { class: "stack" }, ideas.map((idea) => this.ideaCard(idea))) : empty("No ideas right now. They appear as your calendar and email fill in.", "sparkles"));
    const rows = sheet.rows || [];
    const days = {};
    for (const row of rows) (days[row.day] = days[row.day] || []).push(row);
    wrap.append(section("Day sheet", !sheet.travel_source ? el("p", { class: "muted small", text: "Travel times show as unknown: no maps/directions connector is set up (see Capabilities)." }) : null,
      rows.length ? el("div", { class: "stack" }, Object.entries(days).map(([day, list]) => el("div", { class: "stack" },
        el("h4", { text: day }), ...list.map((row) => this.dayRow(row))))) : empty("Nothing coming up, or no calendar is connected.", "calendar")));
    return wrap;
  }

  ideaCard(idea) {
    const d = idea.data;
    const card = el("div", { class: `idea-card${idea.stale ? " stale" : ""}`, dataset: { idea: idea.id } },
      el("h4", { text: d.title }), el("p", { text: d.statement }),
      el("div", { class: "chip-row" }, d.evidence.map((e) => el("button", { class: "chip", type: "button", title: `Source: ${e.source || "?"}`,
        onclick: () => toast(e.label, `${e.entity} from ${e.source || "unknown source"}${e.fetched_at ? `, checked ${timeAgo(e.fetched_at)}` : ""}`) },
      el("span", { html: icon(e.entity === "events" ? "calendar" : e.entity === "threads" ? "mail" : "list", 12) }), el("span", { text: e.label })))));
    if (idea.stale) card.append(el("p", { class: "muted small", text: "Something it was based on changed; refreshing." }));
    if (d.note) card.append(el("p", { class: "muted small", text: d.note }));
    const safe = d.actions.findIndex((a) => a.safe);
    card.append(el("div", { class: "row wrap" },
      btn("Discuss", { kind: "soft", size: "sm", icon: "chat", onClick: async () => {
        try { await api.post(`/api/ideas/${idea.id}/discuss`, {}); this.close(); await this.app.openMain(); }
        catch (err) { toast("Couldn't open it", err.message, { kind: "error" }); }
      } }),
      safe >= 0 && !idea.stale && !d.pending_draft ? btn(`Do it: ${d.actions[safe].label}`, { kind: "primary", size: "sm", onClick: async () => {
        try { const result = await api.post(`/api/ideas/${idea.id}/do`, { action: safe });
          toast(result.result?.verified ? "Done" : "Draft requested", d.actions[safe].label,
            { kind: result.result?.verified ? "success" : "info" }); this.refresh(); }
        catch (err) { toast("Didn't do it", err.message, { kind: "error" }); this.refresh(); }
      } }) : null,
      btn("Not useful", { kind: "ghost", size: "sm", onClick: () => this.post(`/api/ideas/${idea.id}/dismiss`) })));
    return card;
  }

  dayRow(row) {
    const flags = [];
    if (row.cancelled) flags.push("cancelled");
    for (const c of row.conflicts || []) flags.push(`overlaps ${c.title}`);
    const t = row.travel;
    if (t) flags.push(t.status === "known" ? `${t.minutes} min travel (${t.source})${t.shortfall_minutes ? `, ${t.shortfall_minutes} min short` : ""}` : t.status === "unknown" ? `travel: unknown, ${t.gap_minutes} min gap` : "same place");
    if (row.timezone) flags.push(`${row.timezone.there} in ${row.timezone.event_tz}`);
    if (row.weather) flags.push(row.weather.source ? row.weather.summary : "weather unknown");
    for (const th of row.threads || []) flags.push(`email: ${th.subject}`);
    for (const p of row.people || []) flags.push(p.name);
    return el("div", { class: "day-row" }, el("div", { class: "row" }, el("strong", { text: row.local_time }), el("span", { text: row.title })),
      row.location ? el("span", { class: "muted small", text: row.location }) : null,
      flags.length ? el("div", { class: "flags" }, flags.map((f) => el("span", { class: "chip", text: f }))) : null,
      el("span", { class: "muted small", text: `Source: ${row.source || "?"}${row.stale ? " · refreshing" : ""}` }));
  }

  // ================================================================== CAPABILITIES (the growth layer)
  async render_capabilities() {
    const [caps, { apps }] = await Promise.all([api.get("/api/capabilities"), api.get("/api/connectors")]);
    const wrap = el("div", { class: "panel" });
    wrap.append(el("p", { class: "panel-intro", text: "Everything Weebo can do, who uses it and what it costs, plus what's missing. New connectors are registered, tested in isolation and approved by you before any bot can use them." }));
    wrap.append(section("Connectors", caps.connectors.length ? el("div", { class: "stack" }, caps.connectors.map((c) => el("div", { class: "cap-row" },
      el("div", { class: "row" }, el("strong", { text: c.name }), pill(c.status), el("span", { class: `risk r-${c.risk}`, text: c.risk })),
      el("span", { class: "muted small", text: `${c.kind === "app" ? "ChatGPT app" : "MCP"} · used by ${c.used_by.join(", ") || "nobody yet"} · ${c.data.uses || 0} uses${c.data.last_used ? `, last ${timeAgo(c.data.last_used)}` : ""}${c.data.failures ? ` · ${c.data.failures} failures in a row` : ""}` }),
      el("div", { class: "row" },
        ["registered", "failed", "tested", "disabled"].includes(c.status) ? btn("Test", { kind: "soft", size: "sm", onClick: () => this.post(`/api/connectors/${c.id}/test`) }) : null,
        c.status === "approved" ? btn("Disable", { kind: "ghost", size: "sm", onClick: () => this.post(`/api/connectors/${c.id}/disable`) }) : null))))
      : el("p", { class: "muted small", text: "No connectors yet." }), this.registerForm()));
    wrap.append(section("ChatGPT apps", apps.available
      ? el("div", { class: "chip-row" }, apps.apps.map((a) => el("span", { class: "chip", text: `${a.name || a.id}${a.isAccessible === false ? " (not connected)" : ""}` })))
      : el("p", { class: "muted small", text: apps.reason || "Unavailable." })));
    if (caps.gaps.length) wrap.append(section("Missing", el("div", { class: "stack" }, caps.gaps.map((g) => el("div", { class: "cap-row" },
      el("div", { class: "row" }, el("strong", { text: g.data.need }), el("span", { class: "muted small", text: `${g.count}×` }), pill(g.status)),
      g.status === "open" ? el("div", { class: "row" }, btn("Build a connector", { kind: "soft", size: "sm", onClick: () => this.post(`/api/gaps/${g.id}/build`) }),
        btn("Not needed", { kind: "ghost", size: "sm", onClick: () => this.post(`/api/gaps/${g.id}/dismiss`) })) : null)))));
    wrap.append(section("Workflows", caps.workflows.length ? el("div", { class: "stack" }, caps.workflows.map((w) => el("div", { class: "cap-row" },
      el("div", { class: "row" }, el("strong", { text: w.name }), pill(w.status)),
      el("span", { class: "muted small", text: `${w.data.steps.length} step(s) · ${w.data.runs || 0} runs · ${w.data.failures || 0} failures · ${w.data.skips || 0} skipped` }),
      el("div", { class: "row" },
        w.status === "proposed" ? btn("Approve", { kind: "primary", size: "sm", onClick: () => this.post(`/api/workflows/${w.id}/approve`) }) : null,
        w.status === "active" ? btn("Run now", { kind: "soft", size: "sm", onClick: () => this.post(`/api/workflows/${w.id}/run`) }) : null,
        w.status === "active" ? btn("Pause", { kind: "ghost", size: "sm", onClick: () => this.post(`/api/workflows/${w.id}/pause`) }) : null,
        w.status === "paused" ? btn("Resume", { kind: "soft", size: "sm", onClick: () => this.post(`/api/workflows/${w.id}/resume`) }) : null))))
      : el("p", { class: "muted small", text: "None yet. Ask for the same multi-step thing a few times and Weebo will offer one." })));
    wrap.append(section("Skills", caps.skills.length ? el("div", { class: "chip-row" }, caps.skills.map((sk) => el("span", { class: "chip", title: sk.description, text: `${sk.name} · ${sk.uses} uses` })))
      : el("p", { class: "muted small", text: "No skills yet." })));
    wrap.append(section("Bots", caps.bots.length ? el("div", { class: "chip-row" }, caps.bots.map((b) => el("button", { class: "chip", type: "button", text: `${b.name} · ${b.status} · ${b.usage_today}/${b.budget} turns`, onclick: () => this.open("bots", { bot: b.id }) })))
      : el("p", { class: "muted small", text: "No bots yet." })));
    return wrap;
  }

  registerForm() {
    const manifest = el("textarea", { class: "input", rows: 6, placeholder: '{"name": "My tool", "transport": "stdio", "command": ["python", "server.py"], "tools": [{"name": "lookup", "risk": "read"}]}' });
    const form = el("form", { class: "stack form-card", hidden: true }, el("p", { class: "muted small", text: "Paste a connector manifest (see weebo/connectors/template). Registering grants nothing: it's tested in isolation, then you approve it." }), manifest,
      el("div", { class: "row end" }, btn("Cancel", { kind: "ghost", size: "sm", onClick: () => { form.hidden = true; } }), btn("Register and test", { kind: "primary", size: "sm", onClick: () => form.requestSubmit() })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const { connector } = await api.post("/api/connectors", JSON.parse(manifest.value));
        await api.post(`/api/connectors/${connector.id}/test`, {});
        manifest.value = "";
        this.refresh();
      } catch (err) { toast("Couldn't register it", err.message, { kind: "error" }); }
    });
    return el("div", { class: "stack" }, btn("Add a connector", { kind: "soft", size: "sm", icon: "plus", onClick: () => { form.hidden = false; manifest.focus(); } }), form);
  }

  // ================================================================== ARCHIVE (older chats, read-only)
  async render_archive() {
    const wrap = el("div", { class: "panel" });
    wrap.append(el("p", { class: "panel-intro", text: "Everything happens in one chat now. Your older chats are kept here to read; Weebo can also search them when you ask about something from back then." }));
    const old = this.app.state.conversations.filter((c) => c.kind === "chat");
    wrap.append(old.length ? el("div", { class: "stack" }, old.map((c) => el("button", { class: "more-row", type: "button",
      onclick: () => { this.close(); this.app.openConversation(c.id); } },
      el("span", { class: "more-ic", html: icon("dot", 10) }),
      el("span", { class: "grow" }, el("div", { text: c.title }), el("div", { class: "muted small", text: timeAgo(c.updated_at) })))))
      : empty("No older chats.", "inbox"));
    return wrap;
  }

  // ================================================================== SCHEDULE
  async render_schedule(opts) {
    const { reminders } = await api.get(`/api/reminders${opts.all ? "?all=1" : ""}`);
    const wrap = el("div", { class: "panel" });
    wrap.append(el("p", { class: "panel-intro", text: "Reminders ping you. Routines make Weebo do something by itself on a schedule, like a morning weather check." }));
    const form = el("form", { class: "stack form-card" });
    const text = el("input", { class: "input", placeholder: "Remind me to… / Every morning, check…", required: true });
    const when = el("input", { class: "input", placeholder: "When? e.g. in 20 minutes, tomorrow 9am, friday 6pm", required: true });
    const repeat = el("select", { class: "input" }, [["", "Once"], ["daily", "Daily"], ["weekdays", "Weekdays"], ["weekly", "Weekly"], ["hourly", "Hourly"], ["monthly", "Monthly"]].map(([v, l]) => el("option", { value: v, text: l })));
    const action = el("select", { class: "input" }, [["notify", "Reminder"], ["prompt", "Routine (Weebo acts)"]].map(([v, l]) => el("option", { value: v, text: l })));
    form.append(text, el("div", { class: "row" }, when), el("div", { class: "row" }, repeat, action, btn("Add", { kind: "primary", icon: "plus", onClick: () => form.requestSubmit() })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api.post("/api/reminders", { text: text.value, when: when.value, recurrence: repeat.value, action: action.value }); text.value = ""; when.value = ""; this.refresh(); }
      catch (err) { toast("Couldn't schedule", err.message, { kind: "error" }); }
    });
    wrap.append(form);
    const list = reminders.length ? el("div", { class: "stack" }, reminders.map((r) => el("div", { class: `reminder-row st-${r.status}` },
      el("span", { class: "rem-ic", html: icon(r.action === "prompt" ? "zap" : "bell", 16) }),
      el("div", { class: "grow" }, el("div", { text: r.text }),
        el("div", { class: "muted small", text: `${r.status === "pending" ? dateTime(r.due_at) : r.status}${r.recurrence ? " · " + r.recurrence.replace(/^every (\d+)s$/, (_, s) => `every ${Math.round(s / 60)} min`) : ""}${r.action === "prompt" ? " · routine" : ""}` })),
      r.status === "pending" ? btn("", { icon: "x", title: "Cancel", size: "sm", onClick: async () => { await api.del(`/api/reminders/${r.id}`); this.refresh(); } }) : null)))
      : empty("Nothing scheduled.", "clock");
    wrap.append(section(opts.all ? "All" : "Upcoming", list));
    wrap.append(btn(opts.all ? "Show upcoming only" : "Show history", { kind: "link", size: "sm", onClick: () => { this.opts = { all: !opts.all }; this.refresh(); } }));
    return wrap;
  }

  // ================================================================== ACTIVITY
  async render_activity(opts) {
    const [status, { journal }, { notifications }, { diagnostics }] = await Promise.all([
      api.get("/api/status"), api.get("/api/journal?limit=80"), api.get("/api/notifications?limit=30"), api.get("/api/diagnostics"),
    ]);
    const hb = status.heartbeat;
    const wrap = el("div", { class: "panel" });
    const stat = (label, value, ok) => el("div", { class: `stat${ok === false ? " warn" : ""}` }, el("span", { class: "muted small", text: label }), el("strong", { text: value }));
    const unread = this.app.notifications.records();
    wrap.append(section("Notification inbox",
      el("p", { class: "muted small", text: "Open a notification to mark it read. Opening this list leaves other items unread." }),
      unread.length ? el("div", { class: "stack" }, unread.map((n) => this.app.notifications.renderRecord(n))) : empty("No unread notifications.")));
    wrap.append(el("div", { class: "stats-grid" },
      stat("Proactive budget", hb.budget_ok ? "Available" : "Paused", hb.budget_ok),
      stat("Background turns today", `${hb.background_turns_today}`),
      stat("Next brief", hb.next_brief),
      stat("Busy with", hb.busy || "nothing"),
      stat("Last dream", hb.last_dream ? timeAgo(hb.last_dream) : "never"),
      stat("Last self-audit", hb.last_audit ? timeAgo(hb.last_audit) : "never")));
    if (!hb.budget_ok) wrap.append(el("p", { class: "muted small", text: hb.budget_reason }));
    const trigger = (name, label, ic) => btn(label, { kind: "soft", icon: ic, size: "sm", onClick: async () => {
      try { await api.post(`/api/autonomy/${name}`); toast(`${label} started`, "Watch Weebo's desk and this feed.", { kind: "success" }); this.refresh(); }
      catch (err) { toast("Couldn't start", err.message, { kind: "error" }); }
    } });
    wrap.append(el("div", { class: "row wrap" }, trigger("dream", "Dream now", "moon"), trigger("brief", "Brief me now", "bell"), trigger("audit", "Self-audit now", "search"), trigger("eval", "Run behavior checks", "shield")));

    const kindIcon = { dream: "moon", brief: "bell", audit: "search", evolution: "dna", skill: "sparkles", memory: "brain", schedule: "clock", routine: "zap", system: "gear", evals: "shield" };
    wrap.append(section("What Weebo has been up to", journal.length ? el("ol", { class: "timeline" }, journal.map((j) => el("li", {},
      el("span", { class: "tl-ic", html: icon(kindIcon[j.kind] || "dot", 14) }),
      el("div", { class: "tl-body" }, el("strong", { text: j.title }), j.detail ? el("p", { class: "muted small", text: j.detail }) : null, el("span", { class: "tl-time", text: timeAgo(j.created_at) })))))
      : empty("Nothing yet.")));
    const read = notifications.filter((n) => n.read);
    if (read.length) wrap.append(section("Read notifications", el("div", { class: "stack" },
      read.map((n) => this.app.notifications.renderRecord(n)))));
    const open = diagnostics.filter((d) => d.status !== "fixed");
    if (open.length) {
      wrap.append(section("Self-diagnostics", el("p", { class: "muted small", text: "Failures Weebo noticed in itself. Repeated ones feed its self-audits." }),
        el("div", { class: "stack" }, open.slice(0, 12).map((d) => el("div", { class: "diag" }, el("span", { class: "pill", text: `×${d.count}` }), el("span", { class: "small", text: d.message.slice(0, 200) }))))));
    }
    const logsBox = el("div");
    wrap.append(section("Logs", btn(opts.logs ? "Hide logs" : "Show recent logs", { kind: "link", size: "sm", onClick: () => { this.opts = { logs: !opts.logs }; this.refresh(); } }), logsBox));
    if (opts.logs) {
      const { logs } = await api.get("/api/logs?limit=200&level=INFO");
      logsBox.append(el("pre", { class: "logs" }, logs.map((l) => el("span", { class: `lv-${l.level}`, text: `${new Date(l.ts * 1000).toLocaleTimeString()} ${l.level.padEnd(7)} ${l.logger.replace("weebo.", "")}: ${l.message}\n` }))));
    }
    return wrap;
  }

  // ================================================================== SETTINGS
  async render_settings(opts) {
    const [{ settings }, engine, status] = await Promise.all([api.get("/api/settings"), api.get("/api/engine"), api.get("/api/status")]);
    this.app.state.settings = settings;
    this.app.state.engine = engine;
    const wrap = el("div", { class: "panel settings" });
    wrap.append(section("Notifications",
      el("p", { class: "small", text: this.app.notifications.capability() }),
      el("p", { class: "muted small", text: "Unread alerts return when you open Weebo, reconnect, or bring it to the foreground. True background delivery, including phone Web Push, is not currently configured. Browser alerts require this page to be running; permission alone does not guarantee delivery." }),
      window.isSecureContext && "Notification" in window && Notification.permission === "default"
        ? btn("Enable browser alerts", { kind: "soft", size: "sm", onClick: () => this.app.notifications.enableDesktop() }) : null));
    const save = async (key, value) => {
      try {
        const res = await api.patch("/api/settings", { changes: { [key]: value } });
        this.app.applySettings(res.settings);
        toast("Saved", "", { kind: "success", timeout: 1200 });
      } catch (err) { toast("Couldn't save", err.message, { kind: "error" }); this.refresh(); }
    };
    const get = (key) => key.split(".").reduce((o, k) => (o || {})[k], settings);
    const field = (label, control, help) => el("label", { class: "field" }, el("span", { class: "field-label", text: label }), control, help ? el("span", { class: "field-help", text: help }) : null);
    const toggle = (key, label, help) => {
      const input = el("input", { type: "checkbox", class: "switch", checked: Boolean(get(key)), onchange: (e) => save(key, e.target.checked) });
      return el("label", { class: "field toggle" }, el("div", {}, el("span", { class: "field-label", text: label }), help ? el("span", { class: "field-help", text: help }) : null), input);
    };
    const select = (key, label, options, help) => field(label, el("select", { class: "input", onchange: (e) => save(key, e.target.value) },
      options.map(([v, l]) => el("option", { value: v, text: l, selected: String(get(key)) === String(v) }))), help);
    const number = (key, label, min, max, step = 1, help) => field(label, el("input", { class: "input narrow", type: "number", min, max, step, value: get(key), onchange: (e) => save(key, Number(e.target.value)) }), help);
    const text = (key, label, placeholder, help) => field(label, el("input", { class: "input", value: get(key) || "", placeholder, onchange: (e) => save(key, e.target.value) }), help);
    const time = (key, label) => field(label, el("input", { class: "input narrow", type: "time", value: get(key), onchange: (e) => save(key, e.target.value) }));
    const cards = (key, options) => el("div", { class: "choice-cards" }, options.map(([v, title, desc, ic]) => el("button", {
      class: `choice${get(key) === v ? " on" : ""}`, type: "button", onclick: () => save(key, v).then(() => this.refresh()) },
      el("span", { html: icon(ic, 18) }), el("strong", { text: title }), el("span", { class: "muted small", text: desc }))));

    // --- Codex
    const account = engine.account || {};
    const limits = engine.rateLimits || {};
    const meter = (w, label) => {
      if (!w) return null;
      const pct = Math.round(w.usedPercent || 0);
      const resets = w.resetsAt ? `resets ${dateTime(w.resetsAt)}` : "";
      return el("div", { class: "meter-row" }, el("div", { class: "meter-label" }, el("span", { text: label }), el("span", { class: "muted small", text: `${pct}% used ${resets}` })),
        el("div", { class: `meter${pct >= 80 ? " hot" : pct >= 60 ? " warm" : ""}` }, el("i", { style: { width: `${Math.min(100, pct)}%` } })));
    };
    const engineCard = el("div", { class: "form-card stack" },
      el("div", { class: "row" }, el("span", { class: `dot-status s-${engine.status}` }), el("strong", { text: { ready: "Connected", login_required: "Sign-in needed", starting: "Starting…", restarting: "Reconnecting…", error: "Not available", stopped: "Stopped" }[engine.status] || engine.status }),
        el("span", { class: "muted small", text: engine.version ? `Codex ${engine.version} · ${engine.source}` : "" })),
      account.email ? el("div", { class: "muted small", text: `${account.email} · ChatGPT ${account.planType || ""} plan (no API billing)` }) : null,
      engine.error ? el("div", { class: "bad small", text: engine.error }) : null,
      meter(limits.primary, "Plan usage (current window)"), meter(limits.secondary, "Plan usage (long window)"),
      el("div", { class: "row wrap" },
        engine.status === "login_required" ? btn("Sign in with ChatGPT", { kind: "primary", icon: "key", onClick: async () => {
          try { const res = await api.post("/api/engine/login"); if (res.authUrl) window.open(res.authUrl, "_blank", "noopener"); toast("Finish signing in in the new tab", "Weebo connects automatically when you're done."); }
          catch (err) { toast("Sign-in failed", err.message, { kind: "error" }); }
        } }) : null,
        btn("Refresh", { kind: "soft", size: "sm", icon: "refresh", onClick: async () => { await api.post("/api/engine/refresh"); this.refresh(); } }),
        btn("Restart engine", { kind: "soft", size: "sm", icon: "zap", onClick: async () => { await api.post("/api/engine/restart"); toast("Restarting Codex engine…"); } }),
        account.email ? btn("Sign out", { kind: "danger-soft", size: "sm", onClick: async () => { if (await confirmDialog("Sign out of Codex?", "This signs out the Codex CLI on this computer (shared with the Codex app).", "Sign out")) { await api.post("/api/engine/logout"); this.refresh(); } } }) : null));
    const models = engine.models || [];
    const currentModel = models.find((m) => m.id === settings.codex.model) || models.find((m) => m.isDefault) || {};
    const efforts = (currentModel.efforts && currentModel.efforts.length ? currentModel.efforts : ["low", "medium", "high", "xhigh"]).map((e) => [e, e]);
    wrap.append(section("Codex brain", engineCard,
      select("codex.model", "Model", [["", `Default${models.find((m) => m.isDefault) ? ` (${models.find((m) => m.isDefault).name})` : ""}`], ...models.map((m) => [m.id, m.name])], currentModel.description),
      select("codex.chat_effort", "Chat reasoning effort", efforts, "Higher thinks longer. Medium is a good balance for chat."),
      select("codex.agent_effort", "Agent reasoning effort", efforts),
      select("codex.background_effort", "Background reasoning effort", efforts, "Used for dreams, briefs and self-audits."),
      (currentModel.serviceTiers || []).includes("priority") ? toggle("codex.fast_mode", "Fast mode", "Priority speed tier. Uses your plan faster.") : null,
      text("codex.binary", "Codex binary (optional)", "Auto-detect newest", "Leave blank to use the newest Codex found (desktop app or npm).")));

    // --- You
    wrap.append(section("You", text("user.name", "Your name", "What should Weebo call you?")));

    // --- Autonomy
    wrap.append(section("Autonomy",
      cards("autonomy.level", [
        ["cautious", "Cautious", "Read-only. Every change needs your OK.", "shield"],
        ["balanced", "Balanced", "Free in its workspace and the chat's folder; asks before anything else.", "hand"],
        ["full", "Full trust", "No sandbox, no prompts. Like your Codex config.", "zap"],
      ]),
      toggle("autonomy.proactive", "Proactive mode", "Briefs, dreams and self-audits on its own."),
      number("autonomy.usage_ceiling_percent", "Pause background work above plan usage (%)", 0, 100, 5),
      number("autonomy.max_background_turns_per_day", "Max background turns per day", 0, 200),
      el("div", { class: "row" }, time("autonomy.quiet_hours_start", "Quiet hours from"), time("autonomy.quiet_hours_end", "to")),
      el("div", { class: "row" }, toggle("autonomy.daily_brief", "Morning brief"), time("autonomy.daily_brief_time", "at")),
      toggle("autonomy.dream", "Dreaming", "Consolidates memories and finds ways to help while you're away."),
      number("autonomy.dream_idle_minutes", "Dream after idle minutes", 1, 1440),
      toggle("autonomy.self_audit", "Self-audits", "Reviews its own code daily for bugs and upgrades.")));

    wrap.append(section("Team (bots)",
      toggle("bots.enabled", "Bots", "Specialist bots work for Weebo in their own chats. Off: none of them take turns on their own."),
      number("bots.max_bots", "Most bots on the team", 0, 32),
      number("bots.max_turns_per_day", "Turns a day across all bots", 0, 2000),
      number("bots.hop_limit", "Relay limit (chief ↔ bot hops)", 1, 6),
      number("bots.stuck_minutes", "Escalate a job silent this long (minutes)", 5, 1440),
      text("bots.model_fast", "Fast tier model (optional)", "Picked at runtime"),
      text("bots.model_standard", "Standard tier model (optional)", "Picked at runtime"),
      text("bots.model_capable", "Most capable tier model (optional)", "Picked at runtime")));

    wrap.append(section("Ideas and the day sheet",
      toggle("ideas.enabled", "Ideas", "Specific suggestions from your calendar, email and commitments."),
      number("ideas.max_per_day", "New ideas a day", 0, 50),
      number("ideas.horizon_days", "Days ahead on the day sheet", 1, 31),
      toggle("facts.second_pass", "Fact-check what I write", "A cheap second pass checks briefs and drafts against your calendar and email before you rely on them."),
      number("activity.stale_seconds", "Say \u201cstill working\u201d after (seconds of silence)", 15, 3600)));

    wrap.append(section("Agents",
      number("agents.max_parallel", "Agents in parallel", 1, 12),
      number("agents.max_minutes", "Time limit per agent (minutes)", 1, 1440),
      toggle("agents.auto_followup", "Talk about results", "When an agent finishes, Weebo tells you about it in the chat.")));

    wrap.append(section("Self-evolution",
      cards("evolution.mode", [
        ["off", "Off", "Never changes itself.", "x"],
        ["propose", "Propose", "Suggests upgrades; builds when you say go.", "sparkles"],
        ["build", "Build", "Builds and verifies upgrades; you approve merges.", "branch"],
        ["auto_merge", "Auto-merge", "Merges low-risk upgrades itself; core changes wait for you.", "dna"],
      ]),
      text("evolution.test_command", "Custom test command (optional)", "Default: selftest + pytest tests/weebo"),
      field("Protected paths", el("textarea", { class: "input", rows: 3,
        onchange: (e) => save("evolution.protect_paths", e.target.value.split(/[\n,]/).map((s) => s.trim()).filter(Boolean)) },
        (get("evolution.protect_paths") || []).join("\n")),
        "One per line: files, folders or globs (weebo/web/*.js). Upgrades touching these always wait for you, even in auto-merge."),
      toggle("evals.enabled", "Behavior checks", "Replays moments Weebo got wrong; upgrades to its brain or memory must pass at least as many."),
      number("evals.max_cases", "Checks per run", 1, 50, 1, "Each check costs about two quick model turns.")));

    wrap.append(section("Memory & skills",
      toggle("memory.semantic", "Semantic recall", `Finds memories by meaning, not just words, with a small model on this computer. Now: ${(status.memory || {}).recall || "keyword"}.`),
      toggle("skills.auto_learn", "Learn skills from repeated work", "While dreaming, Weebo turns procedures its agents keep repeating into skills."),
      number("skills.prune_unused_days", "Archive unused dream-learned skills after (days, 0 = never)", 0, 365)));

    // --- Voice
    const voices = listVoices();
    wrap.append(section("Voice",
      voiceSupport.tts ? toggle("voice.speak_replies", "Speak replies", "Weebo reads its answers aloud.") : el("p", { class: "muted small", text: "Speech isn't supported in this browser." }),
      voices.length ? select("voice.voice_name", "Voice", [["", "Browser default"], ...voices.map((v) => [v.name, `${v.name} (${v.lang})`])]) : null,
      number("voice.rate", "Speed", 0.5, 2, 0.05), number("voice.pitch", "Pitch", 0, 2, 0.05),
      btn("Test voice", { kind: "soft", icon: "volume", size: "sm", onClick: () => speak("Hi! I'm Weebo. I'm ready when you are.", { force: true }) })));

    wrap.append(section("Appearance",
      toggle("ui.companion", "Show Weebo's stage", "Weebo at the top of the chat with live reactions."),
      toggle("ui.reduce_motion", "Reduce motion")));

    // --- Integrations
    const integrations = (status.integrations || {}).tools || [];
    wrap.append(section("Integrations", el("p", { class: "muted small", text: "Abilities that use your own keys from .env (Google, OpenWeather, Twilio) or your Google Calendar sign-in. Dimmed ones aren't set up yet; hover for what they need." }),
      el("div", { class: "chips" }, integrations.map((t) => el("span", {
        class: `chip static${t.risk === "confirm" ? " warn" : ""}${t.missing.length ? " off" : ""}`,
        title: t.missing.length ? `${t.about}\nNeeds: ${t.missing.join(", ")}` : t.about, text: t.name })))));

    const { tailnet, lan } = await api.get("/api/remote");
    const phone = section("Phone app (Tailscale)");
    const setTailnet = async (enabled) => {
      try {
        toast(enabled ? "Starting Weebo's Tailscale address…" : "Turning off the Tailscale address…", "", { timeout: 2500 });
        await api.post("/api/remote/tailnet", { enabled });
        this.refresh();
      } catch (err) { toast("Couldn't change the Tailscale address", err.message, { kind: "error" }); }
    };
    if (tailnet.state === "ready" && tailnet.origin) {
      phone.append(el("p", { class: "muted small", text: "Your private HTTPS address. It works at home and away, only for your Tailscale devices. Your own Tailscale account opens Weebo with no password." }));
      if (tailnet.qr) phone.append(el("img", { class: "qr", src: tailnet.qr, alt: "QR code for Weebo's Tailscale address" }));
      phone.append(el("div", { class: "lan-link" }, el("div", {}, el("strong", { class: "small", text: "Weebo's address" }), el("code", { text: tailnet.origin })),
        btn("", { icon: "copy", title: "Copy address", size: "sm", onClick: () => copyText(tailnet.origin) })));
      phone.append(el("ol", { class: "install-steps" },
        el("li", { text: "On your phone, turn Tailscale on." }),
        el("li", { text: "Scan the QR code (or open the address) in Chrome." }),
        el("li", { text: "Chrome menu ⋮ → Install app (or Add to Home screen)." })));
      phone.append(el("details", { class: "fold" }, el("summary", { text: "Someone else's Tailscale account?" }),
        el("p", { class: "muted small", text: "Other people on your tailnet need this one-time key link:" }),
        el("div", { class: "lan-link" }, el("code", { text: tailnet.key_link }), btn("", { icon: "copy", title: "Copy key link", size: "sm", onClick: () => copyText(tailnet.key_link) }))));
      phone.append(btn("Turn off Tailscale address", { kind: "danger-soft", size: "sm", onClick: () => setTailnet(false) }));
    } else if (tailnet.auth_url) {
      phone.append(el("p", { class: "small", text: "One step left: connect Weebo's address to your Tailscale account (opens Tailscale's sign-in page)." }),
        btn("Connect to Tailscale", { kind: "primary", icon: "key", size: "sm", onClick: () => window.open(tailnet.auth_url, "_blank", "noopener") }),
        btn("I connected it, check again", { kind: "link", size: "sm", onClick: () => this.refresh() }));
    } else if (tailnet.enabled && !tailnet.helper) {
      phone.append(el("p", { class: "bad small", text: "The Tailscale helper (weebo_data/bin/weebo-tailnet.exe) is missing. Build it from weebo/tailnet_helper." }));
    } else if (tailnet.enabled) {
      phone.append(el("p", { class: "muted small", text: tailnet.error ? `Tailscale address error: ${tailnet.error}` : `Tailscale address is ${tailnet.state}…` }),
        el("div", { class: "row wrap" }, btn("Retry", { kind: "soft", size: "sm", icon: "refresh", onClick: () => setTailnet(true) }),
          btn("Turn off", { kind: "danger-soft", size: "sm", onClick: () => setTailnet(false) })));
    } else {
      phone.append(el("p", { class: "muted small", text: "Give Weebo its own private HTTPS address on your Tailscale network. Use it from your phone anywhere, and install Weebo as an app." }),
        btn("Turn on Tailscale address", { kind: "primary", icon: "zap", size: "sm", onClick: () => setTailnet(true) }));
    }
    if (this.app.installPrompt) {
      phone.append(btn("Install Weebo on this device", { kind: "soft", icon: "plus", size: "sm", onClick: () => this.app.installApp() }));
    }
    wrap.append(phone);

    const switchHost = async (host, label) => {
      if (!(await confirmDialog(label, host === "0.0.0.0"
        ? "Weebo will also listen on your home Wi-Fi over plain http (no app install, no microphone). Devices need the key link. Weebo restarts to apply this."
        : "Weebo will stop listening on your home Wi-Fi. The Tailscale address keeps working. Weebo restarts to apply this.", "Restart now", "primary"))) return;
      try {
        await api.patch("/api/settings", { changes: { "server.host": host } });
        await api.post("/api/system/restart");
      } catch (err) { toast("Couldn't change Wi-Fi access", err.message, { kind: "error" }); }
    };
    const wifi = section("Home Wi-Fi link (optional)");
    if (lan.enabled && (lan.urls || []).length) {
      wifi.append(el("p", { class: "muted small", text: "Plain http on your home network. Prefer the Tailscale address: it's encrypted and installable." }));
      for (const url of lan.urls) {
        wifi.append(el("div", { class: "lan-link" }, el("div", {}, el("strong", { class: "small", text: "Home Wi-Fi" }), el("code", { text: url })),
          btn("", { icon: "copy", title: "Copy link", size: "sm", onClick: () => copyText(url) })));
      }
      wifi.append(btn("Turn off Wi-Fi link", { kind: "danger-soft", size: "sm", onClick: () => switchHost("127.0.0.1", "Turn off the Wi-Fi link?") }));
    } else {
      wifi.append(el("p", { class: "muted small", text: "Off. Weebo only answers on this computer and its Tailscale address." }),
        btn("Turn on Wi-Fi link", { kind: "soft", size: "sm", onClick: () => switchHost("0.0.0.0", "Turn on the Wi-Fi link?") }));
    }
    wrap.append(wifi);

    wrap.append(section("System",
      el("p", { class: "muted small", text: `Weebo ${status.version} · project ${status.project_root} · workspace ${status.workspace}` }),
      el("div", { class: "row wrap" },
        btn("Restart Weebo", { kind: "soft", icon: "refresh", onClick: async () => { if (await confirmDialog("Restart Weebo?", "Running turns, agents and self-improvement builds will stop (builds can be rebuilt).", "Restart", "primary")) await api.post("/api/system/restart"); } }))));
    if (opts.focus) requestAnimationFrame(() => wrap.querySelector(".choice-cards:last-of-type")?.scrollIntoView({ block: "center" }));
    return wrap;
  }
}
