import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { FileEditor } from "./FileEditor";
import { api } from "@/lib/api";
import { useI18nStore } from "@/lib/i18n";
import type { FileAttachment } from "@/lib/types";

vi.mock("@/lib/api", () => ({ api: { editFile: vi.fn(), downloadFile: vi.fn(), getArtifactDraft: vi.fn().mockResolvedValue({ draft: null }), saveArtifactDraft: vi.fn().mockImplementation(async (_id,draft) => ({ draft: { ...draft, revision: 1 } })), deleteArtifactDraft: vi.fn() } }));
const file: FileAttachment = { id: "original", filename: "test.docx", content_type: "docx", file_size_bytes: 3, extraction_status: "completed", created_at: "2026-11-13", version: 1 };
const inspection = { sha256: "hash", format: "docx", fields: [{ id: "body/p/0", value: "Original", type: "text" }], sheets: [], offset: 0, pdf_notes_only: false };
beforeEach(() => { vi.resetAllMocks(); useI18nStore.setState({ locale: "en" }); });
afterEach(cleanup);

it("saves only changed fields with the inspected source fingerprint", async () => {
  const saved = { ...file, id: "revision", version: 2, parent_file_id: file.id };
  vi.mocked(api.editFile).mockImplementation(async (_id, request) => request.mode === "inspect" ? { inspection } : request.mode === "history" ? { versions: [file] } : { file: saved });
  const onSaved = vi.fn(); render(<FileEditor file={file} onSaved={onSaved} />);
  fireEvent.change(await screen.findByLabelText("body/p/0"), { target: { value: "Updated" } });
  fireEvent.click(screen.getByText("Save new version"));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
  expect(api.editFile).toHaveBeenCalledWith(file.id, { mode: "edit", sha256: "hash", sheet: "", offset: 0, changes: [{ id: "body/p/0", value: "Updated", type: "text" }] });
});

it("retains unsaved text and shows conflicts", async () => {
  vi.mocked(api.editFile).mockImplementation(async (_id, req) => {
    if (req.mode === "edit") throw new Error("newer revision; reload");
    return req.mode === "inspect" ? { inspection } : { versions: [file] };
  });
  render(<FileEditor file={file} onSaved={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("body/p/0"), { target: { value: "Unsaved" } });
  fireEvent.click(screen.getByText("Save new version"));
  expect(await screen.findByRole("alert")).toHaveTextContent("newer revision");
  expect(screen.getByLabelText("body/p/0")).toHaveValue("Unsaved");
});

it("restores the selected old version through the current head", async () => {
  const current = { ...file, id: "current", parent_file_id: file.id, version: 2 };
  vi.mocked(api.editFile).mockImplementation(async (_id, req) => req.mode === "inspect" ? { inspection } : req.mode === "history" ? { versions: [file, current] } : { file: { ...current, id: "restored", version: 3 } });
  render(<FileEditor file={current} onSaved={vi.fn()} />);
  await screen.findByLabelText("body/p/0");
  fireEvent.click(screen.getAllByText("Restore as new version")[0]);
  await waitFor(() => expect(api.editFile).toHaveBeenCalledWith("current", { mode: "restore", target_id: "original" }));
});
