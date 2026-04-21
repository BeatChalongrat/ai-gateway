"use client";

import { useEffect } from "react";

const NAV_OFFSET = 80;
const SCROLL_DURATION = 600;

// Mirrors the helper in MarketingNav.tsx — kept duplicated to keep the
// diff minimal. If a third caller ever needs it, lift to a shared
// module under app/_shared.
function smoothScrollTo(targetY: number, duration = SCROLL_DURATION) {
  const startY = window.scrollY;
  const distance = targetY - startY;
  if (Math.abs(distance) < 2) return;

  const startTime = performance.now();
  const easeInOutCubic = (t: number) =>
    t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

  const step = (now: number) => {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    window.scrollTo(0, startY + distance * easeInOutCubic(progress));
    if (progress < 1) requestAnimationFrame(step);
  };

  requestAnimationFrame(step);
}

// Cross-page nav (e.g. /pricing → /#features) updates the URL hash but
// Next's App Router doesn't trigger the browser's native anchor scroll
// after client-side navigation. This effect runs once on mount and
// scrolls to the hash target if there is one.
export default function HashScrollHandler() {
  useEffect(() => {
    const hash = window.location.hash;
    if (!hash || hash.length <= 1) return;

    const id = hash.slice(1);
    // Cancel the browser's native hash jump (which already used
    // scroll-padding-top: 80px). Without this, the native jump and our
    // animated scroll stack and overshoot the target by ~220px.
    window.scrollTo(0, 0);
    // One frame so layout is final after hydration.
    requestAnimationFrame(() => {
      const el = document.getElementById(id);
      if (!el) return;
      const targetY =
        el.getBoundingClientRect().top + window.scrollY - NAV_OFFSET;
      smoothScrollTo(targetY);
    });
  }, []);

  return null;
}
