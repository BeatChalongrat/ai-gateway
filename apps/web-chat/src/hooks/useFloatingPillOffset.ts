"use client";

// Where a bottom-centred floating pill actually belongs.
//
// `position: fixed` is anchored to the viewport, so `left-1/2` centres a
// pill on the SCREEN. On a chat route the sidebar occupies the left edge,
// so the chat column's centre sits half a sidebar to the right of that —
// and a pill centred on the screen reads as visibly off to the left.
//
// The Toast worked this out first and got it right; the UpdateBanner did
// not, and drifted left by 130px whenever the sidebar was open. Two
// pills that sit in the same place on the screen should not each carry
// their own copy of the arithmetic, so it lives here once. Anything else
// that floats bottom-centre over the chat surface should use this too.

import { useEffect, useState } from "react";

import { useSidebarStore } from "@/stores/sidebarStore";

// Sidebar's desktop width — kept in sync with Sidebar.tsx's
// ``md:w-[260px]`` rule. Half this value is the visual offset the chat
// content area shifts to the right of the viewport midline when the
// sidebar is expanded at desktop sizes.
export const SIDEBAR_DESKTOP_WIDTH = 260;

/**
 * Horizontal offset, in pixels, to add to `50%` so a fixed element lands
 * on the visual centre of the chat content area:
 *
 *   * desktop + expanded sidebar              → 130 (half of 260)
 *   * collapsed sidebar, or mobile (overlay)  → 0
 *
 * Non-chat routes don't mount the chat layout, so `collapsed` stays at
 * its store default (false) — but those routes don't render the sidebar
 * either, so the offset would be wrong there. Staying correct everywhere
 * would need a per-route hint; the chat surface is the only mounted UI
 * that floats these pills today. (Carried over from the Toast, along
 * with its limitation, deliberately: this hook is where that gets fixed
 * once, if it ever needs fixing.)
 */
export function useFloatingPillOffset(): number {
  // Read synchronously from the shared store so the very first render
  // already knows where to position itself. No CSS variable, no layout
  // effect, no round-trip through <html> style → no flicker, and no
  // frame where the pill is visibly in the wrong place.
  const collapsed = useSidebarStore((s) => s.collapsed);

  // Desktop-or-not via matchMedia. Lazy initial state reads the real
  // value on the first client render, and falls back to `true` on the
  // server so the SSR shell stays consistent with the layout's flex
  // assumption.
  //
  // `matchMedia` is guarded rather than assumed: it is missing under
  // SSR and under jsdom, and a pill that throws while working out where
  // to sit takes the whole page down over a cosmetic detail. Absent →
  // treat as desktop, matching the SSR fallback.
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? true
      : window.matchMedia("(min-width: 768px)").matches,
  );

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(min-width: 768px)");
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  return isDesktop && !collapsed ? SIDEBAR_DESKTOP_WIDTH / 2 : 0;
}
