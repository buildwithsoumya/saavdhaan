// Caches the page shell so the app opens instantly; live listening still needs the server.
const SHELL = "saavdhaan-v1";
const FILES = ["/", "/index.html", "/app.js", "/styles.css", "/manifest.json", "/icon-192.png",
  "/fonts/noto-devanagari.woff2", "/fonts/work-sans.woff2"];

self.addEventListener("install", (e) => e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES))));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/reports/")) return;
  // network first, so a redeploy shows up at once; cache when offline
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok && r.status === 200) { // never cache errors or partial responses
      const copy = r.clone();
      caches.open(SHELL).then((c) => c.put(e.request, copy)).catch(() => {});
    }
    return r;
  }).catch(() => caches.match(e.request)));
});
