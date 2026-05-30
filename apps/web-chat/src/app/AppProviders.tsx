"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ImageLightbox } from "@/components/ui/ImageLightbox";
import { ToastViewport } from "@/components/ui/Toast";
import { clearLegacyToken } from "@/lib/auth";
import { startSilentRefresh, stopSilentRefresh } from "@/lib/auth-refresh";
import { useHydrateI18n } from "@/lib/i18n";
import { registerServiceWorker } from "@/lib/registerSW";
import { useAuthStore } from "@/stores/authStore";
import { useChatStore } from "@/stores/chatStore";
import { useHydrateTheme } from "@/stores/themeStore";

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { refetchOnWindowFocus: false, retry: 1 },
        },
      }),
  );
  const booted = useRef(false);
  const checkAuth = useAuthStore((s) => s.checkAuth);
  const errorBanner = useChatStore((s) => s.errorBanner);
  const setError = useChatStore((s) => s.setError);
  // Hydrate the theme on first paint so the data-theme attribute
  // is set BEFORE any visible content mounts.
  useHydrateTheme();
  // Same pattern for the active locale — first paint stays in EN to
  // match SSR, then post-hydration swaps in the user's choice and
  // updates ``<html lang>`` for assistive tech + browser translators.
  useHydrateI18n();

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    // Scrub any legacy non-HttpOnly token a prior build wrote.
    clearLegacyToken();
    // Resolve the session from the gateway (cookie-authenticated) and
    // seed the auth store.
    void checkAuth();
    // Keep the access cookie fresh so an active user is never bounced
    // to login mid-session.
    startSilentRefresh();
    // Best-effort service-worker install for PWA support. Skipped in
    // dev to avoid breaking HMR.
    registerServiceWorker();
    return () => stopSilentRefresh();
  }, [checkAuth]);

  // Auto-dismiss toast after 4s.
  useEffect(() => {
    if (!errorBanner) return;
    const t = setTimeout(() => setError(null), 4000);
    return () => clearTimeout(t);
  }, [errorBanner, setError]);

  return (
    <QueryClientProvider client={client}>
      {children}
      {errorBanner ? (
        <div
          role="alert"
          className="fixed bottom-6 right-6 z-50 max-w-sm rounded-md border border-status-danger/40 bg-status-danger/15 px-4 py-3 text-sm text-text-primary shadow-lg fade-in-up"
        >
          {errorBanner}
        </div>
      ) : null}
      <ToastViewport />
      <ImageLightbox />
    </QueryClientProvider>
  );
}
