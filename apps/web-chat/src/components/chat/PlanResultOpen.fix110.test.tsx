// FIX110 Part A — a file the assistant wrote can be opened in the chat.
//
// The Excel / Word / PowerPoint result cards used to offer Download alone.
// They now carry an Open button that goes through the same opener a
// user-attached file uses: the docked side pane when the page hosts it, the
// modal otherwise. The button is absent for a type the viewer cannot show.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockDownload } = vi.hoisted(() => ({ mockDownload: vi.fn(async () => {}) }));

vi.mock("@/lib/api", () => ({
  api: {
    downloadFile: mockDownload,
    getFileText: vi.fn(async () => ({ text: "hello", extraction_status: "completed" })),
    getFilePreviewData: vi.fn(async () => {
      throw new Error("415");
    }),
    fileObjectUrl: vi.fn(async () => "blob:x"),
  },
  ApiError: class ApiError extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";
import type { DocExecuteResponse, ExcelExecuteResponse, FileAttachment } from "@/lib/types";
import { useSidePaneStore } from "@/stores/sidePaneStore";

import { DocResultCard } from "./DocPlanCard";
import { ExcelResultCard } from "./ExcelPlanCard";
import { PptxResultCard } from "./PptxPlanCard";
import { OpenPreviewButton } from "./planCardUI";

const file = (over: Partial<FileAttachment>): FileAttachment =>
  ({ id: "f1", filename: "report.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", file_size_bytes: 4946, extraction_status: "completed", created_at: "2026-09-14T00:00:00Z", is_generated: true, ...over }) as FileAttachment;

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
  useSidePaneStore.setState({ pane: null, hostMounted: false, dockedWidth: 0 });
  mockDownload.mockClear();
});
afterEach(cleanup);

describe("FIX110 Part A — result cards open the file", () => {
  const excel: ExcelExecuteResponse = { file: file({}), validation: null, changes: [] } as unknown as ExcelExecuteResponse;

  it("the Excel result card has Open beside Download; with a pane host, Open docks the preview", () => {
    useSidePaneStore.setState({ hostMounted: true });
    render(<ExcelResultCard result={excel} />);
    const open = screen.getByTestId("plan-result-open");
    expect(open).toHaveTextContent("Open");
    expect(screen.getByText("Download")).toBeTruthy();
    fireEvent.click(open);
    expect(useSidePaneStore.getState().pane).toEqual({ kind: "preview", file: excel.file });
    expect(screen.queryByTestId("preview-backdrop")).toBeNull();
    expect(mockDownload).not.toHaveBeenCalled();
  });

  it("without a pane host (quick-ask, share view) Open falls back to the modal", async () => {
    render(<ExcelResultCard result={excel} />);
    fireEvent.click(screen.getByTestId("plan-result-open"));
    expect(useSidePaneStore.getState().pane).toBeNull();
    expect(await screen.findByTestId("preview-backdrop")).toBeTruthy();
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toBe("report.xlsx");
  });

  it("the Word (docx and pdf) and PowerPoint cards carry the same button", () => {
    const docx = { file: file({ filename: "memo.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }), validation: null, generation: null, changes: [] } as unknown as DocExecuteResponse;
    const pdf = { file: file({ filename: "memo.pdf", content_type: "application/pdf" }), validation: null, generation: null, changes: [] } as unknown as DocExecuteResponse;
    const pptx = { file: file({ filename: "deck.pptx", content_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation" }), validation: null, generation: null, changes: [] } as unknown as DocExecuteResponse;
    const { unmount: u1 } = render(<DocResultCard result={docx} />);
    expect(screen.getByTestId("plan-result-open")).toBeTruthy();
    u1();
    const { unmount: u2 } = render(<DocResultCard result={pdf} />);
    expect(screen.getByTestId("plan-result-open")).toBeTruthy();
    u2();
    render(<PptxResultCard result={pptx} />);
    expect(screen.getByTestId("plan-result-open")).toBeTruthy();
  });

  it("a generated file the viewer cannot show gets no Open button — Download stands alone", () => {
    render(<OpenPreviewButton file={file({ filename: "bundle.zip", content_type: "application/zip" })} />);
    expect(screen.queryByTestId("plan-result-open")).toBeNull();
  });

  it("the file's content type is not needed: the extension alone decides (octet-stream uploads)", () => {
    render(<OpenPreviewButton file={file({ filename: "report.xlsx", content_type: "application/octet-stream" })} />);
    expect(screen.getByTestId("plan-result-open")).toBeTruthy();
  });

  it("Thai label", () => {
    useI18nStore.setState({ locale: "th" });
    render(<ExcelResultCard result={excel} />);
    expect(screen.getByTestId("plan-result-open")).toHaveTextContent("เปิดดู");
  });
});
