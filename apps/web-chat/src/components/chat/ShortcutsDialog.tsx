"use client";

// Phase 10 Task 2 — keyboard-shortcuts cheatsheet.
//
// Opened with "?" or Cmd/Ctrl+/ (see useKeyboardShortcuts) and from the
// /shortcuts quick action. The list is derived from SHORTCUTS below,
// which is the SINGLE source of truth shared with the hook — a binding
// that exists in one and not the other is a bug this arrangement makes
// obvious.
//
// Key rendering is platform-aware: ⌘ on Mac, Ctrl elsewhere, decided at
// render time (not build time) because the same bundle serves both.

import { useEffect, useRef } from "react";

import { useFocusTrap } from "@/hooks/useFocusTrap";

import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";

export interface ShortcutRow {
  /** Dictionary key for what the shortcut does. */
  labelKey: DictKey;
  /** Key tokens. "Mod" renders as ⌘ on Mac and Ctrl elsewhere. */
  keys: string[];
  /** FIX34 — a second binding for the same action (Claude's key next to ours). */
  altKeys?: string[];
}

/** Every binding the web app answers to (hooks/useKeyboardShortcuts.ts +
 *  the composer's own ↑ / Enter handling). Kept next to the dialog so the
 *  cheatsheet can never drift from what is actually bound. FIX34 Part D
 *  added the rows Claude has that we lacked. */
export const SHORTCUTS: ShortcutRow[] = [
  { labelKey: "shortcuts.newChat", keys: ["Mod", "Shift", "N"], altKeys: ["Mod", "Shift", "O"] },
  { labelKey: "shortcuts.toggleSidebar", keys: ["Mod", "Shift", "S"] },
  { labelKey: "shortcuts.searchChats", keys: ["Mod", "K"] },
  { labelKey: "shortcuts.focusInput", keys: ["Mod", "Shift", ";"] },
  { labelKey: "shortcuts.copyLast", keys: ["Mod", "Shift", "C"] },
  { labelKey: "shortcuts.editLast", keys: ["Up"] },
  { labelKey: "shortcuts.stop", keys: ["Esc"] },
  { labelKey: "shortcuts.voice", keys: ["Mod", "Shift", "V"] },
  { labelKey: "shortcuts.deleteConversation", keys: ["Mod", "Shift", "Backspace"] },
  { labelKey: "shortcuts.settings", keys: ["Mod", ","] },
  { labelKey: "shortcuts.cheatsheet", keys: ["Mod", "/"] },
  { labelKey: "shortcuts.send", keys: ["Enter"] },
  { labelKey: "shortcuts.newline", keys: ["Shift", "Enter"] },
  { labelKey: "shortcuts.slash", keys: ["/"] },
];

export function isMacPlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);
}

export function renderKey(token: string, mac: boolean): string {
  if (token === "Mod") return mac ? "⌘" : "Ctrl";
  if (token === "Shift") return mac ? "⇧" : "Shift";
  if (token === "Enter") return mac ? "↵" : "Enter";
  if (token === "Backspace") return mac ? "⌫" : "Backspace";
  if (token === "Up") return "↑";
  return token;
}

export function ShortcutsDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, open);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const mac = isMacPlatform();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      data-testid="shortcuts-backdrop"
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t("shortcuts.title")}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-medium text-text-primary">
            {t("shortcuts.title")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("shortcuts.close")}
            className="focus-ring rounded p-1 text-text-muted transition-colors hover:text-text-primary"
          >
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M18 6L6 18M6 6l12 12"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
        <ul className="flex flex-col divide-y divide-border">
          {SHORTCUTS.map((row) => (
            <li
              key={row.labelKey}
              className="flex items-center justify-between gap-4 px-4 py-2.5"
            >
              <span className="text-sm text-text-secondary">{t(row.labelKey)}</span>
              <span className="flex shrink-0 items-center gap-1">
                {row.keys.map((k) => (
                  <kbd
                    key={k}
                    className="rounded border border-border bg-bg-base px-1.5 py-0.5 font-mono text-[11px] text-text-primary"
                  >
                    {renderKey(k, mac)}
                  </kbd>
                ))}
                {row.altKeys ? (
                  <>
                    <span className="px-0.5 text-[11px] text-text-muted">/</span>
                    {row.altKeys.map((k) => (
                      <kbd
                        key={`alt-${k}`}
                        className="rounded border border-border bg-bg-base px-1.5 py-0.5 font-mono text-[11px] text-text-primary"
                      >
                        {renderKey(k, mac)}
                      </kbd>
                    ))}
                  </>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
