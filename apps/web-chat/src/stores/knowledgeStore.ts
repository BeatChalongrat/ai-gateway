// Knowledge base (RAG) store.
//
// Owns the list of KBs visible to the current user + the
// documents for whichever KB the user has currently expanded in the
// settings panel. Upload progress is tracked per-document so the UI
// can show a per-row progress bar while indexing runs.
//
// Indexing is synchronous on the server (Phase 3 contract): the
// upload promise resolves with a row whose status is already
// "indexed" or "failed". We still keep a transient "uploading" row
// in this store while the bytes are in flight so the UI is
// responsive.

"use client";

import { create } from "zustand";

import { api, ApiError } from "@/lib/api";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";

// Uploading is a UI-only state we layer on top of the server's
// status enum. Once the upload completes the row replaces this entry
// with whatever the server returned.
export interface UploadingDocument {
  tempId: string;
  filename: string;
  progress: number; // 0–100
}

interface KnowledgeState {
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
  uploadDocument: (kbId: string, file: File) => Promise<KnowledgeDocument>;
  deleteDocument: (kbId: string, docId: string) => Promise<void>;
  reindexDocument: (kbId: string, docId: string) => Promise<void>;
}

function tempID(): string {
  return (
    "u_" +
    Math.random().toString(36).slice(2, 10) +
    "_" +
    Date.now().toString(36)
  );
}

export const useKnowledgeStore = create<KnowledgeState>((set, get) => ({
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
      set({
        knowledgeBases: res.knowledge_bases,
        loaded: true,
        loading: false,
      });
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
    set((s) => ({ knowledgeBases: [kb, ...s.knowledgeBases] }));
    return kb;
  },

  async deleteKnowledgeBase(id) {
    await api.deleteKnowledgeBase(id);
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
    try {
      const res = await api.getKnowledgeBase(kbId);
      set((s) => {
        const others = s.knowledgeBases.filter((k) => k.id !== kbId);
        // Refresh the KB row's counters from the detail response so
        // sidebar stats stay in sync with whatever the documents
        // panel just observed.
        return {
          knowledgeBases: [res.knowledge_base, ...others].sort((a, b) =>
            a.created_at < b.created_at ? 1 : -1,
          ),
          documentsByKB: {
            ...s.documentsByKB,
            [kbId]: res.documents,
          },
        };
      });
    } catch (err) {
      // Surface for the page to render; don't blow up the store.
      set({ error: err instanceof Error ? err.message : "Failed to load" });
    }
  },

  async uploadDocument(kbId, file) {
    const tempId = tempID();
    set((s) => ({
      uploadingByKB: {
        ...s.uploadingByKB,
        [kbId]: [
          ...(s.uploadingByKB[kbId] ?? []),
          { tempId, filename: file.name, progress: 0 },
        ],
      },
    }));
    const updateProgress = (p: number) => {
      set((s) => ({
        uploadingByKB: {
          ...s.uploadingByKB,
          [kbId]: (s.uploadingByKB[kbId] ?? []).map((u) =>
            u.tempId === tempId ? { ...u, progress: p } : u,
          ),
        },
      }));
    };
    try {
      const doc = await api.uploadKBDocument(kbId, file, {
        onProgress: updateProgress,
      });
      // Drop the temp upload row, prepend the real document.
      set((s) => ({
        uploadingByKB: {
          ...s.uploadingByKB,
          [kbId]: (s.uploadingByKB[kbId] ?? []).filter(
            (u) => u.tempId !== tempId,
          ),
        },
        documentsByKB: {
          ...s.documentsByKB,
          [kbId]: [doc, ...(s.documentsByKB[kbId] ?? [])],
        },
      }));
      // Refresh KB counters from the server so the sidebar stays
      // honest after an index completes.
      void get().loadDocuments(kbId);
      return doc;
    } catch (err) {
      set((s) => ({
        uploadingByKB: {
          ...s.uploadingByKB,
          [kbId]: (s.uploadingByKB[kbId] ?? []).filter(
            (u) => u.tempId !== tempId,
          ),
        },
      }));
      throw err;
    }
  },

  async deleteDocument(kbId, docId) {
    await api.deleteKBDocument(kbId, docId);
    set((s) => ({
      documentsByKB: {
        ...s.documentsByKB,
        [kbId]: (s.documentsByKB[kbId] ?? []).filter((d) => d.id !== docId),
      },
    }));
    void get().loadDocuments(kbId);
  },

  async reindexDocument(kbId, docId) {
    const doc = await api.reindexKBDocument(kbId, docId);
    set((s) => ({
      documentsByKB: {
        ...s.documentsByKB,
        [kbId]: (s.documentsByKB[kbId] ?? []).map((d) =>
          d.id === doc.id ? doc : d,
        ),
      },
    }));
    void get().loadDocuments(kbId);
  },
}));
