"use client";

import { useEffect, useState } from "react";

// Animate a numeric value from 0 → target over `duration` ms using
// requestAnimationFrame. Reserved for large hero numbers (credit
// balance, billing balance) — small stats like "3 projects" would
// look twitchy. Returns an integer so downstream .toLocaleString()
// stays clean.
//
// Respects prefers-reduced-motion: reports the target value
// immediately when the OS asks us to minimize motion.
export function useCountUp(target: number, duration = 800): number {
  const [val, setVal] = useState(0);

  useEffect(() => {
    // Snap to target when the user has reduced motion enabled.
    if (
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ) {
      setVal(target);
      return;
    }

    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      setVal(Math.floor(target * eased));
      if (t < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        // Final frame: commit exact target so we never end on a
        // rounded-down value (e.g. 199,999 instead of 200,000).
        setVal(target);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return val;
}
