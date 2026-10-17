// Weebo's center stage: the companion sits at the top of the chat with its name
// and a live reaction line underneath (what it's thinking, doing, or feeling).
import { director, WeeboAvatar } from "./avatar.js";
import { icon } from "./icons.js";
import { el } from "./ui.js";

const KIND_ICON = { coding: "code", writing: "edit", reviewing: "list", researching: "search", delegating: "users",
  calling: "phone", syncing: "refresh", thinking: "sparkles", dreaming: "moon", sleeping: "moon", waiting: "hand" };

export function elapsedText(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** Words for an activity entry right now: honest when it has gone quiet ("Still running the tests · 3 min"). */
export function entryText(entry, now = Date.now() / 1000) {
  const quiet = now - entry.last_event_at;
  let words = entry.words;
  if (entry.stale_after && quiet > entry.stale_after) {
    if (!/^still /i.test(words)) words = `Still ${words.charAt(0).toLowerCase()}${words.slice(1)}`;
    if (quiet > entry.stale_after * 6) words += ` (no word for ${elapsedText(quiet)})`;
  }
  return `${words} · ${elapsedText(now - entry.started_at)}`;
}

const POKES = [
  "Hey! That tickles.", "Still here, still sharp.", "Boop received.", "Ready when you are.",
  "Need a hand with something?", "I'm all ears. Well, antenna.",
];

export class Stage {
  constructor() {
    this.weeboHost = el("div", { class: "stage-weebo", title: "Weebo" });
    this.name = el("div", { class: "stage-name", text: "Weebo" });
    this.caption = el("div", { class: "stage-say", role: "status", "aria-live": "polite" });
    // The one "now showing" slot: the most important thing going on, with everything else one tap away.
    this.nowIcon = el("span", { class: "now-ic" });
    this.nowText = el("span", { class: "now-text" });
    this.moreChip = el("button", { class: "now-more", type: "button", hidden: true, onclick: (e) => { e.stopPropagation(); this.toggleList(); } });
    this.now = el("div", { class: "now-showing state-idle", role: "status", "aria-live": "polite" }, this.nowIcon, this.nowText, this.moreChip);
    this.list = el("div", { class: "activity-list", hidden: true, role: "list" });
    this.root = el("div", { class: "stage" }, this.weeboHost, this.name, this.caption, this.now, this.list);
    this.activity = null;
    this.onOpen = null;
    this._tick = setInterval(() => this.renderActivity(), 5000);
    this.avatar = new WeeboAvatar(this.weeboHost, { label: "Weebo" });
    this.resting = "";
    this._timer = null;
    this.weeboHost.addEventListener("click", () => {
      director.flash("happy", 1400);
      this.say(POKES[Math.floor(Math.random() * POKES.length)], 2600);
    });
  }

  /** Show the server's activity snapshot (see weebo/brain/activity_registry.py). */
  setActivity(snapshot, onOpen) {
    this.activity = snapshot;
    if (onOpen) this.onOpen = onOpen;
    this.renderActivity();
  }

  renderActivity() {
    const s = this.activity;
    if (!s) return;
    const top = s.now;
    const state = top ? (top.waiting_on_user ? "waiting" : "working") : s.state || "idle";
    const stale = Boolean(top && top.stale_after && Date.now() / 1000 - top.last_event_at > top.stale_after);
    this.now.className = `now-showing state-${state}${stale ? " stale" : ""}`;
    this.now.dataset.state = state;
    const kind = top ? top.kind : state === "sleeping" ? "sleeping" : "";
    this.nowIcon.innerHTML = kind ? icon(KIND_ICON[kind] || "dot", 14) : "";
    this.nowText.textContent = top ? entryText(top) : state === "sleeping" ? "Sleeping · quiet hours" : "Resting · nothing running";
    this.now.onclick = top && this.onOpen ? () => this.onOpen(top) : null;
    const more = (s.entries || []).length - (top ? 1 : 0);
    this.moreChip.hidden = more <= 0;
    this.moreChip.textContent = `+${more} more`;
    if (more <= 0) this.list.hidden = true;
    if (!this.list.hidden) this.renderList();
  }

  toggleList() {
    this.list.hidden = !this.list.hidden;
    if (!this.list.hidden) this.renderList();
  }

  renderList() {
    const entries = (this.activity?.entries || []);
    this.list.replaceChildren(...entries.map((entry) => el("button", {
      class: `activity-row${entry.waiting_on_user ? " waiting" : ""}`, type: "button", role: "listitem",
      onclick: () => this.onOpen?.(entry) },
    el("span", { class: "now-ic", html: icon(KIND_ICON[entry.kind] || "dot", 13) }),
    el("span", { class: "activity-who", text: entry.owner_name }),
    el("span", { class: "activity-words", text: entryText(entry) }))));
  }

  setBig(big) {
    this.root.classList.toggle("big", big);
  }

  /** Show a reaction for a while, then fall back to the resting caption. */
  say(text, ms = 5000) {
    clearTimeout(this._timer);
    this._show(text || this.resting);
    if (text && ms) this._timer = setTimeout(() => this._show(this.resting), ms);
  }

  /** The caption shown when nothing in particular is happening (e.g. "2 agents working"). */
  setResting(text) {
    const changed = this.resting !== text;
    this.resting = text || "";
    if (changed && !this._timer) this._show(this.resting);
  }

  clear() {
    clearTimeout(this._timer);
    this._timer = null;
    this._show(this.resting);
  }

  _show(text) {
    if (this.caption.textContent === text) return;
    this.caption.classList.remove("in");
    // Restart the fade so each new reaction reads as a fresh "line".
    requestAnimationFrame(() => {
      this.caption.textContent = text;
      if (text) this.caption.classList.add("in");
    });
    if (!text) this._timer = null;
  }
}
