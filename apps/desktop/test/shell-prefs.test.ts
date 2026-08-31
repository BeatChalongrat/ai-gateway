// FIX34 Part A — shell preferences: accelerator validation (Electron grammar +
// the "a global shortcut needs a real modifier" rule), the tray-click default
// (§2.5: main window, NOT the quick entry), and a store that never lets a bad
// file through.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_QUICK_HOTKEY, DEFAULT_SHELL_PREFS, sanitizePrefs, ShellPrefsStore, validateAccelerator } from "../src/main/shell-prefs";

describe("validateAccelerator", () => {
  it("accepts Electron's grammar and canonicalises the spelling", () => {
    expect(validateAccelerator("ctrl+shift+a")).toEqual({ ok: true, accelerator: "Control+Shift+A" });
    expect(validateAccelerator("CommandOrControl+Shift+Space")).toEqual({ ok: true, accelerator: "CommandOrControl+Shift+Space" });
    expect(validateAccelerator("CmdOrCtrl+Alt+F12")).toEqual({ ok: true, accelerator: "CommandOrControl+Alt+F12" });
    expect(validateAccelerator("Alt+`")).toEqual({ ok: true, accelerator: "Alt+`" });
    expect(validateAccelerator("Ctrl+Shift++")).toEqual({ ok: true, accelerator: "Control+Shift+Plus" });
    expect(validateAccelerator("Super+num5")).toEqual({ ok: true, accelerator: "Super+num5" });
  });

  it("refuses a global shortcut without a strong modifier (Shift alone is not one)", () => {
    expect(validateAccelerator("A")).toEqual({ ok: false, reason: "no-modifier" });
    expect(validateAccelerator("Shift+A")).toEqual({ ok: false, reason: "no-modifier" });
    expect(validateAccelerator("Space")).toEqual({ ok: false, reason: "no-modifier" });
  });

  it("refuses malformed strings", () => {
    expect(validateAccelerator("")).toEqual({ ok: false, reason: "empty" });
    expect(validateAccelerator("   ")).toEqual({ ok: false, reason: "empty" });
    expect(validateAccelerator(null)).toEqual({ ok: false, reason: "empty" });
    expect(validateAccelerator("Ctrl+Shift")).toEqual({ ok: false, reason: "no-key" });
    expect(validateAccelerator("Ctrl+A+B")).toEqual({ ok: false, reason: "two-keys" });
    expect(validateAccelerator("Ctrl+Bogus")).toEqual({ ok: false, reason: "unknown-token" });
    expect(validateAccelerator("Ctrl++A")).toEqual({ ok: false, reason: "unknown-token" });
    expect(validateAccelerator("Ctrl+" + "A".repeat(80))).toEqual({ ok: false, reason: "too-long" });
  });
});

describe("shell prefs defaults (§2.5)", () => {
  it("tray click opens the MAIN window by default and the quick hotkey is Ctrl+Shift+A", () => {
    expect(DEFAULT_SHELL_PREFS.trayClick).toBe("main");
    expect(DEFAULT_QUICK_HOTKEY).toBe("CommandOrControl+Shift+A");
  });

  it("sanitizePrefs falls back per field, never wholesale", () => {
    expect(sanitizePrefs({ quickHotkey: "garbage", trayClick: "quick" })).toEqual({ quickHotkey: DEFAULT_QUICK_HOTKEY, trayClick: "quick" });
    expect(sanitizePrefs({ quickHotkey: "alt+q", trayClick: "banana" as never })).toEqual({ quickHotkey: "Alt+Q", trayClick: "main" });
    expect(sanitizePrefs(null)).toEqual({ ...DEFAULT_SHELL_PREFS });
  });
});

describe("ShellPrefsStore", () => {
  const dirs: string[] = [];
  afterEach(() => {
    for (const d of dirs.splice(0)) fs.rmSync(d, { recursive: true, force: true });
  });
  const tmpFile = () => {
    const d = fs.mkdtempSync(path.join(os.tmpdir(), "sran-shell-prefs-"));
    dirs.push(d);
    return path.join(d, "nested", "shell-prefs.json");
  };

  it("round-trips through the file and re-validates on load", () => {
    const file = tmpFile();
    const a = new ShellPrefsStore(file);
    expect(a.load()).toEqual({ ...DEFAULT_SHELL_PREFS });
    a.update({ quickHotkey: "ctrl+alt+space", trayClick: "quick" });
    const b = new ShellPrefsStore(file);
    expect(b.load()).toEqual({ quickHotkey: "Control+Alt+Space", trayClick: "quick" });
    fs.writeFileSync(file, JSON.stringify({ quickHotkey: "Q", trayClick: "quick" }));
    expect(new ShellPrefsStore(file).load()).toEqual({ quickHotkey: DEFAULT_QUICK_HOTKEY, trayClick: "quick" });
    fs.writeFileSync(file, "{not json");
    expect(new ShellPrefsStore(file).load()).toEqual({ ...DEFAULT_SHELL_PREFS });
  });
});
