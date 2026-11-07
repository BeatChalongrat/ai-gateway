// AM2 phase F — a run's exceptions for a person.
//   * open rows: the reason, the claim, where it was read (sheet row / cell,
//     receipt page, printed text, the table's limit "จากตารางนโยบาย"), a
//     policy document's paragraph labelled context only, the proposed action
//     and one button per choice (+ an optional note);
//   * a decision posts {choice: the choice's value as given, note}; decided
//     elsewhere (409) reloads the list; a refused choice (400) is shown;
//   * decided rows say "ตัดสินแล้ว: <label>" + note + time; noted rows are
//     quiet, with no buttons; the header counts; nothing is called rejected;
//   * a run with no exceptions shows nothing at all.
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

import type { RunExceptionRow } from "../types";
import { evidenceParts, groupExceptions, reasonLabelKey } from "./exceptions";
import { RunExceptions } from "./ExceptionsPanel";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

const overLimit: RunExceptionRow = {
  id: "ex-1",
  run_id: "run-1",
  severity: "review",
  reason_code: "over_claim_limit",
  proposed_action: "เกินวงเงินต่อครั้ง 300.00 บาท ให้ผู้อนุมัติตัดสิน",
  choices: [
    { label: "อนุมัติเป็นกรณียกเว้น", value: "approve_exception" },
    { label: "เบิกได้เท่าวงเงิน", value: "reimburse_up_to_limit" },
    { label: "ถามพนักงาน", value: "ask_employee" },
    { label: "ไม่เบิกจ่ายรายการนี้", value: "exclude_from_reimbursement" },
  ],
  status: "open",
  created_at: "2026-10-31T09:00:00Z",
  source_ref: {
    subject: "C02",
    reason: "เกินวงเงินต่อครั้ง",
    detail: "เบิก 1,800.00 บาท เกินวงเงินต่อครั้ง 1,500.00 บาท",
    claim: "C02 สมชาย ค่าอาหาร 1,800.00",
    sheet_name: "รายการเบิก",
    row: 3,
    cell: "F3",
    receipt: "R02.pdf",
    page: 1,
    raw_text: "รวมทั้งสิ้น 1,800.00",
    limit: "1500.00",
    limit_source: "policy_table",
    kb_context: [{ file: "ระเบียบการเบิก.docx", paragraph: 4, text: "ค่าอาหารเบิกได้ประมาณ 2,000 บาท", used_as_limit: false }],
    also_flagged: [{ code: "weekend_or_holiday", reason: "เบิกในวันหยุด", detail: "วันเสาร์" }],
  },
};
const decided: RunExceptionRow = {
  id: "ex-2",
  run_id: "run-1",
  severity: "review",
  reason_code: "missing_receipt",
  choices: [{ label: "ขอใบเสร็จจากพนักงาน", value: "request_receipt" }],
  status: "resolved",
  resolved_at: "2026-10-31T10:00:00Z",
  created_at: "2026-10-31T09:00:00Z",
  source_ref: { reason: "ไม่มีใบเสร็จ ทั้งที่ยอดเกินเกณฑ์", claim: "C04", decision: { choice: "request_receipt", label: "ขอใบเสร็จจากพนักงาน", note: "แจ้งแล้วทางไลน์" } },
};
const noted: RunExceptionRow = {
  id: "ex-3",
  run_id: "run-1",
  severity: "info",
  reason_code: "receipt_not_linked",
  proposed_action: "ใบเสร็จ R99.pdf ไม่มีรายการเบิกใดอ้างถึง",
  choices: [],
  status: "noted",
  created_at: "2026-10-31T09:00:00Z",
  source_ref: { receipt: "R99.pdf" },
};

let rows: RunExceptionRow[] = [];
beforeEach(() => {
  rows = [overLimit, decided, noted];
  raw.mockReset();
  raw.mockImplementation(async (path: string) => (path === "/agent-market/runs/run-1/exceptions" ? json({ exceptions: rows }) : json({}, 404)));
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

const gets = () => raw.mock.calls.filter((c) => c[0] === "/agent-market/runs/run-1/exceptions").length;

describe("helpers", () => {
  it("groups by status and words the evidence", () => {
    const g = groupExceptions([overLimit, decided, noted]);
    expect([g.open.length, g.resolved.length, g.noted.length]).toEqual([1, 1, 1]);
    expect(evidenceParts(overLimit.source_ref, (v) => `฿${v}`).map((p) => p.key)).toEqual([
      "exq.sheetRow",
      "exq.cell",
      "exq.receipt",
      "exq.page",
      "exq.raw",
      "exq.limitTable",
    ]);
    expect(evidenceParts({ limit: "900", limit_source: "other" }, String)[0]!.key).toBe("exq.limit");
    expect(reasonLabelKey("over_claim_limit")).toBe("reason.over_claim_limit");
    expect(reasonLabelKey("expense_no_policy")).toBeNull();
  });
});

describe("RunExceptions", () => {
  it("an open row: reason, claim, evidence, the KB as context only, the proposed action and one button per choice", async () => {
    render(<RunExceptions runId="run-1" />);
    const panel = await screen.findByTestId("am-exceptions");
    expect(screen.getByTestId("am-exceptions-counts").textContent).toBe("รอตัดสิน 1 · ตัดสินแล้ว 1");
    const row = screen.getByTestId("am-exception");
    expect(row.textContent).toContain("เกินวงเงินต่อครั้ง");
    expect(row.textContent).toContain("C02 สมชาย ค่าอาหาร 1,800.00");
    expect(within(row).getByTestId("am-exception-evidence").textContent).toBe(
      "ชีต รายการเบิก แถว 3 · เซลล์ F3 · ใบเสร็จ R02.pdf · หน้า 1 · “รวมทั้งสิ้น 1,800.00” · วงเงิน 1,500.00 บาท จากตารางนโยบาย",
    );
    expect(within(row).getByTestId("am-exception-kb").textContent).toContain("ใช้เป็นบริบทเท่านั้น ไม่ใช่วงเงิน");
    expect(within(row).getByTestId("am-exception-kb").textContent).toContain("“ค่าอาหารเบิกได้ประมาณ 2,000 บาท”");
    expect(row.textContent).toContain("พบด้วย: เบิกในวันหยุด — วันเสาร์");
    expect(within(row).getByTestId("am-exception-proposed").textContent).toBe("ข้อเสนอ: เกินวงเงินต่อครั้ง 300.00 บาท ให้ผู้อนุมัติตัดสิน");
    expect(within(row).getAllByTestId("am-exception-choice").map((b) => b.textContent)).toEqual([
      "อนุมัติเป็นกรณียกเว้น",
      "เบิกได้เท่าวงเงิน",
      "ถามพนักงาน",
      "ไม่เบิกจ่ายรายการนี้",
    ]);
    // nothing on the panel calls anything rejected
    expect(panel.textContent).not.toMatch(/ปฏิเสธ|reject/i);
  });

  it("a choice posts its own value and the note; the row comes back decided", async () => {
    render(<RunExceptions runId="run-1" />);
    await screen.findByTestId("am-exception");
    raw.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/agent-market/exceptions/ex-1/decision") {
        return json({
          exception: {
            ...overLimit,
            status: "resolved",
            resolved_at: "2026-10-31T11:00:00Z",
            source_ref: { ...overLimit.source_ref, decision: { choice: "reimburse_up_to_limit", label: "เบิกได้เท่าวงเงิน", note: "จ่าย 1,500" } },
          },
        });
      }
      return json({ exceptions: rows });
    });
    fireEvent.click(screen.getByTestId("am-exception-note-toggle"));
    fireEvent.change(screen.getByTestId("am-exception-note"), { target: { value: "จ่าย 1,500" } });
    fireEvent.click(screen.getAllByTestId("am-exception-choice")[1]!);
    await waitFor(() => expect(screen.queryByTestId("am-exception")).toBeNull());
    const post = raw.mock.calls.find((c) => c[0] === "/agent-market/exceptions/ex-1/decision")!;
    expect((post[1] as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ choice: "reimburse_up_to_limit", note: "จ่าย 1,500" });
    expect(screen.getByTestId("am-exceptions-counts").textContent).toBe("รอตัดสิน 0 · ตัดสินแล้ว 2");
    expect(screen.getByTestId("am-exceptions-none-open").textContent).toBe("ไม่มีข้อที่รอตัดสินแล้ว");
    fireEvent.click(screen.getByTestId("am-exceptions-resolved-toggle"));
    const done = screen.getAllByTestId("am-exception-resolved").map((r) => r.textContent ?? "");
    expect(done.some((x) => x.includes("ตัดสินแล้ว: เบิกได้เท่าวงเงิน") && x.includes("จ่าย 1,500"))).toBe(true);
  });

  it("decided elsewhere meanwhile (409): the list reloads and says so", async () => {
    render(<RunExceptions runId="run-1" />);
    await screen.findByTestId("am-exception");
    expect(gets()).toBe(1);
    raw.mockImplementation(async (path: string) => {
      if (path === "/agent-market/exceptions/ex-1/decision") return json({ error: { code: "NOT_OPEN", message: "ข้อยกเว้นนี้ไม่ได้รอการตัดสินแล้ว" } }, 409);
      return json({ exceptions: [{ ...overLimit, status: "resolved", source_ref: { ...overLimit.source_ref, decision: { label: "ถามพนักงาน" } } }, decided, noted] });
    });
    fireEvent.click(screen.getAllByTestId("am-exception-choice")[0]!);
    await waitFor(() => expect(gets()).toBe(2));
    expect(await screen.findByTestId("am-exceptions-stale")).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("am-exception")).toBeNull());
  });

  it("a refused choice (400) is shown on the row", async () => {
    render(<RunExceptions runId="run-1" />);
    await screen.findByTestId("am-exception");
    raw.mockImplementation(async () => json({ error: { code: "BAD_CHOICE", message: "ไม่มีตัวเลือกนี้" } }, 400));
    fireEvent.click(screen.getAllByTestId("am-exception-choice")[0]!);
    expect(await within(screen.getByTestId("am-exception")).findByText("ไม่มีตัวเลือกนี้")).toBeTruthy();
    expect(within(screen.getByTestId("am-exception")).getAllByTestId("am-exception-choice")[0]!.hasAttribute("disabled")).toBe(false);
  });

  it("decided rows: the label, the note and the time; noted rows are quiet, with no buttons", async () => {
    render(<RunExceptions runId="run-1" />);
    await screen.findByTestId("am-exceptions");
    fireEvent.click(screen.getByTestId("am-exceptions-resolved-toggle"));
    const r = screen.getByTestId("am-exception-resolved");
    expect(r.textContent).toContain("ตัดสินแล้ว: ขอใบเสร็จจากพนักงาน");
    expect(r.textContent).toContain("แจ้งแล้วทางไลน์");
    expect(r.textContent).toMatch(/2569|2026/);
    expect(within(r).queryAllByRole("button")).toHaveLength(0);
    fireEvent.click(screen.getByTestId("am-exceptions-noted-toggle"));
    const n = screen.getByTestId("am-exception-noted");
    expect(n.textContent).toBe("ใบเสร็จที่ไม่มีรายการเบิกอ้างถึง · ใบเสร็จ R99.pdf ไม่มีรายการเบิกใดอ้างถึง");
    expect(within(screen.getByTestId("am-exceptions-noted")).queryAllByRole("button")).toHaveLength(0);
  });

  it("a run with no exceptions shows nothing", async () => {
    rows = [];
    const { container } = render(<RunExceptions runId="run-1" />);
    await waitFor(() => expect(gets()).toBe(1));
    expect(container.innerHTML).toBe("");
  });
});
