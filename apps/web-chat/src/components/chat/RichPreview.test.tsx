// Phase 16 — rich file preview (spreadsheet grid, document, deck).
//
// The load-bearing assertions here:
//   * an .xlsx opens as a real GRID with sheet tabs, not a text dump;
//   * "load more" APPENDS the next window and keeps row numbers true —
//     the replacement for "showing the first part only";
//   * cell text is escaped: a cell containing markup is shown, not run;
//   * a 415/503 from the structured route degrades to the flat text the
//     preview used before, so no file becomes less viewable than it was.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    fileObjectUrl: vi.fn(),
    downloadFile: vi.fn(),
    getFileText: vi.fn(),
    getFilePreviewData: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: mockApi,
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";
import { dictionaries } from "@/lib/i18n/dict";
import { richPreviewKindFor, rowLabel } from "@/lib/previewData";
import type { FileAttachment } from "@/lib/types";

import { FilePreviewModal, previewKindFor } from "./FilePreviewModal";

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "en" });
  global.URL.createObjectURL = vi.fn(() => "blob:mock");
  global.URL.revokeObjectURL = vi.fn();
});

function file(extra?: Partial<FileAttachment>): FileAttachment {
  return {
    id: "f1",
    conversation_id: "c1",
    filename: "member_no_part_003.xlsx",
    content_type:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    file_size_bytes: 4096,
    extraction_status: "completed",
    created_at: new Date().toISOString(),
    download_url: "/files/f1/download",
    ...extra,
  } as FileAttachment;
}

function sheetPayload(extra?: Record<string, unknown>) {
  return {
    kind: "sheet",
    sheet: {
      names: ["ยอดขาย", "Notes"],
      index: 0,
      name: "ยอดขาย",
      columns: ["A", "B"],
      rows: [
        ["ชื่อ", "จำนวน"],
        ["สมชาย", "12"],
      ],
      offset: 0,
      total_rows: 500,
      has_more: true,
      columns_truncated: false,
      ...extra,
    },
  };
}

// ── Routing ────────────────────────────────────────────────────────────

describe("richPreviewKindFor", () => {
  it("routes office types to their structured view", () => {
    expect(richPreviewKindFor({ content_type: "", filename: "a.xlsx" })).toBe("sheet");
    expect(richPreviewKindFor({ content_type: "text/csv", filename: "a.csv" })).toBe("sheet");
    expect(richPreviewKindFor({ content_type: "", filename: "a.docx" })).toBe("document");
    expect(richPreviewKindFor({ content_type: "", filename: "a.pptx" })).toBe("slides");
  });

  it("leaves everything else to the renderers that already worked", () => {
    expect(richPreviewKindFor({ content_type: "application/pdf", filename: "a.pdf" })).toBeNull();
    expect(richPreviewKindFor({ content_type: "image/png", filename: "a.png" })).toBeNull();
    expect(richPreviewKindFor({ content_type: "text/plain", filename: "a.txt" })).toBeNull();
    expect(richPreviewKindFor({ content_type: "application/zip", filename: "a.zip" })).toBeNull();
  });

  it("does not disturb the Phase 10 kind routing", () => {
    // The flat-text path must stay reachable for exactly the same files:
    // the structured read is an attempt layered on top, not a rewrite.
    expect(previewKindFor({ content_type: "", filename: "a.xlsx" })).toBe("text");
    expect(previewKindFor({ content_type: "application/pdf", filename: "a.pdf" })).toBe("pdf");
    expect(previewKindFor({ content_type: "application/zip", filename: "a.zip" })).toBe("unsupported");
  });

  it("numbers rows from one, offset by the window", () => {
    expect(rowLabel(0, 0)).toBe("1");
    expect(rowLabel(200, 0)).toBe("201");
  });
});

// ── Spreadsheet ────────────────────────────────────────────────────────

describe("spreadsheet preview", () => {
  it("renders a grid with sheet tabs instead of a text dump", async () => {
    mockApi.getFilePreviewData.mockResolvedValue(sheetPayload());
    render(<FilePreviewModal file={file()} onClose={() => {}} />);

    const grid = await screen.findByTestId("preview-sheet");
    expect(grid.tagName).toBe("TABLE");
    expect(grid).toHaveTextContent("สมชาย");
    // Column letters + row numbers — it should read like the file.
    expect(grid).toHaveTextContent("A");
    expect(screen.getAllByTestId("preview-sheet-tab").map((b) => b.textContent)).toEqual([
      "ยอดขาย",
      "Notes",
    ]);
    // No fallback to the flat text.
    expect(mockApi.getFileText).not.toHaveBeenCalled();
    expect(screen.queryByTestId("preview-text")).toBeNull();
  });

  it("switches sheets by asking for that sheet's window", async () => {
    mockApi.getFilePreviewData.mockResolvedValueOnce(sheetPayload());
    render(<FilePreviewModal file={file()} onClose={() => {}} />);
    await screen.findByTestId("preview-sheet");

    mockApi.getFilePreviewData.mockResolvedValueOnce(
      sheetPayload({
        index: 1,
        name: "Notes",
        rows: [["only", "two"]],
        total_rows: 1,
        has_more: false,
      }),
    );
    fireEvent.click(screen.getAllByTestId("preview-sheet-tab")[1]);

    await waitFor(() => expect(screen.getByTestId("preview-sheet")).toHaveTextContent("only"));
    expect(mockApi.getFilePreviewData).toHaveBeenLastCalledWith(
      "f1",
      expect.objectContaining({ sheet: 1 }),
    );
    // The replaced sheet's rows are gone, not stacked underneath.
    expect(screen.getByTestId("preview-sheet")).not.toHaveTextContent("สมชาย");
  });

  it("appends the next window and keeps the row numbers true", async () => {
    mockApi.getFilePreviewData.mockResolvedValueOnce(sheetPayload());
    render(<FilePreviewModal file={file()} onClose={() => {}} />);
    await screen.findByTestId("preview-sheet");

    mockApi.getFilePreviewData.mockResolvedValueOnce(
      sheetPayload({
        rows: [["third", "3"]],
        offset: 2,
        has_more: false,
      }),
    );
    fireEvent.click(screen.getByTestId("preview-load-more"));

    await waitFor(() => expect(screen.getByTestId("preview-sheet")).toHaveTextContent("third"));
    // Both windows on screen — this is the replacement for
    // "showing the first part only".
    expect(screen.getByTestId("preview-sheet")).toHaveTextContent("สมชาย");
    expect(mockApi.getFilePreviewData).toHaveBeenLastCalledWith(
      "f1",
      expect.objectContaining({ offset: 2 }),
    );
    // Third row is row 3 of the file, not row 1 of the second page.
    const rowHeaders = screen.getAllByRole("rowheader").map((h) => h.textContent);
    expect(rowHeaders).toEqual(["1", "2", "3"]);
    // Nothing left to fetch → the button is gone.
    expect(screen.queryByTestId("preview-load-more")).toBeNull();
  });

  it("says how much of the sheet is on screen", async () => {
    mockApi.getFilePreviewData.mockResolvedValue(sheetPayload());
    render(<FilePreviewModal file={file()} onClose={() => {}} />);
    await screen.findByTestId("preview-sheet");
    expect(screen.getByText("Showing 2 of 500 rows")).toBeInTheDocument();
  });

  it("admits when a sheet is too wide to show every column", async () => {
    mockApi.getFilePreviewData.mockResolvedValue(
      sheetPayload({ columns_truncated: true }),
    );
    render(<FilePreviewModal file={file()} onClose={() => {}} />);
    await screen.findByTestId("preview-sheet");
    expect(
      screen.getByText(new RegExp(dictionaries.en["preview.wideSheet"])),
    ).toBeInTheDocument();
  });

  it("shows an empty sheet as empty rather than as a broken grid", async () => {
    mockApi.getFilePreviewData.mockResolvedValue(
      sheetPayload({ rows: [], columns: [], total_rows: 0, has_more: false }),
    );
    render(<FilePreviewModal file={file()} onClose={() => {}} />);
    expect(
      await screen.findByText(dictionaries.en["preview.emptySheet"]),
    ).toBeInTheDocument();
  });

  it("renders a cell containing markup as TEXT", async () => {
    // A spreadsheet is untrusted input. This is the assertion that keeps
    // it that way — the cell must appear verbatim and create no element.
    const payload = sheetPayload({
      rows: [["<img src=x onerror=alert(1)>", "<b>bold</b>"]],
    });
    mockApi.getFilePreviewData.mockResolvedValue(payload);
    render(<FilePreviewModal file={file()} onClose={() => {}} />);

    const grid = await screen.findByTestId("preview-sheet");
    expect(grid).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(grid.querySelector("img")).toBeNull();
    expect(grid.querySelector("b")).toBeNull();
  });
});

// ── Word / PowerPoint ──────────────────────────────────────────────────

describe("document and deck previews", () => {
  it("keeps a Word document's structure, tables included", async () => {
    mockApi.getFilePreviewData.mockResolvedValue({
      kind: "document",
      document: {
        blocks: [
          { type: "heading", level: 1, text: "รายงานประจำเดือน" },
          { type: "paragraph", text: "บทนำ" },
          { type: "table", rows: [["หัว", "ค่า"], ["A", "1"]] },
          { type: "list", level: 0, text: "ข้อหนึ่ง" },
        ],
        offset: 0,
        total_blocks: 4,
        has_more: false,
      },
    });
    render(
      <FilePreviewModal
        file={file({ filename: "report.docx", content_type: "" })}
        onClose={() => {}}
      />,
    );

    const view = await screen.findByTestId("preview-document");
    expect(screen.getByTestId("preview-heading-1")).toHaveTextContent("รายงานประจำเดือน");
    expect(screen.getByTestId("preview-list")).toHaveTextContent("ข้อหนึ่ง");
    // The table survives as a table — the flat text extractor flattens
    // every table to tab-separated lines at the END of the document.
    const table = view.querySelector("table");
    expect(table).not.toBeNull();
    expect(table).toHaveTextContent("หัว");
  });

  it("shows a deck one slide at a time", async () => {
    mockApi.getFilePreviewData.mockResolvedValue({
      kind: "slides",
      slides: {
        slides: [
          { index: 1, title: "สไลด์ 1", blocks: [{ type: "list", level: 0, text: "เนื้อหา 1" }] },
          // No title AND no text — a picture-only slide. A slide with a
          // title but no bullets is NOT empty and must not say so.
          { index: 2, title: "", blocks: [] },
        ],
        offset: 0,
        total_slides: 2,
        has_more: false,
      },
    });
    render(
      <FilePreviewModal
        file={file({ filename: "deck.pptx", content_type: "" })}
        onClose={() => {}}
      />,
    );

    await screen.findByTestId("preview-slides");
    const slides = screen.getAllByTestId("preview-slide");
    expect(slides).toHaveLength(2);
    expect(slides[0]).toHaveTextContent("สไลด์ 1");
    expect(slides[0]).toHaveTextContent("เนื้อหา 1");
    expect(screen.getByText("Slide 1 of 2")).toBeInTheDocument();
    expect(slides[1]).toHaveTextContent(dictionaries.en["preview.emptySlide"]);
  });

  it("pages through a long deck", async () => {
    mockApi.getFilePreviewData.mockResolvedValueOnce({
      kind: "slides",
      slides: {
        slides: [{ index: 1, title: "one", blocks: [] }],
        offset: 0,
        total_slides: 2,
        has_more: true,
      },
    });
    render(
      <FilePreviewModal
        file={file({ filename: "deck.pptx", content_type: "" })}
        onClose={() => {}}
      />,
    );
    await screen.findByTestId("preview-slides");

    mockApi.getFilePreviewData.mockResolvedValueOnce({
      kind: "slides",
      slides: {
        slides: [{ index: 2, title: "two", blocks: [] }],
        offset: 1,
        total_slides: 2,
        has_more: false,
      },
    });
    fireEvent.click(screen.getByTestId("preview-load-more"));

    await waitFor(() => expect(screen.getAllByTestId("preview-slide")).toHaveLength(2));
    expect(mockApi.getFilePreviewData).toHaveBeenLastCalledWith(
      "f1",
      expect.objectContaining({ offset: 1 }),
    );
  });
});

// ── Degradation ────────────────────────────────────────────────────────

describe("falling back", () => {
  it("uses the flat text when the server has no structured view (415)", async () => {
    mockApi.getFilePreviewData.mockRejectedValue(
      Object.assign(new Error("unsupported"), { status: 415 }),
    );
    mockApi.getFileText.mockResolvedValue({
      extraction_status: "completed",
      text: "ยอดขายรวม 363,000 บาท",
      truncated: false,
      ocr: false,
    });
    render(<FilePreviewModal file={file()} onClose={() => {}} />);

    expect(await screen.findByTestId("preview-text")).toHaveTextContent(
      "ยอดขายรวม 363,000 บาท",
    );
  });

  it("uses the flat text when the structured reader is unavailable (503)", async () => {
    // A deployment without the sandbox must still preview office files
    // exactly as well as it did before Phase 16.
    mockApi.getFilePreviewData.mockRejectedValue(
      Object.assign(new Error("unavailable"), { status: 503 }),
    );
    mockApi.getFileText.mockResolvedValue({
      extraction_status: "completed",
      text: "flat text still works",
      truncated: false,
      ocr: false,
    });
    render(<FilePreviewModal file={file()} onClose={() => {}} />);

    expect(await screen.findByTestId("preview-text")).toHaveTextContent(
      "flat text still works",
    );
  });

  it("never asks for structured data for a PDF or an image", async () => {
    mockApi.fileObjectUrl.mockResolvedValue("blob:pdf");
    render(
      <FilePreviewModal
        file={file({ filename: "report.pdf", content_type: "application/pdf" })}
        onClose={() => {}}
      />,
    );
    await screen.findByTestId("preview-pdf");
    expect(mockApi.getFilePreviewData).not.toHaveBeenCalled();
  });

  it("keeps download available while the grid is on screen", async () => {
    mockApi.getFilePreviewData.mockResolvedValue(sheetPayload());
    render(<FilePreviewModal file={file()} onClose={() => {}} />);
    await screen.findByTestId("preview-sheet");

    fireEvent.click(screen.getByLabelText(dictionaries.en["preview.download"]));
    expect(mockApi.downloadFile).toHaveBeenCalledWith("f1", "member_no_part_003.xlsx");
  });
});
