// Nomos One — Service Worker (offline shell)
const CACHE = "nomos-one-v1";
const APP_SHELL = [
  "/",
  "/manifest.json",
  "/nomos-logo.png",
  "/letterhead-logo.png",
  "/favicon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(APP_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Never cache API calls
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/portal/") || url.pathname.startsWith("/stripe/")) {
    return;
  }
  // Cache-first for static assets, network fallback
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((res) => {
        if (res.ok && event.request.method === "GET" && url.origin === self.location.origin) {
          const clone = res.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, clone));
        }
        return res;
      }).catch(() => cached);
    })
  );
});
