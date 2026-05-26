"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ImageLightbox } from "@/components/ui/ImageLightbox";
import { ToastViewport } from "@/components/ui/Toast";
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

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    // Read the Portal token (cookie or ?token=) and seed the auth
    // store. No network call — chat-edge verifies the token on the
    // first protected API hit.
    checkAuth();
    // Best-effort service-worker install for PWA support. Skipped in
    // dev to avoid breaking HMR.
    registerServiceWorker();
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
