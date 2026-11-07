// Silent session refresh for the portal.
//
// Goal: a logged-in user never gets bounced to /login while they're
// active. The access-token cookie lives 60 min; we proactively rotate it
// well before that, and also catch the "tab was backgrounded for an
// hour, now it's focused again" case.
//
// Mechanics:
//   * A timer fires every REFRESH_INTERVAL_MS (50 min < 60 min TTL) and
//     POSTs /api/portal/auth/refresh. The backend rotates both cookies.
//   * On ``visibilitychange`` → visible, if it's been longer than the
//     interval since the last refresh, we refresh immediately.
//   * Cross-tab coordination via BroadcastChannel: whichever tab
//     refreshes broadcasts "refreshed" so the others reset their timers
//     instead of stampeding the endpoint (the backend has a 30s grace
//     window too, so a race is safe — this just avoids the noise).
//
// A refresh that returns 401 means the refresh token itself is dead
// (expired after 90 idle days, revoked, or token_version bumped). That's
// the one legitimate "please log in again" — we send them to /login.

const PREFIX = "/api";
const REFRESH_INTERVAL_MS = 50 * 60 * 1000; // 50 minutes
const CHANNEL_NAME = "portal-auth";

let started = false;
let timer: ReturnType<typeof setInterval> | null = null;
let lastRefresh = 0;
let channel: BroadcastChannel | null = null;

function now(): number {
  return Date.now();
}

async function refreshOnce(): Promise<boolean> {
  try {
    const r = await fetch(`${PREFIX}/portal/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
    if (r.ok) {
      lastRefresh = now();
      return true;
    }
    if (r.status === 401) {
      // Refresh token is truly dead — end the session.
      stopSilentRefresh();
      if (typeof window !== "undefined") window.location.href = "/login";
    }
    return false;
  } catch {
    // Network blip — leave the session alone; the next tick (or the next
    // API call's own 401→refresh path) will recover it.
    return false;
  }
}

async function tick(): Promise<void> {
  const ok = await refreshOnce();
  if (ok && channel) {
    try {
      channel.postMessage({ type: "refreshed", at: lastRefresh });
    } catch {
      /* channel closed — ignore */
    }
  }
}

function onVisible(): void {
  if (document.visibilityState !== "visible") return;
  // If we've been backgrounded past the interval, refresh now so the
  // user doesn't make their first post-focus click against a dead token.
  if (now() - lastRefresh >= REFRESH_INTERVAL_MS) {
    void tick();
  }
}

/** Begin proactive refresh. Idempotent — safe to call on every mount. */
export function startSilentRefresh(): void {
  if (started || typeof window === "undefined") return;
  started = true;
  lastRefresh = now(); // login/page-load implies a fresh-enough token

  timer = setInterval(() => void tick(), REFRESH_INTERVAL_MS);
  document.addEventListener("visibilitychange", onVisible);

  if ("BroadcastChannel" in window) {
    channel = new BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = (ev: MessageEvent) => {
      // Another tab refreshed — adopt its timestamp so we don't also fire.
      if (ev.data?.type === "refreshed" && typeof ev.data.at === "number") {
        lastRefresh = ev.data.at;
      }
    };
  }
}

/** Tear down timers + listeners (logout / hard session end). */
export function stopSilentRefresh(): void {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  if (typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", onVisible);
  }
  if (channel) {
    try {
      channel.close();
    } catch {
      /* ignore */
    }
    channel = null;
  }
  started = false;
}
