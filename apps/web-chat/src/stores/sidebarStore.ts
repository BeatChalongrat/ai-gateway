// Sidebar collapsed/expanded state — extracted from chat/layout.tsx
// into a Zustand store so OTHER components (specifically Toast.tsx)
// can read the value synchronously during render. Previously
// ``collapsed`` was a ``useState`` local to the chat layout that
// published through a ``--toast-x-offset`` CSS variable; the round-
// trip through CSS + a layout effect caused a one-frame flicker on
// the first toast of a page load. Reading from a shared store
// directly is render-synchronous and removes the flicker entirely.
//
// localStorage key is the SAME the legacy layout used
// (``sran-webchat-sidebar-collapsed``) so a returning user's
// preference survives the refactor with no migration step.

"use client";

import { create } from "zustand";

const COLLAPSED_KEY = "sran-webchat-sidebar-collapsed";

// Lazy initial read. Runs ONCE per module load:
//   * server-side (Next.js SSR shell): ``window`` is undefined →
//     default ``false`` (sidebar expanded). Matches what the
//     pre-refactor ``useState(false)`` initial value did, so the
//     server-rendered shell is byte-identical.
//   * client-side first render: localStorage is read directly so
//     the very first paint already reflects the user's saved
//     preference. The previous code flashed to the default first
//     and corrected in an effect; this skips the flash.
function readInitialCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    // Storage disabled / quota — fall through to the expanded default.
    return false;
  }
}

interface SidebarState {
  collapsed: boolean;
  setCollapsed: (v: boolean) => void;
  toggleCollapsed: () => void;
}

export const useSidebarStore = create<SidebarState>((set, get) => ({
  collapsed: readInitialCollapsed(),
  setCollapsed(v) {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(COLLAPSED_KEY, v ? "1" : "0");
      } catch {
        /* quota or disabled — visual state still flips. */
      }
    }
    set({ collapsed: v });
  },
  toggleCollapsed() {
    get().setCollapsed(!get().collapsed);
  },
}));
