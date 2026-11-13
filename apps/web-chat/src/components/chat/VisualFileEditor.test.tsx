import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { VisualFileEditor } from "./VisualFileEditor";
import { api } from "@/lib/api";
import { useI18nStore } from "@/lib/i18n";
import type { FileAttachment } from "@/lib/types";
import type { VisualInspection } from "@/lib/visualEditor";

vi.mock("@/lib/api", () => ({ api: { editFile: vi.fn(), downloadFile: vi.fn(), getArtifactDraft: vi.fn(), saveArtifactDraft: vi.fn(), deleteArtifactDraft: vi.fn(), latestArtifactWork: vi.fn(), startArtifactWork: vi.fn() } }));
const file: FileAttachment = { id: "original", filename: "test.docx", content_type: "docx", file_size_bytes: 3, extraction_status: "completed", created_at: "2026-11-13", version: 1 };
const visual: VisualInspection = { kind: "docx", sha256: "hash", width: 600, height: 800, fields: [{ id: "body/p/0", value: "Original", page: 0, style: { size: 12 } }], blocks: [{ kind: "paragraph", id: "body/p/0" }], page: 0, page_count: 1, ai_available: true };
beforeEach(() => { vi.resetAllMocks(); useI18nStore.setState({ locale: "en" }); vi.mocked(api.getArtifactDraft).mockResolvedValue({ draft: null }); vi.mocked(api.latestArtifactWork).mockResolvedValue({ work: null }); vi.mocked(api.saveArtifactDraft).mockImplementation(async (_id,draft) => ({ draft: { ...draft, revision: 1 } })); });
afterEach(cleanup);

it("submits the exact selected text and UTF-16 offsets for a rewrite proposal", async () => {
  vi.mocked(api.editFile).mockImplementation(async (_id, r) => r.mode === "visual_inspect" ? { visual } : { versions: [file] });
  vi.mocked(api.startArtifactWork).mockResolvedValue({ work: { id: "job", file_id: file.id, status: "unchanged", stage: "complete", updated_at: "now" } });
  render(<VisualFileEditor file={file} onSaved={vi.fn()} />);
  await screen.findByText("Draft saved"); const textbox = screen.getByRole("textbox", { name: "body/p/0" });
  // jsdom does not synthesize text nodes from innerText like Chromium does.
  textbox.textContent = "Original"; fireEvent.focus(textbox);
  const range = document.createRange(); range.setStart(textbox.firstChild!, 0); range.setEnd(textbox.firstChild!, 4);
  window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range); fireEvent.mouseUp(textbox);
  fireEvent.change(screen.getByRole("textbox", { name: "How should this selection change?" }), { target: { value: "Rewrite only this" } });
  fireEvent.click(screen.getByText("Propose rewrite"));
  await waitFor(() => expect(api.startArtifactWork).toHaveBeenCalledWith(file.id, { action: "rewrite", sha256: "hash", page: 0, instruction: "Rewrite only this", selection: { id: "body/p/0", start: 0, end: 4, text: "Orig" } }));
});

it("saves WYSIWYG paragraph formatting and plain text as a new version", async () => {
  const saved = { ...file, id: "revision", version: 2 };
  vi.mocked(api.editFile).mockImplementation(async (_id, r) => r.mode === "visual_inspect" ? { visual } : r.mode === "history" ? { versions: [file] } : { file: saved });
  const onSaved = vi.fn(); render(<VisualFileEditor file={file} onSaved={onSaved} />);
  const textbox = await screen.findByRole("textbox", { name: "body/p/0" });
  await screen.findByText("Draft saved");
  fireEvent.focus(textbox); fireEvent.input(textbox, { target: { innerText: "Changed" } });
  fireEvent.click(screen.getByText("Bold")); fireEvent.click(screen.getByText("Save new version"));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
  expect(api.editFile).toHaveBeenCalledWith(file.id, expect.objectContaining({ mode: "visual_edit", sha256: "hash", changes: [{ id: "body/p/0", value: "Changed", style: { size: 12, bold: true }, box: undefined }] }));
});

it("calls AI only explicitly, shows report and never invents a saved revision", async () => {
  vi.mocked(api.editFile).mockImplementation(async (_id, r) => r.mode === "visual_inspect" ? { visual } : r.mode === "history" ? { versions: [file] } : { review: { model: "mock-vision", status: "unchanged", pages: 1, attempts: 1, assessments: [{ score: 60, passed: false, issues: ["Still clipped"], changes: [], image_prompt: "" }] } });
  const saved = vi.fn(); render(<VisualFileEditor file={file} onSaved={saved} />);
  await screen.findByRole("textbox");
  expect(vi.mocked(api.editFile).mock.calls.some(([, r]) => r.mode === "ai_repair")).toBe(false);
  vi.mocked(api.startArtifactWork).mockResolvedValue({ work: { id: "job", file_id: file.id, status: "unchanged", stage: "complete", updated_at: "now", review: { model: "mock-vision", status: "unchanged", pages: 1, attempts: 1, assessments: [], warning: "Still clipped" } } });
  fireEvent.click(screen.getByText("AI inspect and repair"));
  expect(await screen.findByText("Still clipped")).toBeInTheDocument();
  expect(api.startArtifactWork).toHaveBeenCalledWith(file.id, { action: "repair", sha256: "hash", page: 0 });
  expect(saved).not.toHaveBeenCalled();
});

it("retains the visual draft after conflicts and disables AI on dirty content", async () => {
  vi.mocked(api.editFile).mockImplementation(async (_id, r) => { if (r.mode === "visual_edit") throw new Error("newer version"); return r.mode === "visual_inspect" ? { visual } : { versions: [file] }; });
  render(<VisualFileEditor file={file} onSaved={vi.fn()} />);
  const textbox = await screen.findByRole("textbox");
  await screen.findByText("Draft saved");
  fireEvent.input(textbox, { target: { innerText: "Unsaved" } });
  expect(screen.getByText("AI inspect and repair")).toBeDisabled();
  fireEvent.click(screen.getByText("Save new version"));
  expect(await screen.findByRole("alert")).toHaveTextContent("newer version");
  expect(textbox.innerText).toBe("Unsaved");
});

it("edits native PDF text fields with geometry rather than footer notes", async () => {
  const pdf = { ...visual, kind: "pdf" as const, fields: [{ ...visual.fields[0], id: "page/1/text/0", box: { left: 10, top: 20, width: 200, height: 30 } }], ai_available: false };
  vi.mocked(api.editFile).mockImplementation(async (_id, r) => r.mode === "visual_inspect" ? { visual: pdf } : { versions: [file] });
  render(<VisualFileEditor file={{ ...file, filename: "test.pdf" }} onSaved={vi.fn()} />);
  const textbox = await screen.findByRole("textbox", { name: "page/1/text/0" });
  await screen.findByText("Draft saved");
  fireEvent.focus(textbox); fireEvent.change(screen.getByLabelText("Width"), { target: { value: "250" } });
  fireEvent.click(screen.getByText("Save new version"));
  await waitFor(() => expect(api.editFile).toHaveBeenCalledWith(file.id, expect.objectContaining({ mode: "visual_edit", changes: [expect.objectContaining({ id: "page/1/text/0", box: { left: 10, top: 20, width: 250, height: 30 } })] })));
});
