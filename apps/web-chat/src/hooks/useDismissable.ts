// FIX84 Part A row 3 — the ONE dismiss rule for every popover and menu.
//
// Before this hook each popover carried its own copy of an outside-click
// effect, and only two of them (MemoryIndicator, CreditBadge) remembered
// Escape. The result FIX83 measured: on the conversation screen Escape
// closed five popovers and left the other seven open. There was no shared
// primitive to fix — so this is it, and every popover goes through it.
//
// Rules (the same for all of them):
//   * pointer-down outside the popover's root closes it. ``mousedown``
//     rather than ``click`` so the guard fires BEFORE the trigger's own
//     click could toggle the popover straight back open.
//   * Escape closes it and marks the event handled (``preventDefault``),
//     so the layout's Esc → stop-generation binding (useKeyboardShortcuts,
//     which honours ``defaultPrevented``) does not also fire on the same
//     key press. Escape is listened for on ``document`` — before the
//     window-level global handler in the bubbling order.
//   * nothing is attached while the popover is closed, so idle popovers
//     cost no listeners.
import { useEffect, type RefObject } from "react";

export function useDismissable(
  ref: RefObject<HTMLElement | null>,
  open: boolean,
  onClose: () => void,
): void {
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      e.preventDefault();
      onClose();
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [ref, open, onClose]);
}
