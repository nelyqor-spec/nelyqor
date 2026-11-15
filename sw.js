// Nelyqor — service worker : fonctionnement hors-ligne (cache des fichiers de l'app).
const CACHE = 'nelyqor-v1.0.0';
const FILES = [
  './', './index.html', './manifest.webmanifest', './css/app.css',
  './js/app.js', './js/core/money.js', './js/core/dates.js', './js/core/calc.js', './js/core/store.js',
  './js/content/quotes.js', './js/content/backgrounds.js',
  './js/ui/dom.js', './js/ui/components.js', './js/ui/forms.js', './js/ui/premium.js', './js/ui/onboarding.js',
  './js/ui/pages/objectif.js', './js/ui/pages/operations.js', './js/ui/pages/profil.js',
  './assets/logo-symbol.png', './assets/icons/icon-192.png', './assets/icons/icon-512.png', './assets/icons/icon-180.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

// Réseau d'abord (les mises à jour s'affichent tout de suite), cache si hors-ligne.
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET' || new URL(e.request.url).origin !== self.location.origin) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request)),
  );
});
