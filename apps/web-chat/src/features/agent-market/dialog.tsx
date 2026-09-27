"use client";

// AM3 — a small dialog for the feature's own questions (report a problem,
// sign off, share). A card over a backdrop; Escape and the backdrop close it;
// focus stays inside while it is open and returns to the button afterwards.

import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

import { useFocusTrap } from "@/hooks/useFocusTrap";

import { useAmT } from "./i18n";

export function AmDialog({
  open,
  onClose,
  title,
  description,
  children,
  testId,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  testId?: string;
}) {
  const t = useAmT();
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="max-h-[90vh] w-full overflow-y-auto rounded-t-2xl border border-border bg-bg-surface shadow-xl sm:max-w-lg sm:rounded-2xl"
        data-testid={testId}
      >
        <header className="flex items-start gap-3 border-b border-border-subtle px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
            {description ? <p className="mt-0.5 text-xs text-text-muted">{description}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-muted hover:bg-bg-elevated hover:text-text-primary"
          >
            <X width={15} height={15} aria-hidden />
          </button>
        </header>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
