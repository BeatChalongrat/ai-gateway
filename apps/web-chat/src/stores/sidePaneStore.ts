"use client";

// FIX76 — the ONE side-pane slot beside the chat.
//
// Three panels used to cover the conversation (canvas, workspace, file
// preview), each a fixed slide-over behind its own backdrop, so only one
// could ever be reached at a time — by accident of the backdrop, not by
// decision. Now the conversation page has a single docked slot on the
// right (≥ lg) and this store says what is in it:
//
//   * canvas    — the artifact drawer (its content state stays in
//                 canvasStore; this store only owns the SLOT)
//   * workspace — the generated-files lineage panel
//   * preview   — one attachment opened from a message card
//
// Opening a pane while another is open REPLACES it (Part A Q2: one slot,
// the thread never gives way). Each panel registers a closer so the
// replaced panel can reset its own state (the canvas closes its draft,
// the workspace drops its selected lineage) without this store importing
// them — canvasStore imports this store, never the other way round.
//
// `hostMounted` says whether a page is rendering the slot at all. Surfaces
// that show message cards WITHOUT the slot (quick-ask, the public share
// view) must fall back to the old modal, or a preview click would do
// nothing. `dockedWidth` is the measured width of the docked pane (0 when
// closed or overlaid) so bottom-centred floating pills and the credits
// badge can stay over the thread instead of the pane.

import { create } from "zustand";

import type { FileAttachment } from "@/lib/types";

export type SidePaneKind = "canvas" | "workspace" | "preview";

export type SidePane =
  | { kind: "canvas" }
  | { kind: "workspace" }
  | { kind: "preview"; file: FileAttachment };

interface SidePaneState {
  pane: SidePane | null;
  files: FileAttachment[];
  rememberFile: (file: FileAttachment) => void;
  hostMounted: boolean;
  dockedWidth: number;
  /** Open (or replace with) this pane. The previous pane's closer runs
   *  first when the kind changes. */
  open: (pane: SidePane) => void;
  /** Close whatever is open (runs its closer). */
  close: () => void;
  /** Close only if the given kind is the one open. Used by a panel that
   *  closed itself (its own state is already reset, so no closer runs). */
  closeIf: (kind: SidePaneKind) => void;
  registerCloser: (kind: SidePaneKind, fn: () => void) => void;
  setHostMounted: (mounted: boolean) => void;
  setDockedWidth: (px: number) => void;
}

const closers: Partial<Record<SidePaneKind, () => void>> = {};

export const useSidePaneStore = create<SidePaneState>((set, get) => ({
  files: [],
  rememberFile: (file) => set(state => {
    const files = state.files.filter(f => f.id !== file.id && f.id !== file.parent_file_id);
    return { files: [...files, file].slice(-20) };
  }),
  pane: null,
  hostMounted: false,
  dockedWidth: 0,
  open: (pane) => {
    if (pane.kind === "preview") get().rememberFile(pane.file);
    const prev = get().pane;
    if (prev && prev.kind !== pane.kind) closers[prev.kind]?.();
    set({ pane });
  },
  close: () => {
    const prev = get().pane;
    if (!prev) return;
    set({ pane: null, dockedWidth: 0 });
    closers[prev.kind]?.();
  },
  closeIf: (kind) => {
    const prev = get().pane;
    if (prev && prev.kind === kind) set({ pane: null, dockedWidth: 0 });
  },
  registerCloser: (kind, fn) => {
    closers[kind] = fn;
  },
  setHostMounted: (mounted) => set({ hostMounted: mounted, ...(!mounted ? { files: [] } : {}) }),
  setDockedWidth: (px) => {
    if (get().dockedWidth !== px) set({ dockedWidth: px });
  },
}));

/** Test-only: forget registered closers (modules re-register on import). */
export function _resetSidePaneClosersForTests(): void {
  for (const k of Object.keys(closers) as SidePaneKind[]) delete closers[k];
}
