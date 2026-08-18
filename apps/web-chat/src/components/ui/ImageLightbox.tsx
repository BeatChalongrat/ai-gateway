"use client";

// Minimal full-screen image viewer. Mounted via a small singleton
// store so any component can open it without prop drilling.
//
// W2 adds: a persistent download control (fed the filename by the
// opener; downloads reuse the already-loaded object URL — zero extra
// fetch), wheel zoom, drag pan while zoomed, and reset via the `0`
// key or the reset button. Pinch-zoom on touch is NOT implemented —
// wheel + drag + reset only.

import { useEffect, useRef, useState } from "react";

import { Download } from "lucide-react";

import { useFocusTrap } from "@/hooks/useFocusTrap";
import { downloadFromObjectUrl } from "@/lib/fileUtil";
import { useT } from "@/lib/i18n";

import { create } from "zustand";

interface LightboxState {
  src: string | null;
  alt: string | null;
  /** Save-as name for the download control; null hides the control
   *  (callers that only have a bare URL keep their old behaviour). */
  filename: string | null;
  open: (src: string, alt?: string, filename?: string) => void;
  close: () => void;
}

export const useLightboxStore = create<LightboxState>((set) => ({
  src: null,
  alt: null,
  filename: null,
  open(src, alt, filename) {
    set({ src, alt: alt ?? null, filename: filename ?? null });
  },
  close() {
    set({ src: null, alt: null, filename: null });
  },
}));

// Tiny imperative helper for non-React callers.
export const openLightbox = (src: string, alt?: string, filename?: string) =>
  useLightboxStore.getState().open(src, alt, filename);

const MIN_SCALE = 1;
const MAX_SCALE = 8;
const WHEEL_STEP = 1.2;

export function ImageLightbox() {
  const t = useT();
  const src = useLightboxStore((s) => s.src);
  const alt = useLightboxStore((s) => s.alt);
  const filename = useLightboxStore((s) => s.filename);
  const close = useLightboxStore((s) => s.close);
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, Boolean(src));

  // Zoom / pan state. scale=1 means "fit" (the classic max-h/max-w
  // contain layout); offsets only apply while zoomed.
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ px: number; py: number } | null>(null);

  // A new image always opens at fit.
  useEffect(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  }, [src]);

  // Close on Escape, reset zoom on `0`. Only attaches the handler
  // while the lightbox is actually open so an empty viewport doesn't
  // intercept keys.
  useEffect(() => {
    if (!src) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "0") {
        setScale(1);
        setOffset({ x: 0, y: 0 });
      }
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

  const zoomed = scale > 1;

  const applyZoom = (dirIn: boolean) => {
    setScale((s) => {
      const next = Math.min(
        MAX_SCALE,
        Math.max(MIN_SCALE, dirIn ? s * WHEEL_STEP : s / WHEEL_STEP),
      );
      if (next === 1) setOffset({ x: 0, y: 0 });
      return next;
    });
  };

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
        onClick={(e) => {
          e.stopPropagation();
          // Fit-state click zooms in (matches the zoom-in cursor);
          // while zoomed, drag is the interaction and click is inert.
          if (!zoomed) applyZoom(true);
        }}
        // Body scroll is locked while open, so wheel events are free
        // to drive zoom without preventDefault (which React's passive
        // root listeners would ignore anyway).
        onWheel={(e) => applyZoom(e.deltaY < 0)}
        onPointerDown={(e) => {
          if (!zoomed) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          dragRef.current = { px: e.clientX, py: e.clientY };
        }}
        onPointerMove={(e) => {
          const drag = dragRef.current;
          if (!drag) return;
          const dx = e.clientX - drag.px;
          const dy = e.clientY - drag.py;
          dragRef.current = { px: e.clientX, py: e.clientY };
          setOffset((o) => ({ x: o.x + dx, y: o.y + dy }));
        }}
        onPointerUp={(e) => {
          if (dragRef.current) e.currentTarget.releasePointerCapture(e.pointerId);
          dragRef.current = null;
        }}
        decoding="async"
        draggable={false}
        className="max-h-[90vh] max-w-[90vw] rounded-md object-contain shadow-2xl"
        style={{
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
          cursor: zoomed ? "grab" : "zoom-in",
          touchAction: "none",
        }}
      />
      <div className="absolute right-4 top-4 flex items-center gap-2">
        {zoomed ? (
          <button
            type="button"
            aria-label={t("lightbox.zoomReset")}
            title={t("lightbox.zoomReset")}
            data-testid="lightbox-zoom-reset"
            onClick={(e) => {
              e.stopPropagation();
              setScale(1);
              setOffset({ x: 0, y: 0 });
            }}
            className="focus-ring rounded-full bg-bg-surface/80 px-3 py-2 text-xs text-text-primary hover:bg-bg-elevated"
          >
            100%
          </button>
        ) : null}
        {filename ? (
          <button
            type="button"
            aria-label={t("common.download")}
            title={t("common.download")}
            data-testid="lightbox-download"
            onClick={(e) => {
              e.stopPropagation();
              downloadFromObjectUrl(src, filename);
            }}
            className="focus-ring rounded-full bg-bg-surface/80 p-2 text-text-primary hover:bg-bg-elevated"
          >
            <Download className="h-[18px] w-[18px]" aria-hidden />
          </button>
        ) : null}
        <button
          type="button"
          aria-label={t("a11y.close")}
          onClick={close}
          className="focus-ring rounded-full bg-bg-surface/80 p-2 text-text-primary hover:bg-bg-elevated"
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
    </div>
  );
}
