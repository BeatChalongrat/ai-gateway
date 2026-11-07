"use client";

// Minimal ephemeral toast. Held in a tiny Zustand store so any
// component can push a message without prop-drilling. Auto-dismisses
// after the configured delay; only the most-recent toast renders to
// keep things simple (no stacking).
//
// FIX132 F18 — errors no longer vanish unread. NIGHT3 measured the 200 MB
// refusal on screen for 2.0 s, in a region mounted together with its text
// (screen readers often skip those). Now:
//   · toastError(): stays ERROR_TOAST_MS (10 s), has a close button, and is
//     announced through an always-mounted role="alert" region;
//   · toast(): stays long enough to read — at least 4 s, more for long
//     text — and is announced through an always-mounted role="status".

import { useEffect } from "react";

import { create } from "zustand";

import { PILL_GAP, useBottomInset } from "@/hooks/useBottomInset";
import { useT } from "@/lib/i18n";
import { useFloatingPillOffset } from "@/hooks/useFloatingPillOffset";

/** FIX27 §2.2 — an optional one-click action on a toast ("Switch to GPT-4o"),
 *  so a message that names the way out also offers it. */
export interface ToastAction {
  label: string;
  onClick: () => void;
}

export type ToastKind = "info" | "error";

/** FIX132 F18 — how long an error toast stays unless it is closed. */
export const ERROR_TOAST_MS = 10_000;
/** FIX132 F18 — an info toast: at least this long, plus reading time. */
const INFO_TOAST_MIN_MS = 4_000;
const INFO_TOAST_MAX_MS = 8_000;
const READING_MS_PER_CHAR = 50;

/** How long a toast stays: errors until read (10 s, or closed); info long
 *  enough to read at a glance. An action keeps it at least 8 s to be clicked. */
export function toastDuration(message: string, kind: ToastKind, hasAction: boolean, base = INFO_TOAST_MIN_MS): number {
  if (kind === "error") return ERROR_TOAST_MS;
  const reading = Math.min(INFO_TOAST_MAX_MS, Math.max(base, INFO_TOAST_MIN_MS, 1_500 + message.length * READING_MS_PER_CHAR));
  return hasAction ? Math.max(reading, 8_000) : reading;
}

interface ToastState {
  message: string | null;
  action: ToastAction | null;
  kind: ToastKind;
  // Bumped on every push so an identical message still re-triggers
  // the auto-dismiss timer.
  nonce: number;
  show: (message: string, action?: ToastAction | null, kind?: ToastKind) => void;
  clear: () => void;
}

export const useToastStore = create<ToastState>((set) => ({
  message: null,
  action: null,
  kind: "info",
  nonce: 0,
  show(message, action = null, kind = "info") {
    set((s) => ({ message, action, kind, nonce: s.nonce + 1 }));
  },
  clear() {
    set({ message: null, action: null });
  },
}));

// Convenience wrapper so call sites read cleanly.
export const toast = (message: string, action?: ToastAction | null) => useToastStore.getState().show(message, action ?? null);
/** FIX132 F18 — something went wrong: stays 10 s (or until closed), announced as an alert. */
export const toastError = (message: string, action?: ToastAction | null) =>
  useToastStore.getState().show(message, action ?? null, "error");

export function ToastViewport({ duration = INFO_TOAST_MIN_MS }: { duration?: number }) {
  const t = useT();
  const message = useToastStore((s) => s.message);
  const action = useToastStore((s) => s.action);
  const kind = useToastStore((s) => s.kind);
  const nonce = useToastStore((s) => s.nonce);
  const clear = useToastStore((s) => s.clear);
  // Where a bottom-centred pill belongs over the chat column. Shared
  // with UpdateBanner so the two cannot drift apart.
  const sidebarOffset = useFloatingPillOffset();
  // FIX86 — and above the composer, whatever its height is right now.
  const inset = useBottomInset();

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(clear, toastDuration(message, kind, Boolean(action), duration));
    return () => window.clearTimeout(timer);
  }, [message, action, kind, nonce, duration, clear]);

  const isError = kind === "error";
  return (
    <>
      {/* FIX132 F18 — always mounted, so a new text is announced. */}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {message && !isError ? message : ""}
      </div>
      <div className="sr-only" role="alert" aria-live="assertive" aria-atomic="true">
        {message && isError ? message : ""}
      </div>
      {message ? (
        <div
          className={`toast-appear fixed z-50 flex max-w-[calc(100vw-2rem)] -translate-x-1/2 items-center gap-3 rounded-full border bg-bg-surface px-4 py-2 text-xs text-text-primary shadow-2xl backdrop-blur-sm ${
            isError ? "border-status-danger/40" : "border-border"
          } ${action || isError ? "pointer-events-auto" : "pointer-events-none"}`}
          style={{ left: `calc(50% + ${sidebarOffset}px)`, bottom: inset + PILL_GAP }}
          data-testid="toast"
          data-kind={kind}
          key={nonce}
        >
          <span>{message}</span>
          {action ? (
            // FIX27 §2.2 — the way out, one click (e.g. "Switch to GPT 5.6 Terra").
            <button
              type="button"
              onClick={() => {
                action.onClick();
                clear();
              }}
              className="shrink-0 rounded-full bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-500"
            >
              {action.label}
            </button>
          ) : null}
          {isError ? (
            <button
              type="button"
              onClick={clear}
              aria-label={t("toast.close")}
              className="focus-ring -mr-1 shrink-0 rounded-full p-1 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
            >
              <svg width={12} height={12} viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
