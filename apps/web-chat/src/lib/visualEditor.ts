export interface VisualStyle {
  size?: number; bold?: boolean; italic?: boolean; underline?: boolean;
  color?: string; align?: "left" | "center" | "right" | "justify"; space_after?: number;
}
export interface VisualBox { left: number; top: number; width: number; height: number }
export interface VisualField { id: string; value: string; page?: number; style: VisualStyle; box?: VisualBox; insets?: Record<string, number>; vertical?: "top" | "middle" | "bottom" }
export interface VisualBlock { kind: "paragraph" | "table" | "image"; id?: string; rows?: VisualBlock[][][]; image?: string; width?: number; height?: number }
export interface VisualPage { page: number; width: number; height: number; image: string }
export interface VisualInspection {
  kind: "docx" | "pptx" | "pdf" | "image";
  sha256: string; width: number; height: number; fields: VisualField[];
  blocks?: VisualBlock[]; margins?: Record<string, number>;
  page: number; page_count: number; background?: string; pages?: VisualPage[];
  ai_available: boolean; ai_model?: string;
}
export interface VisualReview {
  status: string; model: string; pages: number; attempts: number; warning?: string;
  assessments: { score: number; passed: boolean; issues: string[] }[];
}
