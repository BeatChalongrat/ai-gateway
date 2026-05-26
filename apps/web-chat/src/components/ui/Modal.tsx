"use client";

// Shared modal shell. Renders a dark backdrop + centered card with
// title, body, and an optional footer. Closes on Escape and on
// backdrop click. Mounted inline (no portal) — Next 14 hydrates
// fine for our usage because we only render the modal when open.

import { useEffect } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  footer?: React.ReactNode;
  // Optional max-width override — defaults to 480px.
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
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/65 px-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="popover-appear flex w-full flex-col overflow-hidden rounded-2xl border border-border bg-bg-surface text-text-primary shadow-2xl"
        style={{ maxWidth }}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border/60 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
            {description ? (
              <p className="mt-0.5 text-[11px] text-text-muted">{description}</p>
            ) : null}
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="focus-ring rounded p-1 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
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
