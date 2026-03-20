self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch (_) {
      // no-op
    }
    await self.registration.unregister();
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', () => {
  // intentionally disabled
});
