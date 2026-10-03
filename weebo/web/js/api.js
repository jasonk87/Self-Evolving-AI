// REST + WebSocket client. The per-launch token lives in a <meta> tag that only
// Weebo's own page carries; it is sent as a header (never in URLs) for the API.
const TOKEN = document.querySelector('meta[name="weebo-token"]')?.content || "";

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function request(method, path, body, { raw = false } = {}) {
  const headers = { "X-Weebo-Token": TOKEN };
  let payload = body;
  if (body !== undefined && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch (err) {
    throw new ApiError("Weebo isn't reachable. Is it still running?", 0);
  }
  if (raw) return res;
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError(data.error || `Request failed (${res.status})`, res.status);
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  patch: (p, b = {}) => request("PATCH", p, b),
  del: (p) => request("DELETE", p),
  upload: (file) => { const fd = new FormData(); fd.append("file", file, file.name || "image.png"); return request("POST", "/api/uploads", fd); },
};

// ---------------------------------------------------------------- live events
const listeners = new Map();
let socket = null;
let retry = 0;
let statusCb = () => {};

export function on(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type)?.delete(fn);
}

function emit(type, data) {
  for (const fn of listeners.get(type) || []) {
    try { fn(data, type); } catch (err) { console.error("listener failed", type, err); }
  }
  for (const fn of listeners.get("*") || []) {
    try { fn(data, type); } catch (err) { console.error("listener failed", type, err); }
  }
}

export function onConnection(fn) { statusCb = fn; }

export function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let opened = false;
  socket = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
  socket.addEventListener("open", () => { opened = true; retry = 0; statusCb("online"); });
  socket.addEventListener("message", (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    emit(msg.type, msg.data || {});
  });
  socket.addEventListener("close", async () => {
    statusCb("offline");
    if (!opened && retry >= 1 && await serverHasNewSession()) {
      location.reload();  // Weebo restarted (e.g. after a self-upgrade) and issued a new session token.
      return;
    }
    const delay = Math.min(8000, 400 * 2 ** retry++);
    setTimeout(connect, delay);
  });
}

async function serverHasNewSession() {
  try {
    const res = await fetch("/", { cache: "no-store" });
    if (!res.ok) return false;
    const html = await res.text();
    const match = html.match(/name="weebo-token" content="([^"]+)"/);
    return Boolean(match && match[1] !== TOKEN);
  } catch {
    return false;
  }
}

export function sendActivity() {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "activity" }));
}
