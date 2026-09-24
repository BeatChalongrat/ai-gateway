// FIX132 B4 — every document row can open its original file. The bytes come
// through the authenticated client (GET …/documents/{id}/file); a PDF opens
// in a new tab with the browser's viewer, other types are saved under their
// own (Thai) name, and a file that cannot be fetched says so on its row.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    getKnowledgeAvailability: vi.fn(),
    listKnowledgeBases: vi.fn(),
    listKBDocuments: vi.fn(),
    kbDocumentFile: vi.fn(),
  },
}));
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
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
const doc = (id: string, filename: string, content_type: string): KnowledgeDocument =>
  ({
    id, knowledge_base_id: "kb1", filename, content_type, file_size_bytes: 2048, status: "indexed",
    chunk_count: 5, char_count: 0, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
  }) as KnowledgeDocument;

beforeEach(() => {
  vi.clearAllMocks();
  (knowledgeStoreModule as { _resetKnowledgeAvailability?: () => void })._resetKnowledgeAvailability?.();
  useKnowledgeStore.setState({ ...kbInitial }, true);
  useI18nStore.setState({ locale: "th" });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:kb-file");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  mockApi.getKnowledgeAvailability.mockResolvedValue({ available: true });
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [kb] });
  mockApi.listKBDocuments.mockResolvedValue([
    doc("d1", "คู่มือพนักงาน 2569.pdf", "application/pdf"),
    doc("d2", "สัญญาจ้าง.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
  ]);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useI18nStore.setState({ locale: "en" });
});

async function openKB() {
  render(<KnowledgeBasePage />);
  fireEvent.click(await screen.findByText("คู่มือ HR"));
  await screen.findByText("คู่มือพนักงาน 2569.pdf");
}
const rowOf = (name: string) => screen.getByText(name).closest("[data-testid='kb-doc-row']") as HTMLElement;

describe("FIX132 — เปิดไฟล์ on a document row", () => {
  it("a PDF opens in a new tab through the authenticated fetch", async () => {
    const tab = { location: { href: "" }, close: vi.fn(), opener: {} } as unknown as Window;
    const open = vi.spyOn(window, "open").mockReturnValue(tab);
    mockApi.kbDocumentFile.mockResolvedValue({ blob: new Blob(["%PDF"], { type: "application/pdf" }), filename: "คู่มือพนักงาน 2569.pdf" });
    await openKB();
    fireEvent.click(within(rowOf("คู่มือพนักงาน 2569.pdf")).getByRole("button", { name: th["kb.doc.open"] }));
    expect(open).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(tab.location.href).toBe("blob:kb-file"));
    expect(mockApi.kbDocumentFile).toHaveBeenCalledWith("kb1", "d1");
  });

  it("a DOCX is saved under its Thai name", async () => {
    const open = vi.spyOn(window, "open");
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
    mockApi.kbDocumentFile.mockResolvedValue({ blob: new Blob(["PK"]), filename: "สัญญาจ้าง.docx" });
    await openKB();
    fireEvent.click(within(rowOf("สัญญาจ้าง.docx")).getByRole("button", { name: th["kb.doc.open"] }));
    await waitFor(() => expect(saved).toEqual(["สัญญาจ้าง.docx"]));
    expect(open).not.toHaveBeenCalled();
    expect(mockApi.kbDocumentFile).toHaveBeenCalledWith("kb1", "d2");
  });

  it("a file that cannot be fetched says so on its row", async () => {
    mockApi.kbDocumentFile.mockRejectedValue(Object.assign(new Error("x"), { status: 500 }));
    await openKB();
    const row = rowOf("สัญญาจ้าง.docx");
    fireEvent.click(within(row).getByRole("button", { name: th["kb.doc.open"] }));
    expect(await within(row).findByRole("alert")).toHaveTextContent(th["kb.open.failed"]);
  });
});
