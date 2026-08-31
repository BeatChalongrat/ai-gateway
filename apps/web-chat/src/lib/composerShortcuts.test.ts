// FIX34 Part D — paste threshold, ↑-only-when-empty, copy-last lookup.
import { describe, expect, it } from "vitest";

import type { Message } from "@/lib/types";

import {
  PASTE_AS_FILE_THRESHOLD,
  lastAssistantResponse,
  lastEditableUserMessageId,
  pastedTextFile,
  shouldEditLastOnArrowUp,
  shouldPasteAsFile,
} from "./composerShortcuts";

function msg(id: string, role: Message["role"], content: string, extra?: Partial<Message>): Message {
  return { id, role, content, status: "complete", created_at: "2026-08-31T00:00:00Z", ...extra } as Message;
}

describe("paste-as-file", () => {
  it("triggers at the ChatGPT-sized threshold and never when an image is on the clipboard", () => {
    expect(PASTE_AS_FILE_THRESHOLD).toBe(5_000);
    expect(shouldPasteAsFile("a".repeat(4_999), false)).toBe(false);
    expect(shouldPasteAsFile("a".repeat(5_000), false)).toBe(true);
    expect(shouldPasteAsFile("a".repeat(50_000), true)).toBe(false);
  });
  it("makes a plain-text file with a dated, sequenced name", () => {
    const f = pastedTextFile("hello", 1, new Date(2026, 7, 31));
    expect(f.name).toBe("pasted-text-20260831.txt");
    expect(f.type).toBe("text/plain");
    expect(f.size).toBe(5);
    expect(pastedTextFile("x", 3, new Date(2026, 7, 31)).name).toBe("pasted-text-20260831-3.txt");
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
