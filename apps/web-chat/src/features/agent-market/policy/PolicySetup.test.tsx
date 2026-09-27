// AM2 phase F — the expense policy in the install wizard and in settings.
//   * the step starts from the manifest's sample and says plainly it is one;
//     the one-line rule "limits come only from this table" is there; an edit
//     makes it the company's own (source "install");
//   * a limit that is not a plain amount ("ประมาณ 2,000") is refused here,
//     before anything is sent; the server's own refusal is shown as written;
//   * saved as PUT /defaults/policy (+ /defaults/holidays, or DELETE when a
//     saved calendar is emptied: the package's Thai 2569 holidays apply).
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

import type { Default } from "../types";
import { PolicySection, PolicyStep } from "./PolicySetup";
import { EXPENSE_MANIFEST, SAMPLE } from "./testData";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
const def = (key: string, value: unknown): Default => ({ key, value, source: "install", label: key, note: "", updated_at: "" });

let stored: Default[] = [];
function route(path: string, init?: RequestInit) {
  const method = init?.method ?? "GET";
  if (path === "/agent-market/installs/i-1/defaults" && method === "GET") return json({ defaults: stored });
  if (path.startsWith("/agent-market/installs/i-1/defaults/")) return method === "DELETE" ? json({ removed: true }) : json({ saved: true });
  return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
}
const writes = () =>
  raw.mock.calls
    .filter((c) => ["PUT", "DELETE"].includes((c[1] as RequestInit | undefined)?.method ?? ""))
    .map((c) => ({ method: (c[1] as RequestInit).method, path: String(c[0]), body: (c[1] as RequestInit).body ? JSON.parse(String((c[1] as RequestInit).body)) : undefined }));

beforeEach(() => {
  stored = [];
  raw.mockReset();
  raw.mockImplementation(async (path: string, init?: RequestInit) => route(path, init));
  useI18nStore.setState({ locale: "th" });
});
afterEach(cleanup);

const input = (label: string, within_: HTMLElement = document.body) => within(within_).getByLabelText(label) as HTMLInputElement;
const categoryRow = (code: string) => screen.getAllByTestId("am-policy-category").find((r) => r.getAttribute("data-code") === code)!;

describe("PolicyStep (install wizard)", () => {
  it("starts from the sample, says it is one, and refuses a limit that is not an amount before sending", async () => {
    const onNext = vi.fn();
    render(<PolicyStep installId="i-1" manifest={EXPENSE_MANIFEST} onNext={onNext} />);
    await screen.findByTestId("am-policy-editor");
    expect(screen.getByTestId("am-policy-sample").textContent).toContain("ตารางตัวอย่าง");
    expect(screen.getByTestId("am-policy-only-source").textContent).toBe(
      "วงเงินทุกข้อมาจากตารางนี้เท่านั้น เอกสารระเบียบที่แนบมากับงานจะถูกยกมาเป็นบริบทประกอบ และไม่กลายเป็นวงเงิน",
    );
    expect(screen.getAllByTestId("am-policy-category").map((r) => r.getAttribute("data-code"))).toEqual(["meals", "entertainment", "contractor"]);
    expect(input("วงเงินต่อครั้ง (บาท)", categoryRow("meals")).value).toBe("1500.00");

    // an edit: the sample notice goes (the table is now the company's own)
    fireEvent.change(input("วงเงินต่อครั้ง (บาท)", categoryRow("meals")), { target: { value: "ประมาณ 2,000" } });
    expect(screen.queryByTestId("am-policy-sample")).toBeNull();
    expect(within(categoryRow("meals")).getByTestId("am-policy-error").textContent).toBe("ต้องเป็นจำนวนเงิน เช่น 1,500 (ไม่ใช่ “ประมาณ …”)");

    fireEvent.click(screen.getByTestId("am-policy-save"));
    expect(await screen.findByText("แก้ช่องที่มีข้อความสีแดงก่อนบันทึก")).toBeTruthy();
    expect(writes()).toEqual([]);
    expect(onNext).not.toHaveBeenCalled();

    fireEvent.change(input("วงเงินต่อครั้ง (บาท)", categoryRow("meals")), { target: { value: "๒,๐๐๐" } });
    fireEvent.click(screen.getByTestId("am-policy-save"));
    await waitFor(() => expect(onNext).toHaveBeenCalled());
    const w = writes();
    expect(w).toHaveLength(1); // no calendar typed, none saved before: nothing to send for holidays
    expect(w[0]!.path).toBe("/agent-market/installs/i-1/defaults/policy");
    expect(w[0]!.body.value.source).toBe("install");
    expect(w[0]!.body.value.categories.meals).toEqual({ label: "ค่าอาหาร", per_claim: "2000.00", per_person_month: "5000.00" });
    expect(w[0]!.body.value.receipt_required_above).toBe("500.00");
  });

  it("a WHT rate needs its starting amount; a new category needs a code", async () => {
    render(<PolicyStep installId="i-1" manifest={EXPENSE_MANIFEST} onNext={vi.fn()} />);
    await screen.findByTestId("am-policy-editor");
    fireEvent.change(input("หักเมื่อยอดตั้งแต่ (บาท)", categoryRow("contractor")), { target: { value: "" } });
    expect(within(categoryRow("contractor")).getByTestId("am-policy-error").textContent).toBe("มีอัตราหัก ณ ที่จ่ายแล้ว ต้องระบุว่าหักตั้งแต่ยอดเท่าใด");
    fireEvent.click(screen.getByTestId("am-policy-add-category"));
    fireEvent.click(screen.getByTestId("am-policy-save"));
    const fresh = categoryRow("");
    expect(within(fresh).getByTestId("am-policy-error").textContent).toBe("ยังไม่ได้กรอก");
    expect(writes()).toEqual([]);
  });

  it("a company calendar is saved as {date: name}", async () => {
    const onNext = vi.fn();
    render(<PolicyStep installId="i-1" manifest={EXPENSE_MANIFEST} onNext={onNext} />);
    await screen.findByTestId("am-policy-editor");
    expect(screen.getByTestId("am-holidays").textContent).toContain("ถ้าไม่ใส่ จะใช้วันหยุดราชการไทยปี 2569 ที่มากับ agent");
    fireEvent.click(screen.getByTestId("am-holiday-add"));
    fireEvent.change(screen.getByTestId("am-holiday-date"), { target: { value: "2026-10-23" } });
    fireEvent.change(screen.getByTestId("am-holiday-name"), { target: { value: "วันปิยมหาราช" } });
    fireEvent.click(screen.getByTestId("am-policy-save"));
    await waitFor(() => expect(onNext).toHaveBeenCalled());
    expect(writes().map((w) => [w.method, w.path, w.body?.value?.["2026-10-23"] ?? null])).toEqual([
      ["PUT", "/agent-market/installs/i-1/defaults/policy", null],
      ["PUT", "/agent-market/installs/i-1/defaults/holidays", "วันปิยมหาราช"],
    ]);
    // the sample was never edited: it is saved as the sample it is
    expect(writes()[0]!.body.value.source).toBe("package_sample");
  });

  it("the server's refusal is shown as written", async () => {
    raw.mockImplementation(async (path: string, init?: RequestInit) =>
      init?.method === "PUT"
        ? json({ error: { code: "BAD_VALUE", message: "ค่าไม่ถูกต้อง: ตารางนโยบายการเบิก — categories.meals.per_claim: ไม่ใช่จำนวนเงิน" } }, 400)
        : route(path, init),
    );
    const onNext = vi.fn();
    render(<PolicyStep installId="i-1" manifest={EXPENSE_MANIFEST} onNext={onNext} />);
    await screen.findByTestId("am-policy-editor");
    fireEvent.click(screen.getByTestId("am-policy-save"));
    expect(await screen.findByText("ค่าไม่ถูกต้อง: ตารางนโยบายการเบิก — categories.meals.per_claim: ไม่ใช่จำนวนเงิน")).toBeTruthy();
    expect(onNext).not.toHaveBeenCalled();
  });

  it("what the install already has wins over the sample", async () => {
    stored = [def("policy", { ...SAMPLE, source: "install", categories: { travel: { label: "ค่าเดินทาง", per_claim: "900.00" } } })];
    render(<PolicyStep installId="i-1" manifest={EXPENSE_MANIFEST} onNext={vi.fn()} />);
    await screen.findByTestId("am-policy-editor");
    expect(screen.getAllByTestId("am-policy-category").map((r) => r.getAttribute("data-code"))).toEqual(["travel"]);
    expect(screen.queryByTestId("am-policy-sample")).toBeNull();
  });
});

describe("PolicySection (settings)", () => {
  it("summarises the table, marks the sample, and edits the same table", async () => {
    const onChanged = vi.fn();
    const defaults = [def("policy", SAMPLE), def("holidays", { "2026-10-23": "วันปิยมหาราช" })];
    render(<PolicySection installId="i-1" manifest={EXPENSE_MANIFEST} defaults={defaults} onChanged={onChanged} />);
    expect(screen.getByTestId("am-policy-summary").textContent).toBe("3 หมวด · ต้องมีใบเสร็จเมื่อเกิน 500 บาท · วงเงินอนุมัติ 3,000 บาท");
    expect(screen.getByText("ตารางตัวอย่าง")).toBeTruthy();
    expect(screen.getByTestId("am-policy-holidays-line").textContent).toBe("วันหยุดของบริษัท 1 วัน");

    fireEvent.click(screen.getByTestId("am-policy-edit"));
    fireEvent.change(screen.getByTestId("am-policy-prohibited"), { target: { value: "คาสิโน" } });
    // emptying the calendar removes it: the package's holidays apply again
    fireEvent.click(screen.getByRole("button", { name: "ลบวันหยุด 2026-10-23" }));
    fireEvent.click(screen.getByTestId("am-policy-submit"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const w = writes();
    expect(w.map((x) => [x.method, x.path])).toEqual([
      ["PUT", "/agent-market/installs/i-1/defaults/policy"],
      ["DELETE", "/agent-market/installs/i-1/defaults/holidays"],
    ]);
    expect(w[0]!.body.value.prohibited_merchants).toEqual(["คาสิโน"]);
    expect(w[0]!.body.value.source).toBe("install");
  });
});
