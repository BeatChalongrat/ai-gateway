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
}

export const useComposerStore = create<ComposerState>((set, get) => ({
  height: 0,
  setHeight: (px) => {
    const next = Math.max(0, Math.round(px));
    if (get().height !== next) set({ height: next });
  },
}));
