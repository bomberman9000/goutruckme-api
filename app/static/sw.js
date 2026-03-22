// GruzPotok Service Worker — Web Push

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

// Handle incoming push
self.addEventListener('push', (event) => {
  let data = { title: 'ГрузПоток', body: 'Новое уведомление', url: '/' };
  try { data = { ...data, ...event.data.json() }; } catch {}

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body:  data.body,
      icon:  '/static/icons/icon-192x192.png',
      badge: '/static/icons/icon-192x192.png',
      data:  { url: data.url },
      vibrate: [200, 100, 200],
    })
  );
});

// Click → open / focus the correct page
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes(self.location.origin) && 'focus' in client)
          return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
