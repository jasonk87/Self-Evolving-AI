// Bump this whenever the cached UI shell changes so installed clients receive
// the current CSS/JS instead of silently retaining an older layout.
const CACHE_NAME = 'weebo-shell-v5';
const STATIC_ASSETS = [
    '/static/css/style.css',
    '/static/css/ghost_mode.css',
    '/static/css/watcher.css',
    '/static/manifest.webmanifest'
];

self.addEventListener('install', event => {
    event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)));
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
        ))
    );
    self.clients.claim();
});

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET' || new URL(event.request.url).origin !== self.location.origin) return;
    const url = new URL(event.request.url);
    if (url.pathname === '/') {
        event.respondWith(fetch(event.request).catch(() => caches.match('/')));
        return;
    }
    if (!url.pathname.startsWith('/static/')) return;
    event.respondWith(
        caches.match(event.request).then(cached => cached || fetch(event.request).then(response => {
            const copy = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
            return response;
        }))
    );
});
