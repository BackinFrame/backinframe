/* Back in Frame service worker: shows pushed memories and opens the app on tap.
   It caches nothing: photos are only ever loaded from your own server. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data && event.data.text() }; }
  const options = {
    body: data.body || '',
    icon: 'icon.png',
    badge: 'icon.png',
    tag: data.tag || undefined,
    data: { url: data.url || self.registration.scope },
  };
  if (data.image) options.image = data.image;
  event.waitUntil(self.registration.showNotification(data.title || 'Back in Frame', options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || self.registration.scope;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of windows) {
      if (client.url.startsWith(self.registration.scope) && 'focus' in client) {
        await client.navigate(url).catch(() => {});
        return client.focus();
      }
    }
    return self.clients.openWindow(url);
  })());
});
