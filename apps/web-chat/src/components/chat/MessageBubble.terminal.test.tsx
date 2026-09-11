// FIX107 Part A1 — a terminal table pasted into a user message.
//
// jsdom has no layout, so the width itself was measured live (the bubble went
// 1,637 px wide in a 1,280 px window). What is pinned here is the structure
// that fixes it: the bubble is capped at its column, the box-drawn lines are
// one terminal block, and a Thai message renders exactly as before.
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/hooks/useVoiceOutput", () => ({
  useVoiceOutput: () => ({ speak: vi.fn(), stop: vi.fn(), speakingId: null }),
}));

import type { Message } from "@/lib/types";
import { useI18nStore } from "@/lib/i18n";

import { MessageBubble } from "./MessageBubble";

const msg = (role: Message["role"], content: string): Message => ({ id: "m1", role, content, status: "completed", created_at: "2026-08-01T10:00:00Z" });

const TABLE = ["┌────┬─────────┐", "│ id │ service │", "├────┼─────────┤", "│ 1  │ gateway │", "└────┴─────────┘"].join("\n");

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("a terminal table in a user message", () => {
  it("renders the box-drawn lines as one terminal block inside a bubble capped at its column", () => {
    render(<MessageBubble message={msg("user", `ช่วยดูผลนี้หน่อยครับ\n${TABLE}\n(1 row)`)} convId="c1" />);
    const blocks = document.querySelectorAll("[data-testid='user-msg-terminal']");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]!.textContent).toBe(TABLE);
    expect(blocks[0]!.className).toBe("user-msg-terminal");
    const bubble = document.querySelector(".bg-bubble-user")!;
    expect(bubble.className.split(/\s+/)).toContain("max-w-full");
    expect(document.querySelector(".user-msg-text")!.textContent).toBe(`ช่วยดูผลนี้หน่อยครับ${TABLE}(1 row)`);
  });

  it("a Thai message has no terminal block and its text node is the message itself", () => {
    const thai = "ช่วยอ่าน log ด้านล่างแล้วบอกหน่อยครับว่าบริการไหนมีปัญหา ".repeat(20);
    render(<MessageBubble message={msg("user", thai)} convId="c1" />);
    expect(document.querySelector("[data-testid='user-msg-terminal']")).toBeNull();
    const span = document.querySelector(".user-msg-text")!;
    expect(span.childNodes).toHaveLength(1);
    expect(span.firstChild!.nodeType).toBe(Node.TEXT_NODE);
    expect(span.textContent).toBe(thai);
  });
});
