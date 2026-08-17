"use client";

// Minimal full-screen image viewer. Mounted via a small singleton
// store so any component can open it without prop drilling.

import { useEffect, useRef } from "react";

import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useT } from "@/lib/i18n";

import { create } from "zustand";

interface LightboxState {
  src: string | null;
  alt: string | null;
  open: (src: string, alt?: string) => void;
  close: () => void;
}

export const useLightboxStore = create<LightboxState>((set) => ({
  src: null,
  alt: null,
  open(src, alt) {
    set({ src, alt: alt ?? null });
  },
  close() {
    set({ src: null, alt: null });
  },
}));

// Tiny imperative helper for non-React callers.
export const openLightbox = (src: string, alt?: string) =>
  useLightboxStore.getState().open(src, alt);

export function ImageLightbox() {
  const t = useT();
  const src = useLightboxStore((s) => s.src);
  const alt = useLightboxStore((s) => s.alt);
  const close = useLightboxStore((s) => s.close);
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, Boolean(src));

  // Close on Escape. Only attaches the handler while the lightbox
  // is actually open so an empty viewport doesn't intercept keys.
  useEffect(() => {
    if (!src) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [src, close]);

  // Lock body scroll while open.
  useEffect(() => {
    if (!src) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [src]);

  if (!src) return null;
  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={alt || t("a11y.imagePreview")}
      onClick={close}
      className="popover-appear fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm"
    >
      <img
        src={src}
        alt={alt ?? ""}
        onClick={(e) => e.stopPropagation()}
        decoding="async"
        className="max-h-[90vh] max-w-[90vw] rounded-md object-contain shadow-2xl"
      />
      <button
        type="button"
        aria-label={t("a11y.close")}
        onClick={close}
        className="focus-ring absolute right-4 top-4 rounded-full bg-bg-surface/80 p-2 text-text-primary hover:bg-bg-elevated"
      >
        <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M6 6l12 12M18 6l-12 12"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
      </button>
    </div>
  );
}
