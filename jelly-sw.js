// Offline support for Jelly Jam (jelly.html).
// The page itself comes from the network first (skipping the browser's HTTP cache), so a new
// version shows the first time the game is opened online; the saved copy is only for when the
// network is down or too slow. Icons, fonts and the like come from the cache straight away and
// are refreshed in the background.
// Registered with scope "/jelly" so the rest of the site is left alone.
const CACHE = 'jelly-jam-v2';
const LOCAL_FILES = [
    'jelly.html',
    'jelly.webmanifest',
    'jelly-favicon.svg',
    'jelly-icon-180.png',
    'jelly-icon-192.png',
    'jelly-icon-512.png',
];
const CDN_CSS = [
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.1/css/all.min.css',
    'https://fonts.googleapis.com/css2?family=Fredoka:wght@400;500;600;700&display=swap',
];

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE);
        await cache.addAll(LOCAL_FILES);
        // Icon and text fonts are best effort: the game still works without them.
        await Promise.allSettled(CDN_CSS.map(url => cacheCssAndFonts(cache, url)));
        await self.skipWaiting();
    })());
});

// Cache a stylesheet plus the .woff2 files it points at, so icons and text render offline.
async function cacheCssAndFonts(cache, url) {
    const res = await fetch(url, { mode: 'cors' });
    if (!res.ok) return;
    await cache.put(url, res.clone());
    const css = await res.text();
    const fonts = [...css.matchAll(/url\(["']?([^"')]+\.woff2)["']?\)/g)].map(m => new URL(m[1], url).href);
    await Promise.allSettled([...new Set(fonts)].map(async (font) => {
        const fontRes = await fetch(font, { mode: 'cors' });
        if (fontRes.ok) await cache.put(font, fontRes);
    }));
}

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const names = await caches.keys();
        await Promise.all(names.filter(n => n.startsWith('jelly-jam-') && n !== CACHE).map(n => caches.delete(n)));
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);
    const sameOrigin = url.origin === self.location.origin;
    if (/(^|\.)(googletagmanager|google-analytics)\.com$/.test(url.hostname)) return;   // analytics stays online-only
    if (sameOrigin && !url.pathname.includes('/jelly')) return;   // not ours
    if (sameOrigin && (req.mode === 'navigate' || url.pathname.endsWith('.html'))) {
        event.respondWith(networkFirst(req));
        return;
    }
    event.respondWith(staleWhileRevalidate(event, req, sameOrigin));
});

async function networkFirst(req) {
    const cache = await caches.open(CACHE);
    const key = new URL(req.url).pathname;
    try {
        const res = await Promise.race([
            fetch(req.url, {cache: 'no-cache', credentials: 'same-origin'}),
            new Promise((_, reject) => setTimeout(() => reject(new Error('slow')), 4000)),
        ]);
        if (res.ok) {
            cache.put(key, res.clone());
            return res;
        }
    } catch (e) { /* offline or slow: use the saved copy */ }
    return (await cache.match(key)) || fetch(req);
}

async function staleWhileRevalidate(event, req, sameOrigin) {
    const cache = await caches.open(CACHE);
    // Our own files are cached by path, so ?level= and the like share one copy that stays fresh.
    const key = sameOrigin ? new URL(req.url).pathname : req;
    const cached = await cache.match(key);
    const fresh = fetch(req)
        .then((res) => {
            if (res.ok || res.type === 'opaque') cache.put(key, res.clone());
            return res;
        })
        .catch(() => cached || Response.error());
    if (cached) {
        event.waitUntil(fresh);
        return cached;
    }
    return fresh;
}
