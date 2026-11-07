"use client";

// FIX86 — the composer's measured height, published once.
//
// Everything that floats at the bottom of the chat column (the credits
// badge, the toast, the update banner, the error banner) used to carry
// its own literal for how far up it sat — `bottom-[76px]`, `bottom-6` —
// each a guess at the composer's height. The composer grows (multi-line
// text, an attachment chip, the folder chip, the recording bar, an
// in-composer notice), and every guess was wrong the moment it did:
// FIX83 measured the badge on top of the composer at 1280/1440/1600/
// 1920 and 390.
//
// This is the vertical twin of `sidePaneStore.dockedWidth` (FIX76/82):
// ONE producer (the composer's ResizeObserver, `useComposerHeightProbe`)
// and every consumer asks `useBottomInset()` instead of guessing. On a
// surface with no composer mounted the height is 0 and a consumer sits
// where it always did.

import { create } from "zustand";

interface ComposerState {
  /** Measured outer height of the mounted composer, in CSS px; 0 when
   *  no composer is mounted. */
  height: number;
  setHeight: (px: number) => void;
  /** FIX130 C4 — what the page stacks directly on top of the composer (the
   *  phone's long-chat chip, the temporary-chat strip, the message queue),
   *  in CSS px; 0 when nothing. The credits badge floats above this too:
   *  it used to sit ON the memory strip at 360 px (NIGHT2 F11). */
  above: number;
  setAbove: (px: number) => void;
  /** FIX130 C4 — how far the credits badge reaches up from the top of that
   *  stack (its height + its gap); 0 while it is not on screen. The thread
   *  keeps this much room under its last message, so the badge never
   *  covers the last timestamp (FIX128 §6, NIGHT2 F11). */
  badge: number;
  setBadge: (px: number) => void;
}

const settle = (px: number) => Math.max(0, Math.round(px));

export const useComposerStore = create<ComposerState>((set, get) => ({
  height: 0,
  setHeight: (px) => {
    const next = settle(px);
    if (get().height !== next) set({ height: next });
  },
  above: 0,
  setAbove: (px) => {
    const next = settle(px);
    if (get().above !== next) set({ above: next });
  },
  badge: 0,
  setBadge: (px) => {
    const next = settle(px);
    if (get().badge !== next) set({ badge: next });
  },
}));
