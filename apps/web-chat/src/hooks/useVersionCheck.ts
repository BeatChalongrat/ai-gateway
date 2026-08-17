"use client";

// Update-available detection for long-open tabs. The SPA never reloads
// on its own, and sw.js is network-first with a constant filename, so a
// tab opened before a deploy keeps running the old bundle indefinitely
// with no signal. This hook compares the build id BAKED into this
// bundle (NEXT_PUBLIC_BUILD_ID) against GET /api/version (answered by
// whatever build is currently deployed): mismatch → updateAvailable.
//
// Checks every POLL_MS and whenever the tab regains visibility/focus.
// Latches true once (one notification per build, never re-fires) and
// NEVER auto-reloads — the banner asks; the user clicks refresh.

import { useEffect, useState } from "react";

const POLL_MS = 5 * 60 * 1000;

export function useVersionCheck(): boolean {
  const [updateAvailable, setUpdateAvailable] = useState(false);

  useEffect(() => {
    const baked = process.env.NEXT_PUBLIC_BUILD_ID;
    // No baked id (tests / unusual dev states) — nothing to compare.
    if (!baked) return;
    let latched = false;
    let disposed = false;

    async function check() {
      if (latched || disposed) return;
      try {
        const res = await fetch("/api/version", { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as { build?: string };
        if (data.build && data.build !== baked) {
          latched = true;
          setUpdateAvailable(true);
        }
      } catch {
        // Offline / transient — the next tick will try again.
      }
    }

    const interval = window.setInterval(() => void check(), POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      disposed = true;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, []);

  return updateAvailable;
}
