// Network-first service worker. Weebo upgrades its own UI, so cached files never win over fresh ones.
// The cache holds only public styling/icons and the offline screen, never chats or API responses.
const CACHE = "weebo-2-shell-v2";
const SHELL = ["/static/css/weebo.css", "/static/icon.svg", "/static/icons/icon-192.png"];

const OFFLINE_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#101117">
<title>Weebo is offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;
background:#101117;color:#ecebf4;font:15px/1.5 system-ui,sans-serif;text-align:center}main{max-width:320px;padding:24px}
img{width:96px;opacity:.7;filter:grayscale(.4)}h1{font-size:21px;margin:14px 0 6px}p{color:#a3a5b6;margin:0 0 18px}
button{padding:11px 20px;border:0;border-radius:12px;background:#5ad7ff;color:#04161d;font:600 15px system-ui}</style>
</head><body><main><img src="/static/icon.svg" alt=""><h1>Can't reach Weebo</h1>
<p>Weebo lives on your PC. Check that the PC is on, Weebo is running, and Tailscale is connected on this phone.</p>
<button onclick="location.reload()">Try again</button></main></body></html>`;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    // PC off or Weebo stopped: the network fails, or the Tailscale helper answers 502/503/504.
    event.respondWith(
      fetch(request)
        .then((response) => (response.status >= 502 && response.status <= 504 ? offline() : response))
        .catch(() => offline()),
    );
    return;
  }
  if (!url.pathname.startsWith("/static/")) return;
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(() => caches.match(request)),
  );
});

function offline() {
  return new Response(OFFLINE_PAGE, { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } });
}
