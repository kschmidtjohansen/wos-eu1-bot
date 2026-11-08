/* WOS EU1 app: works offline with what was loaded last time, and always tries the network first
   so players see new codes and fixes straight away. Bump VERSION when the file list changes. */
const VERSION = 'wos-eu1-v1';
const SHELL = ['index.html', 'codes.html', 'known-issues.html', 'report.html', 'changelog.html', 'style.css', 'app.js', 'config.js',
  'manifest.json', 'fonts/unbounded.woff2', 'fonts/figtree.woff2', 'fonts/figtree-ext.woff2', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', e => {
  // one missing file must never stop the app from installing
  e.waitUntil(caches.open(VERSION).then(c => Promise.all(SHELL.map(u => c.add(u).catch(() => null)))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;              // reports (POST) and other sites go straight through
  if (/\.(woff2|png)$/.test(url.pathname)) {                                         // fonts and icons never change: cache first
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => { const copy = res.clone(); caches.open(VERSION).then(c => c.put(req, copy)); return res; })));
    return;
  }
  e.respondWith(fetch(req).then(res => {                                             // everything else: network first, cache as backup
    if (res.ok) { const copy = res.clone(); caches.open(VERSION).then(c => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req).then(hit => hit || (req.mode === 'navigate' ? caches.match('index.html') : Response.error()))));
});
