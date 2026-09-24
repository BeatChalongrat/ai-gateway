// FIX132 A — no "used N memories" badge on answers. FIX130 A3a put a chip
// "ใช้ความจำ N รายการ" (with a popover listing the memories) under every
// answer whose prompt carried saved memories; the owner decided memory works
// in the background: ChatGPT only says "Memory updated" when it SAVES, and
// Claude shows nothing per answer. So the used-chip is gone everywhere —
// on a live turn, while streaming, and on old chats after a reload (their
// persisted saved_memory context_sources must not bring it back). The server
// still records which memories went into the turn; the page just does not
// badge it.
//
// FIX130 A3b stays: an answer where a save happened says so once —
// "บันทึกความจำแล้ว" (saved) or "เสนอความจำใหม่ — รอยืนยัน" (suggested) —
// which IS the quiet "Memory updated" notice.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ContextSource, Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

function msg(over: Partial<Message> & Record<string, unknown>): Message {
  return { id: "a1", role: "assistant", content: "สวัสดีคุณบีท", status: "completed", created_at: "2026-09-23T00:00:00Z", ...over } as Message;
}
const src = (over: Partial<ContextSource>): ContextSource =>
  ({ id: "s", source_type: "saved_memory", display_label: "", created_at: "2026-09-23T00:00:00Z", ...over }) as ContextSource;
const th = dictionaries.th;
// The old chip's wording, spelled out so the test does not depend on dict
// keys that no longer exist.
const USED_WORDS = /ใช้ความจำ\s*\d+\s*รายการ|Used \d+ memor/;

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

function expectNoUsedChip(container: HTMLElement) {
  expect(screen.queryByTestId("memory-used-chip")).toBeNull();
  expect(screen.queryByTestId("memory-used-popover")).toBeNull();
  expect(container.textContent ?? "").not.toMatch(USED_WORDS);
}

describe("FIX132 A — no 'used memory' chip on answers", () => {
  it("a live turn whose memory_used frame reached the row shows nothing", () => {
    const { container } = render(
      <MessageBubble
        convId="c1"
        message={msg({ memory_used: { count: 2, items: [{ id: "m1", label: "ชื่อเล่นว่าบีท" }, { id: "m2", label: "อยู่เชียงใหม่" }] } })}
      />,
    );
    expectNoUsedChip(container);
    expect(screen.queryByText("ชื่อเล่นว่าบีท")).toBeNull();
    expect(screen.queryByTestId("memory-markers")).toBeNull();
  });

  it("an old chat after a reload: persisted saved_memory context_sources do not bring it back", () => {
    const { container } = render(
      <MessageBubble
        convId="c1"
        message={msg({
          context_sources: [
            src({ source_id: "m1", display_label: "ชื่อเล่นว่าบีท" }),
            src({ source_id: "m2", display_label: "[deleted]" }),
          ],
        })}
      />,
    );
    expectNoUsedChip(container);
    expect(screen.queryByTestId("memory-markers")).toBeNull();
  });

  it("nothing appears while the answer streams either", () => {
    const streaming = msg({ status: "streaming", isStreaming: true, content: "" });
    const { container, rerender } = render(<MessageBubble convId="c1" message={streaming} />);
    rerender(<MessageBubble convId="c1" message={{ ...streaming, memory_used: { count: 1, items: [{ id: "m1", label: "x" }] } } as Message} />);
    expectNoUsedChip(container);
  });

  it("English locale: no 'Used N memories' either", () => {
    useI18nStore.setState({ locale: "en" });
    const { container } = render(<MessageBubble convId="c1" message={msg({ memory_used: { count: 3, items: [] }, context_sources: [src({ source_id: "m1", display_label: "x" })] })} />);
    expectNoUsedChip(container);
  });
});

describe("FIX130 A3b kept — 'memory saved / suggested' marker on the answer where a save happened", () => {
  it("an explicit save says บันทึกความจำแล้ว; a suggestion says it waits for the user", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({
          memory_saved: [
            { id: "m1", label: "ชื่อบีท", status: "active" },
            { id: "m2", label: "แพ้กุ้ง", status: "pending_confirmation" },
          ],
        })}
      />,
    );
    const saved = screen.getByTestId("memory-saved-chip");
    expect(saved.textContent).toContain(th["memory.saved.active"]);
    const pending = screen.getByTestId("memory-suggested-chip");
    expect(pending.textContent).toContain(th["memory.saved.pending"]);
    expect(pending.closest("a")?.getAttribute("href") ?? pending.querySelector("a")?.getAttribute("href")).toBe("/chat/memory");
  });

  it("after a reload: derived from memory_saved context sources", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({ context_sources: [src({ source_type: "memory_saved", source_id: "m2", display_label: "แพ้กุ้ง", metadata: { status: "pending_confirmation" } })] })}
      />,
    );
    expect(screen.getByTestId("memory-suggested-chip")).toBeTruthy();
    expect(screen.queryByTestId("memory-saved-chip")).toBeNull();
  });

  it("an answer that both used and saved memory shows only the saved notice", () => {
    const { container } = render(
      <MessageBubble
        convId="c1"
        message={msg({
          memory_used: { count: 1, items: [{ id: "m9", label: "อยู่เชียงใหม่" }] },
          context_sources: [
            src({ source_id: "m9", display_label: "อยู่เชียงใหม่" }),
            src({ source_type: "memory_saved", source_id: "m1", display_label: "ชื่อบีท", metadata: { status: "active" } }),
          ],
        })}
      />,
    );
    expect(screen.getByTestId("memory-saved-chip").textContent).toContain(th["memory.saved.active"]);
    expectNoUsedChip(container);
  });

  it("a saved memory deleted since is named as deleted, not '[deleted]'", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({ context_sources: [src({ source_type: "memory_saved", source_id: "m1", display_label: "[deleted]", metadata: { status: "active" } })] })}
      />,
    );
    const chip = screen.getByTestId("memory-saved-chip");
    expect(chip.getAttribute("title")).toBe(th["memory.used.deletedItem"]);
  });

  it("the memo re-renders when a memory_saved frame lands alone", () => {
    const base = msg({ status: "streaming", isStreaming: true });
    const { rerender } = render(<MessageBubble convId="c1" message={base} />);
    rerender(<MessageBubble convId="c1" message={{ ...base, memory_saved: [{ id: "m1", label: "x", status: "active" }] }} />);
    expect(screen.getByTestId("memory-saved-chip")).toBeTruthy();
  });
});
