import type { FileAttachment, OfficeQualityReport } from "./types";
import type { VisualStyle, VisualBox, VisualInspection, VisualReview } from "./visualEditor";

export interface EditorField { id: string; value: string; type?: string; style?: VisualStyle; box?: VisualBox }
export interface EditorInspection {
  sha256: string; format: string; fields: EditorField[]; sheets: string[];
  offset: number; pdf_notes_only: boolean;
}
export interface EditorRequest {
  mode: "inspect" | "edit" | "history" | "restore" | "visual_inspect" | "visual_edit" | "visual_preview" | "ai_repair";
  page?: number; image?: { rotation?: number; brightness?: number; contrast?: number; crop?: VisualBox };
  sha256?: string; sheet?: string; offset?: number;
  changes?: EditorField[]; target_id?: string;
}
export interface EditorResponse {
  visual?: VisualInspection; review?: VisualReview;
  inspection?: EditorInspection; file?: FileAttachment;
  versions?: FileAttachment[]; quality?: OfficeQualityReport;
}
