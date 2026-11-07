// FIX34 Part D — ↑-only-when-empty, copy-last lookup.
// FIX89 Part A — how tall the composer may grow (paste-as-file is gone).
import { describe, expect, it } from "vitest";

import type { Message } from "@/lib/types";

import {
  COMPOSER_VIEWPORT_FRACTION,
  MIN_TEXTAREA_HEIGHT_DESKTOP,
  MIN_TEXTAREA_HEIGHT_MOBILE,
  lastAssistantResponse,
  lastEditableUserMessageId,
  maxTextareaHeight,
  shouldEditLastOnArrowUp,
} from "./composerShortcuts";

function msg(id: string, role: Message["role"], content: string, extra?: Partial<Message>): Message {
  return { id, role, content, status: "complete", created_at: "2026-08-31T00:00:00Z", ...extra } as unknown as Message;
}

// FIX89 Part A — the composer's height is a RELATIONSHIP: about half of the
// window it is in, minus its own chrome, and never less than the cap it had
// before (so the rule can only give it more room, never less).
describe("how tall the composer may grow", () => {
  const CHROME = 100; // toolbar row + padding, as measured in the real box

  it("gives the box about half the window, whatever the window is", () => {
    // the desktop main window: 900 → a 450 px box, of which the text area is
    // 350 (it was 180 — a keyhole for a long paste)
    expect(maxTextareaHeight(900, CHROME, false)).toBe(350);
    expect(maxTextareaHeight(900, CHROME, false) + CHROME).toBe(450);
    // a tall monitor
    expect(maxTextareaHeight(1440, CHROME, false)).toBe(620);
    expect(COMPOSER_VIEWPORT_FRACTION).toBe(0.5);
  });

  it("never shrinks below the cap it had: the ~460 px quick window and a phone with the keyboard up keep theirs", () => {
    // quick-ask window (680×460): half is 230, minus chrome = 130 — smaller
    // than the old cap, so the old cap stands.
    expect(maxTextareaHeight(460, CHROME, false)).toBe(MIN_TEXTAREA_HEIGHT_DESKTOP);
    // a phone whose visible area is 300 px because the keyboard is up
    expect(maxTextareaHeight(300, CHROME, true)).toBe(MIN_TEXTAREA_HEIGHT_MOBILE);
    // the same phone with the keyboard down has room to grow
    expect(maxTextareaHeight(740, CHROME, true)).toBe(270);
  });

  it("the chrome comes out of the text area's share, so the BOX stays at half", () => {
    // a second row of attachment chips grows the chrome; the box does not
    // grow past half the window, the text area yields the room
    expect(maxTextareaHeight(900, 100, false)).toBe(350);
    expect(maxTextareaHeight(900, 160, false)).toBe(290);
    expect(maxTextareaHeight(900, 160, false) + 160).toBe(450);
  });

  it("survives a viewport it cannot measure", () => {
    expect(maxTextareaHeight(0, CHROME, false)).toBe(MIN_TEXTAREA_HEIGHT_DESKTOP);
    expect(maxTextareaHeight(Number.NaN, CHROME, true)).toBe(MIN_TEXTAREA_HEIGHT_MOBILE);
    expect(maxTextareaHeight(900, Number.NaN, false)).toBe(450);
  });
});

describe("↑ edits the last message only when the composer is empty", () => {
  const base = { key: "ArrowUp", text: "", slashOpen: false, hasPendingFiles: false, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false };
  it("yes on a bare ↑ in an empty box", () => {
    expect(shouldEditLastOnArrowUp(base)).toBe(true);
  });
  it("no when text is present (the arrow moves the caret), a menu is open, files are staged, or a modifier is held", () => {
    expect(shouldEditLastOnArrowUp({ ...base, text: "typing" })).toBe(false);
    expect(shouldEditLastOnArrowUp({ ...base, slashOpen: true })).toBe(false);
    expect(shouldEditLastOnArrowUp({ ...base, hasPendingFiles: true })).toBe(false);
    expect(shouldEditLastOnArrowUp({ ...base, shiftKey: true })).toBe(false);
    expect(shouldEditLastOnArrowUp({ ...base, ctrlKey: true })).toBe(false);
    expect(shouldEditLastOnArrowUp({ ...base, key: "ArrowDown" })).toBe(false);
  });
});

describe("message lookups", () => {
  const thread = [
    msg("u1", "user", "first"),
    msg("a1", "assistant", "reply one"),
    msg("u2", "user", "second"),
    msg("a2", "assistant", "reply two"),
  ];
  it("finds the newest live user message and skips optimistic/regenerated rows", () => {
    expect(lastEditableUserMessageId(thread)).toBe("u2");
    expect(lastEditableUserMessageId([...thread, msg("tmp-user-1", "user", "pending")])).toBe("u2");
    expect(lastEditableUserMessageId([msg("u9", "user", "x", { status: "regenerated" } as never)])).toBeNull();
    expect(lastEditableUserMessageId(undefined)).toBeNull();
    expect(lastEditableUserMessageId([])).toBeNull();
  });
  it("copies the newest finished assistant reply", () => {
    expect(lastAssistantResponse(thread)).toBe("reply two");
    expect(lastAssistantResponse([...thread, msg("a3", "assistant", "partial", { isStreaming: true })])).toBe("reply two");
    expect(lastAssistantResponse([msg("u1", "user", "only me")])).toBeNull();
    expect(lastAssistantResponse(undefined)).toBeNull();
  });
});
