"use client";

// FIX34 Part A — the quick-ask window's layout. Deliberately OUTSIDE /chat:
// no sidebar, no banners, no route-change focus management — a frameless
// always-on-top box the desktop shell shows on a hotkey (Claude's "quick
// entry"). The auth bootstrap is the same as chat/layout.tsx: resolve the
// cookie session via /portal/me, seed the model catalog, then render.

import { useEffect, useState } from "react";

import { Spinner } from "@/components/ui/Spinner";
import { redirectToPortalLogin } from "@/lib/auth";
import { useAuthStore } from "@/stores/authStore";
import { useModelStore } from "@/stores/modelStore";

export default function QuickLayout({ children }: { children: React.ReactNode }) {
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      await useAuthStore.getState().checkAuth();
      if (!alive) return;
      if (!useAuthStore.getState().user) {
        redirectToPortalLogin();
        return;
      }
      if (!useModelStore.getState().loaded) {
        void useModelStore.getState().loadModels();
      }
      setIsReady(true);
    })();
    return () => {
      alive = false;
    };
  }, []);

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
