// Structured file preview — Phase 16.
//
// GET /files/{id}/preview-data returns what a person needs to READ a
// document: a spreadsheet as sheets and cells, a Word file as ordered
// blocks, a deck as slides. It is a companion to /files/{id}/text (the
// flat text the MODEL was given), not a replacement — the text stays
// the fallback whenever the structured read is unavailable.
//
// The shapes here mirror scripts/preview_data.py exactly. Cell and block
// text is plain text and is rendered as such: nothing from a document
// ever reaches the DOM as markup.

/** One paragraph-level element of a Word document or a slide. */
export type PreviewBlock =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; level?: number; text: string }
  | { type: "table"; rows: string[][] };

export interface SheetPreview {
  /** Every sheet in the workbook — the tab strip. */
  names: string[];
  /** Which one this payload holds. */
  index: number;
  name: string;
  /** Column letters (A, B, … AA) for the header row. */
  columns: string[];
  rows: string[][];
  /** Index of rows[0] within the sheet, so row numbers stay true. */
  offset: number;
  total_rows: number;
  has_more: boolean;
  /** The sheet is wider than the column budget; some columns are absent. */
  columns_truncated: boolean;
}

export interface DocumentPreview {
  blocks: PreviewBlock[];
  offset: number;
  total_blocks: number;
  has_more: boolean;
}

export interface SlidePreview {
  index: number;
  title: string;
  blocks: PreviewBlock[];
}

export interface SlidesPreview {
  slides: SlidePreview[];
  offset: number;
  total_slides: number;
  has_more: boolean;
}

export type PreviewData =
  | { kind: "sheet"; sheet: SheetPreview }
  | { kind: "document"; document: DocumentPreview }
  | { kind: "slides"; slides: SlidesPreview };

export type RichPreviewKind = "sheet" | "document" | "slides";

const RICH_TYPES: Record<string, RichPreviewKind> = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "sheet",
  "application/vnd.ms-excel.sheet.macroEnabled.12": "sheet",
  "text/csv": "sheet",
  "application/csv": "sheet",
  "text/tab-separated-values": "sheet",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "slides",
};

const RICH_EXTENSIONS: Record<string, RichPreviewKind> = {
  xlsx: "sheet",
  xlsm: "sheet",
  csv: "sheet",
  tsv: "sheet",
  docx: "document",
  pptx: "slides",
};

/**
 * Which structured view a file gets, or null when it has none.
 *
 * Separate from previewKindFor() rather than folded into it: this is a
 * a hint for an OPTIONAL request. A file that qualifies here still
 * renders as flat text when the server answers 415/503, so the flat-text
 * routing must stay exactly as it was.
 *
 * Content type first, extension second — the same precedence the server
 * uses (service.PreviewDataKind), because uploads regularly arrive as
 * application/octet-stream.
 */
export function richPreviewKindFor(file: {
  content_type: string;
  filename: string;
}): RichPreviewKind | null {
  const ct = (file.content_type || "").split(";")[0].trim().toLowerCase();
  if (RICH_TYPES[ct]) return RICH_TYPES[ct];
  const ext = (file.filename || "").toLowerCase().split(".").pop() ?? "";
  return RICH_EXTENSIONS[ext] ?? null;
}

/** Excel-style label for the row at `offset + i` (1-based, like the app). */
export function rowLabel(offset: number, i: number): string {
  return String(offset + i + 1);
}
