// FIX34 Part A — turning a captured keydown into an Electron accelerator
// string, and rendering one for humans. Pure; unit-tested.
//
// Claude on Windows lets the user "set a custom keyboard shortcut" for the
// quick entry (support.claude.com, Quick entry); this is the capture half of
// that setting. The host validates the result again (shell-prefs.ts) and
// refuses a shortcut without a strong modifier, so this only has to produce
// a well-formed string — it never decides what is allowed.

export interface CapturedKey {
  key: string;
  code?: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
}

const NAMED: Record<string, string> = {
  " ": "Space",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  Escape: "Escape",
  Enter: "Enter",
  Tab: "Tab",
  Backspace: "Backspace",
  Delete: "Delete",
  Insert: "Insert",
  Home: "Home",
  End: "End",
  PageUp: "PageUp",
  PageDown: "PageDown",
  "+": "Plus",
};

const MODIFIER_KEYS = new Set(["Control", "Shift", "Alt", "Meta", "OS", "AltGraph", "CapsLock", "NumLock"]);

/**
 * Returns the accelerator for a captured key, or null while only modifiers
 * are held (the capture UI keeps waiting). Ctrl and ⌘ both map to
 * CommandOrControl so one saved value works on every platform.
 */
export function acceleratorFromKey(e: CapturedKey): string | null {
  if (MODIFIER_KEYS.has(e.key)) return null;
  const mods: string[] = [];
  if (e.ctrlKey || e.metaKey) mods.push("CommandOrControl");
  if (e.altKey) mods.push("Alt");
  if (e.shiftKey) mods.push("Shift");
  let key: string | null = null;
  // Prefer the physical key for letters/digits so a Thai keyboard layout
  // (which reports "ฟ" for the A key) still yields "A".
  const code = e.code ?? "";
  if (/^Key[A-Z]$/.test(code)) key = code.slice(3);
  else if (/^Digit[0-9]$/.test(code)) key = code.slice(5);
  else if (/^F([1-9]|1[0-9]|2[0-4])$/.test(e.key)) key = e.key;
  else if (NAMED[e.key]) key = NAMED[e.key];
  else if (/^[a-z0-9]$/i.test(e.key)) key = e.key.toUpperCase();
  else if (e.key.length === 1 && /[)!@#$%^&*(:;<>=,._?`~{}|\-\[\]\\'"/]/.test(e.key)) key = e.key;
  if (key === null) return null;
  return [...mods, key].join("+");
}

/** "CommandOrControl+Shift+A" → "Ctrl + Shift + A" (or ⌘ on a Mac). */
export function describeAccelerator(accelerator: string, mac = false): string {
  return accelerator
    .split("+")
    .map((tok) => {
      const low = tok.toLowerCase();
      if (low === "commandorcontrol" || low === "cmdorctrl") return mac ? "⌘" : "Ctrl";
      if (low === "control" || low === "ctrl") return "Ctrl";
      if (low === "command" || low === "cmd") return "⌘";
      if (low === "shift") return mac ? "⇧" : "Shift";
      if (low === "alt" || low === "option") return mac ? "⌥" : "Alt";
      return tok;
    })
    .join(" + ");
}
