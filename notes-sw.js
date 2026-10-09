// Offline support for Notes (notes.html).
// The page and its icons are served from the cache straight away and refreshed in the background,
// so the app opens instantly with no signal and picks up site updates on the next visit.
// Notes themselves live in IndexedDB and sync with GitHub directly; this worker never touches them.
// Registered with scope "/notes" so the rest of the site is left alone.
const CACHE = 'notes-v1';
const LOCAL_FILES = [
    'notes.html',
    'notes.webmanifest',
    'notes-icon-180.png',
    'notes-icon-192.png',
    'notes-icon-512.png',
];

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE);
        await cache.addAll(LOCAL_FILES);
        await self.skipWaiting();
    })());
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(names.filter(n => n.startsWith('notes-') && n !== CACHE).map(n => caches.delete(n)));
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);
    if (url.origin !== self.location.origin || !url.pathname.includes('/notes')) return;   // not ours
    event.respondWith(staleWhileRevalidate(event, req));
});

async function staleWhileRevalidate(event, req) {
    const cache = await caches.open(CACHE);
    // ?title=... (shared text) and similar query strings shouldn't miss the cache
    const cached = await cache.match(req, { ignoreSearch: true });
    const fresh = fetch(req)
        .then((res) => {
            if (res.ok) cache.put(new Request(new URL(req.url).pathname), res.clone());
            return res;
        })
        .catch(() => cached || Response.error());
    if (cached) {
        event.waitUntil(fresh);
        return cached;
    }
    return fresh;
}
