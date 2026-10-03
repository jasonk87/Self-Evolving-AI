// Weebo's center stage: the companion sits at the top of the chat with its name
// and a live reaction line underneath (what it's thinking, doing, or feeling).
import { director, WeeboAvatar } from "./avatar.js";
import { el } from "./ui.js";

const POKES = [
  "Hey! That tickles.", "Still here, still sharp.", "Boop received.", "Ready when you are.",
  "Need a hand with something?", "I'm all ears. Well, antenna.",
];

export class Stage {
  constructor() {
    this.weeboHost = el("div", { class: "stage-weebo", title: "Weebo" });
    this.name = el("div", { class: "stage-name", text: "Weebo" });
    this.caption = el("div", { class: "stage-say", role: "status", "aria-live": "polite" });
    this.root = el("div", { class: "stage" }, this.weeboHost, this.name, this.caption);
    this.avatar = new WeeboAvatar(this.weeboHost, { label: "Weebo" });
    this.resting = "";
    this._timer = null;
    this.weeboHost.addEventListener("click", () => {
      director.flash("happy", 1400);
      this.say(POKES[Math.floor(Math.random() * POKES.length)], 2600);
    });
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
