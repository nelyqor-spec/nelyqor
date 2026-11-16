// Nelyqor — service worker : fonctionnement hors-ligne (cache des fichiers de l'app uniquement).
// Sécurité : on ne met JAMAIS en cache une réponse d'un autre domaine (Supabase…), ni une adresse
// contenant des paramètres (ex. ?code= renvoyé après une connexion), ni une réponse en erreur.
const CACHE = 'nelyqor-v1.2.0';
const FILES = [
  './', './index.html', './manifest.webmanifest', './css/app.css',
  './js/app.js', './js/config.js',
  './js/core/money.js', './js/core/dates.js', './js/core/calc.js', './js/core/store.js', './js/core/sync.js', './js/core/cloud.js',
  './js/content/quotes.js', './js/content/backgrounds.js',
  './js/ui/dom.js', './js/ui/components.js', './js/ui/forms.js', './js/ui/premium.js', './js/ui/onboarding.js',
  './js/ui/theme.js', './js/ui/image.js', './js/ui/account.js', './js/ui/expenses.js',
  './js/ui/calendar.js', './js/ui/contrib.js', './js/ui/backup.js',
  './js/ui/pages/objectif.js', './js/ui/pages/operations.js', './js/ui/pages/profil.js',
  './js/vendor/supabase-2.117.3.umd.js',
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
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) return;
  // Retour de connexion (?code=…) : on sert la page sans jamais stocker l'adresse.
  const cacheKey = url.search ? new Request(url.origin + url.pathname) : e.request;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok && res.type === 'basic' && !url.search) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(cacheKey)),
  );
});
