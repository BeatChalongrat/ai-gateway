import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { useI18nStore } from "@/lib/i18n";
import { PptxPlanCard, PptxResultCard } from "./PptxPlanCard";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, executeDocumentPlan: vi.fn() } };
});
beforeEach(() => useI18nStore.setState({ locale: "en" }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("shows and submits edit actions with the source deck, without fictitious layout or slide count", async () => {
  const plan = { source_file_id: "source", actions: [{ type: "replace_text", find: "2025", replace: "2026" }, { type: "move_slide", slide: 2, to: 1 }] };
  vi.mocked(api.executeDocumentPlan).mockRejectedValue(new Error("offline"));
  render(<PptxPlanCard convId="conversation" plan={plan} />);
  expect(screen.getByText("2 actions")).toBeTruthy();
  expect(screen.queryByText(/16:9/)).toBeNull();
  expect(screen.getByText(/2025.*2026/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(api.executeDocumentPlan).toHaveBeenCalledWith("conversation", expect.objectContaining({ source_file_id: "source", plan, format: "pptx" })));
});

it("renders action change logs without an undefined slide number", () => {
  const result = { file: { id: "file", filename: "edited.pptx", file_size_bytes: 100 }, generation: { success: true, change_log: [{ action: 1, type: "move_slide", status: "ok", detail: "Moved slide 2 to 1" }] } };
  render(<PptxResultCard result={result as never} />);
  expect(screen.getByText(/Moved slide 2 to 1/)).toBeTruthy();
  expect(document.body.textContent).not.toContain("undefined");
});
