// A calm, source-backed list. Goals record intent; only explicit delegation starts work.
import { api } from "./api.js";
import { icon } from "./icons.js";
import { el, btn, toast, modal, confirmDialog, dateTime, timeAgo } from "./ui.js";

const text = (value, cls = "muted small") => el("p", { class: cls, text: value });
const group = (title, items, empty) => el("section", { class: "feed-section" },
  el("h4", { text: title }), items.length ? items : text(empty));

function row(id, title, preview, glyph, cls = "") {
  return el("details", { class: `feed-item ${cls}`, dataset: { feedKey: id } },
    el("summary", {}, el("span", { class: "feed-icon", html: icon(glyph, 21) }),
      el("span", { class: "feed-copy" }, el("strong", { text: title }), text(preview, "feed-preview")),
      el("span", { class: "feed-chevron", html: icon("chevronDown", 15) })));
}

function openLink(p, link) {
  if (link.card) { p.close(); p.app.showInChat(link.card); }
  else if (link.conversation) { p.close(); p.app.openConversation(link.conversation).then(() => {
    if (link.message) p.app.showInChat(link.message, null, link.conversation);
  }); }
  else if (link.panel) p.open(link.panel, link);
}

async function change(p, work, title = "Saved") {
  try { const result = await work(); toast(title, "", { kind: "success" }); p.refresh(); return result; }
  catch (err) { toast("Couldn't do that", err.message, { kind: "error" }); return null; }
}

function sourceChip(e) {
  return el("button", { class: "chip", type: "button", title: "View source", onclick: async () => {
    try {
      const { evidence: current } = await api.get(`/api/feed/evidence/${encodeURIComponent(e.entity)}/${encodeURIComponent(e.id)}`);
      const provenance = current.provenance || {};
      modal("Source", el("div", { class: "stack" },
        text(current.text || current.title || current.subject || current.name || e.label, "source-text"),
        text(`${provenance.source || e.source || "Unknown source"} · updated ${timeAgo(provenance.fetched_at)}`),
        ["memories", "messages"].includes(e.entity) ? text("A remembered statement, not independent verification. Discuss it with Weebo if it has changed.") : null));
    } catch (err) { toast("Source unavailable", err.message, { kind: "error" }); }
  } }, el("span", { html: icon(e.entity === "events" ? "calendar" : e.entity === "threads" ? "mail" : "brain", 14) }),
  el("span", { text: e.label || e.id }));
}

function ideaCard(p, idea) {
  const d = idea.data;
  const card = row(`idea:${idea.id}`, d.title, d.statement, "sparkles", `idea-card${idea.stale ? " stale" : ""}`);
  card.dataset.idea = idea.id;
  const body = el("div", { class: "feed-detail" }, text(d.statement, "feed-statement"),
    el("div", { class: "chip-row" }, (d.evidence || []).map(sourceChip)));
  if (idea.stale) body.append(text("Its evidence changed. Refresh the Feed before acting."));
  if (d.note) body.append(text(d.note));
  const actions = el("div", { class: "row wrap feed-actions" },
    btn("Discuss", { kind: "soft", icon: "chat", onClick: async () => {
      const result = await change(p, () => api.post(`/api/ideas/${idea.id}/discuss`), "Opening in chat");
      if (result) { p.close(); p.app.openMain(); }
    } }));
  if (!idea.stale && !d.pending_draft) {
    actions.append(btn("Start a goal", { kind: "primary", icon: "plus", onClick: async () => {
      const result = await change(p, () => api.post(`/api/ideas/${idea.id}/start`), "Added to goals · no work started");
      if (result) editGoal(p, result.goal);
    } }));
    (d.actions || []).forEach((a, index) => {
      if (!a.safe) return;
      actions.append(btn(`Do it: ${a.label}`, { kind: "soft", onClick: async () => {
        try {
          const result = await api.post(`/api/ideas/${idea.id}/do`, { action: index });
          toast(result.result?.verified ? "Done" : "Draft requested", a.label); p.refresh();
        } catch (err) { toast("Didn't do it", err.message, { kind: "error" }); }
      } }));
    });
    actions.append(btn("Tomorrow", { title: "Snooze for 24 hours", onClick: () => change(p,
      () => api.post(`/api/ideas/${idea.id}/snooze`, { hours: 24 }), "Snoozed for 24 hours") }));
  }
  actions.append(btn("Not useful", { onClick: () => change(p, () => api.post(`/api/ideas/${idea.id}/dismiss`), "Dismissed") }));
  body.append(actions);
  card.append(body);
  return card;
}

export function editGoal(p, goal = null) {
  const d = goal?.data || {};
  const title = el("input", { class: "input", value: goal?.title || "", required: true, maxlength: 160, "aria-label": "Goal title" });
  const detail = el("textarea", { class: "input", rows: 3, "aria-label": "Goal details" }, d.details || "");
  const next = el("textarea", { class: "input", rows: 2, "aria-label": "Next step" }, d.next_step || "");
  const blocker = el("input", { class: "input", value: d.blocker || "", "aria-label": "Blocker" });
  const due = el("input", { class: "input", type: "datetime-local", "aria-label": "Goal deadline" });
  if (goal?.due) {
    const date = new Date(goal.due * 1000);
    due.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  const field = (label, input) => el("label", { class: "field" }, el("span", { class: "field-label", text: label }), input);
  const form = el("form", { class: "stack" }, field("What outcome do you want?", title), field("Details", detail),
    field("Next step", next), field("What's in the way?", blocker),
    field(`Deadline (optional · ${Intl.DateTimeFormat().resolvedOptions().timeZone})`, due),
    text("Saving records your goal. It does not launch a bot, schedule monitoring, or grant permissions."));
  const save = btn("Save goal", { kind: "primary", onClick: () => form.requestSubmit() });
  form.append(save);
  const dialog = modal(goal ? "Edit goal" : "Choose a goal", form);
  form.addEventListener("submit", async e => {
    e.preventDefault(); if (save.disabled) return; save.disabled = true;
    const values = { title: title.value, details: detail.value, next_step: next.value, blocker: blocker.value,
      due: due.value ? new Date(due.value).getTime() / 1000 : null };
    const result = await change(p, () => goal ? api.patch(`/api/goals/${goal.id}`, values) : api.post("/api/goals", values), "Goal saved");
    save.disabled = false;
    if (result) dialog();
  });
  title.focus();
}

async function delegateGoal(p, goal) {
  try {
    const { bots } = await api.get("/api/bots");
    const active = bots.filter(b => b.status === "active");
    if (!active.length) { toast("No active bots", "Approve or resume a bot in Manage bots first."); return; }
    const select = el("select", { class: "input", "aria-label": "Delegate to bot" },
      active.map(b => el("option", { value: b.id, text: b.name })));
    const instructions = el("textarea", { class: "input", rows: 4, "aria-label": "Job instructions" }, goal.data.next_step || goal.title);
    const body = el("div", { class: "stack" }, text("This starts one job using the bot's existing budget and permissions. External actions still need their normal approvals."), select, instructions);
    const send = btn("Assign job", { kind: "primary", onClick: async () => {
      if (send.disabled) return; send.disabled = true;
      const result = await change(p, () => api.post(`/api/goals/${goal.id}/delegate`, { bot_id: select.value, instructions: instructions.value }), "Job assigned");
      send.disabled = false; if (result) dialog();
    } });
    body.append(send);
    const dialog = modal(`Delegate: ${goal.title}`, body);
  } catch (err) { toast("Couldn't load bots", err.message, { kind: "error" }); }
}

async function linkWork(p, goal) {
  try {
    const feed = await api.get("/api/feed");
    const types = { reminder: "reminders", routine: "reminders", commitment: "commitments", job: "jobs" };
    const options = feed.tracking.filter(t => types[t.kind]);
    if (!options.length) { toast("Nothing to link", "There are no unlinked reminders, commitments or bot jobs."); return; }
    const select = el("select", { class: "input", "aria-label": "Existing work" }, options.map(t =>
      el("option", { value: t.id, text: `${t.kind}: ${t.title}` })));
    const close = modal("Link existing work", el("div", { class: "stack" },
      text("This keeps the existing record and its schedule. It doesn't start or duplicate anything."), select), {
      actions: [{ label: "Link", kind: "primary", run: async () => {
        const item = options.find(t => t.id === select.value);
        const result = await change(p, () => api.post(`/api/goals/${goal.id}/link`, { entity: types[item.kind], id: item.id }), "Work linked");
        if (result) close();
      } }],
    });
  } catch (err) { toast("Couldn't load existing work", err.message, { kind: "error" }); }
}

function goalCard(p, g) {
  const d = g.data, closed = ["done", "dropped"].includes(g.status);
  const preview = `${g.owner_name} · ${g.status}${g.due ? ` · ${dateTime(g.due)}` : ""}\n${d.next_step || "Choose a next step."}`;
  const card = row(`goal:${g.id}`, g.title, preview, "check", "goal-card");
  card.dataset.goal = g.id;
  const body = el("div", { class: "feed-detail" }, d.details ? text(d.details, "feed-statement") : null,
    d.blocker ? text(`Blocked: ${d.blocker}`, "warnline") : null,
    text(g.note || "A goal records intent; work starts only when you delegate or schedule it."));
  if (g.memory_id) body.append(sourceChip({ entity: "memories", id: g.memory_id, label: "Original memory" }));
  for (const link of g.links || []) {
    if (link.entity === "jobs") continue;
    body.append(el("div", { class: "goal-job" }, text(link.item ? `${link.item.text} · ${link.item.status}` : "Linked item is no longer available."),
      link.item ? btn("Open linked item", { kind: "link", onClick: () => p.open(link.entity === "reminders" ? "schedule" : "facts") }) : null));
  }
  for (const j of g.jobs || []) {
    const b = p.app.state.bots.find(b => b.id === j.bot_id);
    const label = j.status === "done" ? "Job finished · review the outcome" : `Job ${j.status}`;
    body.append(el("div", { class: "goal-job" }, el("strong", { text: label }),
      text(j.data.result_summary || j.data.instructions),
      b?.conversation_id ? btn(`Chat with ${b.name}`, { kind: "link", onClick: () => openLink(p, { conversation: b.conversation_id }) }) : null));
  }
  const patch = status => change(p, () => api.patch(`/api/goals/${g.id}`, { status }), "Goal updated");
  const actions = el("div", { class: "row wrap feed-actions" }, btn("Discuss", { kind: "soft", icon: "chat", onClick: async () => {
    const result = await change(p, () => api.post(`/api/goals/${g.id}/discuss`), "Opening in chat");
    if (result) { p.close(); p.app.openMain(); }
  } }), btn("Edit", { onClick: () => editGoal(p, g) }));
  if (!closed) {
    actions.append(btn("Link existing work", { onClick: () => linkWork(p, g) }));
    if (g.status === "active" && !(g.jobs || []).some(j => ["open", "running"].includes(j.status)))
      actions.append(btn("Delegate", { kind: "primary", onClick: () => delegateGoal(p, g) }));
    actions.append(btn(g.status === "paused" ? "Resume goal" : "Pause goal", { onClick: async () => {
      if (g.status === "paused" || await confirmDialog("Pause this goal?", "Existing jobs and routines keep their own controls. This pauses the goal only.", "Pause goal")) patch(g.status === "paused" ? "active" : "paused");
    } }), btn("Mark done", { onClick: () => patch("done") }), btn("Archive", { onClick: async () => {
      if (await confirmDialog("Archive this goal?", "Its history stays readable. This does not stop or delete linked work.", "Archive")) patch("dropped");
    } }));
  } else actions.append(btn("Reopen goal", { onClick: () => patch("active") }));
  body.append(actions); card.append(body); return card;
}

export async function renderFeed(p, opts = {}) {
  const data = await api.get("/api/feed");
  p.app.setIdeasCount(data.ideas.length);
  const filter = opts.filter || "all";
  const wrap = el("div", { class: "panel feed" });
  wrap.append(el("div", { class: "feed-heading" },
    el("div", {}, el("h3", { text: "Your next steps, together." }), text("Chosen goals, real work, and ideas worth a look.")),
    btn("Add goal", { kind: "primary", icon: "plus", onClick: () => editGoal(p) })));
  wrap.append(el("nav", { class: "feed-filters", "aria-label": "Feed filters" },
    [["all", "All"], ["goals", "Goals"], ["ideas", "Ideas"], ["tracking", "Tracking"]].map(([key, title]) =>
      el("button", { class: `chip${key === filter ? " selected" : ""}`, type: "button", "aria-pressed": key === filter ? "true" : "false",
        onclick: () => { p.opts = { filter: key }; p.refresh(); } }, title))));
  if (data.needs_you.length) wrap.append(group("Needs you", data.needs_you.map(n =>
    el("button", { class: "feed-attention", type: "button", onclick: () => openLink(p, n.link) },
      el("span", { html: icon("bell", 19) }), el("span", { text: n.title }), el("span", { text: "Review →" })))));
  if (["all", "goals"].includes(filter)) wrap.append(group("Goals", data.goals.map(g => goalCard(p, g)),
    "No chosen goals yet. Add one, or start an idea. Nothing is accepted automatically."));
  if (["all", "tracking"].includes(filter)) wrap.append(group("Tracking", data.tracking.map(t => {
    const item = row(`tracking:${t.id}`, t.title, `${t.kind} · ${t.status}${t.next_at ? ` · ${dateTime(t.next_at)}` : ""}`, "clock");
    item.append(el("div", { class: "feed-detail" }, text(t.detail),
      t.updated_at ? text(`Last update ${timeAgo(t.updated_at)}`) : null,
      btn("Open", { kind: "soft", onClick: () => openLink(p, t.link) })));
    return item;
  }), "No scheduled reminders, open commitments, or background jobs. Nothing is being monitored here."));
  if (["all", "ideas"].includes(filter)) wrap.append(group("Ideas for you", data.ideas.map(i => ideaCard(p, i)),
    "No fresh suggestions right now. Save a goal or project in Memory, or connect a source. Dismissed and snoozed ideas stay out of the way."));
  const sheet = el("details", { class: "feed-extra", dataset: { feedKey: "daysheet" } }, el("summary", { text: "Day sheet" }));
  const sheetBody = el("div", { class: "stack" }); sheet.append(sheetBody);
  let loaded = false;
  sheet.addEventListener("toggle", async () => {
    if (!sheet.open || loaded) return; loaded = true;
    try {
      const day = await api.get("/api/daysheet");
      if (!day.travel_source) sheetBody.append(text("Travel is unknown without a maps/directions source."));
      sheetBody.append(...(day.rows.length ? day.rows.map(r => p.dayRow(r)) : [text("Nothing coming up, or no calendar is connected.")]));
    } catch (err) { loaded = false; sheetBody.replaceChildren(text(`Couldn't load: ${err.message}`)); }
  });
  wrap.append(sheet);
  if (["all", "goals"].includes(filter) && data.closed_goals.length) wrap.append(el("details", { class: "feed-extra", dataset: { feedKey: "closed" } },
    el("summary", { text: "Completed & archived goals" }), data.closed_goals.map(g => goalCard(p, g))));
  return wrap;
}
