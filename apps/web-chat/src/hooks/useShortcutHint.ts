// FIX84 Part A row 1 — shortcut hints that name the key the user presses.
//
// The sidebar used to print "⌘⇧N" / "⌘K" / "⌘⇧S" as string literals, so a
// Windows user (every desktop user of this product) saw a Mac glyph next
// to a key that does not exist on their keyboard, while the cheatsheet on
// the same screen said "Ctrl". One renderer now serves both: the
// cheatsheet's platform rule (ShortcutsDialog.renderKey) applied here.
//
// Server-rendered HTML has no navigator, so the first paint says "Ctrl";
// a Mac client flips to "⌘" after mount. That is a deliberate
// SSR-safe default (no hydration mismatch), not a bug: Windows and Linux
// are the common case for this product.
"use client";

import { useEffect, useState } from "react";

import { isMacPlatform, renderKey } from "@/components/chat/ShortcutsDialog";

/** "Mod+Shift+N" → "⌘⇧N" on a Mac, "Ctrl+Shift+N" elsewhere. Pure. */
export function formatShortcut(combo: string, mac: boolean): string {
  const parts = combo.split("+").map((token) => renderKey(token, mac));
  // Mac glyphs read as one cluster (⌘⇧N); everyone else gets the words
  // joined the way the OS itself prints them (Ctrl+Shift+N).
  return mac ? parts.join("") : parts.join("+");
}

/** Returns a formatter bound to the platform detected after mount. */
export function useShortcutHint(): (combo: string) => string {
  const [mac, setMac] = useState(false);
  useEffect(() => {
    setMac(isMacPlatform());
  }, []);
  return (combo: string) => formatShortcut(combo, mac);
}
