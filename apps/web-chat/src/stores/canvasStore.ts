// Phase 6 — Canvas store.
//
// C1: held the artifact currently open in the read-only side panel.
// C3: additionally owns the EDIT buffer (draft), the preview/code mode
// and the persisted version lineage.
//
// Still strictly in-memory on the client (NO localStorage/sessionStorage
// — artifact rule). Durability comes from the SERVER lineage
// (chat.canvas_artifacts): a saved edit is a new version bound to the
// conversation, so it survives a reload and is visible wherever that
// conversation is opened.

import { create } from "zustand";

import { api } from "@/lib/api";
import { tOutside } from "@/lib/i18n";
import type { CanvasArtifactRow } from "@/lib/types";
import { useSidePaneStore } from "@/stores/sidePaneStore";

/** Artifact types the canvas can render. C2: html/react/mermaid are
 *  EXECUTABLE (scripts run inside the allow-scripts opaque-origin
 *  sandbox — see canvasUtil.ts security model); svg/markdown stay
 *  static exactly as in C1. */
export type CanvasArtifactType = "html" | "svg" | "markdown" | "mermaid" | "react";

export interface CanvasArtifact {
  type: CanvasArtifactType;
  /** Artifact source currently CONSIDERED SAVED (server latest, or the
   *  original chat block when there is no lineage yet). */
  content: string;
  /** Optional filename from the ```lang:filename fence tag. */
  title?: string;
  /** Persistence binding (C3). Absent → ad-hoc mode: edits still work
   *  in-session but cannot be saved. */
  conversationId?: string;
  messageId?: string;
  artifactKey?: string;
}

export type CanvasMode = "preview" | "code";

interface CanvasState {
  open: boolean;
  artifact: CanvasArtifact | null;
  /** Editable buffer. Preview renders THIS (debounced by the panel), so
   *  edits show live without being persisted. */
  draft: string;
  mode: CanvasMode;
  dirty: boolean;
  versions: CanvasArtifactRow[];
  versionsLoading: boolean;
  saving: boolean;
  error: string | null;

  openCanvas: (artifact: CanvasArtifact) => void;
  closeCanvas: () => void;
  setMode: (mode: CanvasMode) => void;
  setDraft: (draft: string) => void;
  saveVersion: () => Promise<void>;
  loadVersions: () => Promise<void>;
  revertTo: (version: number) => Promise<void>;
  /** Phase 8 — re-read the lineage after an assistant turn, so a version
   *  the MODEL wrote (update_canvas_artifact) shows up in the open panel
   *  without the user reopening it. No-op when nothing is open. */
  refreshIfOpen: () => void;
  /** Phase 8 — persist the original block as version 1 the first time it
   *  is opened, giving the artifact a server-side identity the model can
   *  address. Internal: called by loadVersions, never by a component. */
  adoptInitialVersion: (artifact: CanvasArtifact) => Promise<void>;
}

/** True when this artifact is bound to a persisted lineage. */
export function canPersist(a: CanvasArtifact | null): a is CanvasArtifact & {
  conversationId: string;
  artifactKey: string;
} {
  return Boolean(a?.conversationId && a?.artifactKey);
}

export const useCanvasStore = create<CanvasState>()((set, get) => ({
  open: false,
  artifact: null,
  draft: "",
  mode: "preview",
  dirty: false,
  versions: [],
  versionsLoading: false,
  saving: false,
  error: null,

  openCanvas: (artifact) => {
    // FIX76 — take the one side-pane slot (replaces a workspace or
    // preview pane that was open).
    useSidePaneStore.getState().open({ kind: "canvas" });
    set({
      open: true,
      artifact,
      draft: artifact.content,
      mode: "preview",
      dirty: false,
      versions: [],
      error: null,
    });
    // Bound artifacts hydrate from the server: if the user (or a later
    // turn) already saved versions of this block, the LATEST saved
    // version is the truth — that is what "the edit survived the
    // reload" means from the user's point of view.
    if (canPersist(artifact)) {
      void get().loadVersions();
    }
  },

  closeCanvas: () => {
    useSidePaneStore.getState().closeIf("canvas");
    set({
      open: false,
      artifact: null,
      draft: "",
      mode: "preview",
      dirty: false,
      versions: [],
      error: null,
    });
  },

  setMode: (mode) => set({ mode }),

  setDraft: (draft) =>
    set((s) => ({ draft, dirty: draft !== (s.artifact?.content ?? "") })),

  refreshIfOpen: () => {
    const s = get();
    if (!s.open || !canPersist(s.artifact)) return;
    void s.loadVersions();
  },

  async loadVersions() {
    const artifact = get().artifact;
    if (!canPersist(artifact)) return;
    set({ versionsLoading: true });
    try {
      const res = await api.listCanvasVersions(
        artifact.conversationId,
        artifact.artifactKey,
      );
      const versions = res?.versions ?? [];
      // Phase 8 — ADOPT: a block the user just opened for the first time
      // has no lineage yet, so nothing server-side identifies it and the
      // model cannot revise it. Persisting the original source as
      // version 1 is what makes "AI, change the button colour" possible.
      // Only on a real empty list (a failed request lands in catch), and
      // never over an in-progress edit.
      if (versions.length === 0 && !get().dirty && get().artifact === artifact) {
        void get().adoptInitialVersion(artifact);
        set({ versionsLoading: false });
        return;
      }
      set((s) => {
        // Adopt the newest saved version as the current content — but
        // never clobber unsaved edits the user is in the middle of.
        const latest = versions[0];
        if (!latest || s.dirty || s.artifact !== artifact) {
          return { versions, versionsLoading: false };
        }
        return {
          versions,
          versionsLoading: false,
          artifact: { ...artifact, content: latest.content, type: latest.type },
          draft: latest.content,
        };
      });
    } catch {
      // History is an enhancement; failing to load it must not break
      // the preview the user came for.
      set({ versionsLoading: false });
    }
  },

  async adoptInitialVersion(artifact) {
    if (!canPersist(artifact) || !artifact.content) return;
    try {
      const res = await api.saveCanvasArtifact(artifact.conversationId, {
        artifact_key: artifact.artifactKey,
        type: artifact.type,
        title: artifact.title,
        content: artifact.content,
        message_id: artifact.messageId,
      });
      const saved = res?.artifact;
      set((s) =>
        // Only if the user is still looking at the same artifact — an
        // adopt that lands after they switched must not touch state.
        saved && s.artifact === artifact ? { versions: [saved] } : {},
      );
    } catch {
      // Adoption is best-effort: the preview and in-session editing work
      // regardless; only the model-driven update needs the lineage.
    }
  },

  async saveVersion() {
    const { artifact, draft } = get();
    if (!canPersist(artifact) || get().saving) return;
    set({ saving: true, error: null });
    try {
      const res = await api.saveCanvasArtifact(artifact.conversationId, {
        artifact_key: artifact.artifactKey,
        type: artifact.type,
        title: artifact.title,
        content: draft,
        message_id: artifact.messageId,
      });
      const saved = res?.artifact;
      set((s) => ({
        saving: false,
        dirty: false,
        artifact: s.artifact ? { ...s.artifact, content: draft } : s.artifact,
        versions: saved ? [saved, ...s.versions] : s.versions,
      }));
    } catch {
      set({ saving: false, error: tOutside("canvas.saveFailed") });
    }
  },

  async revertTo(version) {
    const artifact = get().artifact;
    if (!canPersist(artifact) || get().saving) return;
    set({ saving: true, error: null });
    try {
      const res = await api.revertCanvasArtifact(
        artifact.conversationId,
        artifact.artifactKey,
        version,
      );
      const reverted = res?.artifact;
      if (!reverted) {
        set({ saving: false });
        return;
      }
      set((s) => ({
        saving: false,
        dirty: false,
        draft: reverted.content,
        artifact: s.artifact
          ? { ...s.artifact, content: reverted.content, type: reverted.type }
          : s.artifact,
        versions: [reverted, ...s.versions],
      }));
    } catch {
      set({ saving: false, error: tOutside("canvas.revertFailed") });
    }
  },
}));

// FIX76 — when another pane takes the slot, the canvas resets its own
// state the same way its close button does.
useSidePaneStore.getState().registerCloser("canvas", () => {
  if (useCanvasStore.getState().open) useCanvasStore.getState().closeCanvas();
});
