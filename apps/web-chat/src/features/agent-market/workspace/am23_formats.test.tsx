// AM23 §1.3/§1.4 — the formats a person can ask for, the way they meet them.
//   * the install wizard shows every format the agent declares, and a PDF as an option on the document it prints
//     ("and a PDF of it"), never as a file beside it;
//   * the files card calls a workbook's PDF for what it is, and its "more files" menu offers every declared format.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import { FormatsStep } from "../install/steps";
import type { Manifest, OutputsPayload } from "../types";
import { OutputsCard } from "./OutputsCard";

// backend/agent_market/packages/bank_reconciliation/agent.json, the parts these screens read
const bank: Manifest = {
  id: "sran.bank-reconciliation",
  inputs: [{ role: "bank", label: "รายการเดินบัญชีจากธนาคาร", required: true, accept: [".xlsx", ".csv", ".pdf"] }],
  outputs: [
    { format: "xlsx", label: "งบกระทบยอด .xlsx", default: true },
    { format: "docx", label: "รายงานกระทบยอด .docx", default: false },
    { format: "pptx", label: "สไลด์สรุปการกระทบยอด .pptx", default: false },
    { format: "pdf", label: "งบกระทบยอด PDF (พิมพ์จาก .xlsx)", default: false, of: "xlsx" },
  ],
  install: { defaults: { formats: ["xlsx"] }, questions: ["company", "theme", "formats"] },
};

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

beforeEach(() => {
  useI18nStore.setState({ locale: "th" });
  raw.mockReset();
  raw.mockImplementation(async () => json({ saved: true }));
});
afterEach(cleanup);

describe("the install wizard's file set", () => {
  it("shows every format the bank reconciliation makes, the workbook ticked, and its PDF under the workbook", async () => {
    const onNext = vi.fn();
    render(<FormatsStep installId="i-1" manifest={bank} onBack={vi.fn()} onNext={onNext} />);
    const step = screen.getByTestId("am-step-formats");
    for (const f of ["xlsx", "docx", "pptx"]) expect(within(step).getByTestId(`am-format-${f}`)).toBeTruthy();
    expect(within(step).queryByTestId("am-format-pdf")).toBeNull(); // not a file beside the others
    const workbook = within(step).getByTestId("am-format-xlsx");
    const pdf = within(workbook).getByTestId("am-format-pdf-of");
    expect(pdf.textContent).toContain("และ PDF ของไฟล์นี้");
    expect(pdf.textContent).toContain("พิมพ์จาก .xlsx");
    const boxes = within(step).getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.filter((b) => b.checked)).toHaveLength(1); // the default: the workbook alone
    // Word, and the workbook's PDF
    fireEvent.click(within(within(step).getByTestId("am-format-docx")).getByRole("checkbox"));
    fireEvent.click(within(pdf).getByRole("checkbox"));
    fireEvent.click(screen.getByTestId("am-formats-save"));
    await waitFor(() => expect(onNext).toHaveBeenCalled());
    const put = raw.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PUT");
    expect(JSON.parse(String((put![1] as RequestInit).body)).value).toEqual(["xlsx", "docx", "pdf"]);
  });

  it("in English", () => {
    useI18nStore.setState({ locale: "en" });
    render(<FormatsStep installId="i-1" manifest={bank} onBack={vi.fn()} onNext={vi.fn()} />);
    expect(screen.getByTestId("am-format-pdf-of").textContent).toContain("and a PDF of it");
  });
});

describe("the files card", () => {
  const outputs: OutputsPayload = {
    run_id: "run-1",
    period_label: "กันยายน 2569",
    artifacts: [
      { id: "a1", name: "งบกระทบยอด_2569-09_v1.xlsx", format: "xlsx", size: 2048, version: 1 },
      { id: "a2", name: "งบกระทบยอด_2569-09_v1.pdf", format: "pdf", size: 8192, version: 1 },
    ],
    more_formats: ["xlsx", "docx", "pptx", "pdf"],
    pdf_of: "xlsx",
  };

  it("calls the workbook's PDF what it is, and offers every format the agent makes", () => {
    render(<OutputsCard outputs={outputs} />);
    const rows = screen.getAllByTestId("am-artifact");
    expect(rows[1].textContent).toContain("ไฟล์ Excel เป็น PDF");
    expect(rows[1].textContent).not.toContain("รายงาน PDF");
    fireEvent.click(screen.getByTestId("am-more-files"));
    const items = screen.getAllByRole("menuitem").map((m) => m.textContent ?? "");
    expect(items).toHaveLength(4);
    expect(items.join(" ")).toContain("Word (.docx)");
    expect(items.join(" ")).toContain("PowerPoint (.pptx)");
    expect(items.join(" ")).toContain("ไฟล์ Excel เป็น PDF");
  });

  it("a report's PDF is still the report's", () => {
    render(<OutputsCard outputs={{ ...outputs, pdf_of: "docx" }} />);
    expect(screen.getAllByTestId("am-artifact")[1].textContent).toContain("รายงาน PDF");
  });
});
