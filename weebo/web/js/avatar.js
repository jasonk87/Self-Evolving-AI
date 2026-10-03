// Weebo — an original hovering-robot cartoon drawn in SVG and animated with CSS.
// A single "director" decides Weebo's mood from app state; every avatar on the
// page (the chat stage, the sidebar badge, the restart screen) mirrors it.

let uid = 0;

const MOODS = [
  "idle", "listening", "thinking", "talking", "working", "happy", "sad", "alert", "asking", "sleepy",
  "dreaming", "celebrate", "evolving", "inspecting", "delegating", "looking", "proud", "offline",
];

function svgMarkup(id) {
  return `
<svg class="weebo-svg" viewBox="0 0 200 200" role="img" aria-label="Weebo">
  <defs>
    <radialGradient id="wb-shell-${id}" cx="36%" cy="28%" r="80%">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset=".55" stop-color="#eaf0f9"/>
      <stop offset="1" stop-color="#aeb9cf"/>
    </radialGradient>
    <linearGradient id="wb-fin-${id}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#e3e9f3"/>
      <stop offset="1" stop-color="#9ea9c0"/>
    </linearGradient>
    <linearGradient id="wb-visor-${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1f2a3f"/>
      <stop offset="1" stop-color="#090d17"/>
    </linearGradient>
    <radialGradient id="wb-bulb-${id}" cx="40%" cy="35%" r="70%">
      <stop offset="0" stop-color="#fff4cf"/>
      <stop offset=".45" class="wb-bulb-mid"/>
      <stop offset="1" class="wb-bulb-edge"/>
    </radialGradient>
    <radialGradient id="wb-jet-${id}" cx="50%" cy="50%" r="50%">
      <stop offset="0" class="wb-jet-core"/>
      <stop offset="1" class="wb-jet-fade"/>
    </radialGradient>
    <filter id="wb-glow-${id}" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="2.2" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>

  <ellipse class="wb-shadow" cx="100" cy="191" rx="38" ry="5"/>
  <ellipse class="wb-jet" cx="100" cy="181" rx="26" ry="9" fill="url(#wb-jet-${id})"/>

  <g class="wb-body">
    <g class="wb-antenna">
      <path d="M100 55 Q98 40 104 29" fill="none" stroke="#c9d2e2" stroke-width="4.5" stroke-linecap="round"/>
      <circle class="wb-bulb" cx="105" cy="24" r="8" fill="url(#wb-bulb-${id})" filter="url(#wb-glow-${id})"/>
      <circle cx="102.5" cy="21.5" r="2.4" fill="#fff" opacity=".85"/>
    </g>
    <path class="wb-fin wb-fin-l" d="M37 96 C21 97 16 117 29 128 C34 132 40 128 40 121 Z" fill="url(#wb-fin-${id})"/>
    <path class="wb-fin wb-fin-r" d="M163 96 C179 97 184 117 171 128 C166 132 160 128 160 121 Z" fill="url(#wb-fin-${id})"/>
    <path class="wb-thruster" d="M84 163 h32 a5 5 0 0 1 -4.5 8 h-23 a5 5 0 0 1 -4.5 -8z" fill="#9aa6bd"/>
    <path class="wb-shell" d="M100 52 C141 52 168 73 168 109 C168 147 140 168 100 168 C60 168 32 147 32 109 C32 73 59 52 100 52Z" fill="url(#wb-shell-${id})"/>
    <path d="M44 138 C66 156 134 156 156 138" fill="none" stroke="#000" stroke-opacity=".07" stroke-width="3" stroke-linecap="round"/>
    <rect class="wb-visor" x="50" y="80" width="100" height="60" rx="30" fill="url(#wb-visor-${id})" stroke="#8d99b2" stroke-width="2"/>
    <path d="M66 86 C80 81 120 81 134 86" fill="none" stroke="#fff" stroke-opacity=".16" stroke-width="3" stroke-linecap="round"/>

    <g class="wb-face">
      <g class="wb-cheeks">
        <ellipse cx="63" cy="127" rx="7" ry="3.6" fill="#ff8fb7"/>
        <ellipse cx="137" cy="127" rx="7" ry="3.6" fill="#ff8fb7"/>
      </g>
      <g class="wb-eyes" filter="url(#wb-glow-${id})">
        <g class="wb-eye wb-eye-l">
          <rect class="wb-eye-open" x="71.5" y="99" width="15" height="22" rx="7.5"/>
          <path class="wb-eye-happy" d="M70 114 Q79 101 88 114"/>
          <path class="wb-eye-closed" d="M70 111 Q79 117 88 111"/>
          <path class="wb-eye-sad" d="M71 106 L87 112"/>
          <circle class="wb-eye-spark" cx="83" cy="104" r="2.2"/>
        </g>
        <g class="wb-eye wb-eye-r">
          <rect class="wb-eye-open" x="113.5" y="99" width="15" height="22" rx="7.5"/>
          <path class="wb-eye-happy" d="M112 114 Q121 101 130 114"/>
          <path class="wb-eye-closed" d="M112 111 Q121 117 130 111"/>
          <path class="wb-eye-sad" d="M129 106 L113 112"/>
          <circle class="wb-eye-spark" cx="125" cy="104" r="2.2"/>
        </g>
        <g class="wb-mouth">
          <rect class="wb-mouth-line" x="93" y="126" width="14" height="4" rx="2"/>
          <path class="wb-mouth-smile" d="M90 125 Q100 134 110 125"/>
          <path class="wb-mouth-frown" d="M91 131 Q100 124 109 131"/>
          <ellipse class="wb-mouth-o" cx="100" cy="128" rx="4.5" ry="5"/>
        </g>
      </g>
    </g>
    <ellipse cx="71" cy="70" rx="17" ry="7.5" transform="rotate(-24 71 70)" fill="#fff" opacity=".6"/>
    <circle cx="90" cy="61" r="3" fill="#fff" opacity=".7"/>
  </g>

  <g class="wb-fx">
    <g class="wb-dots"><circle cx="146" cy="52" r="3.4"/><circle cx="157" cy="41" r="4.6"/><circle cx="171" cy="29" r="6"/></g>
    <g class="wb-gear">
      <circle class="wb-gear-teeth" cx="161" cy="46" r="11" stroke-dasharray="4.3 4.3"/>
      <circle class="wb-gear-ring" cx="161" cy="46" r="8"/>
      <circle class="wb-gear-hole" cx="161" cy="46" r="3.2"/>
    </g>
    <g class="wb-zzz"><text x="138" y="50">z</text><text x="151" y="36">z</text><text x="165" y="22">Z</text></g>
    <g class="wb-bubble wb-bang"><circle cx="160" cy="40" r="15"/><text x="160" y="47">!</text></g>
    <g class="wb-bubble wb-ask"><circle cx="160" cy="40" r="15"/><text x="160" y="47">?</text></g>
    <g class="wb-sparkles">
      <path d="M28 52 l3 7 7 3 -7 3 -3 7 -3 -7 -7 -3 7 -3z"/>
      <path d="M173 72 l2.2 5 5 2.2 -5 2.2 -2.2 5 -2.2 -5 -5 -2.2 5 -2.2z"/>
      <path d="M38 160 l2 4.5 4.5 2 -4.5 2 -2 4.5 -2 -4.5 -4.5 -2 4.5 -2z"/>
      <path d="M166 150 l2.6 6 6 2.6 -6 2.6 -2.6 6 -2.6 -6 -6 -2.6 6 -2.6z"/>
    </g>
  </g>
</svg>`;
}

export class WeeboAvatar {
  constructor(host, { size = null, track = true, label = "Weebo" } = {}) {
    this.id = ++uid;
    this.host = host;
    this.root = document.createElement("div");
    this.root.className = "weebo mood-idle";
    if (size) this.root.style.setProperty("--wb-size", `${size}px`);
    this.root.innerHTML = svgMarkup(this.id);
    this.root.setAttribute("aria-label", label);
    host.append(this.root);
    this.mood = "idle";
    this.track = track;
    this._blinkTimer = null;
    this._scheduleBlink();
    director.register(this);
  }

  setMood(mood) {
    if (!MOODS.includes(mood) || mood === this.mood) return;
    this.root.classList.remove(`mood-${this.mood}`);
    this.mood = mood;
    this.root.classList.add(`mood-${mood}`);
    if (mood === "celebrate") this.confetti();
  }

  look(dx, dy) {
    this.root.style.setProperty("--lx", `${dx.toFixed(2)}px`);
    this.root.style.setProperty("--ly", `${dy.toFixed(2)}px`);
  }

  _scheduleBlink() {
    const next = 2200 + Math.random() * 4200;
    this._blinkTimer = setTimeout(() => {
      if (!this.root.isConnected) return;
      this.root.classList.add("blink");
      setTimeout(() => this.root.classList.remove("blink"), 150);
      if (Math.random() < 0.18) {  // the occasional double blink
        setTimeout(() => { this.root.classList.add("blink"); setTimeout(() => this.root.classList.remove("blink"), 120); }, 260);
      }
      this._scheduleBlink();
    }, next);
  }

  confetti() {
    const colors = ["#6ff3ff", "#ffc14d", "#ff8fb7", "#9b8cff", "#7dffa8"];
    for (let i = 0; i < 26; i++) {
      const piece = document.createElement("i");
      piece.className = "wb-confetti";
      piece.style.setProperty("--x", `${(Math.random() - 0.5) * 220}px`);
      piece.style.setProperty("--y", `${-60 - Math.random() * 140}px`);
      piece.style.setProperty("--r", `${Math.random() * 720 - 360}deg`);
      piece.style.background = colors[i % colors.length];
      piece.style.animationDelay = `${Math.random() * 120}ms`;
      this.root.append(piece);
      setTimeout(() => piece.remove(), 1800);
    }
  }

  destroy() {
    clearTimeout(this._blinkTimer);
    director.unregister(this);
    this.root.remove();
  }
}

// ---------------------------------------------------------------- the director
export const director = {
  avatars: new Set(),
  base: "idle",
  transient: null,
  _timer: null,
  register(a) { this.avatars.add(a); a.setMood(this.current()); },
  unregister(a) { this.avatars.delete(a); },
  current() { return this.transient || this.base; },
  setBase(mood) {
    if (this.base === mood) return;
    this.base = mood;
    this.apply();
  },
  flash(mood, ms = 2400) {
    this.transient = mood;
    clearTimeout(this._timer);
    this._timer = setTimeout(() => { this.transient = null; this.apply(); }, ms);
    this.apply();
  },
  apply() { const m = this.current(); for (const a of this.avatars) a.setMood(m); },
};

// Eyes follow the pointer (subtly) — the single most "alive" detail.
let raf = 0;
window.addEventListener("pointermove", (event) => {
  if (raf) return;
  raf = requestAnimationFrame(() => {
    raf = 0;
    for (const a of director.avatars) {
      if (!a.track || !a.root.isConnected) continue;
      const rect = a.root.getBoundingClientRect();
      if (!rect.width) continue;
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height * 0.55;
      const dx = event.clientX - cx;
      const dy = event.clientY - cy;
      const dist = Math.hypot(dx, dy) || 1;
      const reach = Math.min(1, dist / 400);
      a.look((dx / dist) * 5 * reach, (dy / dist) * 3.5 * reach);
    }
  });
}, { passive: true });
