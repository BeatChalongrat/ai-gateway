"use client";

// FIX34 Part A — the quick-ask window's layout. Deliberately OUTSIDE /chat:
// no sidebar, no banners, no route-change focus management — a frameless
// always-on-top box the desktop shell shows on a hotkey (Claude's "quick
// entry"). The auth bootstrap is the same as chat/layout.tsx: resolve the
// cookie session via /portal/me, seed the model catalog, then render.

import { useCallback, useEffect, useState } from "react";

import { SessionUnreachable } from "@/components/ui/SessionUnreachable";
import { Spinner } from "@/components/ui/Spinner";
import { redirectToPortalLogin } from "@/lib/auth";
import { useAuthStore } from "@/stores/authStore";
import { useModelStore } from "@/stores/modelStore";

export default function QuickLayout({ children }: { children: React.ReactNode }) {
  const [isReady, setIsReady] = useState(false);
  // FIX130 C3.1 — same rule as chat/layout.tsx: only a refused session goes
  // to the login page; an unanswered check gets the retry screen.
  const [unreachable, setUnreachable] = useState(false);
  const [checking, setChecking] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      setChecking(true);
      await useAuthStore.getState().checkAuth();
      if (!alive) return;
      setChecking(false);
      const { user, status } = useAuthStore.getState();
      if (!user) {
        if (status === "rejected") redirectToPortalLogin();
        else setUnreachable(true);
        return;
      }
      setUnreachable(false);
      if (!useModelStore.getState().loaded) {
        void useModelStore.getState().loadModels();
      }
      setIsReady(true);
    })();
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (!isReady && unreachable) return <SessionUnreachable onRetry={retry} checking={checking} />;

  return (
    <div className="flex h-dvh w-full flex-col overflow-hidden bg-bg-base text-text-primary">
      {isReady ? (
        children
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <Spinner size={20} />
        </div>
      )}
    </div>
  );
}
