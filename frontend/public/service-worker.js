// Nomos One — Service Worker (offline shell)
//
// Πώς φτάνει μια νέα έκδοση στους χρήστες (ανασκόπηση 5, D-02):
//  · Το όνομα της κρυφής μνήμης παίρνει τον κωδικό του build. Το `frontend/Dockerfile` γράφει στη σταθερά BUILD (παρακάτω)
//    τον κατακερματισμό του index.html, που ονομάζει τα bundles με hash. Κάθε νέα έκδοση αλλάζει έτσι τα bytes αυτού του
//    αρχείου· ο browser τον εγκαθιστά ξανά και το `activate` σβήνει την παλιά κρυφή μνήμη. Με σταθερό όνομα ("nomos-one-v1")
//    τα bytes ήταν ίδια σε κάθε έκδοση, ο worker δεν ξανάτρεχε ποτέ και οι χρήστες έμεναν στην πρώτη έκδοση που άνοιξαν.
//  · Οι σελίδες (navigations) πηγαίνουν ΠΡΩΤΑ στο δίκτυο, με επαλήθευση από τον server· η αποθηκευμένη σελίδα-κέλυφος
//    χρησιμεύει μόνο όταν δεν υπάρχει σύνδεση. Ακόμη κι αν ξεχαστεί η σφραγίδα του build, η αρχική σελίδα δεν μένει παλιά.
//  · Cache-first μόνο για τα αρχεία του /static/, που έχουν hash στο όνομά τους και δεν αλλάζουν ποτέ με το ίδιο όνομα.
//  · Ποτέ από την κρυφή μνήμη: API, πύλη πελάτη, Stripe, αιτήματα εκτός GET και άλλα origins.
const BUILD = "__BUILD_ID__";
const CACHE = `nomos-one-${BUILD}`;
const SHELL = "/";
const APP_SHELL = [SHELL, "/manifest.json", "/nomos-logo.png", "/letterhead-logo.png", "/favicon.png"];
const NEVER_CACHED = ["/api/", "/portal/", "/stripe/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(APP_SHELL.map((path) => new Request(path, { cache: "reload" }))))
      .catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

// Σελίδα της εφαρμογής: το SPA επιστρέφει το ίδιο index.html για κάθε διαδρομή, άρα αρκεί ένα κέλυφος.
async function freshShell(event) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(new Request(SHELL, { cache: "no-cache" }));
    if (response.ok) event.waitUntil(cache.put(SHELL, response.clone()));
    return response;
  } catch (error) {
    const saved = await cache.match(SHELL);
    if (saved) return saved;
    throw error;
  }
}

async function networkFirst(event) {
  const { request } = event;
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) event.waitUntil(cache.put(request, response.clone()));
    return response;
  } catch (error) {
    const saved = await cache.match(request);
    if (saved) return saved;
    throw error;
  }
}

async function cacheFirst(event) {
  const { request } = event;
  const cache = await caches.open(CACHE);
  const saved = await cache.match(request);
  if (saved) return saved;
  const response = await fetch(request);
  if (response.ok) event.waitUntil(cache.put(request, response.clone()));
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (NEVER_CACHED.some((prefix) => url.pathname.startsWith(prefix))) return;

  const isPage = request.mode === "navigate" && !/\.[A-Za-z0-9]+$/.test(url.pathname);
  if (isPage) event.respondWith(freshShell(event));
  else if (url.pathname.startsWith("/static/")) event.respondWith(cacheFirst(event));
  else event.respondWith(networkFirst(event));
});
