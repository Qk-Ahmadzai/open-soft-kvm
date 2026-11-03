/* SoftKVM service worker.
 *
 * Scope is deliberately narrow: the live MJPEG stream and the telemetry API
 * are always fetched from the network, while the app shell (HTML, CSS, JS,
 * wallpapers) is cached so the dashboard opens instantly and still renders
 * when the host is briefly unreachable.
 */

const VERSION = "softkvm-v1";
const SHELL_CACHE = "softkvm-shell-v1";

const SHELL_ASSETS = [
  "/static/css/softkvm.css",
  "/static/js/core.js",
  "/static/js/dashboard.js",
  "/static/js/kvm.js",
  "/static/js/controls.js",
  "/static/js/schedule.js",
  "/static/js/settings.js",
  "/static/manifest.webmanifest",
  "/static/icons/favicon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .catch(() => undefined)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

function isLive(url) {
  return (
    url.pathname === "/stream" ||
    url.pathname === "/snapshot" ||
    url.pathname.startsWith("/api/") ||
    url.pathname === "/healthz"
  );
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isLive(url)) return; // always live, never cached

  // Wallpapers are large and stable - cache them aggressively.
  if (url.pathname.startsWith("/static/img/wallpapers/")) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            const copy = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
            return response;
          })
      )
    );
    return;
  }

  // Navigations: network first, cached shell as fallback.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() =>
          caches.match(request).then(
            (cached) =>
              cached ||
              new Response(
                "<h1>Offline</h1><p>SoftKVM is unreachable. Reconnect to the host and retry.</p>",
                { headers: { "Content-Type": "text/html" }, status: 503 }
              )
          )
        )
    );
    return;
  }

  // Static assets: cache first, revalidate in the background.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then((cached) => {
        const network = fetch(request)
          .then((response) => {
            if (response && response.status === 200) {
              const copy = response.clone();
              caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          })
          .catch(() => cached);
        return cached || network;
      })
    );
  }
});
