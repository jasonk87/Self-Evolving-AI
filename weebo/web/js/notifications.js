// Persisted notification inbox. Display and navigation never acknowledge a record.
import { api } from "./api.js";
import { el, btn, toast, timeAgo } from "./ui.js";

export class NotificationInbox {
  constructor(app) {
    this.app = app;
    this.unread = new Map();
    this.seen = new Set();
    this.acknowledged = new Set();
    this.versions = new Map();
    this.version = 0;
    this.desktopError = "";
    this.syncError = "";
    this.banner = el("section", { class: "notification-banner", hidden: true, "aria-label": "Unread notifications", "aria-live": "polite" });
    document.getElementById("topbar").after(this.banner);
  }

  records() { return [...this.unread.values()].sort((a, b) => b.seq - a.seq); }

  receive(note) {
    if (!note?.id || this.acknowledged.has(note.id)) return false;
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
      this.acknowledged.add(id);
      this.unread.delete(id);
    }
    this.update();
  }

  async acknowledge(id) {
    try {
      await api.post("/api/notifications/read", { ids: [id] });
      this.read([id]);
    } catch (err) { toast("Couldn't acknowledge notification", err.message, { kind: "error" }); }
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
          const { notifications } = await api.get("/api/notifications?unread_only=1");
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
    if (data.proposal_id) return { label: "Review", run: () => this.app.panels.open("evolution", { proposal: data.proposal_id }) };
    if (data.conversation_id) return { label: "Open", run: () => this.app.openConversation(data.conversation_id) };
    if (data.task_id) return { label: "Open", run: () => this.app.panels.open("agents", { task: data.task_id }) };
    return null;
  }

  renderRecord(note) {
    const action = this.action(note);
    return el("article", { class: `note${note.read ? "" : " unread"}`, dataset: { notificationId: note.id } },
      el("strong", { text: note.title }), note.body ? el("p", { class: "muted small", text: note.body }) : null,
      el("span", { class: "tl-time", text: timeAgo(note.created_at) }),
      el("div", { class: "row wrap" },
        action ? btn(action.label, { kind: "soft", size: "sm", onClick: action.run }) : null,
        !note.read ? btn("Acknowledge", { size: "sm", onClick: () => this.acknowledge(note.id) }) : null));
  }

  update() {
    const records = this.records();
    this.app.setUnread(records.length);
    this.banner.hidden = !records.length && !this.syncError;
    const latest = records.find((n) => ["evolution", "agent", "approval"].includes(n.kind)) || records[0];
    const action = latest && this.action(latest);
    this.banner.replaceChildren(
      el("div", {}, latest ? el("strong", { text: latest.title }) : null,
        el("p", { class: "small", text: this.syncError || `${records.length} unread notification${records.length === 1 ? "" : "s"}. Available until acknowledged.` })),
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
