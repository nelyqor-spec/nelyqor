// Service worker : met en cache uniquement la coquille statique. Les données /api/ ne sont JAMAIS mises en cache
// (évite d'afficher des chiffres périmés comme s'ils étaient actuels).
const CACHE = "nelyqor-shell-v2";
const SHELL = ["./", "index.html", "app.css", "app.js", "demo.js", "manifest.webmanifest", "icons/icon-192.png"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL))));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/") || e.request.method !== "GET") return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
