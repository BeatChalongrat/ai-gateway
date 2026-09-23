// FIX130 A3a/A3b — the answer says when memory was used and when something
// was saved. NIGHT2: the only trace of memory on an answer was the Sources
// rail, which is switched off (SHOW_SOURCES_RAIL), so a user could not tell
// that "how did it know my name?" came from a saved memory. The chip is its
// own component, independent of that rail, and reads the live frame or the
// persisted context_sources the same way.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ContextSource, Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

function msg(over: Partial<Message>): Message {
  return { id: "a1", role: "assistant", content: "สวัสดีคุณบีท", status: "completed", created_at: "2026-09-23T00:00:00Z", ...over } as Message;
}
const src = (over: Partial<ContextSource>): ContextSource =>
  ({ id: "s", source_type: "saved_memory", display_label: "", created_at: "2026-09-23T00:00:00Z", ...over }) as ContextSource;
const th = dictionaries.th;

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("FIX130 — 'used memory' chip on the answer", () => {
  it("live: shows ใช้ความจำ N รายการ and lists the memories with a link to the Memory page", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({ memory_used: { count: 2, items: [{ id: "m1", label: "ชื่อเล่นว่าบีท" }, { id: "m2", label: "อยู่เชียงใหม่" }] } })}
      />,
    );
    const chip = screen.getByTestId("memory-used-chip");
    expect(chip.textContent).toContain(th["memory.used.many"].replace("{n}", "2"));
    expect(screen.queryByText("ชื่อเล่นว่าบีท")).toBeNull();
    fireEvent.click(chip);
    const pop = screen.getByTestId("memory-used-popover");
    expect(pop.textContent).toContain("ชื่อเล่นว่าบีท");
    expect(pop.textContent).toContain("อยู่เชียงใหม่");
    const link = pop.querySelector("a[href='/chat/memory']");
    expect(link?.textContent).toBe(th["memory.used.manage"]);
  });

  it("after a reload: derived from the saved_memory context sources", () => {
    render(<MessageBubble convId="c1" message={msg({ context_sources: [src({ source_id: "m1", display_label: "ชื่อเล่นว่าบีท" })] })} />);
    expect(screen.getByTestId("memory-used-chip").textContent).toContain(th["memory.used.one"]);
  });

  // A memory deleted later: chat-edge rewrites the older rows' label to
  // "[deleted]" so the text is gone everywhere. The answer still used it,
  // so it still counts — but the popover says so in words, not the token.
  it("a memory deleted since shows as a placeholder, still counted", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({
          context_sources: [
            src({ source_id: "m1", display_label: "[deleted]" }),
            src({ source_id: "m2", display_label: "อยู่เชียงใหม่" }),
          ],
        })}
      />,
    );
    const chip = screen.getByTestId("memory-used-chip");
    expect(chip.textContent).toContain(th["memory.used.many"].replace("{n}", "2"));
    fireEvent.click(chip);
    const pop = screen.getByTestId("memory-used-popover");
    expect(pop.textContent).toContain(th["memory.used.deletedItem"]);
    expect(pop.textContent).not.toContain("[deleted]");
    expect(pop.textContent).toContain("อยู่เชียงใหม่");
  });

  it("no chip when the answer used no memory", () => {
    render(<MessageBubble convId="c1" message={msg({ context_sources: [src({ source_type: "compacted_history", display_label: "x" })] })} />);
    expect(screen.queryByTestId("memory-used-chip")).toBeNull();
    cleanup();
    render(<MessageBubble convId="c1" message={msg({})} />);
    expect(screen.queryByTestId("memory-used-chip")).toBeNull();
  });

  it("appears while the answer streams — the memo must not hide the field when it arrives alone", () => {
    const streaming = msg({ status: "streaming", isStreaming: true, content: "" });
    const { rerender } = render(<MessageBubble convId="c1" message={streaming} />);
    expect(screen.queryByTestId("memory-used-chip")).toBeNull();
    rerender(<MessageBubble convId="c1" message={{ ...streaming, memory_used: { count: 1, items: [{ id: "m1", label: "x" }] } }} />);
    expect(screen.getByTestId("memory-used-chip")).toBeTruthy();
  });

  it("English wording too", () => {
    useI18nStore.setState({ locale: "en" });
    render(<MessageBubble convId="c1" message={msg({ memory_used: { count: 3, items: [] } })} />);
    expect(screen.getByTestId("memory-used-chip").textContent).toContain(dictionaries.en["memory.used.many"].replace("{n}", "3"));
  });
});

describe("FIX130 — 'memory saved / suggested' marker on the answer", () => {
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
