// FIX34 Part A — HOST-owned shell preferences: the quick-ask hotkey and what a
// click on the tray icon opens.
//
// Why a host file and not a sidecar setting: both values are consumed by the
// Electron shell itself (globalShortcut.register, tray.on("click")) before
// the sidecar is up, and neither concerns the tools process. Kept beside
// window-state.json as <userData>/shell-prefs.json; the page reads/writes them
// through the shell.prefs.get / shell.prefs.set control-plane ops.
//
// The accelerator grammar is Electron's (docs/api/accelerator): modifiers
// joined with '+' and ONE key. We additionally require at least one real
// modifier — a bare letter as a GLOBAL shortcut would swallow that key for
// every application on the machine.
//
// §2.5 of the FIX34 prompt: Claude on Windows makes a taskbar/tray click open
// the quick entry with no way to switch it off (anthropics/claude-code#22420,
// closed "not planned"). Here it is a setting, and the default is what every
// ordinary app does — the click opens the main window.
//
// No 'electron' import; pure parsing + a small JSON store (unit-tested).

import fs from "node:fs";
import path from "node:path";

export type TrayClickAction = "main" | "quick";

export interface ShellPrefs {
  /** Electron accelerator for the quick-ask window (default Ctrl+Shift+A). */
  quickHotkey: string;
  /** What a single click on the tray icon opens. Default: the main window. */
  trayClick: TrayClickAction;
}

export const DEFAULT_QUICK_HOTKEY = "CommandOrControl+Shift+A";
export const DEFAULT_SHELL_PREFS: Readonly<ShellPrefs> = Object.freeze({ quickHotkey: DEFAULT_QUICK_HOTKEY, trayClick: "main" });

const MODIFIERS = new Set(["commandorcontrol", "cmdorctrl", "command", "cmd", "control", "ctrl", "alt", "option", "altgr", "shift", "super", "meta"]);
/** Modifiers that count as "a real modifier" for a global shortcut (Shift alone does not). */
const STRONG_MODIFIERS = new Set(["commandorcontrol", "cmdorctrl", "command", "cmd", "control", "ctrl", "alt", "option", "altgr", "super", "meta"]);
const KEY_CODES = new Set([
  "plus", "space", "tab", "capslock", "numlock", "scrolllock", "backspace", "delete", "insert", "return", "enter", "up", "down", "left", "right",
  "home", "end", "pageup", "pagedown", "escape", "esc", "volumeup", "volumedown", "volumemute", "medianexttrack", "mediaprevioustrack", "mediastop",
  "mediaplaypause", "printscreen",
  "num0", "num1", "num2", "num3", "num4", "num5", "num6", "num7", "num8", "num9", "numdec", "numadd", "numsub", "nummult", "numdiv",
]);
const CANONICAL_MODIFIER: Record<string, string> = {
  commandorcontrol: "CommandOrControl", cmdorctrl: "CommandOrControl", command: "Command", cmd: "Command", control: "Control", ctrl: "Control",
  alt: "Alt", option: "Option", altgr: "AltGr", shift: "Shift", super: "Super", meta: "Meta",
};
const CANONICAL_KEY: Record<string, string> = {
  plus: "Plus", space: "Space", tab: "Tab", capslock: "Capslock", numlock: "Numlock", scrolllock: "Scrolllock", backspace: "Backspace", delete: "Delete",
  insert: "Insert", return: "Return", enter: "Enter", up: "Up", down: "Down", left: "Left", right: "Right", home: "Home", end: "End", pageup: "PageUp",
  pagedown: "PageDown", escape: "Escape", esc: "Esc", volumeup: "VolumeUp", volumedown: "VolumeDown", volumemute: "VolumeMute",
  medianexttrack: "MediaNextTrack", mediaprevioustrack: "MediaPreviousTrack", mediastop: "MediaStop", mediaplaypause: "MediaPlayPause", printscreen: "PrintScreen",
  num0: "num0", num1: "num1", num2: "num2", num3: "num3", num4: "num4", num5: "num5", num6: "num6", num7: "num7", num8: "num8", num9: "num9",
  numdec: "numdec", numadd: "numadd", numsub: "numsub", nummult: "nummult", numdiv: "numdiv",
};

export type AcceleratorVerdict =
  | { ok: true; accelerator: string }
  | { ok: false; reason: "empty" | "too-long" | "no-key" | "two-keys" | "unknown-token" | "no-modifier" };

/**
 * Validates + canonicalises an Electron accelerator string. Accepts what
 * Electron accepts (case-insensitive tokens, '+' separated), returns the
 * canonical spelling, and refuses a global shortcut without a strong modifier.
 */
export function validateAccelerator(raw: string | null | undefined): AcceleratorVerdict {
  const value = (raw ?? "").trim();
  if (!value) return { ok: false, reason: "empty" };
  if (value.length > 64) return { ok: false, reason: "too-long" };
  // "Plus" is spelled out in Electron's grammar; a trailing literal '+' is the Plus key too.
  const tokens = value.endsWith("++") ? [...value.slice(0, -2).split("+"), "Plus"] : value.split("+");
  const mods: string[] = [];
  let key: string | null = null;
  for (const t of tokens) {
    const tok = t.trim();
    const low = tok.toLowerCase();
    if (!tok) return { ok: false, reason: "unknown-token" };
    if (MODIFIERS.has(low)) {
      const c = CANONICAL_MODIFIER[low]!;
      if (!mods.includes(c)) mods.push(c);
      continue;
    }
    let canonical: string | null = null;
    if (KEY_CODES.has(low)) canonical = CANONICAL_KEY[low]!;
    else if (/^[a-z0-9]$/.test(low)) canonical = low.toUpperCase();
    else if (/^f([1-9]|1[0-9]|2[0-4])$/.test(low)) canonical = low.toUpperCase();
    else if (/^[)!@#$%^&*(:;<>=,._?`~{}|\-\[\]\\'"/]$/.test(tok)) canonical = tok;
    if (canonical === null) return { ok: false, reason: "unknown-token" };
    if (key !== null) return { ok: false, reason: "two-keys" };
    key = canonical;
  }
  if (key === null) return { ok: false, reason: "no-key" };
  if (!mods.some((m) => STRONG_MODIFIERS.has(m.toLowerCase()))) return { ok: false, reason: "no-modifier" };
  return { ok: true, accelerator: [...mods, key].join("+") };
}

export function isTrayClickAction(v: unknown): v is TrayClickAction {
  return v === "main" || v === "quick";
}

/** Human wording for the settings page / the boot notification. */
export function describeAccelerator(accelerator: string): string {
  return accelerator.replace(/CommandOrControl|CmdOrCtrl/gi, process.platform === "darwin" ? "⌘" : "Ctrl").replace(/Control/gi, "Ctrl");
}

export class ShellPrefsStore {
  private prefs: ShellPrefs = { ...DEFAULT_SHELL_PREFS };
  constructor(private readonly filePath: string) {}

  load(): ShellPrefs {
    try {
      if (fs.existsSync(this.filePath)) {
        const raw = JSON.parse(fs.readFileSync(this.filePath, "utf8")) as Partial<ShellPrefs>;
        this.prefs = sanitizePrefs(raw);
      }
    } catch {
      this.prefs = { ...DEFAULT_SHELL_PREFS };
    }
    return { ...this.prefs };
  }

  current(): ShellPrefs {
    return { ...this.prefs };
  }

  update(patch: Partial<ShellPrefs>): ShellPrefs {
    this.prefs = sanitizePrefs({ ...this.prefs, ...patch });
    try {
      fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
      const tmp = `${this.filePath}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(this.prefs, null, 2));
      fs.renameSync(tmp, this.filePath);
    } catch {
      /* best-effort — a preference is not worth a crash */
    }
    return { ...this.prefs };
  }
}

/** A hand-edited or stale file cannot smuggle an invalid value: every field falls back to its default. */
export function sanitizePrefs(raw: Partial<ShellPrefs> | null | undefined): ShellPrefs {
  const hk = validateAccelerator(typeof raw?.quickHotkey === "string" ? raw.quickHotkey : null);
  return {
    quickHotkey: hk.ok ? hk.accelerator : DEFAULT_QUICK_HOTKEY,
    trayClick: isTrayClickAction(raw?.trayClick) ? raw.trayClick : DEFAULT_SHELL_PREFS.trayClick,
  };
}
