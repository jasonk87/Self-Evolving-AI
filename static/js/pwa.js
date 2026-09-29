(() => {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/service-worker.js').catch(error => {
        console.warn('[PWA] Service worker registration failed:', error);
    });
})();
