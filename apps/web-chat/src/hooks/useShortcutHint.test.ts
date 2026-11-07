// FIX84 row 1 — the sidebar's shortcut hints name the key the user presses.
// Before: "⌘⇧N", "⌘K", "⌘⇧S" were string literals, shown on Windows too.

import { describe, expect, it } from "vitest";

import { formatShortcut } from "./useShortcutHint";

describe("formatShortcut (FIX84 row 1)", () => {
  it("Windows/Linux spell the modifiers out, joined with +", () => {
    expect(formatShortcut("Mod+K", false)).toBe("Ctrl+K");
    expect(formatShortcut("Mod+Shift+N", false)).toBe("Ctrl+Shift+N");
    expect(formatShortcut("Mod+Shift+S", false)).toBe("Ctrl+Shift+S");
  });

  it("a Mac gets the glyph cluster the cheatsheet uses", () => {
    expect(formatShortcut("Mod+K", true)).toBe("⌘K");
    expect(formatShortcut("Mod+Shift+N", true)).toBe("⌘⇧N");
  });

  it("never emits a ⌘ for a non-Mac platform (the FIX83 defect)", () => {
    for (const combo of ["Mod+K", "Mod+Shift+N", "Mod+Shift+S", "Mod+,"]) {
      expect(formatShortcut(combo, false)).not.toMatch(/⌘|⇧/);
    }
  });
});
