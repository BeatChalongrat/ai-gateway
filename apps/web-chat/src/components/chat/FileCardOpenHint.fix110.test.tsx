// FIX110 Part B — the plain file card says the name opens the file.
//
// The card has opened a preview since Phase 10 without saying so. Its size
// line now ends with "· Open" for a previewable file; for an unsupported
// type the name downloads instead and no hint is shown.
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
import type { FileAttachment } from "@/lib/types";
import { useSidePaneStore } from "@/stores/sidePaneStore";

import { MessageAttachments } from "./MessageAttachments";

const file = (over: Partial<FileAttachment>): FileAttachment =>
  ({ id: "f1", filename: "report.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", file_size_bytes: 4946, extraction_status: "completed", created_at: "2026-09-14T00:00:00Z", is_generated: true, ...over }) as FileAttachment;

beforeEach(() => {
  useI18nStore.setState({ locale: "en" });
  useSidePaneStore.setState({ pane: null, hostMounted: false, dockedWidth: 0 });
  mockDownload.mockClear();
});
afterEach(cleanup);

describe("FIX110 Part B — the file card says the name opens the file", () => {
  it("a previewable file shows the Open hint and the name opens the preview", () => {
    useSidePaneStore.setState({ hostMounted: true });
    const f = file({ filename: "notes.txt", content_type: "text/plain" });
    render(<MessageAttachments files={[f]} />);
    expect(screen.getByTestId("file-card-open-hint")).toHaveTextContent("Open");
    fireEvent.click(screen.getByTestId("file-card-preview"));
    expect(useSidePaneStore.getState().pane).toEqual({ kind: "preview", file: f });
    // Download is still its own button, unchanged.
    expect(screen.getByTestId("file-card-download")).toBeTruthy();
  });

  it("an unsupported type shows no hint; the name downloads instead of opening a 'no preview' view", () => {
    useSidePaneStore.setState({ hostMounted: true });
    const f = file({ filename: "bundle.zip", content_type: "application/zip" });
    render(<MessageAttachments files={[f]} />);
    expect(screen.queryByTestId("file-card-open-hint")).toBeNull();
    expect(screen.queryByTestId("file-card-preview")).toBeNull();
    fireEvent.click(screen.getByTestId("file-card-name-download"));
    expect(mockDownload).toHaveBeenCalledWith("f1", "bundle.zip");
    expect(useSidePaneStore.getState().pane).toBeNull();
  });

  it("the hint is one inline span on the existing size line — no extra row", () => {
    const f = file({ filename: "report.xlsx" });
    render(<MessageAttachments files={[f]} />);
    const hint = screen.getByTestId("file-card-open-hint");
    expect(hint.tagName).toBe("SPAN");
    expect(hint.parentElement?.textContent).toContain("5 KB");
  });
});
