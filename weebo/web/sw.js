// Network-first service worker. Weebo upgrades its own UI, so cached files never win over fresh ones.
// The cache holds only public styling/icons and the offline screen, never chats or API responses.
const CACHE = "weebo-2-shell-v3";
const SHELL = ["/static/css/weebo.css", "/static/icon.svg", "/static/icons/icon-192.png"];

const OFFLINE_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#101117">
<title>Weebo is offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;
background:#101117;color:#ecebf4;font:15px/1.5 system-ui,sans-serif;text-align:center}main{max-width:320px;padding:24px}
img{width:96px;opacity:.7;filter:grayscale(.4)}h1{font-size:21px;margin:14px 0 6px}p{color:#a3a5b6;margin:0 0 18px}
button{padding:11px 20px;border:0;border-radius:12px;background:#5ad7ff;color:#04161d;font:600 15px system-ui}
#address{font-size:12px;overflow-wrap:anywhere;margin-top:20px}#recovery{font-size:13px;margin-top:16px}</style>
</head><body><main><img src="/static/icon.svg" alt=""><h1>Can't reach Weebo</h1>
<p>The connection may be returning after a restart. Keep the PC on and Tailscale connected on this phone.</p>
<form><button type="submit">Try again</button></form>
<p id="recovery" role="status" aria-live="polite">Reconnecting automatically…</p>
<p id="address"></p></main><script>
(() => {
  // This is embedded so recovery works with no network and no cached JS modules.
  // Probe the real page, not an API carrying the pre-restart session token.
  const status = document.getElementById('recovery');
  const button = document.querySelector('button');
  document.getElementById('address').textContent = location.host;
  let timer, busy = false, delay = 3000, leaving = false;
  function schedule() {
    clearTimeout(timer);
    if (!leaving && !document.hidden) timer = setTimeout(check, delay);
  }
  async function check() {
    clearTimeout(timer);
    if (busy || leaving || document.hidden) return;
    busy = true;
    button.disabled = true;
    status.textContent = 'Checking connection…';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(location.href, {
        cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
      });
      if ((response.ok || response.status === 401) &&
          response.headers.get('content-type')?.includes('text/html')) {
        const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
        const ready = response.ok && doc.querySelector('meta[name="weebo-token"]')?.content;
        const locked = response.status === 401 && doc.title === 'Weebo is private';
        if (ready || locked) {
          leaving = true;
          status.textContent = 'Connected. Opening Weebo…';
          location.reload();
          return;
        }
      }
    } catch { /* A restart, an offline phone or a sleeping PC: keep the retry screen. */ }
    finally {
      clearTimeout(timeout);
      busy = false;
      button.disabled = false;
    }
    status.textContent = "Still can't reach Weebo. Retrying automatically…";
    delay = Math.min(15000, delay * 1.5);
    schedule();
  }
  function wake() { delay = 3000; check(); }
  document.querySelector('form').addEventListener('submit', event => { event.preventDefault(); wake(); });
  window.addEventListener('online', wake);
  window.addEventListener('pageshow', wake);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(timer);
    else wake();
  });
  schedule();
})();
</script></body></html>`;

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
  return new Response(OFFLINE_PAGE, { status: 503, headers: {
    "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
  } });
}
