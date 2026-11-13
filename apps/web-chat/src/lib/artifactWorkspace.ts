import type { VisualField, VisualPage, VisualReview } from "./visualEditor";
import type { FileAttachment } from "./types";

export interface ArtifactDraftBody { structured?: Record<string, import("./fileEditor").EditorField>; sheet?: string; offset?: number; changes: Record<string, VisualField>; image: { rotation: number; brightness: number; contrast: number }; page: number; selected: string }
export interface ArtifactDraft { sha256: string; revision: number; body: ArtifactDraftBody; updated_at?: string }
export interface WorkSelection { id: string; start: number; end: number; text: string }
export interface ArtifactWorkRequest { action: "repair" | "rewrite"; sha256: string; page: number; instruction?: string; selection?: WorkSelection }
export interface ArtifactWork {
  id: string; file_id: string; status: "running" | "ready" | "unchanged" | "failed" | "cancelled" | "accepting" | "accepted" | "rejected";
  stage: string; error?: string; kind?: string; result_file_id?: string; updated_at: string;
  before_pages?: VisualPage[]; after_pages?: VisualPage[];
  changes?: { id: string; before: VisualField; after: VisualField }[]; review?: VisualReview;
}
export interface ArtifactLibraryItem { file: FileAttachment; conversation_id: string }
export const EMPTY_ARTIFACT_DRAFT: ArtifactDraftBody = { changes: {}, image: { rotation: 0, brightness: 1, contrast: 1 }, page: 0, selected: "" };
export function hasDraftChanges(body: ArtifactDraftBody) { return Object.keys(body.changes).length > 0 || body.image.rotation !== 0 || body.image.brightness !== 1 || body.image.contrast !== 1; }
