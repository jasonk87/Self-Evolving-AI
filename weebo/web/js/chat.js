// The conversation view: message list, live streaming, work groups and composer.
import { api, sendActivity } from "./api.js";
import { director } from "./avatar.js";
import { Stage } from "./stage.js";
import { icon } from "./icons.js";
import { renderMessage, renderWorkItem, summarizeWork, workLabel, WORK_KINDS } from "./items.js";
import { renderMarkdown } from "./markdown.js";
import { el, btn, autoGrow, toast, escapeHtml, duration, copyText } from "./ui.js";
import { startListening, isListening, voiceSupport, speak } from "./voice.js";

const SUGGESTIONS = [
  { icon: "clock", text: "Plan my day and set the reminders I'll need" },
  { icon: "bot", text: "Build a small browser game in my workspace while we chat" },
  { icon: "brain", text: "What do you remember about me so far?" },
  { icon: "dna", text: "Look at your own code and propose one upgrade" },
];

function versionOf(msg) {
  return `${msg.updated_at}|${msg.status}|${(msg.content || "").length}|${JSON.stringify(msg.data || {}).length}`;
}

function reconcile(parent, desired) {
  let cursor = parent.firstChild;
  for (const node of desired) {
    if (node === cursor) { cursor = cursor.nextSibling; continue; }
    parent.insertBefore(node, cursor);
  }
  while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next; }
}

export class ChatView {
  constructor(app) {
    this.app = app;
    this.convId = null;
    this.messages = [];
    this.byId = new Map();
    this.nodes = new Map();        // message id -> {node, version}
    this.groups = new Map();       // first item id -> group record
    this.streams = new Map();      // message id -> text
    this.pendingImages = [];
    this.busy = false;
    this.turnStartedAt = 0;
    this.reasoning = "";
    this._raf = 0;
    this._dirtyStreams = new Set();
    this.build();
  }

  // ------------------------------------------------------------------ layout
  build() {
    this.root = document.getElementById("chat");
    this.scroller = el("div", { class: "chat-scroll" });
    this.list = el("div", { class: "chat-list", role: "log", "aria-live": "polite" });
    this.status = el("div", { class: "turn-status", hidden: true });
    this.hero = el("div", { class: "hero", hidden: true });
    this.stage = new Stage();
    this.scroller.append(this.stage.root, this.hero, this.list, this.status);
    this.composer = this.buildComposer();
    this.root.append(this.scroller, this.composer);

    this.root.addEventListener("click", (e) => {
      const fileLink = e.target.closest(".file-link:not(.openable)");
      if (fileLink) { copyText(fileLink.dataset.path || fileLink.textContent); return; }
      const copy = e.target.closest(".code-copy");
      if (copy) {
        const code = copy.closest(".code")?.querySelector("code")?.innerText || "";
        navigator.clipboard?.writeText(code).then(() => { copy.textContent = "Copied"; setTimeout(() => (copy.textContent = "Copy"), 1200); });
      }
    });
    this.scroller.addEventListener("scroll", () => {
      this.stick = this.scroller.scrollTop + this.scroller.clientHeight >= this.scroller.scrollHeight - 120;
      if (this.scroller.scrollTop < 60 && this.hasMore && !this.loadingMore) this.loadOlder();
    }, { passive: true });
    this.stick = true;

    for (const type of ["dragenter", "dragover"]) this.root.addEventListener(type, (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); this.root.classList.add("dropping"); } });
    for (const type of ["dragleave", "drop"]) this.root.addEventListener(type, (e) => { if (type === "dragleave" && this.root.contains(e.relatedTarget)) return; this.root.classList.remove("dropping"); });
    this.root.addEventListener("drop", (e) => { e.preventDefault(); this.addFiles([...(e.dataTransfer?.files || [])]); });
  }

  buildComposer() {
    const wrap = el("div", { class: "composer-wrap" });
    this.attachments = el("div", { class: "attachments", hidden: true });
    this.input = el("textarea", { class: "composer-input", rows: 1, placeholder: "Message Weebo…", "aria-label": "Message Weebo" });
    this.fileInput = el("input", { type: "file", accept: "image/*", multiple: true, hidden: true, onchange: (e) => { this.addFiles([...e.target.files]); e.target.value = ""; } });
    this.attachBtn = btn("", { icon: "clip", title: "Attach images", onClick: () => this.fileInput.click() });
    this.micBtn = btn("", { icon: "mic", title: "Talk to Weebo", onClick: () => this.toggleMic() });
    if (!voiceSupport.stt) this.micBtn.hidden = true;
    this.sendBtn = el("button", { class: "send-btn", type: "button", title: "Send", "aria-label": "Send", html: icon("up", 20), onclick: () => this.submit() });
    const box = el("div", { class: "composer" }, this.attachments,
      el("div", { class: "composer-row" }, this.attachBtn, this.input, this.micBtn, this.sendBtn), this.fileInput);
    this.chips = el("div", { class: "composer-chips" });
    wrap.append(box, this.chips);

    this.input.addEventListener("input", () => {
      autoGrow(this.input);
      this.updateSendButton();
      if (!this.busy) director.setBase(this.input.value ? "listening" : this.app.restingMood());
      sendActivity();
    });
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.submit(); }
      if (e.key === "Escape" && this.busy) { e.preventDefault(); this.interrupt(); }
    });
    this.input.addEventListener("paste", (e) => {
      const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
      if (files.length) { e.preventDefault(); this.addFiles(files); }
    });
    this.input.addEventListener("blur", () => { if (!this.busy && !this.input.value) director.setBase(this.app.restingMood()); });
    return wrap;
  }

  updateChips() {
    const conv = this.app.conversation(this.convId);
    const s = this.app.state.settings || {};
    const engine = this.app.state.engine || {};
    const model = (engine.models || []).find((m) => m.id === (s.codex?.model || "")) || (engine.models || []).find((m) => m.isDefault);
    const chips = [
      el("button", { class: "chip ghost", type: "button", title: "Model and reasoning effort", onclick: () => this.app.openPanel("settings") },
        el("span", { html: icon("zap", 13) }), `${model?.name || "Codex"} · ${s.codex?.chat_effort || "medium"}`),
      el("button", { class: "chip ghost", type: "button", title: "Working folder for this chat", onclick: () => this.app.pickFolder(this.convId) },
        el("span", { html: icon("folder", 13) }), conv?.cwd ? conv.cwd.split(/[\\/]/).slice(-2).join("/") : "Weebo workspace"),
      el("button", { class: "chip ghost", type: "button", title: "Autonomy level", onclick: () => this.app.openPanel("settings") },
        el("span", { html: icon("shield", 13) }), { cautious: "Cautious", balanced: "Balanced", full: "Full trust" }[s.autonomy?.level] || "Balanced"),
    ];
    this.chips.replaceChildren(...chips);
  }

  // ------------------------------------------------------------------ loading
  async open(convId) {
    this.convId = convId;
    this.messages = [];
    this.byId.clear();
    this.nodes.clear();
    this.groups.clear();
    this.streams.clear();
    this.list.replaceChildren();
    this.busy = false;
    this.hasMore = false;
    this.root.classList.add("loading");
    try {
      const data = await api.get(`/api/conversations/${encodeURIComponent(convId)}`);
      if (this.convId !== convId) return;
      this.setMessages(data.messages || []);
      this.hasMore = (data.messages || []).length >= 200;
      const live = data.live || {};
      for (const [id, text] of Object.entries(live.streams || {})) this.streams.set(id, text);
      this.setBusy(Boolean(live.busy), live.started_at, live.reasoning);
      this.app.markRead(convId, data.conversation);
    } catch (err) {
      toast("Couldn't open that chat", err.message, { kind: "error" });
    } finally {
      this.root.classList.remove("loading");
    }
    this.render(true);
    this.updateChips();
    this.input.focus({ preventScroll: true });
  }

  async loadOlder() {
    const first = this.messages[0];
    if (!first) return;
    this.loadingMore = true;
    const before = this.scroller.scrollHeight;
    try {
      const data = await api.get(`/api/conversations/${encodeURIComponent(this.convId)}?before=${first.seq}&limit=150`);
      const older = data.messages || [];
      this.hasMore = older.length >= 150;
      if (older.length) {
        this.setMessages([...older, ...this.messages]);
        this.render();
        this.scroller.scrollTop += this.scroller.scrollHeight - before;
      }
    } finally {
      this.loadingMore = false;
    }
  }

  setMessages(list) {
    this.messages = list;
    this.byId = new Map(list.map((m) => [m.id, m]));
  }

  // ------------------------------------------------------------------ events
  upsert(msg) {
    if (msg.conversation_id !== this.convId) return;
    const existing = this.byId.get(msg.id);
    if (existing) {
      Object.assign(existing, msg);
    } else {
      this.messages.push(msg);
      this.messages.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
    }
    this.byId.set(msg.id, existing || msg);
    if (msg.status !== "streaming") this.streams.delete(msg.id);
    if (WORK_KINDS.has(msg.kind) && msg.status === "running" && msg.kind !== "reasoning") {
      const info = workLabel(msg);
      const label = msg.kind === "command" && info.ic === "terminal" ? `Running: ${info.text}` : (info.text || "");
      this.stage.say(label.length > 90 ? label.slice(0, 87) + "…" : label, 9000);
    } else if (msg.kind === "approval" && msg.status === "pending") {
      this.stage.say("I need your OK on something below.", 0);
    }
    this.render();
    if (msg.role === "assistant" && msg.kind === "text" && msg.status === "done" && msg.data?.phase !== "commentary") {
      speak(msg.content);
    }
  }

  delta({ conversation_id, message_id, delta }) {
    if (conversation_id !== this.convId) return;
    if (!this.streams.has(message_id)) this.stage.clear();  // the words are on screen now
    this.streams.set(message_id, (this.streams.get(message_id) || "") + delta);
    this._dirtyStreams.add(message_id);
    director.setBase("talking");
    this.scheduleStreamPaint();
  }

  output({ conversation_id, message_id, delta }) {
    if (conversation_id !== this.convId) return;
    const pre = this.list.querySelector(`.cmd-out[data-output="${CSS.escape(message_id)}"]`);
    if (pre) {
      pre.textContent = (pre.textContent + delta).slice(-20000);
      pre.scrollTop = pre.scrollHeight;
    }
  }

  reasoningDelta({ conversation_id, delta, reset }) {
    if (conversation_id !== this.convId) return;
    this.reasoning = reset ? "" : (this.reasoning + (delta || "")).slice(-400);
    const snippet = this.reasoning.replace(/\*\*/g, "").split("\n").filter(Boolean).pop() || "";
    if (snippet && this.statusText) {
      this.statusText.textContent = snippet.length > 120 ? snippet.slice(0, 117) + "…" : snippet;
      this.stage.say(snippet.length > 110 ? snippet.slice(0, 107) + "…" : snippet, 8000);
    }
    if (!this._dirtyStreams.size) director.setBase("thinking");
  }

  scheduleStreamPaint() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      for (const id of this._dirtyStreams) {
        // Completion can replace the node and clear its stream before this frame runs.
        // The persisted reply must take precedence over a queued partial paint.
        if (this.byId.get(id)?.status !== "streaming") continue;
        const record = this.nodes.get(id);
        const md = record?.node?.querySelector(".md");
        if (md) md.innerHTML = renderMarkdown(this.streams.get(id) || "");
      }
      this._dirtyStreams.clear();
      this.keepBottom();
    });
  }

  setBusy(busy, startedAt, reasoning) {
    this.busy = busy;
    this.root.classList.toggle("busy", busy);
    if (busy) {
      this.turnStartedAt = startedAt || Date.now() / 1000;
      this.reasoning = reasoning || "";
      this.renderStatus();
      director.setBase("thinking");
    } else {
      this.status.hidden = true;
      clearInterval(this._timer);
      this.stage.clear();
    }
    this.updateSendButton();
  }

  renderStatus() {
    this.status.hidden = false;
    this.statusText = el("span", { class: "turn-text", text: "Thinking…" });
    const timer = el("span", { class: "turn-time" });
    this.status.replaceChildren(el("span", { class: "typing" }, el("i"), el("i"), el("i")), this.statusText, timer,
      btn("Stop", { kind: "soft", size: "sm", icon: "stop", onClick: () => this.interrupt() }));
    clearInterval(this._timer);
    const tick = () => { timer.textContent = duration(Date.now() / 1000 - this.turnStartedAt); };
    tick();
    this._timer = setInterval(tick, 1000);
  }

  turnStarted() { this.setBusy(true); this.keepBottom(true); }

  turnCompleted({ status }) {
    this.setBusy(false);
    if (status === "completed") {
      director.flash("happy", 1800);
      this.stage.say(["Done!", "All set.", "There you go.", "Finished."][Math.floor(Math.random() * 4)], 2500);
    } else if (status === "failed") {
      director.flash("sad", 2600);
      this.stage.say("Hmm, that didn't work. Details below.", 5000);
    }
    director.setBase(this.app.restingMood());
    this.render();
  }

  // ------------------------------------------------------------------ rendering
  nodeFor(msg) {
    const version = versionOf(msg);
    const cached = this.nodes.get(msg.id);
    if (cached && cached.version === version) return cached.node;
    const view = msg.status === "streaming" ? { ...msg, content: this.streams.get(msg.id) || msg.content } : msg;
    const node = renderMessage(view, this.ctx());
    if (cached?.node?.parentNode) cached.node.replaceWith(node);
    this.nodes.set(msg.id, { node, version });
    return node;
  }

  itemNodeFor(msg) {
    const version = versionOf(msg);
    const cached = this.nodes.get(msg.id);
    if (cached && cached.version === version) return cached.node;
    const node = renderWorkItem(msg);
    if (cached?.node?.classList.contains("open")) node.querySelector(".work-row")?.click();
    if (cached?.node?.parentNode) cached.node.replaceWith(node);
    this.nodes.set(msg.id, { node, version });
    return node;
  }

  groupFor(firstId) {
    let group = this.groups.get(firstId);
    if (!group) {
      const head = el("button", { class: "group-head", type: "button" });
      const items = el("div", { class: "group-items" });
      const node = el("div", { class: "msg work-group" }, head, items);
      group = { node, head, items, userToggled: false, open: true };
      head.addEventListener("click", () => {
        group.userToggled = true;
        group.open = !group.open;
        node.classList.toggle("collapsed", !group.open);
      });
      this.groups.set(firstId, group);
    }
    return group;
  }

  updateGroup(group, items) {
    const summary = summarizeWork(items);
    const label = summary.running ? "Working" : `Worked${summary.seconds >= 1 ? ` for ${duration(summary.seconds)}` : ""}`;
    group.head.innerHTML = `${summary.running ? '<span class="spin"></span>' : icon("chevronRight", 14, "chev")}`
      + `<span class="group-title">${label}</span><span class="group-sub">${escapeHtml(summary.text)}</span>`
      + (summary.failed ? `<span class="group-fail">${summary.failed} failed</span>` : "");
    if (!group.userToggled) group.open = summary.running;
    group.node.classList.toggle("collapsed", !group.open);
    group.node.classList.toggle("running", summary.running);
    reconcile(group.items, items.map((m) => this.itemNodeFor(m)));
  }

  render(forceBottom = false) {
    const desired = [];
    let run = null;
    const flush = () => {
      if (!run) return;
      const group = this.groupFor(run[0].id);
      this.updateGroup(group, run);
      desired.push(group.node);
      run = null;
    };
    for (const msg of this.messages) {
      if (WORK_KINDS.has(msg.kind)) {
        (run ||= []).push(msg);
        continue;
      }
      flush();
      desired.push(this.nodeFor(msg));
    }
    flush();
    reconcile(this.list, desired);
    const conv = this.app.conversation(this.convId);
    this.hero.hidden = !(this.messages.length === 0 && conv && conv.kind === "chat");
    this.stage.setBig(!this.hero.hidden);
    if (!this.hero.hidden) this.renderHero();
    this.keepBottom(forceBottom);
  }

  renderHero() {
    if (this.hero.childElementCount) return;
    const name = this.app.state.settings?.user?.name;
    const hour = new Date().getHours();
    const greet = hour < 5 ? "Burning the midnight oil" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    this.hero.append(
      el("h1", { text: `${greet}${name ? `, ${name}` : ""}.` }),
      el("p", { class: "muted", text: "I can chat, run commands, build things with parallel agents, remember what matters, and upgrade myself. What are we doing?" }),
      el("div", { class: "suggestions" }, SUGGESTIONS.map((s) => el("button", { class: "suggestion", type: "button", onclick: () => { this.input.value = s.text; this.submit(); } },
        el("span", { html: icon(s.icon, 16) }), el("span", { text: s.text })))));
  }

  keepBottom(force = false) {
    if (force || this.stick) requestAnimationFrame(() => { this.scroller.scrollTop = this.scroller.scrollHeight; });
  }

  ctx() {
    return {
      resolve: (id, decision, answers) => this.app.resolveInteraction(id, decision, answers),
      openTask: (id) => this.app.openPanel("agents", { task: id }),
      openProposal: (id) => this.app.openPanel("evolution", { proposal: id }),
    };
  }

  // ------------------------------------------------------------------ composer actions
  updateSendButton() {
    const hasText = this.input.value.trim() || this.pendingImages.length;
    const stopMode = this.busy && !hasText;
    this.sendBtn.innerHTML = icon(stopMode ? "stop" : "up", 20);
    this.sendBtn.title = stopMode ? "Stop (Esc)" : this.busy ? "Add to the current task" : "Send";
    this.sendBtn.classList.toggle("stop", stopMode);
    this.sendBtn.disabled = !stopMode && !hasText;
    this.input.placeholder = this.busy ? "Add to the task… (Esc stops)" : "Message Weebo…";
  }

  async addFiles(files) {
    for (const file of files.filter((f) => f.type.startsWith("image/")).slice(0, 6)) {
      const chip = el("div", { class: "attachment uploading" }, el("img", { src: URL.createObjectURL(file), alt: file.name }));
      this.attachments.append(chip);
      this.attachments.hidden = false;
      try {
        const res = await api.upload(file);
        chip.classList.remove("uploading");
        const record = { name: res.name, chip };
        this.pendingImages.push(record);
        chip.append(btn("", { icon: "x", title: "Remove", size: "sm", onClick: () => {
          this.pendingImages = this.pendingImages.filter((r) => r !== record);
          chip.remove();
          this.attachments.hidden = !this.pendingImages.length;
          this.updateSendButton();
        } }));
      } catch (err) {
        chip.remove();
        toast("Upload failed", err.message, { kind: "error" });
      }
    }
    this.attachments.hidden = !this.attachments.childElementCount;
    this.updateSendButton();
  }

  toggleMic() {
    const base = this.input.value;
    let heard = false; // only a successful final transcript may auto-send
    const ok = startListening({
      onPartial: (text) => { this.input.value = (base ? base + " " : "") + text; autoGrow(this.input); },
      onFinal: (text) => { heard = true; this.input.value = (base ? base + " " : "") + text; autoGrow(this.input); this.updateSendButton(); },
      onState: (on, error) => {
        this.micBtn.classList.toggle("live", on);
        if (!on && !this.busy) director.setBase(this.app.restingMood());
        if (error && error !== "no-speech" && error !== "aborted") toast("Microphone", `Speech recognition error: ${error}`, { kind: "warn" });
        if (!on && !error && heard && this.input.value.trim() && this.app.state.settings?.voice?.speak_replies) this.submit();
      },
    });
    if (!ok) toast("Voice input isn't supported in this browser", "Try Chrome or Edge.", { kind: "warn" });
  }

  async submit() {
    const text = this.input.value.trim();
    const images = this.pendingImages.map((r) => r.name);
    if (!text && !images.length) {
      if (this.busy) this.interrupt();
      return;
    }
    if (!this.convId) return;
    this.input.value = "";
    autoGrow(this.input);
    this.pendingImages = [];
    this.attachments.replaceChildren();
    this.attachments.hidden = true;
    this.updateSendButton();
    if (isListening()) this.toggleMic();
    director.setBase("thinking");
    this.stage.say("On it!", 2500);
    try {
      await api.post(`/api/conversations/${encodeURIComponent(this.convId)}/messages`, { text, images });
    } catch (err) {
      toast("Message not sent", err.message, { kind: "error" });
      this.input.value = text;
      autoGrow(this.input);
      this.updateSendButton();
      director.flash("sad", 2000);
    }
  }

  async interrupt() {
    if (!this.convId) return;
    try {
      await api.post(`/api/conversations/${encodeURIComponent(this.convId)}/interrupt`);
      this.stage.say("Stopped.", 2500);
    } catch (err) {
      toast("Couldn't stop", err.message, { kind: "error" });
    }
  }
}
