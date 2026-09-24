// FIX132 — the knowledge base as the person sees it: which files the page
// accepts before sending them, what each server status and error code means
// in words, which documents an answer drew on, and how the original file is
// opened. One module so the knowledge page, the store and the answer bubble
// all read the contract (scripts/probes/FIX132/results/kb/CONTRACT.md) the
// same way.

import type { DictKey } from "@/lib/i18n/dict";
import type { ContextSource, KnowledgeDocument, Message } from "@/lib/types";

// ---------------------------------------------------------------------------
// What an upload may be — the SAME limits chat-edge enforces
// (knowledge_service.go: allowedKBContentTypes, MaxFileSizeBytes 50 MB).
// Checked here so a file the server would refuse never leaves the page, and
// the person reads why at once instead of after the bytes went up.
// ---------------------------------------------------------------------------

export const KB_MAX_FILE_BYTES = 50 * 1024 * 1024;

const KB_MIME_BY_EXT: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  csv: "text/csv",
  txt: "text/plain",
};

/** The file picker's `accept`: extensions AND types, because Windows reports
 *  a .csv as application/vnd.ms-excel and some browsers give Office files no
 *  type at all. */
export const KB_ACCEPT = [
  ...Object.keys(KB_MIME_BY_EXT).map((e) => `.${e}`),
  ...Object.values(KB_MIME_BY_EXT),
].join(",");

function extensionOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

/** Why the page will not send this file, or null when it may. */
export type KBFileProblem = "unsupported_type" | "too_large" | "empty_file";

export function checkKBFile(file: File): KBFileProblem | null {
  if (!KB_MIME_BY_EXT[extensionOf(file.name)]) return "unsupported_type";
  if (file.size === 0) return "empty_file";
  if (file.size > KB_MAX_FILE_BYTES) return "too_large";
  return null;
}

/** The file as the server's allow-list expects it: the type named by its
 *  extension. A .csv from Windows (application/vnd.ms-excel) or an Office
 *  file with no type would otherwise be refused with 415 although it is a
 *  file the knowledge base reads. The name (Thai included) is kept as is. */
export function withKBMime(file: File): File {
  const mime = KB_MIME_BY_EXT[extensionOf(file.name)];
  if (!mime || file.type === mime) return file;
  return new File([file], file.name, { type: mime, lastModified: file.lastModified });
}

// ---------------------------------------------------------------------------
// Statuses and reasons, in words
// ---------------------------------------------------------------------------

/** Server error_code → the sentence the person reads (contract §HTTP). */
const DOC_ERROR_KEY: Record<string, DictKey> = {
  unsupported_type: "kb.err.unsupported_type",
  too_large: "kb.err.too_large",
  empty_text: "kb.err.empty_text",
  encrypted: "kb.err.encrypted",
  extract_failed: "kb.err.extract_failed",
  models_unavailable: "kb.err.models_unavailable",
  internal: "kb.err.internal",
};

/** A failed document's reason. An unknown or missing code still gets a
 *  sentence — never a silent "failed". */
export function docErrorKey(code: string | null | undefined): DictKey {
  return DOC_ERROR_KEY[code ?? ""] ?? "kb.err.unknown";
}

/** Why an upload REQUEST did not reach the knowledge base (the file never
 *  became a document), from the page's own check or the HTTP answer. */
export type KBUploadErrorCode =
  | KBFileProblem
  | "network"
  | "session"
  | "kb_missing"
  | "upload_failed";

const UPLOAD_ERROR_KEY: Record<KBUploadErrorCode, DictKey> = {
  unsupported_type: "kb.err.unsupported_type",
  too_large: "kb.err.too_large",
  empty_file: "kb.upload.err.empty_file",
  network: "kb.upload.err.network",
  session: "kb.upload.err.session",
  kb_missing: "kb.upload.err.kb_missing",
  upload_failed: "kb.upload.err.failed",
};

export function uploadErrorKey(code: KBUploadErrorCode): DictKey {
  return UPLOAD_ERROR_KEY[code] ?? "kb.upload.err.failed";
}

/** Retrying the same bytes can help only when the request itself failed. */
export function uploadRetryable(code: KBUploadErrorCode): boolean {
  return code === "network" || code === "session" || code === "upload_failed";
}

export function uploadErrorCodeOf(err: unknown): KBUploadErrorCode {
  const status = (err as { status?: number } | null)?.status;
  switch (status) {
    case 413:
      return "too_large";
    case 415:
      return "unsupported_type";
    case 422:
      return "empty_file";
    case 401:
      return "session";
    case 404:
      return "kb_missing";
    case 0:
      return "network";
    default:
      return "upload_failed";
  }
}

/** How often the knowledge page re-reads while something is being
 *  processed: an open KB's documents every ~2 s, the KB list (whose cards
 *  show "กำลังประมวลผล N ไฟล์") less often. Mutable so tests can shorten it. */
export const KB_POLL = { docsMs: 2_000, listMs: 5_000 };

export function isDocInFlight(doc: Pick<KnowledgeDocument, "status">): boolean {
  return doc.status === "pending" || doc.status === "processing";
}

/** One wire row → the shape the page renders. The contract names the file
 *  `original_filename`; chat-edge before FIX132 sent `filename`. */
export function normalizeKBDocument(raw: KnowledgeDocument): KnowledgeDocument {
  const filename = raw.filename || raw.original_filename || "";
  return { ...raw, filename };
}

/** The indexing summary of a set of documents (the KB card's line). */
export function indexingOf(docs: KnowledgeDocument[]): { pending: number; processing: number; failed: number } {
  let pending = 0;
  let processing = 0;
  let failed = 0;
  for (const d of docs) {
    if (d.status === "pending") pending++;
    else if (d.status === "processing") processing++;
    else if (d.status === "failed") failed++;
  }
  return { pending, processing, failed };
}

// ---------------------------------------------------------------------------
// What an answer drew on (context_sources, contract §Messages)
// ---------------------------------------------------------------------------

export interface KnowledgeExcerpt {
  chunkIndex: number | null;
  text: string;
}

/** One document an answer used, with the passages the model was given. */
export interface KnowledgeCitedDoc {
  documentId: string;
  kbId: string;
  filename: string;
  excerpts: KnowledgeExcerpt[];
}

export interface KnowledgePending {
  kbId: string | null;
  count: number;
  names: string[];
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** The distinct documents behind an answer, in the order the model ranked
 *  their passages. Empty when the answer used no knowledge base. */
export function knowledgeDocsOf(sources: ContextSource[] | null | undefined): KnowledgeCitedDoc[] {
  if (!sources || sources.length === 0) return [];
  const chunks = sources
    .filter((s) => (s.source_type as string) === "knowledge_chunk")
    .map((s) => ({ s, meta: (s.metadata ?? {}) as Record<string, unknown> }))
    .sort((a, b) => {
      const ra = typeof a.meta.rank === "number" ? a.meta.rank : Number.MAX_SAFE_INTEGER;
      const rb = typeof b.meta.rank === "number" ? b.meta.rank : Number.MAX_SAFE_INTEGER;
      return ra - rb;
    });
  const byDoc = new Map<string, KnowledgeCitedDoc>();
  for (const { s, meta } of chunks) {
    const filename = str(meta.filename) || s.display_label || "";
    const documentId = str(meta.document_id);
    const key = documentId || filename;
    if (!key) continue;
    let doc = byDoc.get(key);
    if (!doc) {
      doc = { documentId, kbId: str(meta.kb_id), filename, excerpts: [] };
      byDoc.set(key, doc);
    }
    const text = str(meta.excerpt).trim();
    if (text) {
      doc.excerpts.push({ chunkIndex: typeof meta.chunk_index === "number" ? meta.chunk_index : null, text });
    }
  }
  return [...byDoc.values()];
}

/** The note that some files were still being processed when the answer was
 *  written (so it could not use them). */
export function knowledgePendingOf(sources: ContextSource[] | null | undefined): KnowledgePending | null {
  const src = sources?.find((s) => (s.source_type as string) === "knowledge_pending");
  if (!src) return null;
  const meta = (src.metadata ?? {}) as Record<string, unknown>;
  const count = typeof meta.pending === "number" ? meta.pending : 0;
  if (count <= 0) return null;
  const names = Array.isArray(meta.names) ? meta.names.filter((n): n is string => typeof n === "string") : [];
  return { kbId: str(meta.kb_id) || null, count, names };
}

function comparable(name: string): string {
  return name.normalize("NFC").trim().toLowerCase();
}

/** The cited document a `[Source: name]` marker points at. The model copies
 *  the filename it was shown, but may change its case, spacing or drop the
 *  extension; anything it made up matches nothing (and stays a plain pill). */
export function findCitedDoc(docs: KnowledgeCitedDoc[], name: string): KnowledgeCitedDoc | null {
  const want = comparable(name);
  if (!want) return null;
  const exact = docs.find((d) => comparable(d.filename) === want);
  if (exact) return exact;
  const stem = (n: string) => comparable(n).replace(/\.[a-z0-9]{1,5}$/, "");
  return docs.find((d) => stem(d.filename) === stem(name)) ?? null;
}

export function messageKnowledge(message: Pick<Message, "context_sources">): {
  docs: KnowledgeCitedDoc[];
  pending: KnowledgePending | null;
} {
  return { docs: knowledgeDocsOf(message.context_sources), pending: knowledgePendingOf(message.context_sources) };
}

// ---------------------------------------------------------------------------
// Opening the original file
// ---------------------------------------------------------------------------

/** The name inside a Content-Disposition header. RFC 5987 `filename*` wins
 *  (it is how a Thai name travels); a plain `filename=` is the fallback. */
export function filenameFromDisposition(header: string | null | undefined): string | null {
  if (!header) return null;
  const star = /filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[2].trim().replace(/^"|"$/g, ""));
    } catch {
      /* fall through to the plain form */
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : null;
}

export interface KBFileClient {
  kbDocumentFile(kbId: string, docId: string): Promise<{ blob: Blob; filename: string | null }>;
}

export interface OpenKBFileEnv {
  /** Inside the desktop app a new tab opens in the SYSTEM browser, which
   *  cannot read this page's blob: URL — every file is saved instead. */
  hosted: boolean;
  /** A tab opened synchronously in the click (before any await) so the
   *  popup blocker allows it; null when there is none. */
  openTab?: () => Window | null;
  save?: (url: string, filename: string) => void;
}

function isPdf(filename: string, blob: Blob): boolean {
  return blob.type === "application/pdf" || extensionOf(filename) === "pdf";
}

/** Fetch the original through the authenticated client, then show a PDF in a
 *  new tab (the browser's own viewer) or save any other type. Throws when the
 *  file cannot be fetched; the caller says so on the row. */
export async function openKnowledgeFile(
  client: KBFileClient,
  kbId: string,
  docId: string,
  fallbackName: string,
  env: OpenKBFileEnv,
): Promise<"tab" | "download"> {
  const tab = !env.hosted && extensionOf(fallbackName) === "pdf" && env.openTab ? env.openTab() : null;
  let res: { blob: Blob; filename: string | null };
  try {
    res = await client.kbDocumentFile(kbId, docId);
  } catch (err) {
    tab?.close();
    throw err;
  }
  const filename = res.filename || fallbackName;
  const url = URL.createObjectURL(res.blob);
  if (tab && isPdf(filename, res.blob)) {
    tab.location.href = url;
    // The tab holds the document now; the URL itself can go once it loaded.
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    return "tab";
  }
  tab?.close();
  (env.save ?? defaultSave)(url, filename);
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return "download";
}

function defaultSave(url: string, filename: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
