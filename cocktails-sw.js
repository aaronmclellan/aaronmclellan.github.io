// Offline support for Next Round (cocktails.html).
// Everything is served from the cache straight away and refreshed in the background, so the app
// opens instantly with bad bar wifi or none at all, and picks up site updates on the next visit.
// Registered with scope "/cocktails" so the rest of the site is left alone.
const CACHE = 'next-round-v1';
const LOCAL_FILES = [
    'cocktails.html',
    'cocktails-data.js',
    'cocktails.webmanifest',
    'cocktail-favicon.svg',
    'cocktail-icon-180.png',
    'cocktail-icon-192.png',
    'cocktail-icon-512.png',
];
const CDN_CSS = [
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.1/css/all.min.css',
    'https://fonts.googleapis.com/css2?family=Quicksand:wght@400;500;600;700;800&display=swap',
];

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE);
        await cache.addAll(LOCAL_FILES);
        // Icon and text fonts are best effort: the app still works without them.
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
        await Promise.all(names.filter(n => n.startsWith('next-round-') && n !== CACHE).map(n => caches.delete(n)));
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);
    const sameOrigin = url.origin === self.location.origin;
    if (sameOrigin && !url.pathname.includes('/cocktail')) return;   // not ours
    event.respondWith(staleWhileRevalidate(event, req, sameOrigin));
});

async function staleWhileRevalidate(event, req, sameOrigin) {
    const cache = await caches.open(CACHE);
    // ?v= style cache-busters on our own files shouldn't miss the cache
    const cached = await cache.match(req, { ignoreSearch: sameOrigin });
    const fresh = fetch(req)
        .then((res) => {
            if (res.ok || res.type === 'opaque') cache.put(req, res.clone());
            return res;
        })
        .catch(() => cached || Response.error());
    if (cached) {
        event.waitUntil(fresh);
        return cached;
    }
    return fresh;
}
