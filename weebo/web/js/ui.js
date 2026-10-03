// Small DOM + formatting helpers shared by every view.
import { icon } from "./icons.js";

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function btn(label, opts = {}) {
  const { icon: ic, kind = "ghost", onClick, title, size } = opts;
  const node = el("button", { class: `btn btn-${kind}${size ? " btn-" + size : ""}`, type: "button", title: title || null, onclick: onClick });
  node.innerHTML = (ic ? icon(ic, size === "sm" ? 15 : 17) : "") + (label ? `<span>${escapeHtml(label)}</span>` : "");
  if (!label) node.classList.add("btn-icon");
  if (title) node.setAttribute("aria-label", title);
  return node;
}

export function timeAgo(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function clockTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function dateTime(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const tomorrow = new Date(Date.now() + 86400000);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `Today ${time}`;
  if (d.toDateString() === tomorrow.toDateString()) return `Tomorrow ${time}`;
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) + ` ${time}`;
}

export function duration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

// ---------------------------------------------------------------- toasts
let toastHost;
export function toast(title, body = "", { kind = "info", timeout = 5000, action } = {}) {
  toastHost ||= document.getElementById("toasts");
  const node = el("div", { class: `toast toast-${kind}`, role: "status" },
    el("div", { class: "toast-icon", html: icon({ error: "alert", success: "check", warn: "bell" }[kind] || "sparkles", 18) }),
    el("div", { class: "toast-body" }, el("strong", { text: title }), body ? el("p", { text: body }) : null),
  );
  if (action) node.append(btn(action.label, { kind: "soft", size: "sm", onClick: () => { action.run(); close(); } }));
  node.append(btn("", { icon: "x", title: "Dismiss", size: "sm", onClick: () => close() }));
  toastHost.append(node);
  requestAnimationFrame(() => node.classList.add("in"));
  const timer = timeout ? setTimeout(close, timeout) : null;
  function close() {
    clearTimeout(timer);
    node.classList.remove("in");
    setTimeout(() => node.remove(), 250);
  }
  return close;
}

// ---------------------------------------------------------------- modal
export function modal(title, content, { wide = false, actions = [] } = {}) {
  const backdrop = el("div", { class: "modal-backdrop" });
  const box = el("div", { class: `modal${wide ? " modal-wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title });
  const close = () => { backdrop.classList.remove("in"); setTimeout(() => backdrop.remove(), 200); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const footer = actions.length ? el("div", { class: "modal-actions" }, actions.map((a) => btn(a.label, { kind: a.kind || "soft", icon: a.icon, onClick: () => a.run(close) }))) : null;
  box.append(
    el("div", { class: "modal-head" }, el("h3", { text: title }), btn("", { icon: "x", title: "Close", onClick: close })),
    el("div", { class: "modal-body" }, content),
  );
  if (footer) box.append(footer);
  backdrop.append(box);
  backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(); });
  document.addEventListener("keydown", onKey);
  document.body.append(backdrop);
  requestAnimationFrame(() => backdrop.classList.add("in"));
  return close;
}

export function confirmDialog(title, text, okLabel = "Confirm", kind = "danger") {
  return new Promise((resolve) => {
    modal(title, el("p", { class: "muted", text }), {
      actions: [
        { label: "Cancel", run: (close) => { resolve(false); close(); } },
        { label: okLabel, kind, run: (close) => { resolve(true); close(); } },
      ],
    });
  });
}

export function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => toast("Copied", "", { kind: "success", timeout: 1500 }),
    () => toast("Couldn't copy", "Your browser blocked clipboard access.", { kind: "error" }));
}

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function autoGrow(textarea, max = 240) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(max, textarea.scrollHeight) + "px";
}

export function diffStats(diff) {
  let added = 0, removed = 0;
  for (const line of (diff || "").split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) added++;
    else if (line.startsWith("-") && !line.startsWith("---")) removed++;
  }
  return { added, removed };
}

export function renderDiff(diff) {
  const pre = el("pre", { class: "diff" });
  const lines = (diff || "").split("\n");
  const frag = document.createDocumentFragment();
  for (const line of lines.slice(0, 6000)) {
    let cls = "";
    if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ")) cls = "d-meta";
    else if (line.startsWith("@@")) cls = "d-hunk";
    else if (line.startsWith("+")) cls = "d-add";
    else if (line.startsWith("-")) cls = "d-del";
    frag.append(el("span", { class: cls, text: line + "\n" }));
  }
  if (lines.length > 6000) frag.append(el("span", { class: "d-meta", text: `… ${lines.length - 6000} more lines` }));
  pre.append(frag);
  return pre;
}
