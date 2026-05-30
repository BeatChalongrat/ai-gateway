// Silent session refresh for Web Chat.
//
// Mirror of the portal's lib/auth-refresh, pointed at the gateway. Keeps
// a logged-in chat user's access cookie fresh so they're never bounced
// to the Portal login mid-conversation, and catches the "tab idle for an
// hour, now focused" case.
//
//   * Timer every REFRESH_INTERVAL_MS (50 min < 60 min access TTL).
//   * Refresh-on-focus when backgrounded past the interval.
//   * Cross-tab coordination via BroadcastChannel so multiple chat tabs
//     don't all refresh at once (the gateway's 30s grace makes a race
//     safe; this just avoids the noise).
//
// A refresh that fails (refresh token expired/revoked/version-bumped)
// means the session is genuinely over → bounce to the Portal login.

import { redirectToPortalLogin, refreshSession } from "./auth";

const REFRESH_INTERVAL_MS = 50 * 60 * 1000; // 50 minutes
const CHANNEL_NAME = "webchat-auth";

let started = false;
let timer: ReturnType<typeof setInterval> | null = null;
let lastRefresh = 0;
let channel: BroadcastChannel | null = null;

async function refreshOnce(): Promise<boolean> {
  const ok = await refreshSession();
  if (ok) {
    lastRefresh = Date.now();
    return true;
  }
  // A failed proactive refresh is the one legitimate "log in again".
  stopSilentRefresh();
  redirectToPortalLogin();
  return false;
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
  if (Date.now() - lastRefresh >= REFRESH_INTERVAL_MS) {
    void tick();
  }
}

/** Begin proactive refresh. Idempotent. */
export function startSilentRefresh(): void {
  if (started || typeof window === "undefined") return;
  started = true;
  lastRefresh = Date.now();

  timer = setInterval(() => void tick(), REFRESH_INTERVAL_MS);
  document.addEventListener("visibilitychange", onVisible);

  if ("BroadcastChannel" in window) {
    channel = new BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = (ev: MessageEvent) => {
      if (ev.data?.type === "refreshed" && typeof ev.data.at === "number") {
        lastRefresh = ev.data.at;
      }
    };
  }
}

/** Tear down timers + listeners. */
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
