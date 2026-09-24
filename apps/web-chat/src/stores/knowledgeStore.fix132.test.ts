// FIX132 B4 — uploads into a knowledge base are asynchronous now: the server
// answers 202 with a `pending` document and indexes it in the background.
// The store puts the row on screen at once, keeps it honest while it is
// processed (documents list + counts), and never loses a failed upload — the
// file stays on the list with its reason until it is retried or dismissed.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    getKnowledgeAvailability: vi.fn(),
    listKnowledgeBases: vi.fn(),
    listKBDocuments: vi.fn(),
    uploadKBDocument: vi.fn(),
    deleteKBDocument: vi.fn(),
    reindexKBDocument: vi.fn(),
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

import { ApiError } from "@/lib/api";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";

import { useKnowledgeStore } from "./knowledgeStore";

const initial = useKnowledgeStore.getState();
const kb: KnowledgeBase = {
  id: "kb1", name: "คู่มือ", description: "", embedding_model: "bge-m3", chunk_size: 512, chunk_overlap: 50,
  is_active: true, document_count: 0, total_chunks: 0, created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
};
const doc = (over: Partial<KnowledgeDocument>): KnowledgeDocument =>
  ({
    id: "d1", knowledge_base_id: "kb1", filename: "คู่มือพนักงาน.pdf", content_type: "application/pdf", file_size_bytes: 1000,
    status: "pending", chunk_count: 0, char_count: 0, progress_done: 0, progress_total: 0, error_code: null,
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z", ...over,
  }) as KnowledgeDocument;
const fileOf = (name: string, size = 100, type = "") => {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
};
const s = () => useKnowledgeStore.getState();

beforeEach(() => {
  vi.clearAllMocks();
  useKnowledgeStore.setState({ ...initial, knowledgeBases: [kb], loaded: true, documentsByKB: { kb1: [] }, uploadingByKB: {} }, true);
  mockApi.listKBDocuments.mockResolvedValue([]);
});
afterEach(() => vi.restoreAllMocks());

describe("FIX132 — upload returns at once and the row is there", () => {
  it("202 pending → the document row shows immediately, no spinner row left behind", async () => {
    mockApi.uploadKBDocument.mockResolvedValue(doc({ id: "d9", filename: "นโยบาย.pdf", status: "pending" }));
    mockApi.listKBDocuments.mockResolvedValue([doc({ id: "d9", filename: "นโยบาย.pdf", status: "pending" })]);
    await s().uploadFiles("kb1", [fileOf("นโยบาย.pdf", 100, "application/pdf")]);
    expect(s().documentsByKB.kb1.map((d) => [d.id, d.status])).toEqual([["d9", "pending"]]);
    expect(s().uploadingByKB.kb1 ?? []).toEqual([]);
    expect(s().knowledgeBases[0].document_count).toBe(1);
    expect(s().knowledgeBases[0].indexing).toEqual({ pending: 1, processing: 0, failed: 0 });
  });

  it("several files: every one gets a row straight away, then they go up one by one", async () => {
    let release!: () => void;
    mockApi.uploadKBDocument.mockImplementationOnce(() => new Promise((r) => { release = () => r(doc({ id: "a" })); }));
    mockApi.uploadKBDocument.mockResolvedValue(doc({ id: "b", filename: "b.txt" }));
    const done = s().uploadFiles("kb1", [fileOf("ก.pdf"), fileOf("b.txt")]);
    await Promise.resolve();
    expect((s().uploadingByKB.kb1 ?? []).map((u) => u.filename)).toEqual(["ก.pdf", "b.txt"]);
    release();
    await done;
    expect(mockApi.uploadKBDocument).toHaveBeenCalledTimes(2);
  });

  it("a Windows .csv is sent as text/csv (the server's allow-list)", async () => {
    mockApi.uploadKBDocument.mockResolvedValue(doc({ id: "c" }));
    await s().uploadFiles("kb1", [fileOf("ยอดขาย.csv", 10, "application/vnd.ms-excel")]);
    const sent = mockApi.uploadKBDocument.mock.calls[0][1] as File;
    expect(sent.type).toBe("text/csv");
    expect(sent.name).toBe("ยอดขาย.csv");
  });
});

describe("FIX132 — a file that did not go up stays on the list with its reason", () => {
  it("a type or size the server would refuse is not sent at all", async () => {
    await s().uploadFiles("kb1", [fileOf("slides.pptx"), fileOf("big.pdf", 60 * 1024 * 1024)]);
    expect(mockApi.uploadKBDocument).not.toHaveBeenCalled();
    expect((s().uploadingByKB.kb1 ?? []).map((u) => [u.filename, u.state, u.errorCode])).toEqual([
      ["slides.pptx", "failed", "unsupported_type"],
      ["big.pdf", "failed", "too_large"],
    ]);
  });

  it("a failed request keeps the file (not a toast) and ลองใหม่ sends the same bytes again", async () => {
    mockApi.uploadKBDocument.mockRejectedValueOnce(new ApiError("NETWORK_ERROR", "Network error", 0));
    await s().uploadFiles("kb1", [fileOf("สัญญา.docx")]);
    const [entry] = s().uploadingByKB.kb1;
    expect(entry).toMatchObject({ filename: "สัญญา.docx", state: "failed", errorCode: "network" });
    mockApi.uploadKBDocument.mockResolvedValueOnce(doc({ id: "d5", filename: "สัญญา.docx" }));
    mockApi.listKBDocuments.mockResolvedValue([doc({ id: "d5", filename: "สัญญา.docx" })]);
    await s().retryUpload("kb1", entry.tempId);
    expect((mockApi.uploadKBDocument.mock.calls[1][1] as File).name).toBe("สัญญา.docx");
    expect(s().uploadingByKB.kb1).toEqual([]);
    expect(s().documentsByKB.kb1.map((d) => d.id)).toContain("d5");
  });

  it("dismiss removes a failed upload", async () => {
    await s().uploadFiles("kb1", [fileOf("x.pptx")]);
    s().dismissUpload("kb1", s().uploadingByKB.kb1[0].tempId);
    expect(s().uploadingByKB.kb1).toEqual([]);
  });
});

describe("FIX132 — the documents list keeps itself and the KB counts honest", () => {
  it("reads GET …/documents (bare array or {documents}), names from original_filename, counts from the rows", async () => {
    mockApi.listKBDocuments.mockResolvedValue([
      doc({ id: "a", status: "indexed", chunk_count: 12 }),
      doc({ id: "b", status: "processing", progress_done: 3, progress_total: 9 }),
      { ...doc({ id: "c", status: "failed", error_code: "empty_text" }), filename: undefined, original_filename: "สแกน.pdf" } as unknown as KnowledgeDocument,
    ]);
    await s().loadDocuments("kb1");
    expect(mockApi.listKBDocuments).toHaveBeenCalledWith("kb1");
    expect(s().documentsByKB.kb1.map((d) => d.filename)).toEqual(["คู่มือพนักงาน.pdf", "คู่มือพนักงาน.pdf", "สแกน.pdf"]);
    expect(s().knowledgeBases[0]).toMatchObject({ document_count: 3, total_chunks: 12, indexing: { pending: 0, processing: 1, failed: 1 } });
  });

  it("delete removes the row and the count; a refused delete keeps it and throws", async () => {
    useKnowledgeStore.setState({ documentsByKB: { kb1: [doc({ id: "a", status: "indexed", chunk_count: 4 }), doc({ id: "b", status: "indexed", chunk_count: 6 })] } });
    mockApi.deleteKBDocument.mockResolvedValueOnce({ ok: false, status: 500 } as Response);
    await expect(s().deleteDocument("kb1", "a")).rejects.toBeTruthy();
    expect(s().documentsByKB.kb1).toHaveLength(2);
    mockApi.deleteKBDocument.mockResolvedValueOnce({ ok: true, status: 204 } as Response);
    mockApi.listKBDocuments.mockResolvedValue([doc({ id: "b", status: "indexed", chunk_count: 6 })]);
    await s().deleteDocument("kb1", "a");
    expect(s().documentsByKB.kb1.map((d) => d.id)).toEqual(["b"]);
    expect(s().knowledgeBases[0]).toMatchObject({ document_count: 1, total_chunks: 6 });
  });

  it("ลองใหม่ on a failed document puts it back in the queue at once", async () => {
    useKnowledgeStore.setState({ documentsByKB: { kb1: [doc({ id: "a", status: "failed", error_code: "extract_failed", error_message: "x" })] } });
    mockApi.reindexKBDocument.mockResolvedValue(null);
    mockApi.listKBDocuments.mockResolvedValue([doc({ id: "a", status: "pending" })]);
    const p = s().reindexDocument("kb1", "a");
    expect(s().documentsByKB.kb1[0]).toMatchObject({ status: "pending", error_code: null });
    await p;
    expect(mockApi.reindexKBDocument).toHaveBeenCalledWith("kb1", "a");
  });
});
