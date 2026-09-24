// FIX131 — the plan card when a document is made from an attachment.
//
// NIGHT3: a PDF → Word plan answered "doc: generator failed: script reported
// failure" (raw English on the card) and, after a reload, the card offered
// "สร้างเอกสาร" again as if nothing had happened. Pinned here:
//   - in progress: the button says it is working and cannot be pressed twice;
//   - done: the result names the file that went in and the one that came
//     out ("จาก fixture.pdf → สร้าง summary.docx"), and is not "v2" of it;
//   - failed live: a Thai reason with what to do + a retry, never the
//     server's English;
//   - failed after a reload: the failure GET /messages kept on the message
//     (plan_outcomes) — not a pristine "สร้างเอกสาร";
//   - unreadable input: the file's own chip says why (password / scan).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockApi } = vi.hoisted(() => ({
  mockApi: {
    executeDocumentPlan: vi.fn(),
    executeExcelPlan: vi.fn(),
    downloadFile: vi.fn(),
    fileObjectUrl: vi.fn(),
    getFileText: vi.fn(),
    getFilePreviewData: vi.fn(),
  },
}));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public code: string,
      message: string,
      public status: number,
      public details?: Record<string, unknown>,
    ) {
      super(message);
    }
  }
  return { api: mockApi, ApiError };
});
vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { ApiError } from "@/lib/api";
import { useI18nStore } from "@/lib/i18n";
import type { FileAttachment, Message, PlanOutcome } from "@/lib/types";
import type { PendingFile } from "@/stores/chatStore";
import { useChatStore } from "@/stores/chatStore";

import { FilePreviewCard } from "./FilePreviewCard";
import { MessageBubble } from "./MessageBubble";

const CONV = "c1";
const PLAN_ID = "7a1c2b3d-0000-4000-8000-00000000abcd";
const USER_ID = "7a1c2b3d-0000-4000-8000-00000000aaaa";
const PDF_ID = "5f0e0000-0000-4000-8000-0000000000f1";
const initial = useChatStore.getState();

const DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const docPlan = {
  format: "docx",
  source_file_id: PDF_ID,
  output_filename: "summary.docx",
  actions: [{ type: "add_heading", text: "สรุป", level: 1 }],
};
const fence = (kind: string, plan: unknown) => `สรุปเป็น Word ให้แล้ว\n\n:::${kind}\n${JSON.stringify(plan)}\n:::\n`;
const file = (over: Partial<FileAttachment>): FileAttachment =>
  ({
    id: PDF_ID,
    filename: "fixture.pdf",
    content_type: "application/pdf",
    file_size_bytes: 2048,
    extraction_status: "completed",
    created_at: "2026-09-24T00:00:00Z",
    ...over,
  }) as FileAttachment;
const planMsg = (content: string, extra: Partial<Message> = {}): Message =>
  ({ id: PLAN_ID, role: "assistant", content, status: "completed", created_at: "2026-09-24T00:00:01Z", ...extra }) as Message;
const userMsg = (files: FileAttachment[]): Message =>
  ({ id: USER_ID, role: "user", content: "ช่วยสรุป PDF นี้เป็น Word", status: "completed", created_at: "2026-09-24T00:00:00Z", files }) as Message;
const outcome = (over: Partial<PlanOutcome>): PlanOutcome => ({
  fence: "doc-plan",
  format: "docx",
  output_filename: "summary.docx",
  status: "failed",
  reason: "generator_failed",
  at: "2026-09-24T00:00:02Z",
  ...over,
});

/** The conversation the page holds: the user's message with the PDF. */
function seed(files: FileAttachment[] = [file({})]) {
  useChatStore.setState({ messages: { [CONV]: [userMsg(files)] } });
}

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...initial }, true);
  seed();
});
afterEach(cleanup);

describe("FIX131 — the doc plan card, from a PDF to a Word file", () => {
  it("names the input before it runs, and is not pressable twice while running", async () => {
    let resolve: (v: unknown) => void = () => {};
    mockApi.executeDocumentPlan.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", docPlan))} />);
    expect(screen.getByTestId("plan-provenance").textContent).toContain("จาก fixture.pdf");

    fireEvent.click(screen.getByRole("button", { name: /สร้างเอกสาร/ }));
    const busy = await screen.findByRole("button", { name: /กำลังสร้าง/ });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(busy);
    expect(mockApi.executeDocumentPlan).toHaveBeenCalledTimes(1);
    expect(mockApi.executeDocumentPlan.mock.calls[0][1]).toMatchObject({ source_file_id: PDF_ID, format: "docx" });
    resolve({
      file: file({ id: "g1", filename: "summary.docx", content_type: DOCX_CT, is_generated: true, version: 2,
        made_from: { id: PDF_ID, filename: "fixture.pdf", role: "input" } }),
      validation: { valid: true, warnings: [], errors: [], stats: {} },
      generation: {},
      changes: [],
    });
    expect(await screen.findByText("จาก fixture.pdf → สร้าง summary.docx")).toBeTruthy();
  });

  it("done: says which file went in and which came out, and is not 'v2' of the PDF", () => {
    const gen = file({ id: "g1", filename: "summary.docx", content_type: DOCX_CT, is_generated: true, version: 2,
      made_from: { id: PDF_ID, filename: "fixture.pdf", role: "input" } });
    render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", docPlan), { files: [gen] })} />);
    expect(screen.getByTestId("plan-provenance").textContent).toBe("จาก fixture.pdf → สร้าง summary.docx");
    expect(screen.queryByText("v2")).toBeNull();
    expect(screen.queryByRole("button", { name: /สร้างเอกสาร/ })).toBeNull();
  });

  it("an edit of a .docx keeps its version and says 'แก้ไขจาก'", () => {
    const gen = file({ id: "g2", filename: "draft_edited.docx", content_type: DOCX_CT, is_generated: true, version: 2,
      made_from: { id: "d1", filename: "draft.docx", role: "edit" } });
    render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", { ...docPlan, output_filename: "draft_edited.docx" }), { files: [gen] })} />);
    expect(screen.getByTestId("plan-provenance").textContent).toBe("แก้ไขจาก draft.docx → draft_edited.docx");
    expect(screen.getByText("v2")).toBeTruthy();
  });

  it("failed live: a Thai reason with what to do and a retry — never the server's English", async () => {
    mockApi.executeDocumentPlan.mockRejectedValueOnce(
      new ApiError("INTERNAL_ERROR", "doc: generator failed: script reported failure", 502, { reason: "generator_failed" }),
    );
    render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", docPlan))} />);
    fireEvent.click(screen.getByRole("button", { name: /สร้างเอกสาร/ }));
    const notice = await screen.findByTestId("plan-failure");
    expect(notice.textContent).toBe("ไม่สำเร็จ — สร้างไฟล์ Word ไม่สำเร็จ — ลองกดสร้างอีกครั้ง หรือขอให้ผู้ช่วยสรุปใหม่");
    expect(screen.queryByText(/generator failed|script reported/)).toBeNull();

    mockApi.executeDocumentPlan.mockResolvedValueOnce({
      file: file({ id: "g1", filename: "summary.docx", content_type: DOCX_CT, is_generated: true }),
      validation: { valid: true, warnings: [], errors: [], stats: {} },
      generation: {},
      changes: [],
    });
    fireEvent.click(screen.getByRole("button", { name: /ลองอีกครั้ง/ }));
    await waitFor(() => expect(mockApi.executeDocumentPlan).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId("plan-failure")).toBeNull());
  });

  it("failed live on a locked PDF: names the file and what to do", async () => {
    mockApi.executeDocumentPlan.mockRejectedValueOnce(
      new ApiError("VALIDATION_ERROR", "the source file is password-protected", 422,
        { reason: "source_encrypted", source_filename: "locked.pdf" }),
    );
    render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", docPlan))} />);
    fireEvent.click(screen.getByRole("button", { name: /สร้างเอกสาร/ }));
    expect((await screen.findByTestId("plan-failure")).textContent).toContain(
      "ไฟล์ locked.pdf มีรหัสผ่าน — ปลดล็อกแล้วอัปโหลดใหม่",
    );
  });

  it("failed, then reloaded: the kept failure, not a pristine 'สร้างเอกสาร'", () => {
    const outcomes = [outcome({ reason: "source_encrypted", source_file_id: PDF_ID, source_filename: "locked.pdf" })];
    render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", docPlan), { plan_outcomes: outcomes })} />);
    const notice = screen.getByTestId("plan-failure");
    expect(notice.getAttribute("data-reason")).toBe("source_encrypted");
    expect(notice.textContent).toContain("ไฟล์ locked.pdf มีรหัสผ่าน — ปลดล็อกแล้วอัปโหลดใหม่");
    expect(screen.getByRole("button", { name: /ลองอีกครั้ง/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /สร้างเอกสาร/ })).toBeNull();
  });

  it("a kept failure of another card, or a file that was made after all, does not show", () => {
    const other = [outcome({ output_filename: "other.docx" })];
    const { unmount } = render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", docPlan), { plan_outcomes: other })} />);
    expect(screen.queryByTestId("plan-failure")).toBeNull();
    expect(screen.getByRole("button", { name: /สร้างเอกสาร/ })).toBeTruthy();
    unmount();
    const gen = file({ id: "g1", filename: "summary.docx", content_type: DOCX_CT, is_generated: true });
    render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", docPlan), { files: [gen], plan_outcomes: [outcome({})] })} />);
    expect(screen.queryByTestId("plan-failure")).toBeNull();
    expect(screen.getByTestId("plan-result-open")).toBeTruthy();
  });

  it("English UI: the same reasons in English", () => {
    useI18nStore.setState({ locale: "en" });
    render(<MessageBubble convId={CONV} message={planMsg(fence("doc-plan", docPlan), { plan_outcomes: [outcome({ reason: "sandbox_unavailable" })] })} />);
    expect(screen.getByTestId("plan-failure").textContent).toBe(
      "Failed — The file generator is unavailable right now — try again in a moment.",
    );
  });
});

describe("FIX131 — PowerPoint and Excel cards keep their failure too", () => {
  it("pptx: a kept timeout, and the retry", () => {
    const plan = { format: "pptx", source_file_id: PDF_ID, output_filename: "deck.pptx", slides: [{ type: "title", title: "T" }] };
    render(<MessageBubble convId={CONV} message={planMsg(fence("pptx-plan", plan), {
      plan_outcomes: [outcome({ fence: "pptx-plan", format: "pptx", output_filename: "deck.pptx", reason: "generator_timeout" })],
    })} />);
    expect(screen.getByTestId("plan-failure").textContent).toContain("สร้างไฟล์ PowerPoint นานเกินไป");
    expect(screen.getByTestId("plan-provenance").textContent).toContain("จาก fixture.pdf");
    expect(screen.queryByRole("button", { name: /สร้างงานนำเสนอ/ })).toBeNull();
  });

  it("excel: a scan with no text, named", () => {
    const plan = { source_file_id: PDF_ID, output_filename: "data.xlsx", actions: [{ type: "create_sheet", sheet_name: "S" }] };
    render(<MessageBubble convId={CONV} message={planMsg(fence("excel-plan", plan), {
      plan_outcomes: [outcome({ fence: "excel-plan", format: "xlsx", output_filename: "data.xlsx", reason: "source_scanned", source_filename: "scan.pdf" })],
    })} />);
    expect(screen.getByTestId("plan-failure").textContent).toContain("ไฟล์ scan.pdf เป็นภาพสแกนที่อ่านข้อความไม่ได้");
    expect(screen.getByRole("button", { name: /ลองอีกครั้ง/ })).toBeTruthy();
  });
});

describe("FIX131 — an unreadable input says why on its own chip", () => {
  it("on the sent message (and after a reload)", () => {
    const locked = file({ id: "f2", filename: "locked.pdf", extraction_status: "failed", unreadable_reason: "encrypted" });
    const old = file({ id: "f3", filename: "old.pdf", extraction_status: "failed" });
    render(<MessageBubble convId={CONV} message={userMsg([locked, old])} />);
    const chips = screen.getAllByTestId("file-card-unreadable");
    expect(chips.map((c) => c.textContent)).toEqual([
      "ไฟล์นี้มีรหัสผ่าน — ปลดล็อกแล้วอัปโหลดใหม่",
      "อ่านไฟล์ไม่ได้ — ไฟล์อาจเสียหายหรือมีรหัสผ่าน",
    ]);
  });

  it("in the composer", () => {
    const pending = {
      id: "p1",
      file: new File(["x"], "scan.pdf", { type: "application/pdf" }),
      status: "ready",
      progress: 100,
      serverId: "s1",
      extractionStatus: "failed",
      unreadableReason: "scanned",
    } as PendingFile;
    render(<FilePreviewCard pending={pending} onRemove={() => {}} />);
    expect(screen.getByTestId("file-unreadable").textContent).toBe(
      "เป็นภาพสแกนที่อ่านข้อความไม่ได้ — อัปโหลดไฟล์ที่มีข้อความ หรือสแกนให้คมชัดขึ้น",
    );
  });
});
