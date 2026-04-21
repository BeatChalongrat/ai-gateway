"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";

// One IntersectionObserver attached at the layout level handles all
// .reveal elements (sections + grid items). A second observer watches
// #steps for the connector-line draw animation. Both run once and
// disconnect — there's no lasting overhead.
//
// The effect re-runs on every pathname change so client-side
// navigations within the (marketing) route group re-observe the
// fresh page's .reveal nodes. Without this, returning to / after
// visiting /pricing (or any sibling route) leaves the new DOM's
// .reveal elements stuck at opacity:0 — the layout persists,
// ScrollReveal doesn't remount, and the stale observer only holds
// references to nodes that were unmounted during navigation. The
// cleanup below disconnects the previous observers before each
// re-run so we don't leak them across navigations.

export function ScrollReveal() {
  const pathname = usePathname();
  useEffect(() => {
    if (typeof window === "undefined") return;

    // Reduced-motion: skip the staged reveal entirely so users see the
    // final layout immediately. CSS @media handles the rest.
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      document.querySelectorAll(".reveal").forEach((el) => el.classList.add("visible"));
      const steps = document.getElementById("steps");
      if (steps) steps.classList.add("revealed");
      return;
    }

    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("visible");
            revealObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -60px 0px" }
    );
    document.querySelectorAll(".reveal").forEach((el) => revealObserver.observe(el));

    let stepsObserver: IntersectionObserver | null = null;
    const stepsEl = document.getElementById("steps");
    if (stepsEl) {
      stepsObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              entry.target.classList.add("revealed");
              stepsObserver?.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.3 }
      );
      stepsObserver.observe(stepsEl);
    }

    return () => {
      revealObserver.disconnect();
      stepsObserver?.disconnect();
    };
  }, [pathname]);

  return null;
}
