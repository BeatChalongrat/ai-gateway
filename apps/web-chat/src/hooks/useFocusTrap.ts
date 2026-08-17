"use client";

// Focus trap for dialogs (WCAG 2.4.3 / 2.1.2). While `active`:
//   1. remembers the previously focused element,
//   2. moves focus to the first focusable element inside `ref`
//      (or the container itself when none exists),
//   3. wraps Tab / Shift+Tab between the first and last focusable,
//   4. restores focus to the remembered element on deactivate/unmount.
// SSR-safe: everything lives inside an effect, so no document access
// during server render.

import { useEffect, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  'input:not([disabled]):not([type="hidden"])',
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
) {
  useEffect(() => {
    if (!active) return;
    const container = ref.current;
    if (!container) return;

    const previouslyFocused =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;

    const focusables = () =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));

    const initial = focusables()[0];
    if (initial) {
      initial.focus();
    } else {
      // Nothing focusable — make the container itself the focus anchor
      // so keyboard events (Escape etc.) stay inside the dialog.
      container.tabIndex = -1;
      container.focus();
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;
      // Wrap at the edges; also pull focus back in if it somehow
      // escaped the container (e.g. focus was on a removed node).
      if (e.shiftKey) {
        if (current === first || !container.contains(current)) {
          e.preventDefault();
          last.focus();
        }
      } else if (current === last || !container.contains(current)) {
        e.preventDefault();
        first.focus();
      }
    };

    // Capture phase so the trap wins over per-component key handlers.
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      previouslyFocused?.focus();
    };
  }, [ref, active]);
}
