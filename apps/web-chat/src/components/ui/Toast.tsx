"use client";

// Minimal ephemeral toast. Held in a tiny Zustand store so any
// component can push a message without prop-drilling. Auto-dismisses
// after the configured delay; only the most-recent toast renders to
// keep things simple (no stacking).

import { useEffect } from "react";

import { create } from "zustand";

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

  useEffect(() => {
    if (!message) return;
    const t = window.setTimeout(clear, duration);
    return () => window.clearTimeout(t);
  }, [message, nonce, duration, clear]);

  if (!message) return null;
  return (
    <div
      className="popover-appear pointer-events-none fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full border border-border bg-bg-surface/95 px-4 py-2 text-xs text-text-primary shadow-2xl backdrop-blur-sm"
      role="status"
      aria-live="polite"
      key={nonce}
    >
      {message}
    </div>
  );
}
