// FIX132 — the knowledge-base contract as the page reads it: which files it
// sends, what a status/error code means, which documents an answer used, and
// how the original file (Thai name included) is opened.
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ContextSource } from "@/lib/types";

import {
  checkKBFile,
  docErrorKey,
  filenameFromDisposition,
  findCitedDoc,
  KB_MAX_FILE_BYTES,
  knowledgeDocsOf,
  knowledgePendingOf,
  normalizeKBDocument,
  openKnowledgeFile,
  uploadErrorCodeOf,
  withKBMime,
} from "./knowledge";
import { dictionaries } from "./i18n/dict";

const th = dictionaries.th as Record<string, string>;
const en = dictionaries.en as Record<string, string>;

function fileOf(name: string, size: number, type = ""): File {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
}

const chunk = (over: Record<string, unknown>, label = "คู่มือพนักงาน.pdf"): ContextSource =>
  ({
    id: `s${Math.random()}`,
    source_type: "knowledge_chunk",
    source_id: "chunk",
    display_label: label,
    metadata: { kb_id: "kb1", document_id: "d1", filename: label, chunk_index: 0, excerpt: "ลาพักร้อนได้ 10 วัน", score: 0.9, rank: 1, ...over },
    created_at: "2026-09-24T00:00:00Z",
  }) as unknown as ContextSource;

afterEach(() => vi.restoreAllMocks());

describe("FIX132 — what the page sends", () => {
  it("accepts PDF/DOCX/XLSX/CSV/TXT up to the server's 50 MB, Thai names included", () => {
    expect(checkKBFile(fileOf("นโยบายการลา.pdf", 1000))).toBeNull();
    expect(checkKBFile(fileOf("สัญญา.docx", 1000))).toBeNull();
    expect(checkKBFile(fileOf("ราคา.xlsx", 1000))).toBeNull();
    expect(checkKBFile(fileOf("รายชื่อ.csv", 1000))).toBeNull();
    expect(checkKBFile(fileOf("บันทึก.TXT", KB_MAX_FILE_BYTES))).toBeNull();
  });

  it("refuses what the server would refuse, before sending", () => {
    expect(checkKBFile(fileOf("slides.pptx", 1000))).toBe("unsupported_type");
    expect(checkKBFile(fileOf("photo.png", 1000, "image/png"))).toBe("unsupported_type");
    expect(checkKBFile(fileOf("big.pdf", KB_MAX_FILE_BYTES + 1))).toBe("too_large");
    expect(checkKBFile(fileOf("empty.txt", 0))).toBe("empty_file");
  });

  it("a Windows .csv (application/vnd.ms-excel) goes up as text/csv; the Thai name is kept", () => {
    const f = withKBMime(fileOf("ยอดขาย.csv", 10, "application/vnd.ms-excel"));
    expect(f.type).toBe("text/csv");
    expect(f.name).toBe("ยอดขาย.csv");
    const docx = withKBMime(fileOf("a.docx", 10, ""));
    expect(docx.type).toBe("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  });

  it("an upload request's HTTP failure is a reason, not a raw status", () => {
    expect(uploadErrorCodeOf({ status: 413 })).toBe("too_large");
    expect(uploadErrorCodeOf({ status: 415 })).toBe("unsupported_type");
    expect(uploadErrorCodeOf({ status: 0 })).toBe("network");
    expect(uploadErrorCodeOf({ status: 500 })).toBe("upload_failed");
  });
});

describe("FIX132 — every error code has a Thai sentence", () => {
  it.each([
    ["unsupported_type"],
    ["too_large"],
    ["empty_text"],
    ["encrypted"],
    ["extract_failed"],
    ["models_unavailable"],
    ["internal"],
  ])("%s", (code) => {
    const key = docErrorKey(code);
    expect(key).not.toBe("kb.err.unknown");
    expect(th[key]).toBeTruthy();
    expect(th[key]).not.toBe(en[key]);
  });

  it("a scanned PDF with no text says so; a locked file says it is locked", () => {
    expect(th[docErrorKey("empty_text")]).toContain("ไม่พบข้อความในไฟล์");
    expect(th[docErrorKey("empty_text")]).toContain("สแกน");
    expect(th[docErrorKey("encrypted")]).toContain("รหัสผ่าน");
  });

  it("an unknown code still has words", () => {
    expect(th[docErrorKey("something_new")]).toBeTruthy();
    expect(th[docErrorKey(null)]).toBeTruthy();
  });

  it("the contract's original_filename is the row's name", () => {
    const d = normalizeKBDocument({ id: "d1", original_filename: "คู่มือ.pdf" } as never);
    expect(d.filename).toBe("คู่มือ.pdf");
  });
});

describe("FIX132 — what an answer drew on", () => {
  it("groups chunks by document in rank order, with their excerpts", () => {
    const docs = knowledgeDocsOf([
      chunk({ document_id: "d2", filename: "สวัสดิการ.docx", rank: 2, excerpt: "ค่ารักษาพยาบาล 20,000 บาท" }, "สวัสดิการ.docx"),
      chunk({ rank: 1 }),
      chunk({ rank: 3, chunk_index: 4, excerpt: "ลาป่วยได้ 30 วัน" }),
      { id: "m", source_type: "saved_memory", display_label: "x", created_at: "" } as ContextSource,
    ]);
    expect(docs.map((d) => d.filename)).toEqual(["คู่มือพนักงาน.pdf", "สวัสดิการ.docx"]);
    expect(docs[0].excerpts.map((e) => e.text)).toEqual(["ลาพักร้อนได้ 10 วัน", "ลาป่วยได้ 30 วัน"]);
    expect(docs[0]).toMatchObject({ kbId: "kb1", documentId: "d1" });
  });

  it("a marker matches its document even when the model changed case or dropped the extension", () => {
    const docs = knowledgeDocsOf([chunk({}, "HR-Handbook.pdf")]);
    expect(findCitedDoc(docs, "HR-Handbook.pdf")?.documentId).toBe("d1");
    expect(findCitedDoc(docs, "hr-handbook.pdf")?.documentId).toBe("d1");
    expect(findCitedDoc(docs, "HR-Handbook")?.documentId).toBe("d1");
    expect(findCitedDoc(docs, "made-up.pdf")).toBeNull();
  });

  it("knowledge_pending → how many files the answer could not use yet", () => {
    const p = knowledgePendingOf([
      { id: "p", source_type: "knowledge_pending", display_label: "", metadata: { kb_id: "kb1", pending: 2, names: ["a.pdf", "ข.docx"] }, created_at: "" } as unknown as ContextSource,
    ]);
    expect(p).toEqual({ kbId: "kb1", count: 2, names: ["a.pdf", "ข.docx"] });
    expect(knowledgePendingOf([])).toBeNull();
  });
});

describe("FIX132 — opening the original file", () => {
  it("reads a Thai filename from Content-Disposition (RFC 5987)", () => {
    const thai = "คู่มือพนักงาน 2569.pdf";
    expect(filenameFromDisposition(`inline; filename*=UTF-8''${encodeURIComponent(thai)}`)).toBe(thai);
    expect(filenameFromDisposition(`inline; filename="x.pdf"; filename*=UTF-8''${encodeURIComponent(thai)}`)).toBe(thai);
    expect(filenameFromDisposition('attachment; filename="plain.txt"')).toBe("plain.txt");
    expect(filenameFromDisposition(null)).toBeNull();
  });

  it("web: a PDF goes into the tab opened in the click", async () => {
    const tab = { location: { href: "" }, close: vi.fn() } as unknown as Window;
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:pdf");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const save = vi.fn();
    const client = { kbDocumentFile: vi.fn(async () => ({ blob: new Blob(["%PDF"], { type: "application/pdf" }), filename: "คู่มือ.pdf" })) };
    const how = await openKnowledgeFile(client, "kb1", "d1", "คู่มือ.pdf", { hosted: false, openTab: () => tab, save });
    expect(how).toBe("tab");
    expect(client.kbDocumentFile).toHaveBeenCalledWith("kb1", "d1");
    expect(tab.location.href).toBe("blob:pdf");
    expect(save).not.toHaveBeenCalled();
  });

  it("a DOCX is saved under its Thai name; no tab is opened", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:docx");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const openTab = vi.fn();
    const save = vi.fn();
    const client = { kbDocumentFile: vi.fn(async () => ({ blob: new Blob(["PK"]), filename: "สัญญาเช่า.docx" })) };
    const how = await openKnowledgeFile(client, "kb1", "d2", "fallback.docx", { hosted: false, openTab, save });
    expect(how).toBe("download");
    expect(openTab).not.toHaveBeenCalled();
    expect(save).toHaveBeenCalledWith("blob:docx", "สัญญาเช่า.docx");
  });

  it("desktop: even a PDF is saved (a new tab would be the system browser)", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:pdf");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const openTab = vi.fn();
    const save = vi.fn();
    const client = { kbDocumentFile: vi.fn(async () => ({ blob: new Blob(["%PDF"], { type: "application/pdf" }), filename: null })) };
    expect(await openKnowledgeFile(client, "kb1", "d1", "คู่มือ.pdf", { hosted: true, openTab, save })).toBe("download");
    expect(openTab).not.toHaveBeenCalled();
    expect(save).toHaveBeenCalledWith("blob:pdf", "คู่มือ.pdf");
  });

  it("a failed fetch closes the waiting tab and throws for the caller to say so", async () => {
    const tab = { location: { href: "" }, close: vi.fn() } as unknown as Window;
    const client = { kbDocumentFile: vi.fn(async () => { throw Object.assign(new Error("gone"), { status: 404 }); }) };
    await expect(openKnowledgeFile(client, "kb1", "d1", "a.pdf", { hosted: false, openTab: () => tab })).rejects.toMatchObject({ status: 404 });
    expect(tab.close).toHaveBeenCalled();
  });
});
