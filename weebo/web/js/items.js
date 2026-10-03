// Renderers for every kind of item in a conversation.
import { icon } from "./icons.js";
import { renderMarkdown, hydrateWidgets } from "./markdown.js";
import { el, btn, escapeHtml, clockTime, diffStats, renderDiff, copyText, modal, duration } from "./ui.js";
import { speak } from "./voice.js";

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
        const label = d.tool === "weebo1_tool" ? `Used ${(d.arguments || {}).name || "a Weebo 1.x tool"}` : (TOOL_LABELS[d.tool] || d.tool);
        const detail = d.tool === "weebo1_tool" ? argSummary((d.arguments || {}).arguments) : argSummary(d.arguments);
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
  const pending = msg.status === "pending";
  const titles = {
    command: "Weebo wants to run a command", file_change: "Weebo wants to change files",
    permissions: "Weebo is asking for more access", confirm: d.title || "Weebo wants to take an action",
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
  const pending = msg.status === "pending";
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

const RENDERERS = {
  text: renderAssistantText,
  plan: renderPlan,
  approval: renderApproval,
  question: renderQuestion,
  agent_report: renderAgentReport,
  reminder: renderReminder,
  notice: renderNotice,
  error: renderError,
  diff: renderDiffSummary,
  widget: renderWidget,
  images: renderImages,
  image: renderImages,
  review: renderReview,
};
