// Weebo 2.0 — app controller: state, sidebar, live events, moods.
import { api, connect, on, onConnection } from "./api.js";
import { director, WeeboAvatar } from "./avatar.js";
import { ChatView } from "./chat.js";
import { icon } from "./icons.js";
import { Panels } from "./panels.js";
import { el, btn, toast, modal, timeAgo, confirmDialog, $ } from "./ui.js";
import { configureVoice, speak } from "./voice.js";
import { setWorkspaceRoot } from "./markdown.js";
import { proposalHref, proposalFromHash } from "./evolution-route.js";
import { NotificationInbox } from "./notifications.js";

const ACTIVE_KEY = "weebo.activeConversation";
const THEME_KEY = "weebo.theme";

class App {
  constructor() {
    this.state = { settings: {}, engine: {}, conversations: [], deskId: null, tasks: [], proposals: [], unread: 0, lan: {} };
    this.currentConvId = null;
    this.busyConvs = new Set();
    this.taskProgress = new Map();
    this.runningTasks = 0;
    this.readyProposals = 0;
    this.heartbeatBusy = null;
    this.lastInput = Date.now();
  }

  async start() {
    this.applyTheme(localStorage.getItem(THEME_KEY) || "system");
    this.chat = new ChatView(this);
    this.panels = new Panels(this);
    this.notifications = new NotificationInbox(this);
    this.bindChrome();
    this.bindEvents();
    onConnection((s) => this.setConnection(s));
    connect();
    try {
      await this.bootstrap();
    } catch (err) {
      toast("Couldn't load Weebo", err.message, { kind: "error", timeout: 0 });
      return;
    }
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
    this.watchInstall();
    setInterval(() => this.tickMood(), 15000);
  }

  async bootstrap() {
    const data = await api.get("/api/bootstrap");
    this.state.settings = data.settings;
    this.state.engine = data.snapshot.engine;
    this.state.conversations = data.conversations;
    this.state.deskId = data.desk_id;
    this.state.lan = data.lan;
    setWorkspaceRoot(data.snapshot.workspace);
    this.notifications.read(data.read_notification_ids || []);
    for (const note of data.notifications) this.notifications.receive(note);
    await this.notifications.reconcile();
    this.runningTasks = data.tasks.filter((t) => t.status === "running" || t.status === "queued").length;
    this.state.proposals = data.proposals;
    this.readyProposals = data.proposals.filter((p) => p.status === "ready").length;
    this.evolving = data.proposals.some((p) => ["building", "checking", "merging"].includes(p.status));
    this.heartbeatBusy = data.snapshot.heartbeat.busy;
    for (const turn of data.snapshot.active_turns || []) this.busyConvs.add(turn.conversation_id);
    this.applySettings(data.settings);
    this.renderEngine();
    this.renderSidebar();
    this.renderBadges();
    for (const p of data.snapshot.pending_interactions || []) this.announceInteraction(p);
    const saved = localStorage.getItem(ACTIVE_KEY);
    const target = this.state.conversations.find((c) => c.id === saved) ? saved : null;
    if (target) await this.openConversation(target);
    else await this.newChat();
    this.routeProposal();
    director.setBase(this.restingMood());
  }

  // ------------------------------------------------------------------ chrome
  bindChrome() {
    window.addEventListener("hashchange", () => this.routeProposal());
    window.addEventListener("popstate", () => this.routeProposal());
    document.querySelectorAll("[data-icon]").forEach((n) => n.insertAdjacentHTML("afterbegin", icon(n.dataset.icon, Number(n.dataset.iconSize || 18))));
    $("#new-chat").addEventListener("click", () => this.newChat());
    $("#menu-btn").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));
    $("#scrim").addEventListener("click", () => { document.body.classList.remove("sidebar-open"); this.panels.close(); });
    $("#theme-btn").addEventListener("click", () => {
      const order = ["system", "dark", "light"];
      const next = order[(order.indexOf(localStorage.getItem(THEME_KEY) || "system") + 1) % 3];
      localStorage.setItem(THEME_KEY, next);
      this.applyTheme(next);
      toast(`Theme: ${next}`, "", { timeout: 1200 });
    });
    document.querySelectorAll("[data-panel]").forEach((b) => b.addEventListener("click", () => {
      this.panels.toggle(b.dataset.panel);
      document.body.classList.remove("sidebar-open");
    }));
    $("#conv-title").addEventListener("click", () => this.renameConversation(this.currentConvId));
    this.sidebarWeebo = new WeeboAvatar($("#brand-weebo"), { size: 34, track: false });
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); this.newChat(); }
      else if (e.key === "Escape" && this.panels.current && !document.querySelector(".modal-backdrop")) this.panels.close();
      else if (e.key === "/" && document.activeElement === document.body) { e.preventDefault(); this.chat.input.focus(); }
    });
    document.addEventListener("pointerdown", () => { this.lastInput = Date.now(); }, { passive: true });
    document.addEventListener("keydown", () => { this.lastInput = Date.now(); }, { passive: true });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { this.tickMood(); this.notifications.reconcile(); this.panels.notify("settings.updated"); }
    });
    window.addEventListener("pageshow", () => this.notifications.reconcile());
    window.addEventListener("online", () => this.notifications.reconcile());
  }

  applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $("#theme-btn").innerHTML = icon(theme === "light" ? "sun" : theme === "dark" ? "moon" : "contrast", 17);
    $("#theme-btn").title = `Theme: ${theme}`;
  }

  setConnection(state) {
    document.body.classList.toggle("offline", state !== "online");
    if (state !== "online") director.setBase("offline");
    else director.setBase(this.restingMood());
  }

  applySettings(settings) {
    this.state.settings = settings;
    configureVoice(settings.voice);
    document.body.classList.toggle("reduce-motion", Boolean(settings.ui?.reduce_motion));
    document.body.classList.toggle("no-companion", settings.ui?.companion === false);
    this.chat?.updateChips();
  }

  // ------------------------------------------------------------------ conversations
  conversation(id) { return this.state.conversations.find((c) => c.id === id); }

  async newChat() {
    const empty = this.state.conversations.find((c) => c.kind === "chat" && c.title === "New chat" && !this.busyConvs.has(c.id));
    if (empty) return this.openConversation(empty.id);
    try {
      const { conversation } = await api.post("/api/conversations", {});
      this.upsertConversation(conversation);
      await this.openConversation(conversation.id);
    } catch (err) {
      toast("Couldn't start a chat", err.message, { kind: "error" });
    }
  }

  async openConversation(id) {
    this.currentConvId = id;
    $("#ctx-meter").classList.add("idle");
    localStorage.setItem(ACTIVE_KEY, id);
    document.body.classList.remove("sidebar-open");
    this.renderSidebar();
    this.renderHeader();
    await this.chat.open(id);
  }

  upsertConversation(conv) {
    const i = this.state.conversations.findIndex((c) => c.id === conv.id);
    if (i >= 0) this.state.conversations[i] = { ...this.state.conversations[i], ...conv };
    else this.state.conversations.unshift(conv);
    this.state.conversations.sort((a, b) => (b.pinned - a.pinned) || (b.updated_at - a.updated_at));
    this.renderSidebar();
    if (conv.id === this.currentConvId) this.renderHeader();
  }

  markRead(id, conv) {
    if (conv) this.upsertConversation({ ...conv, unread: 0 });
  }

  async renameConversation(id) {
    const conv = this.conversation(id);
    if (!conv || conv.kind === "desk") return;
    const input = el("input", { class: "input", value: conv.title });
    modal("Rename chat", input, { actions: [
      { label: "Cancel", run: (c) => c() },
      { label: "Save", kind: "primary", run: async (c) => {
        try { const { conversation } = await api.patch(`/api/conversations/${id}`, { title: input.value.trim() || "Untitled" }); this.upsertConversation(conversation); c(); }
        catch (err) { toast("Couldn't rename", err.message, { kind: "error" }); }
      } },
    ] });
    setTimeout(() => { input.focus(); input.select(); }, 50);
  }

  async deleteConversation(id) {
    const conv = this.conversation(id);
    if (!conv) return;
    if (!(await confirmDialog("Delete chat?", `"${conv.title}" and its history will be deleted.`, "Delete"))) return;
    try {
      await api.del(`/api/conversations/${id}`);
    } catch (err) {
      toast("Couldn't delete", err.message, { kind: "error" });
    }
  }

  async pickFolder(convId) {
    const conv = this.conversation(convId);
    const input = el("input", { class: "input", value: conv?.cwd || "", placeholder: "C:\\Users\\you\\projects\\my-app" });
    modal("Working folder", el("div", { class: "stack" },
      el("p", { class: "muted small", text: "Weebo runs commands and edits files in this folder for this chat. Leave empty for Weebo's own workspace." }), input), {
      actions: [
        { label: "Cancel", run: (c) => c() },
        { label: "Use folder", kind: "primary", run: async (c) => {
          try { const { conversation } = await api.patch(`/api/conversations/${convId}`, { cwd: input.value.trim() || null }); this.upsertConversation(conversation); this.chat.updateChips(); c(); }
          catch (err) { toast("Folder not set", err.message, { kind: "error" }); }
        } },
      ],
    });
  }

  renderSidebar() {
    const list = $("#conv-list");
    const items = this.state.conversations.filter((c) => !c.archived).map((c) => {
      const busy = this.busyConvs.has(c.id);
      const row = el("div", { class: `conv${c.id === this.currentConvId ? " active" : ""}${c.unread && c.id !== this.currentConvId ? " unread" : ""}${c.kind === "desk" ? " desk" : ""}`, role: "button", tabindex: 0,
        onclick: () => this.openConversation(c.id), onkeydown: (e) => { if (e.key === "Enter") this.openConversation(c.id); } },
        el("span", { class: "conv-ic", html: busy ? '<span class="spin"></span>' : icon(c.kind === "desk" ? "inbox" : "dot", c.kind === "desk" ? 16 : 8) }),
        el("span", { class: "conv-title", text: c.title }),
        el("span", { class: "conv-time", text: timeAgo(c.updated_at) }));
      if (c.kind !== "desk") {
        row.append(el("span", { class: "conv-actions" },
          btn("", { icon: "edit", title: "Rename", size: "sm", onClick: (e) => { e.stopPropagation(); this.renameConversation(c.id); } }),
          btn("", { icon: "trash", title: "Delete", size: "sm", onClick: (e) => { e.stopPropagation(); this.deleteConversation(c.id); } })));
      }
      return row;
    });
    list.replaceChildren(...items);
  }

  renderHeader() {
    const conv = this.conversation(this.currentConvId);
    $("#conv-title").textContent = conv ? conv.title : "Weebo";
    $("#conv-title").title = conv?.kind === "desk" ? "Weebo's own conversation for briefs, routines and check-ins" : "Click to rename";
  }

  renderBadges() {
    const set = (panel, n) => { document.querySelectorAll(`[data-panel="${panel}"] .badge`).forEach((b) => { b.textContent = n > 9 ? "9+" : String(n); b.hidden = !n; }); };
    set("agents", this.runningTasks);
    set("evolution", this.readyProposals);
    set("activity", this.state.unread);
    const inboxLabel = this.state.unread ? `Notification inbox (${this.state.unread} unread)` : "Notification inbox";
    $("#inbox-btn").setAttribute("aria-label", inboxLabel);
    $("#inbox-btn").title = inboxLabel;
    this.chat?.stage.setResting(this.restingCaption());
  }

  setUnread(n) { this.state.unread = n; this.renderBadges(); }

  renderEngine() {
    const e = this.state.engine || {};
    const pill = $("#engine-pill");
    const label = { ready: "", login_required: "Sign in to Codex", starting: "Starting Codex…", restarting: "Reconnecting…", error: "Codex unavailable", stopped: "Codex stopped" }[e.status] ?? e.status;
    pill.hidden = !label;
    pill.textContent = label || "";
    pill.className = `engine-pill s-${e.status}`;
    pill.onclick = () => this.panels.open("settings");
    const used = Math.round(((e.rateLimits || {}).primary || {}).usedPercent || 0);
    const ring = $("#usage-ring");
    ring.style.setProperty("--p", `${used}`);
    ring.title = e.rateLimits ? `ChatGPT plan usage: ${used}% of the current window` : "Plan usage unknown";
    ring.classList.toggle("hot", used >= 80);
    ring.querySelector("span").textContent = e.rateLimits ? `${used}%` : "–";
    if (e.status !== "ready" && e.status) director.setBase(e.status === "login_required" || e.status === "error" ? "offline" : "thinking");
    this.chat?.stage.setResting(this.restingCaption());
  }

  // ------------------------------------------------------------------ moods
  restingMood() {
    const e = this.state.engine || {};
    if (e.status && e.status !== "ready") return "offline";
    if (this.heartbeatBusy === "dream") return "dreaming";
    if (this.heartbeatBusy === "audit") return "inspecting";
    if (this.evolving) return "evolving";
    if (this.runningTasks) return "delegating";
    if (Date.now() - this.lastInput > 10 * 60 * 1000) return "sleepy";
    return "idle";
  }

  restingCaption() {
    const e = this.state.engine || {};
    if (e.status === "login_required") return "Sign in to Codex in Settings so I can think.";
    if (e.status && !["ready"].includes(e.status)) return "Connecting to my Codex brain…";
    if (this.heartbeatBusy === "dream") return "Dreaming… tidying my memories.";
    if (this.heartbeatBusy === "audit") return "Reviewing my own code.";
    if (this.heartbeatBusy === "brief") return "Putting your brief together.";
    if (this.evolving) return "Building an upgrade to myself.";
    if (this.runningTasks) return `${this.runningTasks} agent${this.runningTasks > 1 ? "s" : ""} working in the background.`;
    return "";
  }

  tickMood() {
    this.chat.stage.setResting(this.restingCaption());
    if (this.busyConvs.has(this.currentConvId) || this.chat.input.value) return;
    director.setBase(this.restingMood());
  }

  // ------------------------------------------------------------------ interactions
  async resolveInteraction(id, decision, answers) {
    try {
      await api.post(`/api/interactions/${encodeURIComponent(id)}`, { decision, answers });
      director.flash(decision === "decline" ? "sad" : "happy", 1200);
    } catch (err) {
      toast("Couldn't send your decision", err.message, { kind: "error" });
    }
  }

  announceInteraction(p) {
    if (p.conversation_id && p.conversation_id === this.currentConvId) return;
    const title = p.task_id ? "An agent needs your approval" : "Weebo needs your approval";
    toast(title, p.command || p.title || p.reason || "", { kind: "warn", timeout: 0, action: {
      label: "Review",
      run: () => p.conversation_id ? this.openConversation(p.conversation_id) : this.panels.open("agents", { task: p.task_id }),
    } });
  }

  // ------------------------------------------------------------------ live events
  bindEvents() {
    on("hello", () => { this.reconcileEvolution(); this.notifications.reconcile(); });
    on("conv.message", ({ conversation_id, message }) => {
      if (conversation_id === this.currentConvId) {
        const existing = this.chat.byId.get(message.id);
        if (message.kind !== "evolution_status" || !existing || message.updated_at >= existing.updated_at) this.chat.upsert(message);
      }
      const conv = this.conversation(conversation_id);
      if (conv) {
        conv.updated_at = message.updated_at || Date.now() / 1000;
        if (conversation_id !== this.currentConvId && message.role !== "user") conv.unread = 1;
        this.renderSidebar();
      }
      if (message.kind === "reminder") { director.flash("alert", 3500); this.chat.stage.say(message.content.slice(0, 80), 6000); }
    });
    on("conv.delta", (d) => this.chat.delta(d));
    on("conv.output", (d) => this.chat.output(d));
    on("conv.reasoning", (d) => this.chat.reasoningDelta(d));
    on("conv.status", (d) => { if (d.conversation_id === this.currentConvId) this.chat.stage.say(d.text, 4000); });
    on("conv.turn.started", ({ conversation_id }) => {
      this.busyConvs.add(conversation_id);
      if (conversation_id === this.currentConvId) this.chat.turnStarted();
      this.renderSidebar();
    });
    on("conv.turn.completed", (d) => {
      this.busyConvs.delete(d.conversation_id);
      if (d.conversation_id === this.currentConvId) this.chat.turnCompleted(d);
      else if (d.final_text && d.status === "completed") {
        const conv = this.conversation(d.conversation_id);
        toast(conv?.title || "Weebo", d.final_text.slice(0, 160), { action: { label: "Open", run: () => this.openConversation(d.conversation_id) } });
      }
      this.renderSidebar();
    });
    on("conv.created", ({ conversation }) => this.upsertConversation(conversation));
    on("conv.updated", ({ conversation }) => this.upsertConversation(conversation));
    on("conv.deleted", ({ conversation_id }) => {
      this.state.conversations = this.state.conversations.filter((c) => c.id !== conversation_id);
      if (conversation_id === this.currentConvId) this.newChat();
      else this.renderSidebar();
    });
    on("conv.usage", ({ conversation_id, contextTokens, contextWindow }) => {
      if (conversation_id !== this.currentConvId || !contextWindow) return;
      const pct = Math.min(100, Math.round((contextTokens / contextWindow) * 100));
      $("#ctx-meter").title = `Context: ${pct}% of the model's window`;
      $("#ctx-meter").style.setProperty("--p", `${pct}`);
      $("#ctx-meter").classList.remove("idle");
    });

    on("task.updated", ({ task }) => {
      if (task.status === "running" || task.status === "queued") this.taskProgress.set(task.id, this.taskProgress.get(task.id) || "Starting…");
      else this.taskProgress.delete(task.id);
      this.refreshTaskCount();
      this.panels.notify("task.updated");
    });
    on("task.progress", ({ task_id, progress }) => {
      if (progress) this.taskProgress.set(task_id, progress);
      this.panels.notify("task.progress");
    });
    on("task.event", () => this.panels.notify("task.event"));

    on("evolution.updated", ({ proposal }) => {
      this.state.proposals = [proposal, ...(this.state.proposals || []).filter((p) => p.id !== proposal.id)];
      this.readyProposals = this.state.proposals.filter((p) => p.status === "ready").length;
      this.evolving = this.state.proposals.some((p) => ["building", "checking", "merging"].includes(p.status));
      this.renderBadges();
      this.panels.notify("evolution.updated");
      if (!this.busyConvs.has(this.currentConvId)) director.setBase(this.restingMood());
    });
    for (const t of ["memory.added", "memory.updated", "memory.deleted", "reminder.updated", "skills.updated", "evals.updated"]) {
      on(t, () => this.panels.notify(t));
    }

    on("notify", ({ notification }) => this.onNotification(notification));
    on("notifications.read", ({ ids }) => this.notifications.read(ids));
    on("interaction.pending", ({ interaction }) => this.announceInteraction(interaction));
    on("engine.status", (snapshot) => { this.state.engine = snapshot; this.renderEngine(); this.chat.updateChips(); this.panels.notify("engine.status"); });
    on("engine.usage", ({ rateLimits }) => { this.state.engine = { ...this.state.engine, rateLimits }; this.renderEngine(); });
    on("settings.updated", ({ settings }) => { this.applySettings(settings); this.panels.notify("settings.updated"); });
    on("weebo.mood", ({ mood, background, conversation_id }) => {
      if (conversation_id && conversation_id !== this.currentConvId && !["celebrate", "alert"].includes(mood)) return;
      if (["happy", "sad", "alert", "celebrate", "proud", "asking", "looking"].includes(mood)) director.flash(mood, mood === "celebrate" ? 3500 : 2600);
      else if (background && !this.busyConvs.has(this.currentConvId)) director.setBase(mood);
    });
    on("weebo.activity", ({ activity, state }) => {
      this.heartbeatBusy = state === "started" ? activity : null;
      this.chat.stage.setResting(this.restingCaption());
      if (!this.busyConvs.has(this.currentConvId)) director.setBase(this.restingMood());
      const say = { dream: "Dreaming… tidying my memories.", audit: "Reviewing my own code…", brief: "Putting your brief together…" }[activity];
      if (state === "started" && say) this.chat.stage.say(say, 5000);
      this.panels.notify("weebo.activity");
    });
    on("system.restarting", ({ reason }) => this.showRestarting(reason));
    on("ui.reload", ({ reason }) => {
      toast("Weebo upgraded its interface", reason || "", { kind: "success" });
      director.flash("celebrate", 2500);
      setTimeout(() => location.reload(), 2500);
    });
  }

  async refreshTaskCount() {
    try {
      const { tasks } = await api.get("/api/tasks");
      this.runningTasks = tasks.filter((t) => t.status === "running" || t.status === "queued").length;
      this.renderBadges();
      if (!this.busyConvs.has(this.currentConvId)) director.setBase(this.restingMood());
    } catch { /* offline */ }
  }

  onNotification(n) {
    if (!this.notifications.receive(n)) return;
    const kind = { reminder: "warn", approval: "warn", evolution: "success", agent: "info" }[n.kind] || "info";
    const data = n.data || {};
    const action = this.notifications.action(n);
    if (n.kind !== "approval") toast(n.title, n.body || "", { kind, timeout: n.kind === "reminder" ? 0 : 7000, action });
    if (n.kind === "reminder" || n.kind === "approval") director.flash("alert", 3000);
    if (n.kind === "evolution" && /upgraded/i.test(n.title)) director.flash("celebrate", 3500);
    if (data.speak) speak(`${n.title}. ${n.body}`);
    this.notifications.desktop(n);
    if (window.isSecureContext && "Notification" in window && Notification.permission === "default" && !this._askedNotify) {
      this._askedNotify = true;
      toast("Want desktop alerts?", "Weebo can ping you for reminders and finished work even when this tab is hidden.", {
        timeout: 12000, action: { label: "Enable", run: () => this.notifications.enableDesktop() },
      });
    }
  }

  showRestarting(reason) {
    const overlay = el("div", { class: "restart-overlay" });
    const host = el("div", { class: "restart-weebo" });
    overlay.append(host, el("h2", { text: "Weebo is restarting" }), el("p", { class: "muted", text: reason || "Back in a few seconds…" }));
    document.body.append(overlay);
    new WeeboAvatar(host, { size: 140, track: false });
    director.setBase("evolving");
    // Wait for the old process to go away (or 10s), then for the new one to answer.
    let sawDown = false;
    const started = Date.now();
    const poll = async () => {
      let up = false;
      try { up = (await fetch("/", { cache: "no-store" })).ok; } catch { up = false; }
      if (!up) sawDown = true;
      if (up && (sawDown || Date.now() - started > 10000)) { location.reload(); return; }
      setTimeout(poll, 1000);
    };
    setTimeout(poll, 1000);
  }

  async reconcileEvolution() {
    if (!this.currentConvId) return; // Initial bootstrap loads the persisted cards.
    const id = this.currentConvId;
    try {
      const [data, { proposals }] = await Promise.all([
        api.get(`/api/conversations/${encodeURIComponent(id)}`), api.get("/api/proposals"),
      ]);
      if (this.currentConvId === id) {
        for (const msg of data.evolution_messages || []) {
          const existing = this.chat.byId.get(msg.id);
          if (!existing && this.chat.messages.length && msg.seq < this.chat.messages[0].seq) continue;
          if (!existing || msg.updated_at >= existing.updated_at) this.chat.upsert(msg);
        }
      }
      this.state.proposals = proposals;
      this.readyProposals = proposals.filter((p) => p.status === "ready").length;
      this.evolving = proposals.some((p) => ["building", "checking", "merging"].includes(p.status));
      this.renderBadges();
      this.panels.notify("evolution.updated");
    } catch { /* The next reconnect or chat load reconciles again. */ }
  }

  routeProposal() {
    const id = proposalFromHash(location.hash);
    if (id) {
      if (this.panels.current !== "evolution" || this.panels.opts.proposal !== id) this.panels.open("evolution", { proposal: id });
    } else if (this.panels.current === "evolution" && this.panels.opts.proposal) this.panels.close();
  }

  setProposalRoute(id) {
    const hash = id ? proposalHref(id).slice(1) : "";
    if (id && !proposalFromHash(hash)) return;
    if (!id && !proposalFromHash(location.hash)) return;
    if (location.hash !== hash) history.pushState(null, "", `${location.pathname}${location.search}${hash}`);
  }

  openPanel(name, opts) { this.panels.open(name, opts); }

  // ------------------------------------------------------------------ installable app
  watchInstall() {
    const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    document.body.classList.toggle("standalone", Boolean(standalone));
    window.addEventListener("beforeinstallprompt", (event) => {
      event.preventDefault();  // show our own, friendlier offer instead of the browser's mini-bar
      this.installPrompt = event;
      if (sessionStorage.getItem("weebo.installOffered")) return;
      sessionStorage.setItem("weebo.installOffered", "1");
      toast("Install Weebo as an app?", "Opens in its own window with a home-screen icon.", {
        timeout: 15000, action: { label: "Install", run: () => this.installApp() },
      });
    });
    window.addEventListener("appinstalled", () => {
      this.installPrompt = null;
      toast("Weebo is installed", "Open it from your home screen.", { kind: "success" });
      director.flash("celebrate", 3000);
    });
  }

  async installApp() {
    const prompt = this.installPrompt;
    if (!prompt) {
      toast("Use the browser menu to install", "Chrome ⋮ menu → Install app (needs Weebo's https Tailscale address).");
      return;
    }
    this.installPrompt = null;
    prompt.prompt();
    try { await prompt.userChoice; } catch { /* dismissed */ }
  }
}

const app = new App();
window.weebo = app;
app.start();
