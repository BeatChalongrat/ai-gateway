// FIX34 Part A — keydown → Electron accelerator (the hotkey capture in Settings).
import { describe, expect, it } from "vitest";

import { acceleratorFromKey, describeAccelerator } from "./accelerator";

const base = { ctrlKey: false, metaKey: false, altKey: false, shiftKey: false };

describe("acceleratorFromKey", () => {
  it("builds CommandOrControl+Shift+<Key> from a Ctrl+Shift+A press", () => {
    expect(acceleratorFromKey({ ...base, key: "A", code: "KeyA", ctrlKey: true, shiftKey: true })).toBe("CommandOrControl+Shift+A");
  });
  it("treats ⌘ like Ctrl so one saved value works everywhere", () => {
    expect(acceleratorFromKey({ ...base, key: "a", code: "KeyA", metaKey: true })).toBe("CommandOrControl+A");
  });
  it("uses the physical key on a Thai layout (ฟ on the A key → A)", () => {
    expect(acceleratorFromKey({ ...base, key: "ฟ", code: "KeyA", ctrlKey: true, altKey: true })).toBe("CommandOrControl+Alt+A");
  });
  it("keeps waiting (null) while only modifiers are down", () => {
    expect(acceleratorFromKey({ ...base, key: "Control", code: "ControlLeft", ctrlKey: true })).toBeNull();
    expect(acceleratorFromKey({ ...base, key: "Shift", code: "ShiftLeft", shiftKey: true })).toBeNull();
  });
  it("maps named keys and function keys", () => {
    expect(acceleratorFromKey({ ...base, key: " ", code: "Space", ctrlKey: true })).toBe("CommandOrControl+Space");
    expect(acceleratorFromKey({ ...base, key: "F9", code: "F9", altKey: true })).toBe("Alt+F9");
    expect(acceleratorFromKey({ ...base, key: "ArrowUp", code: "ArrowUp", ctrlKey: true })).toBe("CommandOrControl+Up");
    expect(acceleratorFromKey({ ...base, key: "1", code: "Digit1", ctrlKey: true })).toBe("CommandOrControl+1");
  });
  it("returns null for a key it cannot name", () => {
    expect(acceleratorFromKey({ ...base, key: "Unidentified", code: "", ctrlKey: true })).toBeNull();
    expect(acceleratorFromKey({ ...base, key: "ฟ", code: "", ctrlKey: true })).toBeNull();
  });
});

describe("describeAccelerator", () => {
  it("renders per platform", () => {
    expect(describeAccelerator("CommandOrControl+Shift+A")).toBe("Ctrl + Shift + A");
    expect(describeAccelerator("CommandOrControl+Shift+A", true)).toBe("⌘ + ⇧ + A");
    expect(describeAccelerator("Alt+Space")).toBe("Alt + Space");
  });
});
