// Service worker registration. Called once from AppProviders after
// hydration so the SW only registers in the browser (never during
// SSR) and never blocks the first interactive paint.

export function registerServiceWorker(): void {
  if (typeof window === "undefined") return;
  if (!("serviceWorker" in navigator)) return;

  // Skip in dev — Next dev server serves through the same origin
  // but the SW would cache hot-reload artifacts, which kills the
  // dev loop. The production build re-enables it.
  if (process.env.NODE_ENV === "development") return;

  // Defer until the page has settled so the SW install doesn't
  // contend with the initial render's network requests.
  if (document.readyState === "complete") {
    void doRegister();
  } else {
    window.addEventListener("load", () => void doRegister(), { once: true });
  }
}

async function doRegister(): Promise<void> {
  try {
    await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch {
    // PWA features unavailable — the chat still works fine without
    // the SW. Don't surface to the user.
  }
}
