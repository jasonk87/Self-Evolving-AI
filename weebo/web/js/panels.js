// Drawer panels: Agents, Evolution, Memory, Schedule, Activity, Settings.
import { api } from "./api.js";
import { icon } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import { el, btn, toast, timeAgo, dateTime, duration, renderDiff, modal, confirmDialog, debounce, escapeHtml, copyText } from "./ui.js";
import { listVoices, speak, voiceSupport } from "./voice.js";
import { renderPendingApproval } from "./items.js";

const PANELS = {
  agents: { title: "Agents", icon: "bot" },
  evolution: { title: "Evolution", icon: "dna" },
  memory: { title: "Memory", icon: "brain" },
  schedule: { title: "Schedule", icon: "clock" },
  activity: { title: "Activity", icon: "pulse" },
  settings: { title: "Settings", icon: "gear" },
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
    const stage = { building: "Building", testing: "Testing", reviewing: "Code review" }[meta.stage] || (p.status === "building" ? "Building" : "Verifying");
    return `${stage} · round ${meta.round || 1} of 3`;
  }
  if (p.status === "proposed" && council.approved === true) return `Council approved: ${council.reason || ""}`;
  if (p.status === "proposed" && council.approved === null && council.reason) return council.reason;
  return "";
}

const BUILD_STEPS = [["council", "Council"], ["building", "Build"], ["testing", "Test"], ["reviewing", "Review"], ["ready", "Ready"]];

function stepper(p) {
  const meta = p.meta || {};
  const skipCouncil = !(meta.council && "reason" in meta.council); // only ideas the (new, up-front) Council vetted
  const current = p.status === "vetting" || p.status === "declined" ? "council"
    : ["building", "checking"].includes(p.status) ? (meta.stage || "building")
    : ["ready", "merging", "merged"].includes(p.status) ? "ready" : "";
  if (!current) return null;
  const at = BUILD_STEPS.findIndex(([key]) => key === current);
  const steps = BUILD_STEPS.filter(([key]) => !(key === "council" && skipCouncil));
  return el("ol", { class: "stepper", "aria-label": "Build progress" }, steps.map(([key, label]) => {
    const i = BUILD_STEPS.findIndex(([k]) => k === key);
    const state = p.status === "declined" && key === "council" ? "fail" : i < at ? "done" : i === at ? (current === "ready" ? "done" : "now") : "todo";
    return el("li", { class: `step ${state}` }, el("span", { class: "step-dot", html: state === "done" ? icon("check", 12) : state === "fail" ? icon("x", 12) : "" }), el("span", { text: label }));
  }));
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
    this._refresh = debounce(() => this.refresh(), 250);
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
    this.current = name;
    this.opts = opts;
    this.title.innerHTML = `${icon(PANELS[name].icon, 18)}<span>${PANELS[name].title}</span>`;
    this.drawer.classList.add("open");
    document.body.classList.add("drawer-open");
    document.querySelectorAll("[data-panel]").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
    this.refresh();
  }

  close() {
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
      agents: ["task.", "interaction."], evolution: ["evolution.", "skills."], memory: ["memory."], schedule: ["reminder."],
      activity: ["weebo.activity", "notify", "legacy."], settings: ["engine.", "settings.", "legacy."],
    }[this.current] || [];
    if (interest.some((p) => type.startsWith(p))) {
      if (this.current === "agents" && type === "task.progress" && !this.opts.task) return this.patchProgress();
      if (this.editing()) { this.pending = true; return; }
      this._refresh();
    }
  }

  async refresh() {
    const name = this.current;
    if (!name) return;
    const scrollTop = this.body.scrollTop;
    try {
      const content = await this[`render_${name}`](this.opts);
      if (this.current !== name) return;
      this.body.replaceChildren(content);
      this.body.scrollTop = scrollTop;
    } catch (err) {
      this.body.replaceChildren(empty(`Couldn't load: ${err.message}`, "alert"));
    }
  }

  // ================================================================== AGENTS
  async render_agents(opts) {
    if (opts.task) return this.renderTask(opts.task);
    const { tasks } = await api.get("/api/tasks");
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
        const { task } = await api.post("/api/tasks", { title: title.value, instructions: instructions.value, cwd: cwd.value, effort: effort.value, conversation_id: this.app.currentConvId });
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
        el("div", { class: "task-progress", text: t.status === "running" ? (this.app.taskProgress.get(t.id) || "Working…") : taskPreview(t) }),
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
    const [{ proposals, mode, current }, { skills }] = await Promise.all([api.get("/api/proposals"), api.get("/api/skills")]);
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
      try { const { proposal } = await api.post("/api/proposals", { title: t.value, description: d.value }); this.opts = { proposal: proposal.id }; this.refresh(); }
      catch (err) { toast("Couldn't propose", err.message, { kind: "error" }); }
    });
    wrap.append(el("div", { class: "row" }, btn("Suggest an upgrade", { kind: "primary", icon: "plus", onClick: () => { form.hidden = false; t.focus(); } })), form);

    const needs = proposals.filter((p) => ["ready", "conflict"].includes(p.status) || (p.status === "proposed" && mode !== "off"));
    const progress = proposals.filter((p) => ["vetting", "queued", "building", "checking", "merging"].includes(p.status));
    const history = proposals.filter((p) => !needs.includes(p) && !progress.includes(p));
    const card = (p) => {
      const step = proposalStep(p);
      return el("button", { class: `proposal-card st-${p.status}`, type: "button", onclick: () => { this.opts = { proposal: p.id }; this.refresh(); } },
        el("div", { class: "task-top" }, el("span", { html: icon(p.id === current ? "gear" : "dna", 16, p.id === current ? "spinning" : "") }), el("strong", { text: p.title }), pill(p.status)),
        step ? el("div", { class: "task-progress", text: step }) : null,
        el("div", { class: "task-meta", text: `${p.source === "user" ? "you asked" : p.source} · ${timeAgo(p.created_at)}${p.diff_stat ? " · " + p.diff_stat.split("\n").pop().trim() : ""}` }));
    };
    wrap.append(section("Needs you", needs.length ? el("div", { class: "stack" }, needs.map(card)) : empty("Nothing waiting for review.", "check")));
    if (progress.length) wrap.append(section("In progress", el("div", { class: "stack" }, progress.map(card))));
    if (history.length) wrap.append(section("History", el("div", { class: "stack" }, history.slice(0, 30).map(card))));

    const skillList = skills.length ? el("div", { class: "stack" }, skills.map((s) => el("div", { class: "skill-row" },
      el("div", {}, el("strong", { text: s.name }), el("p", { class: "muted small", text: s.description })),
      el("div", { class: "row" },
        btn("", { icon: "eye", title: "View", size: "sm", onClick: () => modal(s.name, el("div", { class: "md", html: renderMarkdown(s.preview) }), { wide: true }) }),
        btn("", { icon: "trash", title: "Forget skill", size: "sm", onClick: async () => {
          if (await confirmDialog("Forget skill?", `Weebo will no longer know "${s.name}".`, "Forget")) { await api.del(`/api/skills/${s.name}`); this.refresh(); }
        } })))))
      : empty("No learned skills yet. Weebo saves reusable procedures here as it figures them out.", "sparkles");
    wrap.append(section("Learned skills", skillList));
    return wrap;
  }

  async renderProposal(id) {
    const { proposal: p } = await api.get(`/api/proposals/${encodeURIComponent(id)}`);
    const meta = p.meta || {};
    const wrap = el("div", { class: "panel" });
    wrap.append(btn("All upgrades", { kind: "link", icon: "chevronLeft", onClick: () => { this.opts = {}; this.refresh(); } }));
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
      const row = el("details", { class: `check ${r.skipped ? "skip" : r.ok ? "pass" : "fail"}` },
        el("summary", { html: `${icon(r.skipped ? "dot" : r.ok ? "check" : "x", 14)}<span>${escapeHtml(r.name)}</span><span class="muted small">${r.skipped ? "skipped" : r.seconds.toFixed(1) + "s"}</span>` }),
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
      wrap.append(section("Governance", el("p", { class: "muted small", text: gov.autonomous ? "Low-risk zones only: eligible for automatic merge." : "Touches protected zones: needs your approval to merge." }),
        el("table", { class: "gov" }, gov.files.map((f) => el("tr", {}, el("td", { class: "mono", text: f.path }), el("td", { text: f.zone.replace("_", " ") }), el("td", {}, el("span", { class: `pill tier-${f.tier}`, text: f.tier.replace("_", " ") })))))));
    }
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
  async render_memory(opts) {
    const q = opts.q || "";
    const kind = opts.kind || "";
    const { memories, stats } = await api.get(`/api/memories?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}`);
    const wrap = el("div", { class: "panel" });
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
    wrap.append(el("div", { class: "row wrap" }, trigger("dream", "Dream now", "moon"), trigger("brief", "Brief me now", "bell"), trigger("audit", "Self-audit now", "search")));

    const kindIcon = { dream: "moon", brief: "bell", audit: "search", evolution: "dna", skill: "sparkles", memory: "brain", schedule: "clock", routine: "zap", system: "gear" };
    wrap.append(section("What Weebo has been up to", journal.length ? el("ol", { class: "timeline" }, journal.map((j) => el("li", {},
      el("span", { class: "tl-ic", html: icon(kindIcon[j.kind] || "dot", 14) }),
      el("div", { class: "tl-body" }, el("strong", { text: j.title }), j.detail ? el("p", { class: "muted small", text: j.detail }) : null, el("span", { class: "tl-time", text: timeAgo(j.created_at) })))))
      : empty("Nothing yet.")));
    if (notifications.length) {
      wrap.append(section("Notifications", el("div", { class: "stack" }, notifications.map((n) => el("div", { class: `note${n.read ? "" : " unread"}` },
        el("strong", { text: n.title }), n.body ? el("p", { class: "muted small", text: n.body.slice(0, 220) }) : null, el("span", { class: "tl-time", text: timeAgo(n.created_at) }))))));
      api.post("/api/notifications/read", {}).then(() => this.app.setUnread(0)).catch(() => {});
    }
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
      text("evolution.test_command", "Custom test command (optional)", "Default: selftest + pytest tests/weebo")));

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

    // --- Weebo 1.x
    const legacy = status.legacy || {};
    wrap.append(section("Weebo 1.x abilities", el("p", { class: "muted small", text: `Bridge: ${legacy.status}${legacy.error ? " — " + legacy.error : ""}. These old tools now think with Codex too.` }),
      el("div", { class: "chips" }, (legacy.tools || []).map((t) => el("span", { class: `chip static${t.risk === "confirm" ? " warn" : ""}`, title: t.about, text: t.name })))));

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
