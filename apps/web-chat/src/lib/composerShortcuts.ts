// FIX34 Part D — the pure halves of the new composer/keyboard behaviours, so
// each rule is unit-tested without mounting the composer:
//
//   * paste-as-file — ChatGPT turns a long paste into an attachment instead
//     of a wall of text in the box (its threshold is around 5,000
//     characters); same here, undoable;
//   * ↑ edits the last message ONLY when the composer is empty (Claude's
//     binding; otherwise the arrow keeps moving the caret);
//   * Ctrl+Shift+C copies the last response.
//
// The composer and the message bubble talk through two window events
// (below) rather than new store state: the composer knows the keystroke,
// the bubble owns its edit box.

import type { Message } from "@/lib/types";

/** ChatGPT converts a paste of roughly this length into a file. */
export const PASTE_AS_FILE_THRESHOLD = 5_000;

/** Fired by the composer when ↑ is pressed in an empty box; detail = { messageId }. */
export const EDIT_MESSAGE_EVENT = "sran:edit-message";
/** Fired by the global shortcut (Ctrl+Shift+V); the composer toggles the mic. */
export const VOICE_TOGGLE_EVENT = "sran:voice-toggle";

export function shouldPasteAsFile(text: string, hasImages: boolean): boolean {
  if (hasImages) return false;
  return text.length >= PASTE_AS_FILE_THRESHOLD;
}

/** The attachment a long paste becomes. Plain text, so chat-edge reads it verbatim. */
export function pastedTextFile(text: string, seq: number, now: Date = new Date()): File {
  const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
  const name = `pasted-text-${stamp}${seq > 1 ? `-${seq}` : ""}.txt`;
  return new File([text], name, { type: "text/plain" });
}

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
