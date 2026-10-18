/* Pebble Health service worker.
   App shell: cache-first from a versioned cache.
   /api/summary: network-first, falling back to the last good response.
   Writes (POST/PUT/…) and every other request go straight to the network. */
'use strict';
const VERSION = 'v2';
const SHELL_CACHE = 'pebble-health-shell-' + VERSION;
const DATA_CACHE = 'pebble-health-data-v1';
const SHELL = [
  './', 'index.html', 'styles.css', 'app.js', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/maskable-192.png',
  'icons/maskable-512.png', 'icons/apple-touch-icon.png', 'icons/favicon-32.png'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(SHELL.map(u => new Request(u, { cache: 'reload' })))).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys
        .filter(k => k.startsWith('pebble-health-') && k !== SHELL_CACHE && k !== DATA_CACHE)
        .map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function summary(request) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) {
      const headers = new Headers(response.headers);
      headers.set('X-Cached-At', String(Date.now()));
      const body = await response.clone().arrayBuffer();
      await cache.put(request, new Response(body, { status: 200, headers }));
    }
    return response;
  } catch (err) {
    const hit = await cache.match(request) || await cache.match(request, { ignoreSearch: true });
    if (!hit) throw err;
    const headers = new Headers(hit.headers);
    headers.set('X-Offline-Fallback', '1');
    return new Response(await hit.arrayBuffer(), { status: 200, headers });
  }
}

async function shell(request) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = await cache.match(request, { ignoreSearch: request.mode === 'navigate' });
  if (hit) return hit;
  try {
    return await fetch(request);
  } catch (err) {
    if (request.mode === 'navigate') {
      const page = await cache.match('index.html');
      if (page) return page;
    }
    throw err;
  }
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  const scope = new URL(self.registration.scope);
  const rel = url.pathname.slice(scope.pathname.length);
  if (rel.startsWith('api/summary')) { event.respondWith(summary(request)); return; }
  if (rel.startsWith('api/') || rel.startsWith('download/') || rel.startsWith('pair')) return;
  if (request.mode === 'navigate' || SHELL.includes(rel) || rel === '') event.respondWith(shell(request));
});
