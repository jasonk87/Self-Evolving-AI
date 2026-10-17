// Renderers for every kind of item in a conversation.
import { icon } from "./icons.js";
import { renderMarkdown, hydrateWidgets } from "./markdown.js";
import { el, btn, escapeHtml, clockTime, diffStats, renderDiff, copyText, modal, duration } from "./ui.js";
import { speak } from "./voice.js";
import { proposalHref } from "./evolution-route.js";

export const WORK_KINDS = new Set(["command", "file_change", "tool", "web_search", "reasoning", "subagent", "image_view"]);

const TOOL_LABELS = {
  remember: "Remembered", recall: "Searched memory", forget: "Forgot a memory", update_memory: "Updated a memory",
  set_reminder: "Scheduled", list_reminders: "Checked reminders", cancel_reminder: "Cancelled a reminder",
  start_agent: "Launched an agent", agent_status: "Checked on agents", message_agent: "Messaged an agent",
  stop_agent: "Stopped an agent", propose_improvement: "Proposed a self-improvement", evolution_status: "Checked evolution",
  save_skill: "Learned a skill", notify_user: "Sent a notification", screenshot: "Looked at your screen",
  open_in_browser: "Opened a page", weebo_status: "Checked my own status", update_settings: "Changed settings",
};

const STATUS_ICON = {
  running: '<span class="spin"></span>',
  done: icon("check", 14, "ok"),
  failed: icon("alert", 14, "bad"),
  declined: icon("x", 14, "bad"),
  interrupted: icon("x", 14, "muted"),
};

function shortPath(p) {
  if (!p) return "";
  const parts = String(p).split(/[\\/]/);
  return parts.slice(-2).join("/");
}

function argSummary(args) {
  if (!args || typeof args !== "object") return "";
  for (const key of ["text", "title", "query", "name", "url", "when", "message", "memory_id", "task_id"]) {
    if (args[key]) return String(args[key]);
  }
  const first = Object.values(args).find((v) => typeof v === "string");
  return first || "";
}

// Codex wraps commands in their shell ("C:\...\powershell.exe" -Command "Get-Date"); show just the command.
const SHELL_WRAPPER = /^\s*(?:"[^"]*?(?:powershell|pwsh|cmd|bash|zsh|sh)(?:\.exe)?"|[^"\s]*?(?:powershell|pwsh|cmd|bash|zsh|sh)(?:\.exe)?)\s+(?:-NoLogo\s+|-NoProfile\s+|-NonInteractive\s+)*(?:-Command|-c|-lc|\/d\s+\/c|\/c)\s+/i;

export function prettyCommand(command) {
  let text = String(command || "").trim();
  if (SHELL_WRAPPER.test(text)) {
    text = text.replace(SHELL_WRAPPER, "").trim();
    if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) text = text.slice(1, -1);
  }
  return text.replace(/\\"/g, '"');
}

// ---------------------------------------------------------------- work items
export function workLabel(msg) {
  const d = msg.data || {};
  switch (msg.kind) {
    case "command": {
      const actions = d.actions || [];
      if (actions.length && actions.every((a) => a.type === "read")) return { ic: "file", text: `Read ${actions.map((a) => a.name || shortPath(a.path)).join(", ")}` };
      if (actions.length && actions.every((a) => a.type === "search")) return { ic: "search", text: `Searched ${actions.map((a) => a.query || "files").join(", ")}` };
      if (actions.length && actions.every((a) => a.type === "listFiles")) return { ic: "folder", text: "Listed files" };
      return { ic: "terminal", text: prettyCommand(msg.content), mono: true };
    }
    case "file_change": {
      const changes = d.changes || [];
      let added = 0, removed = 0;
      for (const c of changes) { const s = diffStats(c.diff); added += s.added; removed += s.removed; }
      const verb = changes.every((c) => c.kind === "add") ? "Created" : changes.every((c) => c.kind === "delete") ? "Deleted" : "Edited";
      return { ic: "edit", text: `${verb} ${changes.map((c) => shortPath(c.path)).join(", ")}`, stats: { added, removed } };
    }
    case "tool": {
      if (d.source === "weebo") {
        const wrapped = d.tool === "use_integration" || d.tool === "weebo1_tool";  // weebo1_tool: older chats
        const label = wrapped ? `Used ${(d.arguments || {}).name || "an integration"}` : (TOOL_LABELS[d.tool] || d.tool);
        const detail = wrapped ? argSummary((d.arguments || {}).arguments) : argSummary(d.arguments);
        return { ic: d.tool === "start_agent" ? "bot" : d.tool === "propose_improvement" ? "dna" : d.tool === "remember" ? "brain" : "sparkles", text: label, detail };
      }
      return { ic: "wrench", text: `${d.server}: ${d.tool}`, detail: argSummary(d.arguments) };
    }
    case "web_search": return { ic: "globe", text: msg.content ? `Searched the web for “${msg.content}”` : "Browsed the web" };
    case "reasoning": return { ic: "brain", text: "Thought it through" };
    case "subagent": return { ic: "layers", text: `Sub-agent: ${(msg.content || d.tool || "").slice(0, 80)}` };
    case "image_view": return { ic: "image", text: `Looked at ${shortPath(msg.content)}` };
    default: return { ic: "dot", text: msg.kind };
  }
}

function workDetail(msg) {
  const d = msg.data || {};
  const box = el("div", { class: "work-detail" });
  if (msg.kind === "command") {
    box.append(el("div", { class: "cmd-line" }, el("code", { text: `$ ${msg.content}` })));
    const out = el("pre", { class: "cmd-out", text: d.output || (msg.status === "running" ? "" : "(no output)") });
    out.dataset.output = msg.id;
    box.append(out);
    const meta = [];
    if (d.exitCode !== null && d.exitCode !== undefined) meta.push(`exit ${d.exitCode}`);
    if (d.durationMs) meta.push(duration(d.durationMs / 1000));
    if (d.cwd) meta.push(d.cwd);
    if (meta.length) box.append(el("div", { class: "work-meta", text: meta.join(" · ") }));
  } else if (msg.kind === "file_change") {
    for (const change of d.changes || []) {
      const s = diffStats(change.diff);
      const head = el("div", { class: "file-head" },
        el("span", { class: `file-kind k-${change.kind}`, text: change.kind === "add" ? "A" : change.kind === "delete" ? "D" : "M" }),
        el("span", { class: "file-path", text: change.path }),
        el("span", { class: "stat-add", text: `+${s.added}` }), el("span", { class: "stat-del", text: `−${s.removed}` }));
      box.append(head);
      if (change.diff) box.append(renderDiff(change.diff));
    }
  } else if (msg.kind === "tool") {
    if (d.arguments && Object.keys(d.arguments).length) box.append(el("pre", { class: "tool-args", text: JSON.stringify(d.arguments, null, 2) }));
    if (d.output) box.append(el("pre", { class: "tool-out", text: d.output }));
    if (d.error) box.append(el("pre", { class: "tool-out bad", text: d.error }));
  } else if (msg.kind === "reasoning") {
    box.append(el("div", { class: "md muted", html: renderMarkdown(msg.content) }));
  } else if (msg.kind === "web_search") {
    box.append(el("pre", { class: "tool-args", text: JSON.stringify(d.action || {}, null, 2) }));
  } else if (msg.kind === "subagent") {
    box.append(el("div", { class: "md", html: renderMarkdown(msg.content || "") }));
    if (d.states) box.append(el("pre", { class: "tool-args", text: JSON.stringify(d.states, null, 2) }));
  } else {
    box.append(el("pre", { class: "tool-args", text: msg.content || "" }));
  }
  return box;
}

export function renderWorkItem(msg) {
  const label = workLabel(msg);
  const row = el("div", { class: `work-item st-${msg.status}`, dataset: { id: msg.id } });
  const head = el("button", { class: "work-row", type: "button", "aria-expanded": "false" });
  head.innerHTML = `${icon(label.ic, 15, "work-ic")}<span class="work-text${label.mono ? " mono" : ""}">${escapeHtml(label.text)}</span>`
    + (label.detail ? `<span class="work-sub">${escapeHtml(label.detail).slice(0, 140)}</span>` : "")
    + (label.stats ? `<span class="stat-add">+${label.stats.added}</span><span class="stat-del">−${label.stats.removed}</span>` : "")
    + `<span class="work-status">${STATUS_ICON[msg.status] || ""}</span>`;
  row.append(head);
  head.addEventListener("click", () => {
    const open = row.classList.toggle("open");
    head.setAttribute("aria-expanded", String(open));
    if (open && !row.querySelector(".work-detail")) row.append(workDetail(msg));
    if (!open) row.querySelector(".work-detail")?.remove();
  });
  return row;
}

export function summarizeWork(items) {
  const counts = { command: 0, edit: 0, tool: 0, search: 0, think: 0 };
  let running = false, failed = 0, first = Infinity, last = 0;
  for (const m of items) {
    if (m.status === "running") running = true;
    if (m.status === "failed") failed++;
    first = Math.min(first, m.created_at); last = Math.max(last, m.updated_at || m.created_at);
    if (m.kind === "command") counts.command++;
    else if (m.kind === "file_change") counts.edit += (m.data?.changes || []).length || 1;
    else if (m.kind === "web_search") counts.search++;
    else if (m.kind === "reasoning") counts.think++;
    else counts.tool++;
  }
  const parts = [];
  if (counts.command) parts.push(`${counts.command} command${counts.command > 1 ? "s" : ""}`);
  if (counts.edit) parts.push(`${counts.edit} edit${counts.edit > 1 ? "s" : ""}`);
  if (counts.tool) parts.push(`${counts.tool} tool${counts.tool > 1 ? "s" : ""}`);
  if (counts.search) parts.push(`${counts.search} search${counts.search > 1 ? "es" : ""}`);
  if (!parts.length && counts.think) parts.push("thinking");
  return { text: parts.join(" · "), running, failed, seconds: Math.max(0, last - first) };
}

// ---------------------------------------------------------------- top-level messages
/** A pending approval outside a chat (e.g. an agent's), drawn with the same card. */
export function renderPendingApproval(summary, resolve) {
  return renderApproval({ kind: "approval", status: "pending", data: summary }, { resolve });
}

export function renderMessage(msg, ctx) {
  const fn = RENDERERS[msg.kind] || (msg.role === "user" ? renderUser : renderNotice);
  if (msg.kind === "text" && msg.role === "user") return renderUser(msg, ctx);
  const node = fn(msg, ctx);
  node.dataset.id = msg.id;
  return node;
}

function renderEvolutionStatus(msg, ctx) {
  const d = msg.data || {};
  const card = el("div", { class: "card evolution-status-card" },
    el("div", { class: "card-head" }, el("span", { html: icon("dna", 16) }), el("strong", { text: d.title })),
    el("p", { class: "evolution-status", text: d.status_text || msg.content }));
  if (d.decision) card.append(el("p", { class: "small muted", text: d.decision }));
  for (const reason of d.reasons || []) card.append(el("p", { class: "small evolution-reason", text: reason }));
  // Act right here: a tap on these is the user's decision (the same as the Evolution panel's buttons).
  const act = (action, label, kind = "soft", ic = "") => btn(label, { kind, size: "sm", icon: ic || undefined,
    onClick: (e) => ctx.proposalAction(d.proposal_id, action, e.currentTarget) });
  const actions = el("div", { class: "card-actions" });
  if (!ctx.readOnly && d.status === "ready") actions.append(act("merge", "Merge", "primary", "check"));
  if (!ctx.readOnly && ["failed", "conflict", "rolled_back"].includes(d.status)) actions.append(act("rebuild", "Rebuild", "primary", "refresh"));
  if (!ctx.readOnly && ["proposed", "declined", "vetting"].includes(d.status)) actions.append(act("approve", "Build it", "primary", "play"));
  actions.append(el("a", { class: "btn btn-soft btn-sm", href: proposalHref(d.proposal_id),
    text: d.status === "ready" ? "See changes" : "Details", onclick: (e) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      ctx.openProposal(d.proposal_id);
    } }));
  if (!ctx.readOnly && ["ready", "proposed", "declined", "vetting", "failed", "conflict"].includes(d.status)) actions.append(act("reject", "Not now", "ghost"));
  card.append(actions);
  return el("div", { class: "msg msg-card" }, card);
}

// A background agent, live: what it's doing now in plain words, then how it went.
const AGENT_STATES = {
  queued: ["Waiting", "clock"], running: ["Working", "bot"], completed: ["Done", "check"], failed: ["Failed", "alert"],
  cancelled: ["Stopped", "x"], interrupted: ["Interrupted", "x"],
};

function renderAgentCard(msg, ctx) {
  const d = msg.data || {};
  const [label, ic] = AGENT_STATES[d.status] || [d.status, "bot"];
  const live = d.status === "running" || d.status === "queued";
  const card = el("div", { class: `card agent-live as-${d.status}` },
    el("div", { class: "card-head" },
      el("span", { class: "agent-ic", html: live ? '<span class="spin"></span>' : icon(ic, 16) }),
      el("strong", { text: d.title || msg.content }),
      el("span", { class: "agent-state", text: label })));
  if (live) {
    card.append(el("p", { class: "agent-doing", text: d.status === "queued" ? "Starts when an agent slot frees up." : (d.doing || "Getting started…") }));
  } else {
    // The result itself is in the report further down (and Weebo tells you about it); this card just says how it went.
    const took = d.finished_at && (d.started_at || d.created_at) ? ` in ${duration(d.finished_at - (d.started_at || d.created_at))}` : "";
    card.classList.add("compact");
    card.append(el("p", { class: "agent-doing", text: `${label}${took}.${d.status === "failed" && d.error ? " " + d.error : ""}` }));
  }
  const actions = el("div", { class: "card-actions" },
    btn("Details", { kind: "soft", size: "sm", icon: "external", onClick: () => ctx.openTask(d.task_id) }));
  if (live && !ctx.readOnly) actions.append(btn("Stop", { kind: "ghost", size: "sm", icon: "stop", onClick: (e) => ctx.stopTask(d.task_id, e.currentTarget) }));
  card.append(actions);
  return el("div", { class: "msg msg-card" }, card);
}

// Tap-to-send replies Weebo offered under its latest answer.
export function renderSuggestions(replies, onPick) {
  return el("div", { class: "reply-chips" }, replies.map((text) =>
    el("button", { class: "reply-chip", type: "button", text, onclick: () => onPick(text) })));
}

function renderUser(msg) {
  const d = msg.data || {};
  const bubble = el("div", { class: "bubble" });
  if (msg.content) bubble.append(el("div", { class: "user-text", text: msg.content }));
  if ((d.images || []).length) {
    bubble.append(el("div", { class: "thumbs" }, d.images.map((name) => el("img", { src: `/uploads/${encodeURIComponent(name)}`, alt: "attachment", loading: "lazy" }))));
  }
  const node = el("div", { class: "msg msg-user" }, bubble);
  if (d.steered) node.append(el("div", { class: "msg-tag", text: "added while Weebo was working" }));
  return node;
}

function renderAssistantText(msg, ctx) {
  const phase = (msg.data || {}).phase;
  const node = el("div", { class: `msg msg-weebo${phase === "commentary" ? " commentary" : ""}${msg.status === "streaming" ? " streaming" : ""}` });
  const body = el("div", { class: "md" });
  body.innerHTML = renderMarkdown(msg.content || "");
  node.append(body);
  if (msg.status !== "streaming") {
    hydrateWidgets(body, msg.id);
    if (phase !== "commentary" && msg.content) {
      node.append(el("div", { class: "msg-actions" },
        btn("", { icon: "copy", title: "Copy", size: "sm", onClick: () => copyText(msg.content) }),
        btn("", { icon: "volume", title: "Read aloud", size: "sm", onClick: () => speak(msg.content, { force: true }) }),
        el("span", { class: "msg-time", text: clockTime(msg.created_at) })));
    }
  }
  if (msg.status === "interrupted") node.append(el("div", { class: "msg-tag", text: "interrupted" }));
  return node;
}

function renderPlan(msg) {
  const steps = (msg.data || {}).steps || [];
  const done = steps.filter((s) => s.status === "completed").length;
  const card = el("div", { class: "card plan-card" },
    el("div", { class: "card-head" }, el("span", { html: icon("list", 16) }), el("strong", { text: "Plan" }), el("span", { class: "muted", text: `${done}/${steps.length}` })));
  if (msg.content) card.append(el("p", { class: "muted small", text: msg.content }));
  card.append(el("ol", { class: "plan" }, steps.map((s) => el("li", { class: `ps-${s.status}` },
    el("span", { class: "plan-mark", html: s.status === "completed" ? icon("check", 13) : s.status === "inProgress" ? '<span class="spin"></span>' : "" }),
    el("span", { text: s.step })))));
  return el("div", { class: "msg msg-card" }, card);
}

function renderApproval(msg, ctx) {
  const d = msg.data || {};
  const pending = msg.status === "pending" && !ctx.readOnly;
  const who = d.agent ? `Agent “${d.agent}”` : "Weebo";  // a background agent's request lands in this chat too
  const titles = {
    command: `${who} wants to run a command`, file_change: `${who} wants to change files`,
    permissions: `${who} is asking for more access`, confirm: d.title || `${who} wants to take an action`,
    elicitation: d.title || "A tool needs your OK",
  };
  const card = el("div", { class: `card approval-card${pending ? " pending" : ""}` });
  card.append(el("div", { class: "card-head" }, el("span", { class: "approval-ic", html: icon("hand", 17) }), el("strong", { text: titles[d.kind] || "Approval needed" })));
  if (d.command) card.append(el("pre", { class: "cmd-line", text: `$ ${d.command}` }));
  if (d.files && d.files.length) card.append(el("ul", { class: "file-list" }, d.files.map((f) => el("li", { text: f }))));
  if (d.permissions) card.append(el("pre", { class: "tool-args", text: JSON.stringify(d.permissions, null, 2) }));
  if (d.reason) card.append(el("p", { class: "muted", text: d.reason }));
  if (d.kind === "elicitation") {
    const facts = [d.server && `From ${d.server}`, ...(d.details || []), d.risk === "high" && "High-risk app"].filter(Boolean);
    if (facts.length) card.append(el("div", { class: "work-meta", text: facts.join(" · ") }));
    if (d.url) card.append(el("a", { class: "btn btn-soft btn-sm", href: d.url, target: "_blank", rel: "noopener noreferrer", text: "Open the page it needs" }));
  }
  if (d.cwd) card.append(el("div", { class: "work-meta", text: `in ${d.cwd}` }));
  if (pending) {
    const id = d.id;
    const actions = el("div", { class: "card-actions" },
      btn("Allow", { kind: "primary", icon: "check", onClick: () => ctx.resolve(id, "accept") }));
    if (d.kind === "elicitation") {
      if ((d.persist || []).includes("session")) actions.append(btn("Allow for this session", { kind: "soft", onClick: () => ctx.resolve(id, "acceptForSession") }));
      if ((d.persist || []).includes("always")) actions.append(btn("Always allow", { kind: "soft", onClick: () => ctx.resolve(id, "acceptAlways") }));
    } else if (d.kind !== "confirm") actions.append(btn("Allow for this chat", { kind: "soft", onClick: () => ctx.resolve(id, "acceptForSession") }));
    if (ctx.botChat && d.kind !== "elicitation" && d.kind !== "confirm") actions.append(btn("Always allow", { kind: "soft", onClick: () => ctx.resolve(id, "acceptAlways") }));
    actions.append(btn("Deny", { kind: "danger-soft", icon: "x", onClick: () => ctx.resolve(id, "decline") }));
    card.append(actions);
  } else {
    const label = { accept: "Allowed", acceptForSession: d.kind === "elicitation" ? "Allowed for this session" : "Allowed for this chat", acceptAlways: "Always allowed", decline: "Denied", cancel: "Cancelled", expired: "Expired" }[d.decision || msg.status] || msg.status;
    card.append(el("div", { class: `decision d-${d.decision || msg.status}`, text: label }));
  }
  return el("div", { class: "msg msg-card" }, card);
}

function renderQuestion(msg, ctx) {
  const d = msg.data || {};
  const pending = msg.status === "pending" && !ctx.readOnly;
  const card = el("div", { class: `card question-card${pending ? " pending" : ""}` },
    el("div", { class: "card-head" }, el("span", { html: icon("sparkles", 16) }), el("strong", { text: "Weebo has a question" })));
  const answers = {};
  for (const q of d.questions || []) {
    const block = el("div", { class: "q-block" }, el("div", { class: "q-head", text: q.header || "" }), el("p", { text: q.question }));
    if (q.options && q.options.length) {
      const group = el("div", { class: "q-options" });
      for (const opt of q.options) {
        const choice = el("button", { class: "chip", type: "button", title: opt.description || "", disabled: !pending, onclick: () => {
          answers[q.id] = [opt.label];
          group.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === choice));
        } }, opt.label);
        group.append(choice);
      }
      block.append(group);
    }
    if (!q.options || q.isOther) {
      const input = el("input", { class: "input", type: q.isSecret ? "password" : "text", placeholder: "Your answer", disabled: !pending,
        oninput: (e) => { answers[q.id] = [e.target.value]; } });
      block.append(input);
    }
    card.append(block);
  }
  if (pending) {
    card.append(el("div", { class: "card-actions" }, btn("Send answer", { kind: "primary", onClick: () => ctx.resolve(d.id, "accept", answers) })));
  } else {
    card.append(el("div", { class: "decision", text: msg.status === "answered" ? "Answered" : "Expired" }));
  }
  return el("div", { class: "msg msg-card" }, card);
}

function renderAgentReport(msg, ctx) {
  const d = msg.data || {};
  const ok = d.status === "completed";
  const card = el("div", { class: `card agent-card ${ok ? "ac-ok" : "ac-bad"}` },
    el("div", { class: "card-head" }, el("span", { html: icon(ok ? "bot" : "alert", 17) }), el("strong", { text: msg.content })));
  const summary = d.summary || d.error;
  if (summary) {
    const body = el("div", { class: "md clamp" });
    body.innerHTML = renderMarkdown(summary);
    card.append(body);
    const more = btn("Show more", { kind: "link", size: "sm", onClick: () => { body.classList.toggle("clamp"); more.querySelector("span").textContent = body.classList.contains("clamp") ? "Show more" : "Show less"; } });
    card.append(more);
  }
  if (d.task_id) card.append(el("div", { class: "card-actions" }, btn("Open agent", { kind: "soft", size: "sm", icon: "external", onClick: () => ctx.openTask(d.task_id) })));
  return el("div", { class: "msg msg-card" }, card);
}

function renderReminder(msg) {
  const d = msg.data || {};
  return el("div", { class: "msg msg-notice reminder" },
    el("span", { class: "notice-ic", html: icon(d.routine ? "clock" : "bell", 15) }),
    el("span", { text: msg.content }),
    el("span", { class: "msg-time", text: clockTime(msg.created_at) }));
}

function renderNotice(msg) {
  return el("div", { class: "msg msg-notice" }, el("span", { class: "notice-ic", html: icon("sparkles", 14) }), el("span", { text: msg.content }));
}

function renderError(msg) {
  return el("div", { class: "msg msg-card" }, el("div", { class: "card error-card" },
    el("div", { class: "card-head" }, el("span", { html: icon("alert", 16) }), el("strong", { text: "Something went wrong" })),
    el("p", { text: msg.content })));
}

function renderDiffSummary(msg) {
  const d = msg.data || {};
  const files = d.files || [];
  const open = () => modal(`Changes · ${files.length} file${files.length === 1 ? "" : "s"}`, renderDiff(d.diff), { wide: true });
  return el("div", { class: "msg msg-card" }, el("button", { class: "card diff-card", type: "button", onclick: open },
    el("span", { html: icon("diff", 16) }),
    el("span", { text: `${files.length} file${files.length === 1 ? "" : "s"} changed` }),
    el("span", { class: "stat-add", text: `+${d.added || 0}` }), el("span", { class: "stat-del", text: `−${d.removed || 0}` }),
    el("span", { class: "diff-open", text: "Review changes" })));
}

function renderWidget(msg) {
  const node = el("div", { class: "msg msg-weebo" });
  const body = el("div", { class: "md" }, el("div", { class: "widget-slot", dataset: { widgetIndex: "0" } }));
  node.append(body);
  hydrateWidgets(body, msg.id);
  return node;
}

function renderImages(msg) {
  const d = msg.data || {};
  const urls = d.images || (d.url ? [d.url] : []);
  const grid = el("div", { class: "image-grid" }, urls.map((u) => el("a", { href: u, target: "_blank", rel: "noopener" }, el("img", { src: u, alt: msg.content || "image", loading: "lazy" }))));
  const node = el("div", { class: "msg msg-weebo" }, grid);
  if (msg.content) node.append(el("p", { class: "muted small", text: msg.content }));
  return node;
}

function renderReview(msg) {
  return el("div", { class: "msg msg-card" }, el("div", { class: "card" },
    el("div", { class: "card-head" }, el("span", { html: icon("shield", 16) }), el("strong", { text: "Code review" })),
    el("div", { class: "md", html: renderMarkdown(msg.content || "") })));
}

// ---------------------------------------------------------------- the team: bots, approvals, reports
export const TIER_LABEL = { fast: "Fast · cheap", standard: "Standard", capable: "Most capable" };
const RISK_LABEL = { read: "reads", act: "acts", spend: "spends money", contact_people: "contacts people" };

function kv(rows) {
  return el("dl", { class: "kv" }, rows.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })]));
}

function settled(text) { return el("div", { class: "decision", text }); }

/** A button that posts to the API; the card updates itself when the server says what happened. */
function act(ctx, label, path, { kind = "soft", ic, body, confirm } = {}) {
  return btn(label, { kind, size: "sm", icon: ic, onClick: (e) => ctx.act(path, body, e.currentTarget, confirm) });
}

function renderBotProposal(msg, ctx) {
  const d = msg.data || {};
  const bot = d.bot || {};
  const p = bot.data || {};
  const pending = msg.status === "pending" && !ctx.readOnly;
  const card = el("div", { class: `card bot-card${pending ? " pending" : ""}` },
    el("div", { class: "card-head" }, el("span", { class: "bot-face", text: (bot.name || "?").slice(0, 1) }),
      el("strong", { text: `New bot: ${bot.name}` }),
      el("span", { class: "muted small", text: `${TIER_LABEL[p.tier] || p.tier} · effort ${p.effort}` })));
  card.append(el("p", { text: p.job || "" }));
  if ((p.goals || []).length) card.append(el("ul", { class: "bullets" }, p.goals.map((g) => el("li", { text: g }))));
  card.append(kv([
    ["Model", bot.model || "picked at runtime"], ["Budget", `${p.turns_per_day} turns a day`],
    ["Tools", (p.tools || []).join(", ") || "the basics only"], ["Connectors", (p.connectors || []).join(", ") || "none"],
    ["Without asking", (p.autonomy || []).join(", ")],
    ["Routines", (p.routines || []).map((r) => `${r.text} (${r.recurrence})`).join("; ") || "none"],
  ]));
  if (p.why) card.append(el("p", { class: "muted small", text: `Why: ${p.why}` }));
  const actions = el("div", { class: "card-actions" });
  if (pending) {
    actions.append(act(ctx, "Approve", `/api/bots/${bot.id}/approve`, { kind: "primary", ic: "check" }),
      btn("Edit", { kind: "soft", size: "sm", icon: "edit", onClick: () => ctx.openBot(bot.id) }),
      act(ctx, "Not now", `/api/bots/${bot.id}/reject`, { kind: "ghost" }));
  } else {
    actions.append(settled({ active: "Approved: it's on the team", paused: "Approved (paused now)", rejected: "Declined",
      archived: "Retired", deleted: "Deleted" }[msg.status] || msg.status));
    if (bot.conversation_id && ["active", "paused"].includes(msg.status)) {
      actions.append(btn(`Talk to ${bot.name}`, { kind: "soft", size: "sm", icon: "chat", onClick: () => ctx.openConversation(bot.conversation_id) }));
    }
  }
  card.append(actions);
  return el("div", { class: "msg msg-card" }, card);
}

function renderBotRetire(msg, ctx) {
  const d = msg.data || {};
  const bot = d.bot || {};
  const retire = d.retire || {};
  const pending = msg.status === "pending" && !ctx.readOnly;
  const card = el("div", { class: `card bot-card${pending ? " pending" : ""}` },
    el("div", { class: "card-head" }, el("span", { html: icon("users", 17) }), el("strong", { text: `Retire ${bot.name}?` })));
  card.append(el("p", { text: retire.reason || "" }),
    el("p", { class: "muted small", text: retire.delete
      ? "Delete removes the bot, its chat and its history for good."
      : "Archiving keeps its chat to read, puts its Codex thread away and stops its routines." }));
  const actions = el("div", { class: "card-actions" });
  if (pending) {
    actions.append(act(ctx, "Archive it", `/api/bots/${bot.id}/archive`, { kind: "primary" }));
    if (retire.delete) actions.append(act(ctx, "Delete for good", `/api/bots/${bot.id}/delete`, { kind: "danger-soft", confirm: `Delete ${bot.name} and its chat?` }));
    actions.append(act(ctx, "Keep it", `/api/bots/${bot.id}/keep`, { kind: "ghost" }));
  } else actions.append(settled({ kept: "Kept", archived: "Archived", deleted: "Deleted" }[msg.status] || msg.status));
  card.append(actions);
  return el("div", { class: "msg msg-card" }, card);
}

function renderBotChange(msg, ctx) {
  const d = msg.data || {};
  const change = d.change || {};
  const data = change.data || {};
  const bot = d.bot || {};
  const pending = msg.status === "pending" && !ctx.readOnly;
  const card = el("div", { class: `card bot-card${pending ? " pending" : ""}` },
    el("div", { class: "card-head" }, el("span", { html: icon("hand", 17) }), el("strong", { text: `Change ${bot.name || "a bot"}?` })));
  const show = (v) => Array.isArray(v) ? v.map((x) => (typeof x === "object" ? x.text || JSON.stringify(x) : x)).join(", ") || "none"
    : typeof v === "object" && v ? JSON.stringify(v) : String(v ?? "");
  card.append(el("ul", { class: "bullets" }, Object.entries(data.patch || {}).map(([key, value]) =>
    el("li", { text: `${key}: ${show((data.before || {})[key])} → ${show(value)}` }))));
  if ((data.reasons || []).length) card.append(el("p", { class: "small warnline", text: `Needs your yes: ${data.reasons.join("; ")}` }));
  if (data.reason) card.append(el("p", { class: "muted small", text: `Why: ${data.reason}` }));
  const actions = el("div", { class: "card-actions" });
  if (pending) {
    actions.append(act(ctx, "Approve", `/api/bot-changes/${change.id}/approve`, { kind: "primary", ic: "check" }),
      act(ctx, "Reject", `/api/bot-changes/${change.id}/reject`, { kind: "ghost" }));
  } else actions.append(settled({ applied: "Applied", rejected: "Rejected" }[msg.status] || msg.status));
  card.append(actions);
  return el("div", { class: "msg msg-card" }, card);
}

function renderNeedsYou(msg, ctx) {
  const items = (msg.data || {}).items || [];
  const card = el("div", { class: `card needs-you-card${items.length ? " pending" : ""}` },
    el("div", { class: "card-head" }, el("span", { class: "approval-ic", html: icon("hand", 17) }),
      el("strong", { text: items.length ? `${items.length} thing${items.length > 1 ? "s" : ""} from the team need${items.length > 1 ? "" : "s"} you` : "Nothing waiting on you" })));
  for (const item of items) {
    const d = item.data || {};
    const row = el("div", { class: `ny-item${d.hard ? " hard" : ""}` },
      el("div", { class: "ny-text" }, el("span", { text: d.summary }),
        d.hard ? el("span", { class: "pill st-failed", text: d.action === "contact_people" ? "contacts people" : d.action }) : null));
    if (!ctx.readOnly) {
      row.append(el("div", { class: "card-actions" },
        act(ctx, "Allow once", `/api/needs-you/${item.id}/approve`, { kind: "primary" }),
        act(ctx, "Always allow", `/api/needs-you/${item.id}/always`),
        act(ctx, "Deny", `/api/needs-you/${item.id}/deny`, { kind: "ghost" })));
    }
    card.append(row);
  }
  return el("div", { class: "msg msg-card" }, card);
}

const OUTCOME_ICON = { done: "check", failed: "alert", blocked: "alert", update: "pulse", change: "bell" };

function renderBotReport(msg, ctx) {
  const d = msg.data || {};
  const card = el("div", { class: `card bot-report o-${d.outcome}` },
    el("div", { class: "card-head" }, el("span", { html: icon(OUTCOME_ICON[d.outcome] || "pulse", 16) }),
      el("strong", { text: `${d.bot_name || "A bot"} reported` }),
      d.needs_attention ? el("span", { class: "pill st-failed", text: "needs attention" }) : el("span", { class: "muted small", text: d.outcome })),
    el("p", { class: "report-summary", text: d.summary || msg.content }));
  if (d.conversation_id) card.append(el("div", { class: "card-actions" },
    btn(`Open ${d.bot_name || "its"} chat`, { kind: "soft", size: "sm", icon: "chat", onClick: () => ctx.openConversation(d.conversation_id) })));
  return el("div", { class: "msg msg-card" }, card);
}

function renderConnector(msg, ctx) {
  const row = (msg.data || {}).connector || {};
  const data = row.data || {};
  const pending = msg.status === "pending" && !ctx.readOnly;
  const card = el("div", { class: `card connector-card${pending ? " pending" : ""}` },
    el("div", { class: "card-head" }, el("span", { html: icon("plug", 17) }), el("strong", { text: `Connector: ${row.name}` }),
      el("span", { class: "muted small", text: row.kind === "app" ? "ChatGPT app" : `MCP · ${data.transport}` })));
  if (data.description) card.append(el("p", { text: data.description }));
  const rules = {};
  card.append(el("ul", { class: "tool-risks" }, (data.tools || []).map((t) => {
    const li = el("li", {}, el("strong", { text: t.name }), el("span", { class: `risk r-${t.risk}`, text: RISK_LABEL[t.risk] || t.risk }));
    if (["spend", "contact_people"].includes(t.risk) && pending) {
      const input = el("input", { class: "input", placeholder: "Rule, e.g. businesses only; never personal contacts",
        value: (data.rules || {})[t.name] || "", oninput: (e) => { rules[t.name] = e.target.value; } });
      rules[t.name] = input.value;
      li.append(input);
    }
    return li;
  })));
  const test = data.test || {};
  if (test.at) card.append(el("p", { class: `small ${test.ok ? "ok" : "bad"}`, text: test.ok ? `Tested: found ${(test.found || []).join(", ")}` : `Test failed: ${test.error || ""}` }));
  const actions = el("div", { class: "card-actions" });
  if (pending) {
    actions.append(btn("Approve", { kind: "primary", size: "sm", icon: "check", onClick: (e) => ctx.act(`/api/connectors/${row.id}/approve`, { rules }, e.currentTarget) }),
      act(ctx, "Disable", `/api/connectors/${row.id}/disable`, { kind: "ghost" }));
  } else actions.append(settled({ approved: "Approved", disabled: "Disabled", failed: "Failed its test" }[msg.status] || msg.status));
  card.append(actions);
  return el("div", { class: "msg msg-card" }, card);
}

function renderGap(msg, ctx) {
  const gap = (msg.data || {}).gap || {};
  const data = gap.data || {};
  const option = data.option || {};
  const pending = msg.status === "pending" && !ctx.readOnly;
  const card = el("div", { class: `card gap-card${pending ? " pending" : ""}` },
    el("div", { class: "card-head" }, el("span", { html: icon("plug", 17) }), el("strong", { text: `Can't do yet: ${data.need}` }),
      el("span", { class: "muted small", text: `${gap.count}×` })));
  card.append(el("p", { text: option.kind === "app"
    ? `Your ChatGPT app “${option.name}” could cover this. Connect it in ChatGPT, then register it on the Capabilities page.`
    : "No app covers this. I can build a small MCP connector for it through self-evolution (tested and gated, then onboarded like any connector)." }));
  const actions = el("div", { class: "card-actions" });
  if (pending) {
    if (option.kind === "app" && option.install_url) actions.append(el("a", { class: "btn btn-primary btn-sm", href: option.install_url, target: "_blank", rel: "noopener", text: "Connect the app" }));
    if (option.kind !== "app") actions.append(act(ctx, "Build a connector", `/api/gaps/${gap.id}/build`, { kind: "primary", ic: "wrench" }));
    actions.append(act(ctx, "Not needed", `/api/gaps/${gap.id}/dismiss`, { kind: "ghost" }));
  } else actions.append(settled({ proposed: "Building it through self-evolution", resolved: "Resolved", dismissed: "Not needed" }[msg.status] || msg.status));
  card.append(actions);
  return el("div", { class: "msg msg-card" }, card);
}

function renderWorkflow(msg, ctx) {
  const d = msg.data || {};
  const wf = d.workflow || {};
  const data = wf.data || {};
  const pending = msg.status === "pending" && !ctx.readOnly;
  const card = el("div", { class: `card workflow-card${pending ? " pending" : ""}` },
    el("div", { class: "card-head" }, el("span", { html: icon("layers", 17) }), el("strong", { text: `Workflow: ${wf.name}` })));
  if (d.question) card.append(el("p", { class: "warnline", text: d.question }));
  card.append(el("ol", { class: "bullets" }, (data.steps || []).map((s) => el("li", { text: `${s.bot ? s.bot + ": " : ""}${s.instructions}` }))));
  if (data.schedule) card.append(el("p", { class: "muted small", text: `Runs ${data.schedule.recurrence || ""} from ${data.schedule.when || ""}` }));
  if (data.why) card.append(el("p", { class: "muted small", text: `Why: ${data.why}` }));
  const actions = el("div", { class: "card-actions" });
  if (pending && !d.question) {
    actions.append(act(ctx, "Approve", `/api/workflows/${wf.id}/approve`, { kind: "primary", ic: "check" }),
      act(ctx, "Not now", `/api/workflows/${wf.id}/reject`, { kind: "ghost" }));
  } else if (pending) {
    actions.append(act(ctx, "Keep", `/api/workflows/${wf.id}/resume`), act(ctx, "Pause", `/api/workflows/${wf.id}/pause`),
      act(ctx, "Retire", `/api/workflows/${wf.id}/retire`, { kind: "ghost" }));
  } else actions.append(settled({ active: "Active", paused: "Paused", retired: "Retired", rejected: "Not now" }[wf.status] || wf.status));
  card.append(actions);
  return el("div", { class: "msg msg-card" }, card);
}

function renderFactcheck(msg) {
  return el("div", { class: "msg msg-card" }, el("div", { class: "card factcheck-card" },
    el("div", { class: "card-head" }, el("span", { html: icon("alert", 16) }), el("strong", { text: "Fact-check" })),
    el("ul", { class: "bullets" }, ((msg.data || {}).issues || []).map((i) => el("li", { text: `${i.claim}, but ${i.fact}` })))));
}

function renderBotJob(msg) {
  return el("div", { class: "msg msg-notice" }, el("span", { class: "notice-ic", html: icon("list", 14) }), el("span", { text: msg.content }));
}

const RENDERERS = {
  bot_proposal: renderBotProposal,
  bot_retire: renderBotRetire,
  bot_change: renderBotChange,
  needs_you: renderNeedsYou,
  bot_report: renderBotReport,
  bot_job: renderBotJob,
  connector: renderConnector,
  gap: renderGap,
  workflow: renderWorkflow,
  factcheck: renderFactcheck,
  evolution_status: renderEvolutionStatus,
  text: renderAssistantText,
  plan: renderPlan,
  approval: renderApproval,
  question: renderQuestion,
  agent_report: renderAgentReport,
  agent_card: renderAgentCard,
  reminder: renderReminder,
  notice: renderNotice,
  error: renderError,
  diff: renderDiffSummary,
  widget: renderWidget,
  images: renderImages,
  image: renderImages,
  review: renderReview,
};
