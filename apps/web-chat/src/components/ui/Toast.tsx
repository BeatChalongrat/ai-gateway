"use client";

// Minimal ephemeral toast. Held in a tiny Zustand store so any
// component can push a message without prop-drilling. Auto-dismisses
// after the configured delay; only the most-recent toast renders to
// keep things simple (no stacking).

import { useEffect, useState } from "react";

import { create } from "zustand";

import { useSidebarStore } from "@/stores/sidebarStore";

// Sidebar's desktop width — kept in sync with Sidebar.tsx's
// ``md:w-[260px]`` rule. Half this value is the visual offset the
// chat content area shifts to the right of the viewport midline
// when the sidebar is expanded at desktop sizes.
const SIDEBAR_DESKTOP_WIDTH = 260;

interface ToastState {
  message: string | null;
  // Bumped on every push so an identical message still re-triggers
  // the auto-dismiss timer.
  nonce: number;
  show: (message: string) => void;
  clear: () => void;
}

export const useToastStore = create<ToastState>((set) => ({
  message: null,
  nonce: 0,
  show(message) {
    set((s) => ({ message, nonce: s.nonce + 1 }));
  },
  clear() {
    set({ message: null });
  },
}));

// Convenience wrapper so call sites read cleanly.
export const toast = (message: string) => useToastStore.getState().show(message);

export function ToastViewport({ duration = 2500 }: { duration?: number }) {
  const message = useToastStore((s) => s.message);
  const nonce = useToastStore((s) => s.nonce);
  const clear = useToastStore((s) => s.clear);
  // Sidebar collapsed state — read synchronously from the shared
  // store so the very first render of a toast already knows where
  // to position itself. No CSS variable, no layout effect, no
  // round-trip through ``<html>`` style → no flicker.
  const collapsed = useSidebarStore((s) => s.collapsed);
  // Desktop-or-not via matchMedia. Lazy initial state reads the
  // real value on the very first client render (and falls back to
  // ``true`` on the server so the SSR shell stays consistent with
  // the layout's flex assumption).
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window === "undefined"
      ? true
      : window.matchMedia("(min-width: 768px)").matches,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(min-width: 768px)");
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  useEffect(() => {
    if (!message) return;
    const t = window.setTimeout(clear, duration);
    return () => window.clearTimeout(t);
  }, [message, nonce, duration, clear]);

  if (!message) return null;
  // ``position: fixed`` is always viewport-anchored. Offset =
  // half the sidebar's horizontal footprint, so the toast lands on
  // the visual center of the chat content area:
  //   * desktop + expanded sidebar → 130px (half of 260px)
  //   * collapsed OR mobile (sidebar is overlay)     → 0px
  // Non-chat routes don't mount this layout so ``collapsed`` stays
  // at its store default (false) — but those routes also don't
  // render the sidebar, so the offset would be wrong. To stay
  // correct everywhere, we'd need a per-route hint; for now the
  // chat surface is the only mounted UI that uses toasts.
  const sidebarOffset = isDesktop && !collapsed ? SIDEBAR_DESKTOP_WIDTH / 2 : 0;
  return (
    <div
      className="toast-appear pointer-events-none fixed bottom-6 z-50 -translate-x-1/2 rounded-full border border-border bg-bg-surface/95 px-4 py-2 text-xs text-text-primary shadow-2xl backdrop-blur-sm"
      style={{ left: `calc(50% + ${sidebarOffset}px)` }}
      role="status"
      aria-live="polite"
      key={nonce}
    >
      {message}
    </div>
  );
}
