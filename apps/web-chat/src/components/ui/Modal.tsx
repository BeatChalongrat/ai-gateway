"use client";

// Shared modal shell.
//
// Two render modes driven by viewport width:
//   - desktop (>= 640px): centered card with backdrop
//   - mobile  (<  640px): bottom-sheet that slides up from the
//     bottom of the viewport, with a drag handle, full width, and
//     safe-area-aware padding so it clears the iPhone home indicator
//
// The component uses a CSS media query (matchMedia) to pick the
// mode at mount so SSR doesn't have to guess. Closes on Escape
// + backdrop tap in both modes.

import { useEffect, useRef, useState } from "react";

import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useT } from "@/lib/i18n";

interface Props {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  footer?: React.ReactNode;
  maxWidth?: number;
  children: React.ReactNode;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  footer,
  maxWidth = 480,
  children,
}: Props) {
  const t = useT();
  const [isMobile, setIsMobile] = useState(false);
  // Focus trap + restore-on-close (WCAG 2.4.3/2.1.2) — the ref points at
  // the dialog card in whichever viewport mode rendered.
  const cardRef = useRef<HTMLDivElement>(null);
  useFocusTrap(cardRef, open);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia("(max-width: 639px)");
    const sync = () => setIsMobile(mql.matches);
    sync();
    mql.addEventListener("change", sync);
    return () => mql.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  const header = (
    <header className="flex items-start justify-between gap-4 border-b border-border/60 px-5 py-4">
      <div>
        <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
        {description ? (
          <p className="mt-0.5 text-[11px] text-text-muted">{description}</p>
        ) : null}
      </div>
      <button
        type="button"
        aria-label={t("a11y.close")}
        onClick={onClose}
        className="focus-ring tap-44 -mr-1 rounded p-1 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
      >
        <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M6 6l12 12M18 6l-12 12"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
      </button>
    </header>
  );

  if (isMobile) {
    return (
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={onClose}
        className="fixed inset-0 z-40 flex items-end justify-center bg-black/65 backdrop-blur-sm"
      >
        <div
          ref={cardRef}
          onClick={(e) => e.stopPropagation()}
          className="sheet-up safe-bottom flex w-full max-h-[90vh] flex-col overflow-hidden rounded-t-2xl border-x border-t border-border bg-bg-surface text-text-primary shadow-2xl"
        >
          <div className="flex justify-center pt-2" aria-hidden>
            <span className="h-1 w-10 rounded-full bg-border" />
          </div>
          {header}
          <div className="scrollbar-thin flex-1 overflow-y-auto px-5 py-4">
            {children}
          </div>
          {footer ? (
            <footer className="flex items-center justify-end gap-2 border-t border-border/60 bg-bg-base/40 px-5 py-3">
              {footer}
            </footer>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/65 px-4 backdrop-blur-sm"
    >
      <div
        ref={cardRef}
        onClick={(e) => e.stopPropagation()}
        className="popover-appear flex w-full flex-col overflow-hidden rounded-2xl border border-border bg-bg-surface text-text-primary shadow-2xl"
        style={{ maxWidth }}
      >
        {header}
        <div className="scrollbar-thin max-h-[70vh] overflow-y-auto px-5 py-4">
          {children}
        </div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-border/60 bg-bg-base/40 px-5 py-3">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}
