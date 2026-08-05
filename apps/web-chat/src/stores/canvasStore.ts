// Phase 6 / C1 — Canvas store. Holds the artifact currently open in
// the read-only Canvas side panel. Strictly in-memory (NO
// localStorage/sessionStorage — artifact rule): closing the tab
// forgets the canvas, which is correct for a read-only preview of
// content that still lives in the chat transcript.

import { create } from "zustand";

/** Artifact types the C1 canvas can preview. All render STATICALLY —
 *  no script execution anywhere (see CanvasPanel security model).
 *  ``mermaid`` is deliberately absent in C1: web-chat has no mermaid
 *  renderer to reuse, and adding one is a C2 decision. */
export type CanvasArtifactType = "html" | "svg" | "markdown";

export interface CanvasArtifact {
  type: CanvasArtifactType;
  /** Raw artifact source exactly as it appeared in the code block. */
  content: string;
  /** Optional filename from the ```lang:filename fence tag. */
  title?: string;
}

interface CanvasState {
  open: boolean;
  artifact: CanvasArtifact | null;
  openCanvas: (artifact: CanvasArtifact) => void;
  closeCanvas: () => void;
}

export const useCanvasStore = create<CanvasState>()((set) => ({
  open: false,
  artifact: null,
  openCanvas: (artifact) => set({ open: true, artifact }),
  closeCanvas: () => set({ open: false, artifact: null }),
}));
