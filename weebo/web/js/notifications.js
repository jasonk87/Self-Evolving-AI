// Persisted notification inbox. Opening content marks only that record read.
import { api } from "./api.js";
import { el, btn, toast, timeAgo, modal } from "./ui.js";

export class NotificationInbox {
  constructor(app) {
    this.app = app;
    this.unread = new Map();
    this.seen = new Set();
    this.readIds = new Set();
    this.reading = new Map();
    this.versions = new Map();
    this.version = 0;
    this.desktopError = "";
    this.syncError = "";
    this.banner = el("section", { class: "notification-banner", hidden: true, "aria-label": "Unread notifications", "aria-live": "polite" });
    document.getElementById("topbar").after(this.banner);
  }

  records() { return [...this.unread.values()].sort((a, b) => b.seq - a.seq); }

  receive(note) {
    if (!note?.id || this.readIds.has(note.id)) return false;
    if (note.read) { this.read([note.id]); return false; }
    const fresh = !this.seen.has(note.id);
    this.seen.add(note.id);
    this.unread.set(note.id, note);
    this.versions.set(note.id, ++this.version);
    this.update();
    return fresh;
  }

  read(ids) {
    for (const id of ids) {
      this.readIds.add(id);
      this.unread.delete(id);
    }
    this.update();
  }

  markRead(id) {
    if (this.readIds.has(id)) return;
    if (this.reading.has(id)) return this.reading.get(id);
    const request = api.post("/api/notifications/read", { ids: [id] })
      .then(() => this.read([id]))
      .catch((err) => toast("Couldn't mark notification read", err.message, { kind: "error" }))
      .finally(() => this.reading.delete(id));
    this.reading.set(id, request);
    return request;
  }

  open(note) {
    modal(note.title, el("p", { class: "notification-content", text: note.body || note.title }));
    this.markRead(note.id);
  }

  // Coalesce overlapping recovery requests, then fetch again if another return
  // happened in flight. Keep live arrivals newer than the request's snapshot.
  reconcile() {
    if (this.syncing) { this.again = true; return this.syncing; }
    this.syncing = (async () => {
      do {
        this.again = false;
        const version = this.version;
        try {
          const { notifications, read_notification_ids } = await api.get("/api/notifications?unread_only=1");
          this.read(read_notification_ids || []);
          const ids = new Set(notifications.map((n) => n.id));
          for (const id of this.unread.keys()) {
            if (!ids.has(id) && this.versions.get(id) <= version) this.read([id]);
          }
          for (const note of notifications) this.receive(note);
          this.syncError = "";
        } catch (err) { this.syncError = `Couldn't refresh notifications: ${err.message}`; }
        this.update();
      } while (this.again);
    })().finally(() => { this.syncing = null; });
    return this.syncing;
  }

  action(note) {
    const data = note.data || {};
    let action;
    if (data.proposal_id) action = { label: "Review", run: () => this.app.panels.open("evolution", { proposal: data.proposal_id }) };
    else if (data.conversation_id) action = { label: "Open", run: () => this.app.openConversation(data.conversation_id) };
    else if (data.task_id) action = { label: "Open", run: () => this.app.panels.open("agents", { task: data.task_id }) };
    else return { label: "Open", run: () => this.open(note) };
    return { label: action.label, run: () => { action.run(); this.markRead(note.id); } };
  }

  renderRecord(note) {
    const action = this.action(note);
    return el("article", { class: `note${note.read ? "" : " unread"}`, dataset: { notificationId: note.id } },
      el("button", { class: "note-title", type: "button", onclick: () => this.open(note) }, el("strong", { text: note.title })),
      note.body ? el("p", { class: "muted small", text: note.body }) : null,
      el("span", { class: "tl-time", text: timeAgo(note.created_at) }),
      el("div", { class: "row wrap" },
        btn(action.label, { kind: "soft", size: "sm", onClick: action.run })));
  }

  update() {
    const records = this.records();
    this.app.setUnread(records.length);
    this.banner.hidden = !records.length && !this.syncError;
    const latest = records.find((n) => ["evolution", "agent", "approval"].includes(n.kind)) || records[0];
    const action = latest && this.action(latest);
    this.banner.replaceChildren(
      el("div", {}, latest ? el("strong", { text: latest.title }) : null,
        el("p", { class: "small", text: this.syncError || `${records.length} unread notification${records.length === 1 ? "" : "s"}. Open an item to mark it read.` })),
      el("div", { class: "row wrap" }, action ? btn(action.label, { kind: "soft", size: "sm", onClick: action.run }) : null,
        btn("Notification inbox", { kind: "soft", size: "sm", onClick: () => this.app.panels.open("activity") })));
    this.app.panels?.notify("notify");
  }

  capability() {
    if (!window.isSecureContext) return "Browser alerts unavailable: this address is not a secure context.";
    if (!("Notification" in window)) return "Browser alerts unsupported on this device.";
    if (Notification.permission === "denied") return "Browser alerts blocked in browser permissions.";
    if (Notification.permission !== "granted") return "Browser alert permission has not been granted.";
    if (this.desktopError) return `Browser alert failed: ${this.desktopError}. Your alert remains in the inbox.`;
    return "Browser alert permission granted. Delivery depends on browser support while this page is running.";
  }

  async enableDesktop() {
    try { await Notification.requestPermission(); this.desktopError = ""; }
    catch (err) { this.desktopError = err.message || "Permission request failed"; }
    this.app.panels.notify("settings.updated");
  }

  desktop(note) {
    if (!document.hidden || !window.isSecureContext || !("Notification" in window) || Notification.permission !== "granted") return;
    const failed = (err) => {
      this.desktopError = err?.message || "The browser could not show the notification";
      this.app.panels.notify("settings.updated");
      toast("Browser alert failed", "Your notification remains in the notification inbox.", { kind: "warn" });
    };
    try {
      const alert = new Notification(note.title, { body: note.body, icon: "/static/icon.svg", tag: note.id });
      alert.onerror = failed;
      alert.onshow = () => { this.desktopError = ""; this.app.panels.notify("settings.updated"); };
      alert.onclick = () => { window.focus(); const action = this.action(note); if (action) action.run(); };
    } catch (err) { failed(err); }
  }
}
