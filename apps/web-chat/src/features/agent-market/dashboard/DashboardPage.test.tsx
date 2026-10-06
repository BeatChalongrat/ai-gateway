// AM2 phase G — the per-agent dashboard.
//   * durations in Thai (วินาที / นาที / ชั่วโมง / วัน), rates, bar widths;
//   * the range picker asks for 7 / 30 / 90 days;
//   * KPI tiles, bars (a reason code shows its Thai label when the feature
//     has one, else the code), a models table saying ในองค์กร / ภายนอก;
//   * an agent with nothing yet gets an empty state, not a wall of zeros.
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

import { tAm } from "../i18n";
import type { AgentDashboard } from "../types";
import { bars, formatRate, formatSeconds, recordBars } from "./dashboard";
import { DashboardPage } from "./DashboardPage";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

const busy: AgentDashboard = {
  since: "2026-08-27T00:00:00Z",
  days: 30,
  version: "1.0.0",
  runs: { total: 12, by_status: { succeeded: 9, failed: 2, awaiting_input: 1 }, succeeded: 9, first_pass: 6, first_pass_rate: 6 / 9 },
  time: { queue_p50_s: 4, queue_p90_s: 12, work_p50_s: 95, work_p90_s: 400, human_wait_p50_s: 3700, human_wait_p90_s: 90061 },
  waiting: { runs: 1, oldest_run_s: 7300, open_exceptions: 13, oldest_exception_s: 200000 },
  questions: [
    { key: "expense_weekend_holiday", count: 4, open: 1 },
    { key: "expense_vague_purpose", count: 2 },
  ],
  exceptions: {
    open: 13,
    resolved: 5,
    noted: 2,
    by_reason: [
      { key: "over_claim_limit", count: 6, open: 4 },
      { key: "brand_new_code", count: 3, open: 3 },
    ],
    choices: [{ key: "อนุมัติเป็นกรณียกเว้น", count: 3 }],
  },
  failures: [{ key: "ยังไม่มีตารางนโยบายการเบิกที่ใช้ได้", count: 2 }],
  confidence: { high: 80, medium: 10, low: 5, person: 3, computed: 2 },
  tiers: { digital: 20, scan: 5, photo: 2 },
  documents: 27,
  artifacts: [{ key: "xlsx", count: 9 }],
  versions: [{ version: "1.0.0", runs: 12, last_run_at: "2026-09-25T10:00:00Z" }],
  spend: { credits: 54, per_succeeded_run: 5.5, per_document: 1.83, budget_credits: 500 },
  models: [
    { model: "qwen3.8-27b-fp8", self_hosted: true, runs: 7 },
    { model: "gpt-x", self_hosted: false, runs: 1 },
  ],
  last_self_test: { status: "succeeded", reason: "", version: "1.0.0", finished_at: "2026-08-28T10:00:00Z" },
};
const fresh: AgentDashboard = {
  ...busy,
  runs: { total: 0, by_status: {}, succeeded: 0, first_pass: 0, first_pass_rate: null },
  time: { queue_p50_s: null, queue_p90_s: null, work_p50_s: null, work_p90_s: null, human_wait_p50_s: null, human_wait_p90_s: null },
  waiting: { runs: 0, oldest_run_s: null, open_exceptions: 0, oldest_exception_s: null },
  questions: [],
  exceptions: { open: 0, resolved: 0, noted: 0, by_reason: [], choices: [] },
  failures: [],
  confidence: {},
  tiers: {},
  documents: 0,
  artifacts: [],
  versions: [],
  spend: { credits: 1, per_succeeded_run: null, per_document: null, budget_credits: 500 },
  models: [],
};

let dash: AgentDashboard = busy;
beforeEach(() => {
  dash = busy;
  raw.mockReset();
  raw.mockImplementation(async (path: string) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true } });
    if (path.startsWith("/agent-market/installs/i-1/dashboard")) return json(dash);
    if (path === "/agent-market/installs/i-1") {
      return json({ install: { id: "i-1", slug: "sran.expense-audit", title: "ตรวจค่าใช้จ่าย", definition_version: "1.0.0", created_at: "" }, manifest: {}, defaults: [], budget: { credits: 500, spent_this_month: 54 } });
    }
    return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("helpers", () => {
  it("durations in Thai, the two largest units that follow each other", () => {
    expect(formatSeconds(4, tAm)).toBe("4 วินาที");
    expect(formatSeconds(95, tAm)).toBe("1 นาที 35 วินาที");
    expect(formatSeconds(3700, tAm)).toBe("1 ชั่วโมง 1 นาที");
    expect(formatSeconds(90061, tAm)).toBe("1 วัน 1 ชั่วโมง");
    expect(formatSeconds(86400 + 300, tAm)).toBe("1 วัน");
    expect(formatSeconds(0, tAm)).toBe("0 วินาที");
    expect(formatSeconds(null, tAm)).toBe("—");
  });

  it("rates and bars", () => {
    expect(formatRate(6 / 9)).toBe("67%");
    expect(formatRate(null)).toBe("—");
    expect(bars([{ key: "a", count: 6 }, { key: "b", count: 3, open: 3 }, { key: "c", count: 0 }])).toEqual([
      { key: "a", count: 6, open: 0, pct: 100 },
      { key: "b", count: 3, open: 3, pct: 50 },
    ]);
    expect(recordBars({ low: 1, high: 4, extra: 2 }, ["high", "medium", "low"]).map((b) => b.key)).toEqual(["high", "low", "extra"]);
  });
});

describe("DashboardPage", () => {
  it("tiles, bars, the time table and the models table", async () => {
    render(<DashboardPage installId="i-1" />);
    const tiles = await screen.findAllByTestId("am-dash-tile");
    expect(tiles.map((el) => el.textContent)).toEqual([
      "รอบทำงาน12สำเร็จ 9 · ไม่สำเร็จ 2",
      "ผ่านโดยไม่ต้องถาม67%6 จาก 9 รอบที่สำเร็จ",
      "รอคำตอบอยู่1นานสุด 2 ชั่วโมง 1 นาที",
      "ข้อยกเว้นรอตัดสิน13นานสุด 2 วัน 7 ชั่วโมง",
      "เครดิตต่อเอกสาร1.83ใช้ไป 54 จากงบ 500",
    ]);
    expect(await screen.findByText("ตรวจค่าใช้จ่าย · รุ่น 1.0.0", { exact: false })).toBeTruthy();
    const time = screen.getByTestId("am-dash-time");
    expect(time.textContent).toContain("ทำงาน1 นาที 35 วินาที6 นาที 40 วินาที");
    expect(time.textContent).toContain("รอคน1 ชั่วโมง 1 นาที1 วัน 1 ชั่วโมง");

    const reasons = within(screen.getByTestId("am-dash-reasons")).getAllByTestId("am-dash-bar");
    // AM11: a code the page has no words for is never shown as the code — the row says what it is
    expect(reasons.map((b) => b.textContent)).toEqual(["เกินวงเงินต่อครั้ง6 · รอ 4", "เหตุผลที่ agent บันทึกไว้3 · รอ 3"]);
    expect(reasons[1]!.getAttribute("data-key")).toBe("brand_new_code");
    expect(within(screen.getByTestId("am-dash-questions")).getAllByTestId("am-dash-bar")[0]!.textContent).toBe("คำถามของ agent4 · รอ 1");
    // a failure whose recorded reason is the sentence a person was told is shown as that sentence
    expect(within(screen.getByTestId("am-dash-failures")).getAllByTestId("am-dash-bar")[0]!.textContent).toBe("ยังไม่มีตารางนโยบายการเบิกที่ใช้ได้2");
    expect(within(screen.getByTestId("am-dash-confidence")).getAllByTestId("am-dash-bar").map((b) => b.getAttribute("data-key"))).toEqual([
      "high",
      "medium",
      "low",
      "person",
      "computed",
    ]);
    expect(screen.getByTestId("am-dash-tiers").textContent).toContain("สแกน");
    expect(screen.getByText("ชนิดเอกสาร (27 ฉบับ)")).toBeTruthy();

    const models = screen.getByTestId("am-dash-models");
    const rows = within(models).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows).toEqual(["qwen3.8-27b-fp8ในองค์กร7", "gpt-xภายนอก1"]);
    expect(screen.getByTestId("am-dash-selftest").textContent).toContain("เสร็จแล้ว");
  });

  it("the range picker asks for 7, 30 or 90 days", async () => {
    render(<DashboardPage installId="i-1" />);
    await screen.findAllByTestId("am-dash-tile");
    const range = screen.getByTestId("am-dash-range");
    expect(within(range).getAllByRole("button").map((b) => [b.textContent, b.getAttribute("aria-pressed")])).toEqual([
      ["7 วัน", "false"],
      ["30 วัน", "true"],
      ["90 วัน", "false"],
    ]);
    fireEvent.click(within(range).getByRole("button", { name: "90 วัน" }));
    await waitFor(() => expect(raw.mock.calls.some((c) => c[0] === "/agent-market/installs/i-1/dashboard?days=90")).toBe(true));
    expect(raw.mock.calls.some((c) => c[0] === "/agent-market/installs/i-1/dashboard?days=30")).toBe(true);
  });

  it("a new agent gets an empty state", async () => {
    dash = fresh;
    render(<DashboardPage installId="i-1" />);
    expect((await screen.findByTestId("am-dash-empty")).textContent).toBe("ยังไม่มีรอบทำงานใน 30 วันที่ผ่านมา");
    expect(screen.queryByTestId("am-dash-tile")).toBeNull();
    expect(screen.queryByTestId("am-dash-models")).toBeNull();
  });

  it("someone else's agent (404) is not found", async () => {
    raw.mockImplementation(async (path: string) =>
      path === "/agent-market/availability"
        ? json({ available: true })
        : json({ error: { code: "NOT_FOUND", message: "ไม่พบ agent นี้" } }, 404),
    );
    render(<DashboardPage installId="i-1" />);
    expect(await screen.findByText("ไม่พบ agent นี้")).toBeTruthy();
  });
});
