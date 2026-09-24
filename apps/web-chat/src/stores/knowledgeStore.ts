// Knowledge base (RAG) store.
//
// Owns the list of KBs visible to the current user + the documents of
// whichever KB is open on the knowledge page.
//
// FIX132 — indexing is asynchronous: an upload answers 202 with a `pending`
// document and the server extracts, chunks and embeds it in the background
// (pending → processing with progress_done/progress_total → indexed, or
// failed with an error_code). The store therefore:
//   * puts every chosen file on the list at once — the bytes going up, then
//     the server's pending row;
//   * keeps a file that did not go up (refused by the page's own check, or a
//     failed request) on the list with its reason until it is retried or
//     dismissed — never only a toast;
//   * derives the KB's counts (documents, chunks, what is still indexing)
//     from the documents it holds, so the card never contradicts the rows.
// The knowledge page polls loadDocuments while anything is in flight.

"use client";

import { create } from "zustand";

import { api, ApiError } from "@/lib/api";
import {
  checkKBFile,
  indexingOf,
  normalizeKBDocument,
  uploadErrorCodeOf,
  withKBMime,
  type KBUploadErrorCode,
} from "@/lib/knowledge";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";

/** A chosen file that is not (yet) a document on the server: its bytes are
 *  going up, or it did not get there. A failed entry keeps the File so
 *  "ลองใหม่" sends the same bytes again. */
export interface UploadingDocument {
  tempId: string;
  filename: string;
  progress: number; // 0–100
  state: "uploading" | "failed";
  errorCode?: KBUploadErrorCode;
  file?: File;
}

/** FIX130 C2 — the server's word on whether a knowledge base can work.
 *  "unknown" until it answered (and when asking failed). Nothing
 *  knowledge-base-shaped is offered unless it is "available": NIGHT2 F27 was
 *  a composer button, a project dropdown and a menu entry leading to a
 *  feature whose every upload failed. */
export type KnowledgeAvailabilityState = "unknown" | "available" | "unavailable";

interface KnowledgeState {
  availability: KnowledgeAvailabilityState;
  /** Why it is unavailable (models_loading | models_unavailable). */
  availabilityReason: string | null;
  /** Ask the server; once per session unless `force` (the knowledge page
   *  asks afresh every time it opens, and after a failed upload). */
  loadAvailability: (force?: boolean) => Promise<void>;
  knowledgeBases: KnowledgeBase[];
  documentsByKB: Record<string, KnowledgeDocument[]>;
  uploadingByKB: Record<string, UploadingDocument[]>;
  loaded: boolean;
  loading: boolean;
  error: string | null;

  loadKnowledgeBases: () => Promise<void>;
  createKnowledgeBase: (data: {
    name: string;
    description?: string;
    chunk_size?: number;
    chunk_overlap?: number;
  }) => Promise<KnowledgeBase>;
  deleteKnowledgeBase: (id: string) => Promise<void>;
  loadDocuments: (kbId: string) => Promise<void>;
  /** Check, then send each file in turn; every file is on the list at once. */
  uploadFiles: (kbId: string, files: File[]) => Promise<void>;
  retryUpload: (kbId: string, tempId: string) => Promise<void>;
  dismissUpload: (kbId: string, tempId: string) => void;
  deleteDocument: (kbId: string, docId: string) => Promise<void>;
  reindexDocument: (kbId: string, docId: string) => Promise<void>;
}

/** The KB's counters as its documents say them. */
function withCounts(kb: KnowledgeBase, docs: KnowledgeDocument[]): KnowledgeBase {
  return {
    ...kb,
    document_count: docs.length,
    total_chunks: docs.reduce((n, d) => n + (d.status === "indexed" ? d.chunk_count || 0 : 0), 0),
    indexing: indexingOf(docs),
  };
}

// A documents read that started before a local change (an upload landed, a
// row was deleted or re-queued) would put the old list back; its answer is
// dropped and the list read again.
const docsGeneration = new Map<string, number>();
const docsInFlight = new Set<string>();
const docsWanted = new Set<string>();
function bumpDocs(kbId: string): void {
  docsGeneration.set(kbId, (docsGeneration.get(kbId) ?? 0) + 1);
}

function tempID(): string {
  return (
    "u_" +
    Math.random().toString(36).slice(2, 10) +
    "_" +
    Date.now().toString(36)
  );
}

let availabilityAsked: Promise<void> | null = null;

/** FIX132 — the knowledge base runs on local models that take a while to
 *  load after a restart. "models_loading" ends by itself, so while the
 *  server says it, the page asks again on this interval (one timer for the
 *  whole page, whoever asked) and every knowledge-base affordance appears
 *  without a reload. "models_unavailable" is a failure and is not polled —
 *  the knowledge page's "check again" and its next visit ask afresh.
 *  Mutable so tests can shorten it. */
export const KB_AVAILABILITY_RECHECK = { loadingMs: 15_000 };
let availabilityRecheck: ReturnType<typeof setTimeout> | null = null;

/** Test seam: forget that availability was asked. */
export function _resetKnowledgeAvailability(): void {
  availabilityAsked = null;
  if (availabilityRecheck) clearTimeout(availabilityRecheck);
  availabilityRecheck = null;
}

export const useKnowledgeStore = create<KnowledgeState>((set, get) => ({
  availability: "unknown",
  availabilityReason: null,
  loadAvailability(force = false) {
    if (availabilityAsked && !force) return availabilityAsked;
    availabilityAsked = (async () => {
      try {
        const res = await api.getKnowledgeAvailability();
        set({
          availability: res?.available ? "available" : "unavailable",
          availabilityReason: res?.available ? null : (res?.reason ?? null),
        });
        if (!res?.available && res?.reason === "models_loading" && typeof window !== "undefined") {
          if (availabilityRecheck) clearTimeout(availabilityRecheck);
          availabilityRecheck = setTimeout(() => {
            availabilityRecheck = null;
            void get().loadAvailability(true);
          }, KB_AVAILABILITY_RECHECK.loadingMs);
        }
      } catch {
        // Could not ask: offer nothing rather than something that may not
        // work. A later forced ask (the knowledge page) tries again.
        set({ availability: "unknown", availabilityReason: null });
      }
    })();
    return availabilityAsked;
  },
  knowledgeBases: [],
  documentsByKB: {},
  uploadingByKB: {},
  loaded: false,
  loading: false,
  error: null,

  async loadKnowledgeBases() {
    if (get().loading) return;
    set({ loading: true, error: null });
    try {
      const res = await api.listKnowledgeBases();
      set((s) => ({
        // A KB whose documents are open here is counted from them: they are
        // read more often than the list.
        knowledgeBases: res.knowledge_bases.map((k) => {
          const docs = s.documentsByKB[k.id];
          return docs ? withCounts(k, docs) : k;
        }),
        loaded: true,
        loading: false,
      }));
    } catch (err) {
      // 401 already bounces inside ApiClient — don't loop the error.
      if (err instanceof ApiError && err.status === 401) {
        set({ loading: false });
        return;
      }
      set({
        loading: false,
        loaded: true,
        error: err instanceof Error ? err.message : "Failed to load",
      });
    }
  },

  async createKnowledgeBase(data) {
    const kb = await api.createKnowledgeBase(data);
    set((s) => ({ knowledgeBases: [kb, ...s.knowledgeBases.filter((k) => k.id !== kb.id)] }));
    return kb;
  },

  async deleteKnowledgeBase(id) {
    const res = await api.deleteKnowledgeBase(id);
    if (res && !res.ok && res.status !== 404) {
      throw new ApiError(`HTTP_${res.status}`, res.statusText ?? "", res.status);
    }
    set((s) => {
      const nextDocs = { ...s.documentsByKB };
      delete nextDocs[id];
      const nextUp = { ...s.uploadingByKB };
      delete nextUp[id];
      return {
        knowledgeBases: s.knowledgeBases.filter((k) => k.id !== id),
        documentsByKB: nextDocs,
        uploadingByKB: nextUp,
      };
    });
  },

  async loadDocuments(kbId) {
    if (docsInFlight.has(kbId)) {
      docsWanted.add(kbId);
      return;
    }
    docsInFlight.add(kbId);
    const generation = docsGeneration.get(kbId) ?? 0;
    let stale = false;
    try {
      const list = (await api.listKBDocuments(kbId)).map(normalizeKBDocument);
      stale = (docsGeneration.get(kbId) ?? 0) !== generation;
      if (!stale) {
        set((s) => ({
          documentsByKB: { ...s.documentsByKB, [kbId]: list },
          knowledgeBases: s.knowledgeBases.map((k) => (k.id === kbId ? withCounts(k, list) : k)),
        }));
      }
    } catch (err) {
      // Surface for the page to render; don't blow up the store.
      set({ error: err instanceof Error ? err.message : "Failed to load" });
    } finally {
      docsInFlight.delete(kbId);
      if (docsWanted.delete(kbId) || stale) void get().loadDocuments(kbId);
    }
  },

  async uploadFiles(kbId, files) {
    const entries: UploadingDocument[] = files.map((file) => {
      const problem = checkKBFile(file);
      return {
        tempId: tempID(),
        filename: file.name,
        progress: 0,
        state: problem ? "failed" : "uploading",
        errorCode: problem ?? undefined,
        file,
      };
    });
    set((s) => ({
      uploadingByKB: { ...s.uploadingByKB, [kbId]: [...(s.uploadingByKB[kbId] ?? []), ...entries] },
    }));
    for (const e of entries) {
      if (e.state === "uploading" && e.file) await sendUpload(kbId, e.tempId, e.file);
    }
    if (entries.some((e) => e.state === "uploading")) void get().loadDocuments(kbId);
  },

  async retryUpload(kbId, tempId) {
    const entry = (get().uploadingByKB[kbId] ?? []).find((u) => u.tempId === tempId);
    if (!entry?.file) return;
    patchUpload(kbId, tempId, { state: "uploading", errorCode: undefined, progress: 0 });
    await sendUpload(kbId, tempId, entry.file);
    void get().loadDocuments(kbId);
  },

  dismissUpload(kbId, tempId) {
    set((s) => ({
      uploadingByKB: { ...s.uploadingByKB, [kbId]: (s.uploadingByKB[kbId] ?? []).filter((u) => u.tempId !== tempId) },
    }));
  },

  async deleteDocument(kbId, docId) {
    const res = await api.deleteKBDocument(kbId, docId);
    // 404: it is already gone, which is what was asked.
    if (res && !res.ok && res.status !== 404) {
      throw new ApiError(`HTTP_${res.status}`, res.statusText ?? "", res.status);
    }
    bumpDocs(kbId);
    setDocs(kbId, (docs) => docs.filter((d) => d.id !== docId));
    void get().loadDocuments(kbId);
  },

  async reindexDocument(kbId, docId) {
    const before = (get().documentsByKB[kbId] ?? []).find((d) => d.id === docId);
    bumpDocs(kbId);
    setDocs(kbId, (docs) =>
      docs.map((d) =>
        d.id === docId
          ? { ...d, status: "pending", error_code: null, error_message: null, progress_done: 0, progress_total: 0 }
          : d,
      ),
    );
    try {
      await api.reindexKBDocument(kbId, docId);
    } catch (err) {
      if (before) setDocs(kbId, (docs) => docs.map((d) => (d.id === docId ? before : d)));
      throw err;
    }
    void get().loadDocuments(kbId);
  },
}));

/** Replace one KB's documents and recount it. */
function setDocs(kbId: string, change: (docs: KnowledgeDocument[]) => KnowledgeDocument[]): void {
  useKnowledgeStore.setState((s) => {
    const docs = change(s.documentsByKB[kbId] ?? []);
    return {
      documentsByKB: { ...s.documentsByKB, [kbId]: docs },
      knowledgeBases: s.knowledgeBases.map((k) => (k.id === kbId ? withCounts(k, docs) : k)),
    };
  });
}

function patchUpload(kbId: string, tempId: string, patch: Partial<UploadingDocument>): void {
  useKnowledgeStore.setState((s) => ({
    uploadingByKB: {
      ...s.uploadingByKB,
      [kbId]: (s.uploadingByKB[kbId] ?? []).map((u) => (u.tempId === tempId ? { ...u, ...patch } : u)),
    },
  }));
}

/** One file's request. Success: the entry becomes the server's document row.
 *  Failure: the entry stays, failed, with the reason. */
async function sendUpload(kbId: string, tempId: string, file: File): Promise<void> {
  try {
    const raw = await api.uploadKBDocument(kbId, withKBMime(file), {
      onProgress: (p) => patchUpload(kbId, tempId, { progress: p }),
    });
    const doc = normalizeKBDocument(raw);
    bumpDocs(kbId);
    useKnowledgeStore.setState((s) => ({
      uploadingByKB: { ...s.uploadingByKB, [kbId]: (s.uploadingByKB[kbId] ?? []).filter((u) => u.tempId !== tempId) },
    }));
    setDocs(kbId, (docs) => [doc, ...docs.filter((d) => d.id !== doc.id)]);
  } catch (err) {
    patchUpload(kbId, tempId, { state: "failed", errorCode: uploadErrorCodeOf(err), progress: 0 });
  }
}
