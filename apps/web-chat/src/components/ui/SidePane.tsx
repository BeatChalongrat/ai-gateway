"use client";

// FIX76 — the shared frame for a panel that sits BESIDE the chat.
//
// Two modes, decided by the viewport (Part A Q1):
//
//   docked  (≥ lg, 1024 px) — a flex sibling of the thread column. No
//           backdrop, no focus trap, no `dialog`: the conversation stays
//           live (scroll, type, send) while the pane is open, so the
//           pane is a `complementary` landmark named after its panel.
//           Esc closes it only when focus is inside the pane or on the
//           document body — Esc in the composer must not take away a
//           pane the user is reading.
//   overlay (< lg) — the slide-over the three panels always had, made
//           honest: a backdrop that closes on click, `role="dialog"`
//           + `aria-modal`, a focus trap, and Esc always closes.
//
// Width when docked: min(maxWidth, 45 %) of the row; the thread keeps
// `flex-1 min-w-0`. Fixed per panel, not remembered, not draggable
// (Part A Q6). The measured docked width is published to sidePaneStore
// so floating pills and the credits badge can stay over the thread.
//
// This component moves the FRAME only. What each panel renders inside
// (preview renderers, the canvas sandbox, the version rail) is the
// panel's own and unchanged.

import { useEffect, useRef, useState, type ReactNode } from "react";

import { X } from "lucide-react";

import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useSidePaneStore } from "@/stores/sidePaneStore";

/** The docking breakpoint — Tailwind's `lg`, the next stop after the
 *  `md` the sidebar already uses. */
export const DOCK_MEDIA_QUERY = "(min-width: 1024px)";

export function useDocked(): boolean {
  const [docked, setDocked] = useState(() =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? true
      : window.matchMedia(DOCK_MEDIA_QUERY).matches,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(DOCK_MEDIA_QUERY);
    const handler = (e: MediaQueryListEvent) => setDocked(e.matches);
    mq.addEventListener("change", handler);
    setDocked(mq.matches);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return docked;
}

interface Props {
  open: boolean;
  /** Accessible name of the pane (and the header title unless `title` is given). */
  label: string;
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  /** Header actions rendered before the close button. */
  actions?: ReactNode;
  /** Docked width cap in px (canvas/preview 672, workspace 448). */
  maxWidth: number;
  closeLabel: string;
  onClose: () => void;
  testId?: string;
  children: ReactNode;
}

export function SidePane({ open, label, title, subtitle, icon, actions, maxWidth, closeLabel, onClose, testId, children }: Props) {
  const docked = useDocked();
  const asideRef = useRef<HTMLElement>(null);
  const setDockedWidth = useSidePaneStore((s) => s.setDockedWidth);

  // Overlay mode is a dialog: trap focus while open. Docked mode never traps.
  useFocusTrap(asideRef, open && !docked);

  // Esc — always in overlay mode; docked only when focus is in the pane
  // or nowhere in particular (body).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (!docked) {
        onClose();
        return;
      }
      const active = document.activeElement;
      const inside = asideRef.current?.contains(active) ?? false;
      if (inside || !active || active === document.body) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, docked, onClose]);

  // Publish the docked width (0 when closed or overlaid).
  useEffect(() => {
    const el = asideRef.current;
    if (!open || !docked || !el) {
      setDockedWidth(0);
      return;
    }
    const publish = () => setDockedWidth(Math.round(el.getBoundingClientRect().width));
    publish();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => {
      ro.disconnect();
      setDockedWidth(0);
    };
  }, [open, docked, setDockedWidth]);

  const header = (
    <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-4 py-3">
      {icon}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-text-primary">{title ?? label}</div>
        {subtitle ? <div className="truncate text-xs text-text-tertiary">{subtitle}</div> : null}
      </div>
      {actions}
      <button
        type="button"
        onClick={onClose}
        className="focus-ring rounded p-1 text-text-tertiary transition-colors hover:bg-bg-elevated hover:text-text-primary"
        aria-label={closeLabel}
        data-testid={testId ? `${testId}-close` : undefined}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );

  if (docked) {
    if (!open) return null;
    return (
      <aside
        ref={asideRef}
        role="complementary"
        aria-label={label}
        data-testid={testId}
        data-pane-mode="docked"
        className="relative flex h-full shrink-0 flex-col border-l border-border-default bg-bg-surface"
        style={{ width: `min(${maxWidth}px, 45%)` }}
      >
        {header}
        {children}
      </aside>
    );
  }

  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity duration-200 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
        aria-hidden
        data-testid={testId ? `${testId}-backdrop` : undefined}
      />
      <aside
        ref={asideRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        aria-hidden={open ? undefined : true}
        data-testid={testId}
        data-pane-mode="overlay"
        className={`fixed right-0 top-0 z-50 flex h-full w-full flex-col border-l border-border-default bg-bg-surface shadow-2xl transition-transform duration-200 ease-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        style={{ maxWidth }}
      >
        {header}
        {children}
      </aside>
    </>
  );
}
