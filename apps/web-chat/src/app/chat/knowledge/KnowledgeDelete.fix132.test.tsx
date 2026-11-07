// FIX132 B4 — deleting a document (or a whole knowledge base) is confirmed
// in the app, not with the browser's window.confirm (which the desktop app
// renders as a bare system box), and the confirmation says what deleting
// means for answers: they stop using that file. After the delete the row is
// gone and the KB's counts follow.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    getKnowledgeAvailability: vi.fn(),
    listKnowledgeBases: vi.fn(),
    listKBDocuments: vi.fn(),
    deleteKBDocument: vi.fn(),
    deleteKnowledgeBase: vi.fn(),
  },
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));

import KnowledgeBasePage from "@/app/chat/knowledge/page";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";
import * as knowledgeStoreModule from "@/stores/knowledgeStore";
import { useKnowledgeStore } from "@/stores/knowledgeStore";

const th = dictionaries.th as Record<string, string>;
const kbInitial = useKnowledgeStore.getState();
const kb: KnowledgeBase = {
  id: "kb1", name: "คู่มือ HR", description: "", embedding_model: "bge-m3", chunk_size: 512, chunk_overlap: 50,
  is_active: true, document_count: 2, total_chunks: 10, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
};
const doc = (id: string, filename: string, chunks: number): KnowledgeDocument =>
  ({
    id, knowledge_base_id: "kb1", filename, content_type: "application/pdf", file_size_bytes: 2048, status: "indexed",
    chunk_count: chunks, char_count: 0, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
  }) as KnowledgeDocument;

let confirmSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  vi.clearAllMocks();
  (knowledgeStoreModule as { _resetKnowledgeAvailability?: () => void })._resetKnowledgeAvailability?.();
  useKnowledgeStore.setState({ ...kbInitial }, true);
  useI18nStore.setState({ locale: "th" });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  mockApi.getKnowledgeAvailability.mockResolvedValue({ available: true });
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [kb] });
  mockApi.listKBDocuments.mockResolvedValue([doc("d1", "คู่มือพนักงาน.pdf", 4), doc("d2", "สวัสดิการ.docx", 6)]);
});
afterEach(() => {
  cleanup();
  confirmSpy.mockRestore();
  useI18nStore.setState({ locale: "en" });
});

async function openKB() {
  render(<KnowledgeBasePage />);
  fireEvent.click(await screen.findByText("คู่มือ HR"));
  await screen.findByText("คู่มือพนักงาน.pdf");
}
const rowOf = (name: string) => screen.getByText(name).closest("[data-testid='kb-doc-row']") as HTMLElement;

describe("FIX132 — delete a document", () => {
  it("an in-app dialog says answers will stop using the file; confirm removes the row and the count", async () => {
    await openKB();
    fireEvent.click(within(rowOf("คู่มือพนักงาน.pdf")).getByRole("button", { name: th["kb.doc.delete"] }));
    expect(confirmSpy).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(th["kb.confirmDoc.title"]);
    expect(dialog).toHaveTextContent("คู่มือพนักงาน.pdf");
    expect(th["kb.confirmDoc.body"]).toContain("คำตอบ");
    expect(dialog).toHaveTextContent(th["kb.confirmDoc.body"].replace("{name}", "คู่มือพนักงาน.pdf"));
    mockApi.deleteKBDocument.mockResolvedValue({ ok: true, status: 204 });
    mockApi.listKBDocuments.mockResolvedValue([doc("d2", "สวัสดิการ.docx", 6)]);
    fireEvent.click(within(dialog).getByRole("button", { name: th["kb.confirmDoc.action"] }));
    await waitFor(() => expect(screen.queryByText("คู่มือพนักงาน.pdf")).toBeNull());
    expect(mockApi.deleteKBDocument).toHaveBeenCalledWith("kb1", "d1");
    expect(screen.queryByRole("dialog")).toBeNull();
    const summary = screen.getByTestId("kb-card-summary");
    expect(summary.textContent).toMatch(/^1 /);
    expect(summary).toHaveTextContent("6");
  });

  it("cancel deletes nothing", async () => {
    await openKB();
    fireEvent.click(within(rowOf("สวัสดิการ.docx")).getByRole("button", { name: th["kb.doc.delete"] }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: th["common.cancel"] }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mockApi.deleteKBDocument).not.toHaveBeenCalled();
    expect(screen.getByText("สวัสดิการ.docx")).toBeInTheDocument();
  });

  it("a refused delete says so in the dialog and keeps the row", async () => {
    await openKB();
    mockApi.deleteKBDocument.mockResolvedValue({ ok: false, status: 500 });
    fireEvent.click(within(rowOf("สวัสดิการ.docx")).getByRole("button", { name: th["kb.doc.delete"] }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: th["kb.confirmDoc.action"] }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(th["kb.toast.docDeleteFailed"]);
    expect(screen.getByText("สวัสดิการ.docx")).toBeInTheDocument();
  });
});

describe("FIX132 — delete a knowledge base", () => {
  it("in-app confirm with what goes with it; the card disappears", async () => {
    await openKB();
    fireEvent.click(screen.getByRole("button", { name: th["kb.docs.deleteKb"] }));
    expect(confirmSpy).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("คู่มือ HR");
    mockApi.deleteKnowledgeBase.mockResolvedValue({ ok: true, status: 204 });
    fireEvent.click(within(dialog).getByRole("button", { name: th["kb.confirmKb.action"] }));
    await waitFor(() => expect(screen.queryByText("คู่มือ HR")).toBeNull());
    expect(mockApi.deleteKnowledgeBase).toHaveBeenCalledWith("kb1");
  });
});
