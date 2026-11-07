// FIX126 F4 — a plan card after a reload. chat-edge links the file an
// execute produced to the assistant message that wrote the plan; GET
// /messages returns it on that message's `files` (is_generated=true). The
// card opens in its done state from that file instead of offering
// "สร้างเอกสาร" again, the file is not listed a second time under the
// message, and execute sends the message id so the server can link it.
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
vi.mock("@/lib/api", () => ({ api: mockApi, ApiError: class extends Error {} }));
vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));
vi.mock("@/components/ui/Toast", () => ({ toast: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";
import type { FileAttachment, Message } from "@/lib/types";
import { useChatStore } from "@/stores/chatStore";

import { MessageBubble } from "./MessageBubble";

const MSG_ID = "7a1c2b3d-0000-4000-8000-00000000abcd";
const initial = useChatStore.getState();

const docPlan = { format: "docx", output_filename: "report.docx", actions: [{ type: "add_heading", text: "Hi", level: 1 }] };
const excelPlan = { output_filename: "sales.xlsx", actions: [{ type: "create_sheet", sheet_name: "S" }] };
const pptxPlan = { output_filename: "deck.pptx", slides: [{ type: "title", title: "T" }] };

const fence = (kind: string, plan: unknown) => `Here is the plan.\n\n:::${kind}\n${JSON.stringify(plan)}\n:::\n`;
const gen = (over: Partial<FileAttachment>): FileAttachment =>
  ({ id: "g1", filename: "report.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", file_size_bytes: 2048, extraction_status: "completed", created_at: "2026-09-22T00:00:00Z", is_generated: true, ...over }) as FileAttachment;
const msg = (content: string, files?: FileAttachment[]): Message =>
  ({ id: MSG_ID, role: "assistant", content, status: "completed", created_at: "2026-09-22T00:00:00Z", files }) as Message;

beforeEach(() => {
  vi.clearAllMocks();
  useI18nStore.setState({ locale: "th" });
  useChatStore.setState({ ...initial }, true);
});
afterEach(cleanup);

describe("FIX126 F4 — the doc plan card after a reload", () => {
  it("with the generated file on the message: done, no execute button, file shown once", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg(fence("doc-plan", docPlan), [gen({ change_log: ["Added heading: Hi"] })])}
      />,
    );
    expect(screen.queryByRole("button", { name: /สร้างเอกสาร/ })).toBeNull();
    expect(screen.getAllByText("report.docx")).toHaveLength(1); // the result card, no duplicate chip
    expect(screen.getByTestId("plan-result-open")).toBeTruthy();
    expect(screen.getByText("Added heading: Hi")).toBeTruthy();
  });

  it("without the file: the execute button, and execute sends the message id", async () => {
    mockApi.executeDocumentPlan.mockResolvedValueOnce({
      file: gen({}),
      validation: { valid: true, warnings: [], errors: [], stats: {} },
      generation: {},
      changes: [],
    });
    render(<MessageBubble convId="c1" message={msg(fence("doc-plan", docPlan))} />);
    const btn = screen.getByRole("button", { name: /สร้างเอกสาร/ });
    fireEvent.click(btn);
    await waitFor(() => expect(mockApi.executeDocumentPlan).toHaveBeenCalledTimes(1));
    expect(mockApi.executeDocumentPlan.mock.calls[0][1]).toMatchObject({ format: "docx", message_id: MSG_ID });
  });

  it("a generated file of another format does not close the plan", () => {
    render(<MessageBubble convId="c1" message={msg(fence("doc-plan", docPlan), [gen({ filename: "other.xlsx" })])} />);
    expect(screen.getByRole("button", { name: /สร้างเอกสาร/ })).toBeTruthy();
  });

  it("the file named by output_filename wins over another of the same type", () => {
    render(
      <MessageBubble
        convId="c1"
        message={msg(fence("doc-plan", docPlan), [gen({ id: "g0", filename: "draft.docx" }), gen({ id: "g1", filename: "report.docx" })])}
      />,
    );
    // report.docx is the card's title; draft.docx stays an ordinary attachment.
    expect(screen.getAllByText("report.docx")).toHaveLength(1);
    expect(screen.getAllByText("draft.docx").length).toBeGreaterThan(0);
  });
});

describe("FIX126 F4 — Excel and PowerPoint cards", () => {
  it("excel: done with the workbook, no execute button; without it, execute sends message_id", async () => {
    const { unmount } = render(
      <MessageBubble convId="c1" message={msg(fence("excel-plan", excelPlan), [gen({ filename: "sales.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" })])} />,
    );
    expect(screen.queryByRole("button", { name: /ดำเนินการตามแผน/ })).toBeNull();
    expect(screen.getAllByText("sales.xlsx")).toHaveLength(1);
    unmount();
    mockApi.executeExcelPlan.mockResolvedValueOnce({ file: gen({ filename: "sales.xlsx" }), validation: { valid: true, warnings: [], issues: [], stats: {} }, changes: [] });
    render(<MessageBubble convId="c1" message={msg(fence("excel-plan", excelPlan))} />);
    fireEvent.click(screen.getByRole("button", { name: /ดำเนินการตามแผน/ }));
    await waitFor(() => expect(mockApi.executeExcelPlan).toHaveBeenCalledTimes(1));
    expect(mockApi.executeExcelPlan.mock.calls[0][1]).toMatchObject({ message_id: MSG_ID });
  });

  it("pptx: done with the deck, no execute button; without it, the button", () => {
    const { unmount } = render(
      <MessageBubble convId="c1" message={msg(fence("pptx-plan", pptxPlan), [gen({ filename: "deck.pptx", content_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation" })])} />,
    );
    expect(screen.queryByRole("button", { name: /สร้างงานนำเสนอ/ })).toBeNull();
    unmount();
    render(<MessageBubble convId="c1" message={msg(fence("pptx-plan", pptxPlan))} />);
    expect(screen.getByRole("button", { name: /สร้างงานนำเสนอ/ })).toBeTruthy();
  });
});
