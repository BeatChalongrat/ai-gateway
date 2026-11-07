// The "not being remembered" indicator has to be visible whenever the
// chat is in that state — that is the whole promise the switch makes.
//
// The header chip alone did not keep it: its row is `hidden md:flex`,
// so on a narrow window the only signal vanished. These pins cover the
// strip that replaced that gap, including the case that matters most —
// a conversation payload with no memory_enabled field at all must be
// read as memory ON, never as "private". Getting that default backwards
// would tell a user their chat is not being saved while it is.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { useChatStore } from "@/stores/chatStore";
import type { Conversation } from "@/lib/types";

import { TemporaryChatBanner } from "./TemporaryChatBanner";

function conv(over: Partial<Conversation>): Conversation {
  return {
    id: "c1",
    title: "t",
    model_id: "m",
    message_count: 0,
    created_at: "2026-09-04T00:00:00Z",
    updated_at: "2026-09-04T00:00:00Z",
    ...over,
  } as Conversation;
}

function seed(c: Conversation) {
  useChatStore.setState({ conversations: [c] });
}

beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(cleanup);

describe("TemporaryChatBanner", () => {
  // FIX130 A2 — two states, two sentences. Memory switched off on an
  // ordinary chat and a temporary chat both neither USE nor SAVE memory now
  // (the FIX56.1 "your saved memories are still used" read is gone, server
  // and strip together); what differs is whether memory can come back on.
  it("memory off on an ordinary chat: says it neither uses nor saves memory, and offers to turn it back on", () => {
    seed(conv({ id: "c1", memory_enabled: false }));
    render(<TemporaryChatBanner conversationId="c1" />);
    expect(screen.getByText(dictionaries.en["memory.banner.offTitle"])).toBeTruthy();
    const detail = screen.getByText(dictionaries.en["memory.banner.offDetail"]).textContent ?? "";
    expect(detail).toMatch(/neither uses your saved memories nor saves/i);
    expect(detail).toMatch(/stays in your history/i);
    expect(screen.getByRole("button", { name: dictionaries.en["memory.banner.turnOn"] })).toBeTruthy();
  });

  it("a temporary chat: neither uses nor saves memory, cannot be switched on, kept in history without an AI-made name", () => {
    seed(conv({ id: "c1", memory_enabled: false, is_temporary: true }));
    render(<TemporaryChatBanner conversationId="c1" />);
    expect(screen.getByText(dictionaries.en["memory.banner.title"])).toBeTruthy();
    const detail = screen.getByText(dictionaries.en["memory.banner.detail"]).textContent ?? "";
    expect(detail).toMatch(/neither uses nor saves memory/i);
    expect(detail).toMatch(/can't be turned on/i);
    expect(detail).toMatch(/stays in your history/i);
    expect(detail).toMatch(/first words/i);
    expect(screen.queryByRole("button", { name: dictionaries.en["memory.banner.turnOn"] })).toBeNull();
  });

  it("is temporary even if its memory flag says on (a row switched on before the server refused that)", () => {
    seed(conv({ id: "c1", memory_enabled: true, is_temporary: true }));
    render(<TemporaryChatBanner conversationId="c1" />);
    expect(screen.getByText(dictionaries.en["memory.banner.title"])).toBeTruthy();
  });

  it("the Thai sentences say the same things, and no sentence still promises that saved memories are used", () => {
    const d = dictionaries.th;
    expect(d["memory.banner.offDetail"]).toMatch(/ไม่ใช้/);
    expect(d["memory.banner.offDetail"]).toMatch(/ไม่บันทึก/);
    expect(d["memory.banner.offDetail"]).toMatch(/ประวัติ/);
    expect(d["memory.banner.detail"]).toMatch(/ไม่ใช้และไม่บันทึกความจำ/);
    expect(d["memory.banner.detail"]).toMatch(/เปิดความจำ.*ไม่ได้/);
    expect(d["memory.banner.detail"]).toMatch(/ประวัติ/);
    for (const dict of [dictionaries.en, dictionaries.th]) {
      for (const key of ["memory.banner.detail", "memory.banner.offDetail", "memory.popover.offHint", "memory.temporary.description"] as const) {
        expect(dict[key]).not.toMatch(/still used|ยังใช้ความจำเดิม/i);
      }
    }
    expect("memory.banner.detailStrict" in dictionaries.en).toBe(false);
  });

  it("renders nothing when memory is on", () => {
    seed(conv({ id: "c1", memory_enabled: true }));
    const { container } = render(<TemporaryChatBanner conversationId="c1" />);
    expect(container.textContent).toBe("");
  });

  it("treats a missing memory_enabled field as ON, not as a temporary chat", () => {
    // An older payload, or a server build that does not send the column.
    // Failing open the other way would claim privacy that is not there.
    seed(conv({ id: "c1" }));
    const { container } = render(<TemporaryChatBanner conversationId="c1" />);
    expect(container.textContent).toBe("");
  });

  it("renders nothing for a conversation the store has not loaded", () => {
    seed(conv({ id: "other", memory_enabled: false }));
    const { container } = render(<TemporaryChatBanner conversationId="c1" />);
    expect(container.textContent).toBe("");
  });

  it("carries no breakpoint class, so a narrow window still shows it", () => {
    // The bug this whole component exists for: the header row that holds
    // MemoryIndicator is `hidden md:flex`, so below md the state was
    // invisible. A `hidden`/`md:` class creeping in here would restore
    // exactly that hole.
    seed(conv({ id: "c1", memory_enabled: false }));
    const { container } = render(<TemporaryChatBanner conversationId="c1" />);
    const classes = Array.from(container.querySelectorAll("*"))
      .flatMap((el) => Array.from(el.classList));
    expect(classes).not.toContain("hidden");
    expect(classes.filter((c) => /^(sm|md|lg|xl):/.test(c))).toEqual([]);
  });

  it("is localised in Thai too", () => {
    useI18nStore.setState({ locale: "th" });
    seed(conv({ id: "c1", memory_enabled: false, is_temporary: true }));
    render(<TemporaryChatBanner conversationId="c1" />);
    expect(screen.getByText(dictionaries.th["memory.banner.title"])).toBeTruthy();
    cleanup();
    seed(conv({ id: "c1", memory_enabled: false }));
    render(<TemporaryChatBanner conversationId="c1" />);
    expect(screen.getByText(dictionaries.th["memory.banner.offTitle"])).toBeTruthy();
  });
});
