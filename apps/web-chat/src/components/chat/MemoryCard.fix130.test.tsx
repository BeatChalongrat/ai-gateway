// FIX130 A3c — every saved memory and every suggestion on the Memory page
// says what it is, WHEN (an absolute Thai date plus the relative time), and
// FROM WHICH CHAT (the chat's title as a link, "แชทที่ถูกลบแล้ว" when the
// chat is gone, "เพิ่มเอง" for one typed on this page), with the category
// and source in Thai. Before: "บันทึกเมื่อ 3 วันที่แล้ว", no date, and the
// source chat only when it happened to be loaded in the sidebar.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { Memory } from "@/lib/types";

import { MemoryCard } from "./MemoryCard";
import { PendingPreferenceCard } from "./PendingPreferenceCard";

const th = dictionaries.th;
const mem = (over: Partial<Memory>): Memory =>
  ({
    id: "m1",
    scope_type: "global_user",
    memory_type: "explicit_fact",
    category: "identity",
    content: "ชื่อเล่นว่าภูผา",
    source_type: "assistant_inferred",
    status: "active",
    confidence_score: 0.9,
    importance_score: 0.5,
    is_user_confirmed: true,
    is_pinned: false,
    sensitivity_level: "normal",
    use_count: 0,
    created_at: "2026-09-23T05:00:00Z",
    updated_at: "2026-09-23T05:00:00Z",
    ...over,
  }) as Memory;
const noop = () => {};

beforeEach(() => useI18nStore.setState({ locale: "th" }));
afterEach(cleanup);

describe("MemoryCard — what, when, from which chat", () => {
  it("shows the content, the Thai date, the category in Thai, and the source chat as a link", () => {
    render(
      <MemoryCard
        memory={mem({ source_conversation_id: "c-42", source_conversation_title: "แนะนำร้านกาแฟเชียงใหม่" })}
        onEdit={noop}
        onDelete={noop}
        onTogglePin={noop}
      />,
    );
    expect(screen.getByText("ชื่อเล่นว่าภูผา")).toBeTruthy();
    expect(screen.getByTestId("memory-when").textContent).toContain("23 ก.ย. 2569");
    expect(screen.getByTestId("memory-meta").textContent).toContain(th["memory.category.identity"]);
    const link = screen.getByRole("link", { name: "แนะนำร้านกาแฟเชียงใหม่" });
    expect(link.getAttribute("href")).toBe("/chat/c-42");
  });

  it("a memory from a deleted chat says แชทที่ถูกลบแล้ว and links nowhere", () => {
    render(<MemoryCard memory={mem({ source_conversation_id: "c-gone", source_conversation_deleted: true })} onEdit={noop} onDelete={noop} onTogglePin={noop} />);
    expect(screen.getByTestId("memory-origin").textContent).toContain(th["memory.card.origin.deletedChat"]);
    expect(th["memory.card.origin.deletedChat"]).toBe("แชทที่ถูกลบแล้ว");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("a memory typed on this page says เพิ่มเอง", () => {
    render(<MemoryCard memory={mem({ source_type: "user_explicit", source_conversation_id: null })} onEdit={noop} onDelete={noop} onTogglePin={noop} />);
    expect(screen.getByTestId("memory-origin").textContent).toContain(th["memory.card.origin.manual"]);
    expect(th["memory.card.origin.manual"]).toBe("เพิ่มเอง");
  });
});

describe("PendingPreferenceCard — a suggestion says the same three things", () => {
  it("content, Thai date, source chat link, and the user's own words as evidence", () => {
    render(
      <PendingPreferenceCard
        memory={mem({
          status: "pending_confirmation",
          category: "location",
          content: "อยู่ที่เชียงใหม่",
          evidence_excerpt: "ทำงานเป็นวิศวกรโยธาอยู่ที่เชียงใหม่",
          source_conversation_id: "c-7",
          source_conversation_title: "ร้านกาแฟ",
        })}
        onConfirm={noop}
        onReject={noop}
      />,
    );
    expect(screen.getByText("อยู่ที่เชียงใหม่")).toBeTruthy();
    expect(screen.getByTestId("memory-when").textContent).toContain("23 ก.ย. 2569");
    expect(screen.getByTestId("memory-meta").textContent).toContain(th["memory.category.location"]);
    expect(screen.getByRole("link", { name: "ร้านกาแฟ" }).getAttribute("href")).toBe("/chat/c-7");
    expect(screen.getByText(/ทำงานเป็นวิศวกรโยธา/)).toBeTruthy();
  });
});
