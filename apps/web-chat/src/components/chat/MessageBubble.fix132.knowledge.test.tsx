// FIX132 B4 — an answer from a knowledge base says where it came from.
// Before: `[Source: file]` became a static pill that led nowhere, the
// documents behind an answer were only in the hidden Sources rail
// (SHOW_SOURCES_RAIL = false), and nothing said that files still being
// processed were not used. Now: the pill opens a small popover with the
// cited passage(s) and "เปิดไฟล์ต้นฉบับ"; "แหล่งข้อมูล" under the answer
// lists every document used (even with no marker in the text); and a
// knowledge_pending source becomes one quiet line.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: { kbDocumentFile: vi.fn() },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { ContextSource, Message } from "@/lib/types";

import { MessageBubble } from "./MessageBubble";

const th = dictionaries.th as Record<string, string>;

function msg(over: Partial<Message>): Message {
  return { id: "a1", role: "assistant", content: "", status: "completed", created_at: "2026-09-24T00:00:00Z", ...over } as Message;
}
const chunk = (filename: string, meta: Record<string, unknown>): ContextSource =>
  ({
    id: `s-${filename}-${String(meta.rank)}`,
    source_type: "knowledge_chunk",
    source_id: `c-${String(meta.rank)}`,
    display_label: filename,
    metadata: { kb_id: "kb1", filename, chunk_index: 0, score: 0.8, ...meta },
    created_at: "2026-09-24T00:00:00Z",
  }) as unknown as ContextSource;

const SOURCES = [
  chunk("คู่มือพนักงาน.pdf", { document_id: "d1", rank: 1, excerpt: "พนักงานลาพักร้อนได้ปีละ 10 วันทำการ" }),
  chunk("สวัสดิการ 2569.docx", { document_id: "d2", rank: 2, excerpt: "ค่ารักษาพยาบาลเบิกได้ 20,000 บาทต่อปี" }),
  chunk("คู่มือพนักงาน.pdf", { document_id: "d1", rank: 3, excerpt: "ลาป่วยเกิน 3 วันต้องมีใบรับรองแพทย์" }),
];

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("FIX132 — a [Source: …] pill opens what was cited", () => {
  it("the pill opens a popover with the file name, its passages and เปิดไฟล์ต้นฉบับ", async () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({ content: "ลาพักร้อนได้ 10 วัน [Source: คู่มือพนักงาน.pdf]", context_sources: SOURCES })}
      />,
    );
    const pill = screen.getByTestId("kb-citation");
    expect(pill).toHaveTextContent("คู่มือพนักงาน.pdf");
    expect(screen.queryByTestId("kb-cite-popover")).toBeNull();
    fireEvent.click(pill);
    const pop = screen.getByTestId("kb-cite-popover");
    expect(pop).toHaveTextContent("คู่มือพนักงาน.pdf");
    const excerpts = [...pop.querySelectorAll("[data-testid='kb-cite-excerpt']")].map((e) => e.textContent);
    expect(excerpts).toEqual(["พนักงานลาพักร้อนได้ปีละ 10 วันทำการ", "ลาป่วยเกิน 3 วันต้องมีใบรับรองแพทย์"]);
    // Not the other document's passage.
    expect(pop).not.toHaveTextContent("20,000");
    expect(screen.getByRole("button", { name: th["kb.cite.openOriginal"] })).toBeInTheDocument();
    // A popover, not a modal: no aria-modal, and Escape closes it.
    expect(pop.getAttribute("aria-modal")).toBeNull();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("kb-cite-popover")).toBeNull();
  });

  it("เปิดไฟล์ต้นฉบับ fetches that document of that knowledge base", async () => {
    mockApi.kbDocumentFile.mockResolvedValue({ blob: new Blob(["PK"]), filename: "สวัสดิการ 2569.docx" });
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:x");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<MessageBubble convId="c1" message={msg({ content: "เบิกได้ [Source: สวัสดิการ 2569.docx]", context_sources: SOURCES })} />);
    fireEvent.click(screen.getByTestId("kb-citation"));
    fireEvent.click(screen.getByRole("button", { name: th["kb.cite.openOriginal"] }));
    await waitFor(() => expect(mockApi.kbDocumentFile).toHaveBeenCalledWith("kb1", "d2"));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(create).toHaveBeenCalled();
  });

  it("a file that can no longer be fetched says so in the popover", async () => {
    mockApi.kbDocumentFile.mockRejectedValue(Object.assign(new Error("nf"), { status: 404 }));
    render(<MessageBubble convId="c1" message={msg({ content: "x [Source: สวัสดิการ 2569.docx]", context_sources: SOURCES })} />);
    fireEvent.click(screen.getByTestId("kb-citation"));
    fireEvent.click(screen.getByRole("button", { name: th["kb.cite.openOriginal"] }));
    expect(await screen.findByRole("alert")).toHaveTextContent(th["kb.open.gone"]);
  });

  it("a Thai file name with a space is still a pill, not literal [Source: …] text", () => {
    const { container } = render(<MessageBubble convId="c1" message={msg({ content: "ดูที่ [Source: สวัสดิการ 2569.docx] ครับ" })} />);
    expect(container.textContent).not.toContain("[Source:");
    expect(screen.getByText("สวัสดิการ 2569.docx")).toBeInTheDocument();
  });

  it("a marker naming no document the answer used stays a plain pill", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "ข้อมูล [Source: ไม่มีอยู่จริง.pdf]", context_sources: SOURCES })} />);
    expect(screen.queryByTestId("kb-citation")).toBeNull();
    expect(screen.getByText("ไม่มีอยู่จริง.pdf")).toBeInTheDocument();
  });
});

describe("FIX132 — แหล่งข้อมูล under the answer", () => {
  it("lists each document once, even when the model wrote no [Source: …] marker", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "ลาพักร้อนได้ 10 วันครับ", context_sources: SOURCES })} />);
    const row = screen.getByTestId("kb-sources");
    expect(row).toHaveTextContent(th["kb.sources.title"]);
    const chips = screen.getAllByTestId("kb-source-chip").map((c) => c.textContent);
    expect(chips).toEqual(["คู่มือพนักงาน.pdf", "สวัสดิการ 2569.docx"]);
    fireEvent.click(screen.getAllByTestId("kb-source-chip")[1]);
    expect(screen.getByTestId("kb-cite-popover")).toHaveTextContent("ค่ารักษาพยาบาลเบิกได้ 20,000 บาทต่อปี");
  });

  it("an answer that used no knowledge base has no sources row, and other sources stay hidden (SHOW_SOURCES_RAIL)", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({
          content: "สวัสดี",
          context_sources: [{ id: "x", source_type: "past_conversation", display_label: "แชทเก่า", created_at: "" } as ContextSource],
        })}
      />,
    );
    expect(screen.queryByTestId("kb-sources")).toBeNull();
    expect(screen.queryByText("แชทเก่า")).toBeNull();
  });
});

describe("FIX132 — files still being processed", () => {
  it("knowledge_pending → one quiet line with the count", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg({
          content: "ตอบจากไฟล์ที่พร้อมแล้ว",
          context_sources: [
            SOURCES[0],
            { id: "p", source_type: "knowledge_pending", display_label: "", metadata: { kb_id: "kb1", pending: 2, names: ["ก.pdf", "ข.pdf"] }, created_at: "" } as unknown as ContextSource,
          ],
        })}
      />,
    );
    const note = screen.getByTestId("kb-pending-notice");
    expect(note).toHaveTextContent(th["kb.pending.notice"].replace("{n}", "2"));
    expect(th["kb.pending.notice"]).toContain("ยังประมวลผลไม่เสร็จ");
  });

  it("no line when nothing was pending", () => {
    render(<MessageBubble convId="c1" message={msg({ content: "x", context_sources: SOURCES })} />);
    expect(screen.queryByTestId("kb-pending-notice")).toBeNull();
  });
});
