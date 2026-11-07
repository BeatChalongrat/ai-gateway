// FIX34 Part D — the pure halves of the composer/keyboard behaviours, so
// each rule is unit-tested without mounting the composer:
//
//   * ↑ edits the last message ONLY when the composer is empty (Claude's
//     binding; otherwise the arrow keeps moving the caret);
//   * Ctrl+Shift+C copies the last response;
//   * FIX89 — how tall the composer may grow before it scrolls inside
//     itself.
//
// The composer and the message bubble talk through two window events
// (below) rather than new store state: the composer knows the keystroke,
// the bubble owns its edit box.
//
// FIX89 Part A — paste-as-file is GONE. FIX34 copied ChatGPT, which turns a
// paste of ~5,000 characters into a .txt attachment; the Claude chat app the
// owner compares against keeps it as text in a composer that has room. The
// threshold, `shouldPasteAsFile`, `pastedTextFile` and the undo toast were
// deleted rather than raised — a number nobody reaches leaves dead code and a
// dead toast behind. Pasting an IMAGE is untouched, and attaching a .txt by
// hand still works: an automatic conversion was removed, not a capability.

import type { Message } from "@/lib/types";

/** FIX89 Part A — the composer grows to about half the window and then
 *  scrolls inside itself (the Claude chat app's proportion, which the owner
 *  compared against). Half of the window the composer is actually IN, not a
 *  magic pixel count: the same rule is then right in the 900 px main window,
 *  the ~460 px quick-ask window, and a phone whose visible area shrinks when
 *  the on-screen keyboard opens. */
export const COMPOSER_VIEWPORT_FRACTION = 0.5;

/** The cap before FIX89 — 6 lines × ~24 px on desktop, 4 on phones. Kept as
 *  the FLOOR so the rule can only ever give the composer MORE room than it
 *  had: in a short window (the quick-ask one, or a phone with the keyboard
 *  up) half the viewport is smaller than this, and shrinking the box was
 *  never what was asked for. */
export const MIN_TEXTAREA_HEIGHT_DESKTOP = 180;
export const MIN_TEXTAREA_HEIGHT_MOBILE = 120;

/**
 * How tall the text area may grow.
 *
 * The BOX — text area plus its own chrome (the toolbar row, the padding, any
 * attachment/folder chips) — is what the reader sees taking up the window, so
 * the fraction applies to the box and the chrome is subtracted from the text
 * area's share. Measuring the chrome instead of assuming it means a composer
 * that grows a second row of chips still obeys the same rule.
 *
 * @param viewportHeight the VISIBLE height (visualViewport when there is one,
 *   so an open soft keyboard counts against the window, not against nothing)
 * @param chromeHeight   composer box height − text area height, measured live
 * @param isMobile       only chooses which floor applies
 */
export function maxTextareaHeight(
  viewportHeight: number,
  chromeHeight: number,
  isMobile: boolean,
): number {
  const floor = isMobile ? MIN_TEXTAREA_HEIGHT_MOBILE : MIN_TEXTAREA_HEIGHT_DESKTOP;
  if (!Number.isFinite(viewportHeight) || viewportHeight <= 0) return floor;
  const chrome = Number.isFinite(chromeHeight) && chromeHeight > 0 ? chromeHeight : 0;
  const share = Math.round(viewportHeight * COMPOSER_VIEWPORT_FRACTION) - chrome;
  return Math.max(floor, share);
}

/** Fired by the composer when ↑ is pressed in an empty box; detail = { messageId }. */
export const EDIT_MESSAGE_EVENT = "sran:edit-message";
/** Fired by the global shortcut (Ctrl+Shift+V); the composer toggles the mic. */
export const VOICE_TOGGLE_EVENT = "sran:voice-toggle";

/** FIX84 row 1 — dispatched on ``window`` by Cmd/Ctrl+K (useKeyboardShortcuts)
 *  when the sidebar's search input is not mounted; the Sidebar listens and
 *  expands its search row (whose input autofocuses). Lives here, next to the
 *  other composer/sidebar events, so tests that mock the hook module keep
 *  working. */
export const CONV_SEARCH_EVENT = "sran:conversation-search";

export interface ArrowUpContext {
  key: string;
  text: string;
  slashOpen: boolean;
  hasPendingFiles: boolean;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
}

/** ↑ with nothing typed (and no menu open) = edit the last message. */
export function shouldEditLastOnArrowUp(c: ArrowUpContext): boolean {
  if (c.key !== "ArrowUp") return false;
  if (c.shiftKey || c.altKey || c.ctrlKey || c.metaKey) return false;
  if (c.slashOpen || c.hasPendingFiles) return false;
  return c.text === "";
}

/** The user message ↑ edits: the newest user row that is still a live branch. */
export function lastEditableUserMessageId(messages: readonly Message[] | undefined): string | null {
  if (!messages) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "user" && m.status !== "regenerated" && !m.id.startsWith("tmp-")) return m.id;
  }
  return null;
}

/** The text Ctrl+Shift+C copies: the newest finished assistant reply. */
export function lastAssistantResponse(messages: readonly Message[] | undefined): string | null {
  if (!messages) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "assistant" && !m.isStreaming && m.content.trim() !== "") return m.content;
  }
  return null;
}
