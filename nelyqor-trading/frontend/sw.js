// Service worker : met en cache UNIQUEMENT la coquille statique (HTML, CSS, JS, icônes). Les données /api/ et le sel
// de signature /meta/ ne sont JAMAIS mis en cache ni servis depuis le cache (pas de chiffres périmés affichés comme
// actuels, aucune donnée sensible stockée sur l'iPhone). Requêtes non GET ou d'une autre origine : non interceptées.
const CACHE = "nelyqor-shell-v6";
const SHELL = ["./", "index.html", "app.css", "app.js", "vue.js", "sig.js", "demo.js", "manifest.webmanifest",
  "icons/icon-180.png", "icons/icon-192.png"];
const NEVER = ["/api/", "/meta/"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  .then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin || e.request.method !== "GET" || NEVER.some((p) => url.pathname.startsWith(p))) return;
  // réseau d'abord (version à jour), cache seulement hors connexion ; la réponse réseau n'est pas réécrite en cache
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request, { ignoreSearch: true })));
});
