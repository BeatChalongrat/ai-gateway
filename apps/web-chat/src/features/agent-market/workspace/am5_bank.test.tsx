// AM5 phase E — the bank reconciliation on the screen.
//
//   * A reconciliation whose two adjusted balances differ is not a success,
//     and the files card says so: "ร่าง", the agent's own sentence ("ยังไม่ลงตัว
//     ต่างกัน X บาท"), what to do next — and no way to sign it or to ask for
//     more files of it.
//   * One that ties shows the two adjusted balances, the difference, how many
//     pairs a rule matched and how many a person confirmed.
//   * The gate is a step of the run, named in words.
//   * The tolerances of the match are settings a person can read and edit.
//   * The market no longer says the agent is coming soon.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { raw: vi.fn() },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import { useI18nStore } from "@/lib/i18n";

import { tAm } from "../i18n";
import { COMING_SOON } from "../market/MarketPage";
import { stepLabel } from "../runs/RunDetailPage";
import { describeValue, editorFor } from "../settings/SettingsPage";
import type { Default, Failure, Manifest, OutputsPayload, ThreadItem } from "../types";
import { WorkspaceContext, type WorkspaceCtx } from "./context";
import { OutputsCard } from "./OutputsCard";
import { ThreadItems } from "./ThreadView";

const RUN = "85b347e0-7dfd-4dff-9418-8c48c85f494d";
const ws: WorkspaceCtx = { installId: "i-1", merge: () => {}, threadId: null };
const inWorkspace = (node: React.ReactNode) => render(<WorkspaceContext.Provider value={ws}>{node}</WorkspaceContext.Provider>);
const t = (k: Parameters<typeof tAm>[0], v?: Record<string, string>) => tAm(k, v);

const summary = {
  period_label: "กันยายน 2569",
  kind: "bank_reconciliation",
  matched: 9,
  by_rule: 9,
  by_person: 0,
  items: 7,
  suspect: 0,
  journal: 2,
};

const draft: OutputsPayload = {
  run_id: RUN,
  period_label: "กันยายน 2569",
  draft: true,
  gate: { passed: false, what: "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท", next: "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร" },
  artifacts: [{ id: "a1", name: "bank-reconciliation_2569-09_tables_v1.xlsx", format: "xlsx", size: 20480, version: 1 }],
  summary: { ...summary, tied: false, difference: "-21435.00", adjusted_bank: "1517119.65", adjusted_book: "1538554.65" },
  model: { uses: "none" },
};

const tied: OutputsPayload = {
  ...draft,
  draft: false,
  gate: { passed: true, what: "ลงตัว ยอดหลังปรับปรุงของธนาคารและของบัญชีเท่ากัน" },
  summary: { ...summary, matched: 12, by_rule: 10, by_person: 2, tied: true, difference: "0.00", adjusted_bank: "1538519.65", adjusted_book: "1538519.65" },
};

beforeEach(() => {
  useI18nStore.setState({ locale: "th" }); // AM11: the workspace follows the app's language; these tests read it in Thai
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} })) as unknown as typeof window.matchMedia;
});
afterEach(cleanup);

describe("a reconciliation that does not tie", () => {
  it("is a draft, says how far off it is and what to do next", () => {
    inWorkspace(<OutputsCard outputs={draft} />);
    const banner = screen.getByTestId("am-outputs-draft");
    expect(banner.textContent).toContain("ร่าง");
    expect(banner.textContent).toContain("ยังไม่ลงตัว ต่างกัน 21,435.00 บาท");
    expect(banner.textContent).toContain("ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร");
    expect(banner.getAttribute("role")).toBe("alert");
    // the numbers that do not agree are both there
    const kpis = screen.getByTestId("am-outputs-kpis").textContent ?? "";
    expect(kpis).toContain("ยอดหลังปรับปรุงของธนาคาร");
    expect(kpis).toContain("1,517,119.65");
    expect(kpis).toContain("ยอดหลังปรับปรุงของบัญชี");
    expect(kpis).toContain("1,538,554.65");
    expect(kpis).toContain("ผลต่าง");
    expect(kpis).toContain("21,435.00");
    // the working paper is there to be read
    expect(screen.getByTestId("am-artifact")).toBeTruthy();
  });

  it("cannot be signed, and no other file is made of it", () => {
    inWorkspace(<OutputsCard outputs={draft} />);
    expect(screen.queryByTestId("am-signoff-open")).toBeNull();
    expect(screen.queryByTestId("am-more-files")).toBeNull();
    expect(screen.queryByTestId("am-outputs-gate")).toBeNull();
  });

  it("the round's message says it in the agent's words and does not offer the same files again", () => {
    const failure: Failure = {
      reason: "not_tied",
      what: "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท",
      next: "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร",
      files: "ไฟล์ร่างของรอบนี้ส่งให้แล้ว มีคำว่า “ร่าง” กำกับบนหน้าแรก ไฟล์ที่ส่งมาในรอบนี้ยังอยู่ครบและเก็บไว้ให้อีก 7 วัน ผลงานของรอบก่อน ๆ ไม่ถูกแตะต้อง",
      charged: "รอบนี้ใช้ไป 3 เครดิต สำหรับขั้นตอนที่ทำเสร็จก่อนจะหยุด ขั้นตอนที่ไม่สำเร็จไม่คิดเครดิต",
      credits: 3,
      can_retry: false,
    };
    const items = [
      { id: "o", kind: "outputs", rev: 1, run_id: RUN, created_at: "2026-09-29T00:09:20Z", payload: draft },
      { id: "f", kind: "agent_text", rev: 2, run_id: RUN, created_at: "2026-09-29T00:09:21Z", payload: { text: failure.what, tone: "error", failure } },
    ] as unknown as ThreadItem[];
    inWorkspace(<ThreadItems items={items} />);
    expect(screen.getByTestId("am-failed-what").textContent).toBe("ยังไม่ลงตัว ต่างกัน 21,435.00 บาท");
    expect(screen.getByTestId("am-failed-next").textContent).toContain("รายการเดินบัญชีฉบับเต็ม");
    expect(screen.getByTestId("am-failed-files").textContent).toContain("ร่าง");
    expect(screen.queryByTestId("am-failed-retry")).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(/not_tied|gate/);
  });
});

describe("a reconciliation that ties", () => {
  it("shows the two adjusted balances, no difference, and who matched what", () => {
    inWorkspace(<OutputsCard outputs={tied} />);
    expect(screen.queryByTestId("am-outputs-draft")).toBeNull();
    expect(screen.getByTestId("am-outputs-gate").textContent).toContain("ลงตัว");
    const kpis = screen.getByTestId("am-outputs-kpis").textContent ?? "";
    expect(kpis).toContain("1,538,519.65");
    expect(kpis).toContain("0.00");
    const match = screen.getByTestId("am-outputs-match").textContent ?? "";
    expect(match).toContain("จับคู่ได้ 12 คู่");
    expect(match).toContain("ตามกฎ 10");
    expect(match).toContain("คนยืนยัน 2");
    expect(match).toContain("รายการกระทบยอด 7");
    expect(screen.getByTestId("am-signoff-open")).toBeTruthy();
  });

  it("another agent's files card is what it was", () => {
    inWorkspace(<OutputsCard outputs={{ run_id: "r", artifacts: [], summary: { revenue: "100.00", total_expenses: "40.00", net_profit: "60.00" }, model: { uses: "text" } }} />);
    expect(screen.getByTestId("am-outputs-kpis").textContent).toContain("รายได้100");
    expect(screen.queryByTestId("am-outputs-match")).toBeNull();
    expect(screen.queryByTestId("am-outputs-gate")).toBeNull();
  });
});

describe("the rest of the agent", () => {
  it("the gate is a step of the run, named in words", () => {
    expect(stepLabel("gate", t as never)).toBe("ตรวจว่างานครบตามเกณฑ์ของ agent");
    expect(stepLabel("stopped", t as never)).toBe("รอบหยุดก่อนถึงขั้นตอนถัดไป");
  });

  it("the tolerances are settings a person can read and edit", () => {
    expect(editorFor("match_date_window_days")).toBe("number");
    expect(editorFor("match_amount_tolerance")).toBe("number");
    expect(editorFor("match_ask_above")).toBe("number");
    expect(editorFor("match_auto_same_day")).toBe("yesno");
    expect(editorFor("bank_account_number")).toBe("text");
    expect(editorFor("bank_gl_code")).toBe("text");
    const d = (key: string, value: unknown) => ({ key, value, label: "x", source: "install" }) as unknown as Default;
    expect(describeValue(d("match_auto_same_day", "true"), t as never, {} as Manifest)).toEqual(["ใช่"]);
    expect(describeValue(d("match_auto_same_day", false), t as never, {} as Manifest)).toEqual(["ไม่"]);
    expect(describeValue(d("match_ask_above", "50000.00"), t as never, {} as Manifest)).toEqual(["50,000.00"]);
    expect(describeValue(d("match_date_window_days", "3"), t as never, {} as Manifest)).toEqual(["3"]);
  });

  it("the market's placeholder gives way to the real agent", () => {
    expect(COMING_SOON.find((c) => c.id === "bank")?.slug).toBe("sran.bank-reconciliation");
  });
});
