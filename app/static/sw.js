const CACHE = 'gruzpotok-v2';
const STATIC = ['/manifest.json', '/static/css/tailwind.generated.css?v=20260320-tailwind-static-1'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/') || url.pathname.startsWith('/internal/')) {
    return;
  }

  const isHtmlNavigation = e.request.mode === 'navigate' || (e.request.headers.get('accept') || '').includes('text/html');
  if (isHtmlNavigation) {
    e.respondWith((async () => {
      try {
        const resp = await fetch(e.request);
        if (resp && resp.ok) {
          const clone = resp.clone();
          const cache = await caches.open(CACHE);
          await cache.put(e.request, clone);
        }
        return resp;
      } catch (_) {
        const cached = await caches.match(e.request);
        if (cached) return cached;
        throw _;
      }
    })());
    return;
  }

  e.respondWith(
    caches.match(e.request).then(cached => {
      const fetchPromise = fetch(e.request).then(resp => {
        if (resp && resp.ok && url.pathname.startsWith('/static/')) {
          const clone = resp.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return resp;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
