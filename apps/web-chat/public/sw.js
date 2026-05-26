// SRAN Chat — minimal service worker.
//
// Goal: PWA installability + a graceful offline shell. The chat
// itself can't function without the network (LLM calls go to
// chat-edge → gateway → upstream models), so we DON'T try to cache
// API calls or pretend offline-first capability. The SW just keeps
// the shell loadable when the user opens the installed app with no
// connection.

const CACHE_NAME = "sran-chat-v1";

// Pre-cache only the entry route. Next.js asset URLs include
// content hashes so we can rely on the runtime fetch handler to
// populate them lazily.
const SHELL_URLS = ["/chat", "/manifest.json", "/icons/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) =>
        Promise.all(
          SHELL_URLS.map((u) => cache.add(u).catch(() => undefined)),
        ),
      ),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Drop old caches when the SW version bumps.
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;

  // Only handle same-origin GETs. Anything else (POSTs to chat-edge,
  // SSE streams, cross-origin Portal calls, etc.) goes straight to
  // the network.
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Never cache API-ish paths.
  if (url.pathname.startsWith("/api/")) return;

  // Network-first, falling back to whatever's in cache. On a hit we
  // refresh the cache copy so the next offline open serves the
  // newest revision. Final fallback is the /chat shell page so the
  // app still mounts when there's no network.
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.status === 200 && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(async () => {
        const cached = await caches.match(req);
        if (cached) return cached;
        // Last-ditch: serve the shell so users opening the installed
        // app offline at least see the chat UI even if it can't
        // hydrate fully.
        const shell = await caches.match("/chat");
        if (shell) return shell;
        return new Response("Offline", { status: 503, statusText: "Offline" });
      }),
  );
});
