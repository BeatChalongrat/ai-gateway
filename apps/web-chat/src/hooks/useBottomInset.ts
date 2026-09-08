"use client";

// FIX86 — where the bottom of the chat column actually is.
//
// The horizontal axis is already a relationship (`useFloatingPillOffset`:
// half a sidebar, minus half a docked pane). This is the vertical one:
// the distance from the viewport bottom to the top of the composer, read
// from the ONE measurement the composer publishes. A bottom-floating
// element positions itself at `useBottomInset() + its own gap`; it never
// states how tall it thinks the composer is.
//
// `useComposerHeightProbe` is the producer — attach it to the composer's
// outermost element. A ResizeObserver keeps the value live while the
// composer grows or shrinks (typing to multi-line, an attachment chip,
// the recording bar), not only at first paint; unmounting publishes 0.

import { useEffect, type RefObject } from "react";
import { flushSync } from "react-dom";

import { useComposerStore } from "@/stores/composerStore";

/** Gap between the composer's top edge and a bottom-centred / bottom-right
 *  pill (toast, update banner, error banner). */
export const PILL_GAP = 24;
/** Gap between the composer's top edge and the credits badge. */
export const BADGE_GAP = 12;

/** Distance (px) from the viewport bottom to the composer's top edge,
 *  or 0 when no composer is docked at the bottom of the viewport. */
export function useBottomInset(): number {
  return useComposerStore((s) => s.height);
}

/** How far the composer's bottom edge may sit above the viewport bottom
 *  and still count as "docked at the bottom": the safe-area padding and
 *  the disclaimer lines the quick window renders UNDER the composer
 *  (24 px there). A composer further up than this is the landing page's
 *  centred one — nothing floats over it, so it publishes 0. */
export const DOCKED_TOLERANCE = 64;

/** The value the composer publishes for its current box. Exported so the
 *  test can pin the rule with plain numbers. */
export function insetForRect(rect: { top: number; bottom: number }, viewportHeight: number): number {
  const below = viewportHeight - rect.bottom;
  if (below > DOCKED_TOLERANCE || rect.bottom <= rect.top) return 0;
  // Not the box's height: the distance from the viewport bottom to its
  // TOP edge, so whatever sits under the composer (safe area, disclaimer)
  // is cleared too, and a transform that lifts the composer over the
  // on-screen keyboard is followed (getBoundingClientRect includes it).
  return Math.max(0, viewportHeight - rect.top);
}

/** Producer: publish `ref`'s inset for as long as it is mounted. */
export function useComposerHeightProbe(ref: RefObject<HTMLElement | null>): void {
  const setHeight = useComposerStore((s) => s.setHeight);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const publish = () => setHeight(insetForRect(el.getBoundingClientRect(), window.innerHeight));
    publish();
    // The viewport can change without the composer resizing (a window
    // resize on a fixed-width column, the on-screen keyboard).
    window.addEventListener("resize", publish);
    // jsdom has no ResizeObserver; the first measurement still happens.
    if (typeof ResizeObserver === "undefined")
      return () => {
        window.removeEventListener("resize", publish);
        setHeight(0);
      };
    // The observer fires after layout, before paint. Flushing the store
    // update synchronously inside it means the consumers are re-laid-out
    // in the SAME frame — no frame paints the badge in the old place
    // while the composer is already taller (measured: two of eighteen
    // mid-growth samples overlapped by 11 px without this).
    const ro = new ResizeObserver(() => {
      try {
        flushSync(publish);
      } catch {
        publish();
      }
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", publish);
      setHeight(0);
    };
  }, [ref, setHeight]);
}
