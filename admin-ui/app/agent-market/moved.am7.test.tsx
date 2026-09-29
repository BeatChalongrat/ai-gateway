// AM7 — what the chat app's admin page was tested for, now tested where the
// page lives (these assertions were in apps/web-chat am6_outcome_pages.test.tsx
// until phase 4 removed that page):
//   * the list of every run names each by what it came to — a round that
//     finished open is not a failure;
//   * spend per person counts rounds finished open apart from failures and
//     says what the platform paid but did not charge; a server older than
//     AM6 shows the three counts it had.

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({ api: { runs: vi.fn(), spend: vi.fn(), org: vi.fn() } }));
vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: api };
});
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/agent-market",
}));

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";
import type { RunMeta } from "../../lib/agentMarketApi";

import RunsSection from "./_sections/Runs";
import SpendSection from "./_sections/Spend";
import { outcomeVariant } from "./_sections/shared";

const wrap = (el: React.ReactNode) => render(<I18nProvider><ToastProvider>{el}</ToastProvider></I18nProvider>);

const run = (o: Partial<RunMeta>): RunMeta => ({
  id: "r-1", agent: "กระทบยอดธนาคาร", slug: "sran.bank-reconciliation", person: "สมหญิง บัญชี", email: "a@gbtech.test", status: "failed",
  reason: "not_tied", period_label: "กันยายน 2569", credits: 1134.58, model: "", model_self_hosted: null, created_at: "2026-09-29T03:00:00Z",
  finished_at: "2026-09-29T03:00:41Z", open_now: false, times_opened: 0, ...o,
});

beforeEach(() => {
  vi.clearAllMocks();
  api.org.mockResolvedValue({ org: { user_monthly_credits: 3000000 }, members: [], departments: [], pricing: { credits_per_usd: 3_000_000, thb_per_usd: 35 } });
});
afterEach(cleanup);

describe("every run of the organisation", () => {
  it("names each run by what it came to", async () => {
    api.runs.mockResolvedValue({
      runs: [
        run({ id: "r-1", outcome: "completed_open", reason: "not_tied" }),
        run({ id: "r-2", outcome: "completed_open", reason: "lines_unmatched" }),
        run({ id: "r-3", outcome: "failed", reason: "render_failed" }),
        run({ id: "r-4", reason: "render_failed" }),
      ],
      grant_minutes: 60,
    });
    wrap(<RunsSection />);
    const rows = await screen.findAllByTestId("am-run");
    const state = (i: number) => within(rows[i]!).getByTestId("am-run-status");
    expect(state(0).getAttribute("data-outcome")).toBe("completed_open");
    expect(state(0).textContent).toMatch(/Finished · balances don't tie yet|เสร็จแล้ว · ยังไม่ลงตัว/);
    expect(state(1).textContent).toMatch(/Finished · not yet past the agent's checks|เสร็จแล้ว · ยังไม่ผ่านเกณฑ์ของ agent/);
    expect(state(2).textContent).toMatch(/Failed|ไม่สำเร็จ/);
    expect(state(3).getAttribute("data-outcome")).toBe("failed");
    // a round that finished open is not coloured as a failure
    expect(outcomeVariant("completed_open")).not.toBe(outcomeVariant("failed"));
  });
});

describe("spend", () => {
  it("counts rounds finished open apart, per person and per agent, and what was paid but not charged", async () => {
    api.spend.mockResolvedValue({
      days: 30, since: "", total_credits: 3400, total_cost_not_charged: 212.5, catalog_samples_credits: 0,
      by_user: [{ key: "u1", email: "a@gbtech.test", name: "เอ", department: "บัญชี", runs: 12, succeeded: 9, completed_open: 2, failed: 1, waiting: 0,
        credits: 3400, cost_not_charged: 212.5, month_spent: 3400, last_run_at: null }],
      by_agent: [{ key: "sran.bank-reconciliation", name: "กระทบยอดธนาคาร", runs: 12, succeeded: 9, completed_open: 2, failed: 1, waiting: 0, users: 1,
        credits: 3400, cost_not_charged: 212.5, month_spent: 3400, last_run_at: null }],
    });
    wrap(<SpendSection />);
    const user = await screen.findByTestId("am-spend-user");
    expect(user.textContent).toMatch(/Succeeded \/ finished below the bar \/ failed \/ waiting|สำเร็จ \/ เสร็จแล้วยังไม่ผ่านเกณฑ์ \/ ไม่สำเร็จ \/ รอคำตอบ/);
    expect(within(user).getByTestId("am-spend-row").textContent).toContain("9 / 2 / 1 / 0");
    expect(within(user).getByTestId("am-spend-row").textContent).toContain("212.5");
    expect(screen.getByTestId("am-spend-not-charged-total").textContent).toContain("212.5");
  });

  it("a server older than AM6 shows the three counts it had", async () => {
    api.spend.mockResolvedValue({
      days: 30, since: "", total_credits: 1050, catalog_samples_credits: 2400,
      by_user: [{ key: "u1", email: "a@gbtech.test", name: "เอ", department: "บัญชี", runs: 2, succeeded: 1, failed: 1, waiting: 0, credits: 1000,
        month_spent: 1000, last_run_at: null }],
      by_agent: [],
    });
    wrap(<SpendSection />);
    const user = await screen.findByTestId("am-spend-user");
    expect(within(user).getByTestId("am-spend-row").textContent).toContain("1 / 1 / 0");
    expect(screen.queryByTestId("am-spend-not-charged-total")).toBeNull();
  });
});
