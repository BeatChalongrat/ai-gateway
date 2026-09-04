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
  it("shows the strip when memory is off for the chat", () => {
    seed(conv({ id: "c1", memory_enabled: false }));
    render(<TemporaryChatBanner conversationId="c1" />);
    expect(screen.getByText(dictionaries.en["memory.banner.title"])).toBeTruthy();
    expect(screen.getByText(dictionaries.en["memory.banner.detail"])).toBeTruthy();
  });

  // FIX56.1 1.3 — the strip has to say BOTH things. "Temporary" reads to
  // most people as "this conversation disappears"; the transcript is in
  // fact kept, and only the remembering is off. A strip that mentions
  // only the memory half lets the product imply a deletion it never does.
  it("says the chat is kept AND that nothing new is remembered, in both languages", () => {
    for (const [locale, dict] of [
      ["en", dictionaries.en],
      ["th", dictionaries.th],
    ] as const) {
      useI18nStore.setState({ locale });
      seed(conv({ id: "c1", memory_enabled: false }));
      render(<TemporaryChatBanner conversationId="c1" />);
      const detail = screen.getByText(dict["memory.banner.detail"]).textContent ?? "";
      const kept = locale === "en" ? /saved to your history/i : /บันทึกในประวัติ/;
      const notRemembered = locale === "en" ? /nothing new is remembered/i : /ไม่มีการจดจำอะไรใหม่/;
      const stillUses = locale === "en" ? /still used/i : /ยังใช้ความจำเดิม/;
      expect(kept.test(detail), `${locale}: says the chat is kept`).toBe(true);
      expect(notRemembered.test(detail), `${locale}: says nothing new is remembered`).toBe(true);
      expect(stillUses.test(detail), `${locale}: says saved memories are still used`).toBe(true);
      cleanup();
    }
  });

  // The strict wording exists for a deployment that set
  // TEMPORARY_CHAT_READS_MEMORY=false. It must differ from the default
  // in exactly the "are still used" clause — if the two ever converge,
  // one of the two deployments is being told the wrong thing.
  it("keeps a distinct sentence for the read-nothing deployment", () => {
    for (const dict of [dictionaries.en, dictionaries.th]) {
      expect(dict["memory.banner.detailStrict"]).not.toBe(dict["memory.banner.detail"]);
      expect(dict["memory.banner.detailStrict"]).toBeTruthy();
    }
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

  it("offers a way back — the strip is not a dead end", () => {
    seed(conv({ id: "c1", memory_enabled: false }));
    render(<TemporaryChatBanner conversationId="c1" />);
    expect(
      screen.getByRole("button", { name: dictionaries.en["memory.banner.turnOn"] }),
    ).toBeTruthy();
  });

  it("is localised in Thai too", () => {
    useI18nStore.setState({ locale: "th" });
    seed(conv({ id: "c1", memory_enabled: false }));
    render(<TemporaryChatBanner conversationId="c1" />);
    expect(screen.getByText(dictionaries.th["memory.banner.title"])).toBeTruthy();
  });
});
