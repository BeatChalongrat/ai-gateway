"use client";

import { useEffect } from "react";

import { clearLegacyTokens } from "@/lib/auth";
import { startSilentRefresh, stopSilentRefresh } from "@/lib/auth-refresh";

// Mounted once inside the authenticated app shell. Kicks off proactive
// session refresh so an active user is never bounced to /login, and
// scrubs any legacy localStorage/JS-cookie tokens left by older builds.
export function SilentRefresh() {
  useEffect(() => {
    clearLegacyTokens();
    startSilentRefresh();
    return () => stopSilentRefresh();
  }, []);
  return null;
}
