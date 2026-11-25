const CACHE_PREFIX = 'expiry-tracker-public-';
const CACHE_NAME = CACHE_PREFIX + '{{ cache_version|escapejs }}';
const OFFLINE_URL = '/offline/';
const PUBLIC_ASSETS = [
{% for asset in public_assets %}    new URL('{{ asset|escapejs }}', self.location.origin).href,
{% endfor %}];

self.addEventListener('install', event => {
    event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(PUBLIC_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
    event.waitUntil(caches.keys().then(keys => Promise.all(
        keys.filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME).map(key => caches.delete(key))
    )).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== 'GET' || url.origin !== self.location.origin) return;
    // Every account page goes to the server. Never cache a successful navigation.
    if (request.mode === 'navigate') {
        event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL).then(response =>
            response || new Response('You are offline. Reconnect to use Expiry Tracker.', {status: 503, headers: {'Content-Type': 'text/plain'}})
        )));
        return;
    }
    // Strict allowlist: uploads, APIs, private downloads and account responses bypass caches.
    if (PUBLIC_ASSETS.includes(url.href)) {
        event.respondWith(caches.match(request).then(response => response || fetch(request)));
    }
});
