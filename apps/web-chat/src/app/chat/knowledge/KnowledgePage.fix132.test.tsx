// FIX132 B4 — the knowledge page as the person uses it. Indexing is
// asynchronous now (upload → 202 pending → processing with progress →
// indexed or failed with an error_code), so the page shows each file's state
// as it changes by itself, says in Thai why a file failed, keeps a failed
// upload on screen instead of a two-second toast, and tells the person they
// can already ask while files are still being processed.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi, mockToast } = vi.hoisted(() => ({
  mockApi: {
    getKnowledgeAvailability: vi.fn(),
    listKnowledgeBases: vi.fn(),
    createKnowledgeBase: vi.fn(),
    deleteKnowledgeBase: vi.fn(),
    listKBDocuments: vi.fn(),
    uploadKBDocument: vi.fn(),
    deleteKBDocument: vi.fn(),
    reindexKBDocument: vi.fn(),
    kbDocumentFile: vi.fn(),
  },
  mockToast: vi.fn(),
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/components/ui/Toast", () => ({ toast: mockToast }));

import KnowledgeBasePage from "@/app/chat/knowledge/page";
import { ApiError } from "@/lib/api";
import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import * as knowledgeLib from "@/lib/knowledge";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";
import * as knowledgeStoreModule from "@/stores/knowledgeStore";
import { useKnowledgeStore } from "@/stores/knowledgeStore";

const th = dictionaries.th as Record<string, string>;
const fill = (key: string, vars: Record<string, string | number>) =>
  Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), th[key] ?? `<<${key}>>`);
const kbInitial = useKnowledgeStore.getState();
const poll = (knowledgeLib as { KB_POLL?: { docsMs: number; listMs: number } }).KB_POLL;
const originalPoll = poll ? { ...poll } : null;

const kb = (over: Partial<KnowledgeBase> = {}): KnowledgeBase => ({
  id: "kb1", name: "คู่มือ HR", description: "", embedding_model: "bge-m3", chunk_size: 512, chunk_overlap: 50,
  is_active: true, document_count: 0, total_chunks: 0, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
  ...over,
});
const doc = (over: Partial<KnowledgeDocument>): KnowledgeDocument =>
  ({
    id: "d1", knowledge_base_id: "kb1", filename: "คู่มือพนักงาน.pdf", content_type: "application/pdf", file_size_bytes: 2048,
    status: "pending", chunk_count: 0, char_count: 0, progress_done: 0, progress_total: 0, error_code: null,
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z", ...over,
  }) as KnowledgeDocument;
const fileOf = (name: string, size = 100, type = "") => {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
};

beforeEach(() => {
  vi.clearAllMocks();
  (knowledgeStoreModule as { _resetKnowledgeAvailability?: () => void })._resetKnowledgeAvailability?.();
  useKnowledgeStore.setState({ ...kbInitial }, true);
  useI18nStore.setState({ locale: "th" });
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  mockApi.getKnowledgeAvailability.mockResolvedValue({ available: true });
  mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [kb()] });
  mockApi.listKBDocuments.mockResolvedValue([]);
  if (poll) {
    poll.docsMs = 20;
    poll.listMs = 20;
  }
});
afterEach(() => {
  cleanup();
  if (poll && originalPoll) Object.assign(poll, originalPoll);
  useI18nStore.setState({ locale: "en" });
});

const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });

async function openKB(name = "คู่มือ HR") {
  render(<KnowledgeBasePage />);
  fireEvent.click(await screen.findByText(name));
  await wait(0);
}
const rows = () => screen.queryAllByTestId("kb-doc-row");

describe("FIX132 — create a knowledge base with a Thai name", () => {
  it("the name dialog creates it and opens it", async () => {
    mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [] });
    mockApi.createKnowledgeBase.mockResolvedValue(kb({ id: "kb2", name: "นโยบายบริษัท" }));
    render(<KnowledgeBasePage />);
    fireEvent.click(await screen.findByText(th["kb.create.full"]));
    fireEvent.change(screen.getByLabelText(th["kb.dialog.name"]), { target: { value: "นโยบายบริษัท" } });
    fireEvent.click(screen.getByRole("button", { name: th["kb.create"] }));
    await waitFor(() => expect(mockApi.createKnowledgeBase).toHaveBeenCalledWith(expect.objectContaining({ name: "นโยบายบริษัท" })));
    expect(await screen.findByTestId("kb-dropzone")).toBeInTheDocument();
  });

  it("a refused create says so inside the dialog", async () => {
    mockApi.listKnowledgeBases.mockResolvedValue({ knowledge_bases: [] });
    mockApi.createKnowledgeBase.mockRejectedValue(new ApiError("INTERNAL", "internal error", 500));
    render(<KnowledgeBasePage />);
    fireEvent.click(await screen.findByText(th["kb.create.full"]));
    fireEvent.change(screen.getByLabelText(th["kb.dialog.name"]), { target: { value: "ก" } });
    fireEvent.click(screen.getByRole("button", { name: th["kb.create"] }));
    expect(await screen.findByRole("alert")).toHaveTextContent(th["kb.dialog.createFailed"]);
  });
});

describe("FIX132 — the list says how much is in each KB and what is still indexing", () => {
  it("counts + 'กำลังประมวลผล N ไฟล์' + 'ไม่สำเร็จ N ไฟล์'", async () => {
    mockApi.listKnowledgeBases.mockResolvedValue({
      knowledge_bases: [kb({ document_count: 5, total_chunks: 120, indexing: { pending: 1, processing: 1, failed: 1 } })],
    });
    render(<KnowledgeBasePage />);
    const summary = await screen.findByTestId("kb-card-summary");
    expect(summary).toHaveTextContent("5");
    expect(summary).toHaveTextContent("120");
    expect(summary).toHaveTextContent(fill("kb.card.indexing", { n: 2 }));
    expect(summary).toHaveTextContent(fill("kb.card.failed", { n: 1 }));
  });
});

describe("FIX132 — upload: the row is there at once and updates by itself", () => {
  it("picker with two files → both rows show รอคิว straight away", async () => {
    mockApi.uploadKBDocument
      .mockResolvedValueOnce(doc({ id: "a", filename: "นโยบายการลา.pdf" }))
      .mockResolvedValueOnce(doc({ id: "b", filename: "สวัสดิการ.docx" }));
    mockApi.listKBDocuments.mockResolvedValue([]);
    await openKB();
    mockApi.listKBDocuments.mockResolvedValue([doc({ id: "a", filename: "นโยบายการลา.pdf" }), doc({ id: "b", filename: "สวัสดิการ.docx" })]);
    const input = screen.getByTestId("kb-file-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [fileOf("นโยบายการลา.pdf"), fileOf("สวัสดิการ.docx")] } });
    await waitFor(() => expect(rows()).toHaveLength(2));
    for (const r of rows()) expect(within(r).getByTestId("kb-doc-status")).toHaveTextContent(th["kb.doc.status.pending"]);
    expect(screen.getByText("นโยบายการลา.pdf")).toBeInTheDocument();
  });

  it("processing shows 12/40 with a bar; ready shows พร้อมใช้ + chunks; polling stops when nothing is left", async () => {
    mockApi.listKBDocuments
      .mockResolvedValueOnce([doc({ status: "processing", progress_done: 12, progress_total: 40 })])
      .mockResolvedValueOnce([doc({ status: "processing", progress_done: 30, progress_total: 40 })])
      .mockResolvedValue([doc({ status: "indexed", chunk_count: 40, progress_done: 40, progress_total: 40 })]);
    await openKB();
    const status = await screen.findByTestId("kb-doc-status");
    expect(status).toHaveTextContent(fill("kb.doc.progress", { done: 12, total: 40 }));
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("12");
    expect(bar.getAttribute("aria-valuemax")).toBe("40");
    await waitFor(() => expect(screen.getByTestId("kb-doc-status")).toHaveTextContent(fill("kb.doc.ready", { n: 40 })));
    expect(screen.queryByRole("progressbar")).toBeNull();
    const calls = mockApi.listKBDocuments.mock.calls.length;
    await wait(100);
    expect(mockApi.listKBDocuments.mock.calls.length).toBe(calls);
  });

  it("coming back to the window refreshes the list", async () => {
    mockApi.listKBDocuments.mockResolvedValue([doc({ status: "indexed", chunk_count: 3 })]);
    await openKB();
    const calls = mockApi.listKBDocuments.mock.calls.length;
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    expect(mockApi.listKBDocuments.mock.calls.length).toBe(calls + 1);
  });

  it("drag and drop uploads too", async () => {
    mockApi.uploadKBDocument.mockResolvedValue(doc({ id: "z", filename: "ข้อตกลง.txt" }));
    await openKB();
    fireEvent.drop(screen.getByTestId("kb-dropzone"), { dataTransfer: { files: [fileOf("ข้อตกลง.txt")], types: ["Files"] } });
    await waitFor(() => expect(mockApi.uploadKBDocument).toHaveBeenCalledTimes(1));
    expect((mockApi.uploadKBDocument.mock.calls[0][1] as File).name).toBe("ข้อตกลง.txt");
  });
});

describe("FIX132 — while anything is processing, the page says you can already ask", () => {
  it("the note shows while a file is pending/processing and goes when all are done", async () => {
    mockApi.listKBDocuments
      .mockResolvedValueOnce([doc({ status: "processing", progress_done: 1, progress_total: 4 }), doc({ id: "d2", status: "indexed", chunk_count: 2 })])
      .mockResolvedValue([doc({ status: "indexed", chunk_count: 4 }), doc({ id: "d2", status: "indexed", chunk_count: 2 })]);
    await openKB();
    const note = await screen.findByTestId("kb-processing-note");
    expect(note).toHaveTextContent(th["kb.processingNote"]);
    await waitFor(() => expect(screen.queryByTestId("kb-processing-note")).toBeNull());
  });
});

describe("FIX132 — a failed file says why, in Thai, and can be retried", () => {
  it.each([
    ["empty_text", "kb.err.empty_text"],
    ["encrypted", "kb.err.encrypted"],
    ["unsupported_type", "kb.err.unsupported_type"],
    ["too_large", "kb.err.too_large"],
    ["extract_failed", "kb.err.extract_failed"],
    ["models_unavailable", "kb.err.models_unavailable"],
    ["internal", "kb.err.internal"],
  ])("%s", async (code, key) => {
    mockApi.listKBDocuments.mockResolvedValue([doc({ status: "failed", error_code: code, error_message: "raw english" })]);
    await openKB();
    expect(await screen.findByTestId("kb-doc-status")).toHaveTextContent(th["kb.doc.status.failed"]);
    expect(screen.getByTestId("kb-doc-reason")).toHaveTextContent(th[key]);
    expect(screen.getByTestId("kb-doc-reason")).not.toHaveTextContent("raw english");
  });

  it("extract_unavailable blames the reader being down, not the file", async () => {
    mockApi.listKBDocuments.mockResolvedValue([doc({ status: "failed", error_code: "extract_unavailable", error_message: "raw english" })]);
    await openKB();
    const reason = await screen.findByTestId("kb-doc-reason");
    expect(reason).toHaveTextContent("ระบบอ่านไฟล์ไม่พร้อมทำงาน");
    expect(reason).not.toHaveTextContent(th["kb.err.extract_failed"]);
    expect(reason).not.toHaveTextContent(th["kb.err.unknown"]);
  });

  it("ลองใหม่ reindexes and the row goes back to รอคิว", async () => {
    mockApi.listKBDocuments.mockResolvedValueOnce([doc({ status: "failed", error_code: "extract_failed" })]).mockResolvedValue([doc({ status: "pending" })]);
    mockApi.reindexKBDocument.mockResolvedValue(null);
    await openKB();
    fireEvent.click(await screen.findByRole("button", { name: th["kb.doc.retry"] }));
    expect(mockApi.reindexKBDocument).toHaveBeenCalledWith("kb1", "d1");
    await waitFor(() => expect(screen.getByTestId("kb-doc-status")).toHaveTextContent(th["kb.doc.status.pending"]));
  });
});

describe("FIX132 — never a silent upload failure", () => {
  it("a failed request stays on that file with a reason and ลองใหม่ — not a toast", async () => {
    mockApi.uploadKBDocument.mockRejectedValueOnce(new ApiError("NETWORK_ERROR", "Network error", 0));
    await openKB();
    fireEvent.change(screen.getByTestId("kb-file-input"), { target: { files: [fileOf("สัญญา.docx")] } });
    const row = await screen.findByTestId("kb-upload-row");
    expect(row).toHaveTextContent("สัญญา.docx");
    expect(row).toHaveTextContent(th["kb.upload.err.network"]);
    await wait(50);
    expect(screen.getByTestId("kb-upload-row")).toBeInTheDocument();
    expect(mockToast).not.toHaveBeenCalled();
    mockApi.uploadKBDocument.mockResolvedValueOnce(doc({ id: "d7", filename: "สัญญา.docx" }));
    fireEvent.click(within(row).getByRole("button", { name: th["kb.doc.retry"] }));
    await waitFor(() => expect(screen.queryByTestId("kb-upload-row")).toBeNull());
    expect(mockApi.uploadKBDocument).toHaveBeenCalledTimes(2);
  });

  it("a file of a type the KB cannot read is refused before sending, with the reason on its row", async () => {
    await openKB();
    fireEvent.change(screen.getByTestId("kb-file-input"), { target: { files: [fileOf("สไลด์.pptx")] } });
    const row = await screen.findByTestId("kb-upload-row");
    expect(row).toHaveTextContent(th["kb.err.unsupported_type"]);
    expect(mockApi.uploadKBDocument).not.toHaveBeenCalled();
    fireEvent.click(within(row).getByRole("button", { name: th["kb.upload.dismiss"] }));
    expect(screen.queryByTestId("kb-upload-row")).toBeNull();
  });
});
