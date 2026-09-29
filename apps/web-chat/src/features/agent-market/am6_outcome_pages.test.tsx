// AM6 part F — the words match the billing (the pages).
//
// Wherever a run's state is shown it goes by what the run came to: a round
// that delivered its draft while the agent's gate did not pass reads
// "เสร็จแล้ว · ยังไม่ลงตัว", in the colour of a finished round with a note. The
// run page, the admins' list of runs and a run shared with a colleague said
// "ไม่สำเร็จ" in red for it. The dashboard and the admins' spend count such
// rounds apart from failures, and show what the platform paid for rounds
// that delivered nothing and charged to nobody.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/chat/agents",
}));

import { useI18nStore } from "@/lib/i18n";

import { AdminPage } from "./admin/AdminPage";
import { RunsTab } from "./admin/Am4Tabs";
import { DashboardPage } from "./dashboard/DashboardPage";
import { outcomeKey, outcomeOf, outcomeTone } from "./outcome";
import { RunDetailPage } from "./runs/RunDetailPage";
import { SharedRunPage } from "./share/SharedRunPage";
import type { AgentDashboard, RunMeta } from "./types";
import { resetAgentMarketForTest } from "./useAgentMarket";

const OPEN_TITLE = "เสร็จแล้ว · ยังไม่ลงตัว";
const OPEN_OTHER = "เสร็จแล้ว · ยังไม่ผ่านเกณฑ์ของ agent";
const GATE_WHAT = "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท";
const NOT_CHARGED = "ค่าใช้จ่ายที่ระบบจ่ายแต่ไม่ได้คิดเครดิต";
const PRICING = { credits_per_usd: 3_000_000, thb_per_usd: 35 };

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) =>
    path === "/agent-market/availability"
      ? json({ available: true, runner: { healthy: true }, role: "admin", admin: true, publisher: true })
      : handler(path, init),
  );
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AM6 — what a run came to", () => {
  it("goes by the outcome, and by the status when a server sends none", () => {
    expect(outcomeOf({ status: "failed", outcome: "completed_open" })).toBe("completed_open");
    expect(outcomeOf({ status: "succeeded" })).toBe("completed");
    expect(outcomeOf({ status: "failed" })).toBe("failed");
    expect(outcomeOf({ status: "running", outcome: "running" })).toBe("running");
  });

  it("has words and a colour that is never a failure's", () => {
    expect(outcomeKey("completed_open", "not_tied")).toBe("outcome.open.notTied");
    expect(outcomeKey("completed_open", "lines_unmatched")).toBe("outcome.open.gate");
    expect(outcomeKey("completed_open")).toBe("outcome.open.gate");
    expect(outcomeKey("completed")).toBe("progress.title.succeeded");
    expect(outcomeKey("failed")).toBe("progress.title.failed");
    expect(outcomeTone("completed_open")).toBe("warning");
    expect(outcomeTone("failed")).toBe("danger");
    expect(outcomeTone("completed")).toBe("success");
  });
});

describe("AM6 — the run page", () => {
  const RUN = "85b347e0-7dfd-4dff-9418-8c48c85f494d";
  const detail = (run: Record<string, unknown>) => ({
    run: { id: RUN, install_id: "i-1", kind: "run", period: "2026-09", created_at: "2026-09-29T03:00:00Z", started_at: "2026-09-29T03:00:01Z", finished_at: "2026-09-29T03:00:41Z", credits_spent: 1134.58, ...run },
    steps: [
      { id: "s1", seq: 1, name: "compute", kind: "compute", status: "succeeded", started_at: "2026-09-29T03:00:02Z" },
      { id: "s2", seq: 2, name: "render", kind: "deliverable", status: "succeeded", started_at: "2026-09-29T03:00:20Z" },
      { id: "s3", seq: 3, name: "gate", kind: "check", status: "failed", error: GATE_WHAT, started_at: "2026-09-29T03:00:40Z" },
    ],
    clarifications: [],
    artifacts: [{ id: "a1", format: "xlsx", name: "bank-reconciliation_2569-09_tables_v1.xlsx", version: 1, size: 100, created_at: "" }],
    files: [],
    children: [],
    install: { id: "i-1", title: "กระทบยอดธนาคาร", slug: "sran.bank-reconciliation" },
    budget: { credits: 5000, spent_this_month: 1134.58 },
  });
  const page = (run: Record<string, unknown>): Handler => (path) => {
    if (path === `/agent-market/runs/${RUN}`) return json(detail(run));
    if (path === `/agent-market/runs/${RUN}/computed`)
      return json({ period_label: "กันยายน 2569", gate: { passed: false, code: "not_tied", what: GATE_WHAT, next: "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร" } });
    if (path === `/agent-market/runs/${RUN}/exceptions`) return json({ exceptions: [] });
    if (path === `/agent-market/runs/${RUN}/reviews`) return json({ reviews: [] });
    return json({}, 404);
  };

  it("a round that does not tie is finished, with its note", async () => {
    handler = page({ status: "failed", outcome: "completed_open", status_reason: "not_tied", gate_open: true });
    render(<RunDetailPage runId={RUN} />);
    const pill = await screen.findByTestId("am-run-status");
    expect(pill.textContent).toContain(OPEN_TITLE);
    expect(pill.textContent).not.toContain("ไม่สำเร็จ");
    expect(pill.getAttribute("data-outcome")).toBe("completed_open");
    expect(pill.innerHTML).not.toContain("status-danger");
    expect(document.body.textContent).not.toContain("not_tied");
  });

  it("cannot be signed, and says why in the agent's words", async () => {
    handler = page({ status: "failed", outcome: "completed_open", status_reason: "not_tied", gate_open: true });
    render(<RunDetailPage runId={RUN} />);
    const why = await screen.findByTestId("am-signoff-unavailable");
    expect(why.textContent).toContain("ยังตรวจทานและลงชื่อรอบนี้ไม่ได้");
    await waitFor(() => expect(why.textContent).toContain(GATE_WHAT));
    expect(screen.queryByTestId("am-signoff-open")).toBeNull();
  });

  it("its gate is a finished step with a note, not a failed one", async () => {
    handler = page({ status: "failed", outcome: "completed_open", status_reason: "not_tied", gate_open: true });
    render(<RunDetailPage runId={RUN} />);
    fireEvent.click(await screen.findByTestId("am-run-tab-steps"));
    const rows = within(await screen.findByTestId("am-run-steps")).getAllByTestId("am-run-step");
    const gate = rows[2]!;
    expect(gate.getAttribute("data-step-status")).toBe("open");
    expect(gate.textContent).toContain(GATE_WHAT);
    expect(gate.innerHTML).not.toContain("status-danger");
  });

  it("a round that failed is still a failure, and a server that sends no outcome is read by its status", async () => {
    handler = page({ status: "failed", outcome: "failed", status_reason: "render_failed" });
    const first = render(<RunDetailPage runId={RUN} />);
    expect((await screen.findByTestId("am-run-status")).textContent).toContain("ไม่สำเร็จ");
    expect(screen.queryByTestId("am-signoff-unavailable")).toBeNull();
    first.unmount();

    handler = page({ status: "succeeded" });
    render(<RunDetailPage runId={RUN} />);
    const pill = await screen.findByTestId("am-run-status");
    expect(pill.textContent).toContain("เสร็จแล้ว");
    expect(pill.textContent).not.toContain("ยังไม่ลงตัว");
    expect(await screen.findByTestId("am-signoff-open")).toBeTruthy();
  });
});

describe("AM6 — the admins' list of runs", () => {
  const run = (o: Partial<RunMeta>): RunMeta => ({
    id: "r-1", agent: "กระทบยอดธนาคาร", slug: "sran.bank-reconciliation", person: "สมหญิง บัญชี", email: "a@gbtech.test", status: "failed", reason: "not_tied",
    period_label: "กันยายน 2569", credits: 1134.58, model: "", model_self_hosted: null, created_at: "2026-09-29T03:00:00Z", finished_at: "2026-09-29T03:00:41Z", open_now: false, times_opened: 0, ...o,
  });

  it("names each run by what it came to", async () => {
    handler = (path) =>
      path.startsWith("/agent-market/admin/runs")
        ? json({
            runs: [
              run({ id: "r-1", outcome: "completed_open", reason: "not_tied" }),
              run({ id: "r-2", outcome: "completed_open", reason: "lines_unmatched" }),
              run({ id: "r-3", outcome: "failed", reason: "render_failed" }),
              run({ id: "r-4", reason: "render_failed" }),
            ],
            grant_minutes: 60,
          })
        : json({}, 404);
    render(<RunsTab pricing={PRICING} />);
    const rows = await screen.findAllByTestId("am-admin-run");
    const state = (i: number) => within(rows[i]!).getByTestId("am-admin-run-status");
    expect(state(0).textContent).toBe(OPEN_TITLE);
    expect(state(0).innerHTML).not.toContain("status-danger");
    expect(state(1).textContent).toBe(OPEN_OTHER);
    expect(state(2).textContent).toBe("ไม่สำเร็จ");
    expect(state(2).innerHTML).toContain("status-danger");
    expect(state(3).textContent).toBe("ไม่สำเร็จ");
  });
});

describe("AM6 — a run shared with a colleague", () => {
  it("reads as finished with its note, and is not offered for sign-off", async () => {
    handler = (path) =>
      path === "/agent-market/shared/s-1"
        ? json({
            share: { id: "s-1", run_id: "r-1", recipient_email: "cfo@x.test", can_review: true, created_at: "", expires_at: null, revoked_at: null, last_opened_at: null, opens: 1 },
            owner: "สมหญิง บัญชี",
            agent: { title: "กระทบยอดธนาคาร", name: "กระทบยอดเงินฝากธนาคาร", slug: "sran.bank-reconciliation", version: "1.0.0" },
            run: { id: "r-1", status: "failed", outcome: "completed_open", status_reason: "not_tied", period: "2026-09", created_at: "", finished_at: "2026-09-29T03:00:41Z", assumptions: [] },
            artifacts: [],
            exceptions: [],
            reviews: [],
          })
        : json({}, 404);
    render(<SharedRunPage shareId="s-1" />);
    const pill = await screen.findByTestId("am-shared-status");
    expect(pill.textContent).toBe(OPEN_TITLE);
    expect(pill.innerHTML).not.toContain("status-danger");
    expect(screen.queryByTestId("am-signoff-open")).toBeNull();
  });
});

describe("AM6 — the dashboard and the admins' spend", () => {
  const dash: AgentDashboard = {
    since: "2026-08-30T00:00:00Z",
    days: 30,
    version: "1.0.0",
    runs: { total: 12, by_status: { succeeded: 9, failed: 3 }, by_outcome: { completed: 9, completed_open: 2, failed: 1 }, succeeded: 9, completed_open: 2, failed: 1, first_pass: 6, first_pass_rate: 6 / 9 },
    time: { queue_p50_s: 4, queue_p90_s: 12, work_p50_s: 95, work_p90_s: 400, human_wait_p50_s: null, human_wait_p90_s: null },
    waiting: { runs: 0, oldest_run_s: null, open_exceptions: 0, oldest_exception_s: null },
    questions: [],
    exceptions: { open: 0, resolved: 0, noted: 0, by_reason: [], choices: [] },
    failures: [{ key: "render_failed", count: 1 }],
    open: [{ key: "not_tied", count: 2 }],
    confidence: {},
    tiers: {},
    documents: 0,
    artifacts: [],
    versions: [],
    spend: { credits: 3400, cost_not_charged: 212.5, per_succeeded_run: 300, per_document: null, budget_credits: 50000 },
    models: [],
    last_self_test: { status: "failed", outcome: "completed_open", reason: "not_tied", version: "1.0.0", finished_at: "2026-09-01T10:00:00Z" },
  };
  const dashboard = (d: AgentDashboard): Handler => (path) => {
    if (path.startsWith("/agent-market/installs/i-1/dashboard")) return json(d);
    if (path === "/agent-market/installs/i-1")
      return json({ install: { id: "i-1", slug: "sran.bank-reconciliation", title: "กระทบยอดธนาคาร", definition_version: "1.0.0", created_at: "" }, manifest: {}, defaults: [], budget: { credits: 50000, spent_this_month: 3400 } });
    return json({}, 404);
  };

  it("the dashboard counts rounds that finished open apart from failures", async () => {
    handler = dashboard(dash);
    render(<DashboardPage installId="i-1" />);
    const tiles = within(await screen.findByTestId("am-dash-kpis")).getAllByTestId("am-dash-tile");
    expect(tiles[0]!.textContent).toContain("สำเร็จ 9 · เสร็จแล้วแต่ยังไม่ลงตัว 2 · ไม่สำเร็จ 1");
    const open = screen.getByTestId("am-dash-open");
    expect(open.textContent).toContain(OPEN_TITLE);
    expect(open.textContent).not.toContain("not_tied");
    const states = screen.getByTestId("am-dash-states").textContent ?? "";
    expect(states).toContain("เสร็จแล้ว 9");
    expect(states).toContain("เสร็จแล้วแต่ยังไม่ลงตัว 2");
    expect(states).toContain("ไม่สำเร็จ 1");
    expect(states).not.toContain("ไม่สำเร็จ 3");
  });

  it("rounds of more than one gate are counted under the agent's gate", async () => {
    handler = dashboard({ ...dash, open: [{ key: "not_tied", count: 1 }, { key: "lines_unmatched", count: 1 }] });
    render(<DashboardPage installId="i-1" />);
    const tiles = within(await screen.findByTestId("am-dash-kpis")).getAllByTestId("am-dash-tile");
    expect(tiles[0]!.textContent).toContain("สำเร็จ 9 · เสร็จแล้วแต่ยังไม่ผ่านเกณฑ์ 2 · ไม่สำเร็จ 1");
    const open = screen.getByTestId("am-dash-open");
    expect(open.textContent).toContain(OPEN_TITLE);
    expect(open.textContent).toContain(OPEN_OTHER);
    expect(open.textContent).not.toContain("lines_unmatched");
  });

  it("the dashboard shows what the platform paid and charged to nobody", async () => {
    handler = dashboard(dash);
    render(<DashboardPage installId="i-1" />);
    const line = await screen.findByTestId("am-dash-not-charged");
    expect(line.textContent).toContain(NOT_CHARGED);
    expect(line.textContent).toContain("212.5");
  });

  it("the last self-test is named by what it came to", async () => {
    handler = dashboard(dash);
    render(<DashboardPage installId="i-1" />);
    const line = await screen.findByTestId("am-dash-selftest");
    expect(line.textContent).toContain(OPEN_TITLE);
    expect(line.textContent).not.toContain("not_tied");
    expect(line.textContent).not.toContain("ไม่สำเร็จ");
  });

  it("a dashboard of a server older than this shows what it showed", async () => {
    const old = { ...dash, runs: { total: 12, by_status: { succeeded: 9, failed: 3 }, succeeded: 9, first_pass: 6, first_pass_rate: 6 / 9 }, open: undefined, spend: { credits: 3400, per_succeeded_run: 300, per_document: null, budget_credits: 50000 }, last_self_test: null };
    handler = dashboard(old as AgentDashboard);
    render(<DashboardPage installId="i-1" />);
    const tiles = within(await screen.findByTestId("am-dash-kpis")).getAllByTestId("am-dash-tile");
    expect(tiles[0]!.textContent).toContain("สำเร็จ 9 · ไม่สำเร็จ 3");
    expect(screen.queryByTestId("am-dash-not-charged")).toBeNull();
    expect(screen.queryByTestId("am-dash-open")).toBeNull();
  });

  it("the admins' spend: the same, per person and per agent, and in total", async () => {
    handler = (path) =>
      path.startsWith("/agent-market/admin/spend")
        ? json({
            days: 30,
            since: "",
            total_credits: 3400,
            total_cost_not_charged: 212.5,
            catalog_samples_credits: 0,
            by_user: [{ key: "u1", email: "a@gbtech.test", name: "เอ", department: "บัญชี", runs: 12, succeeded: 9, completed_open: 2, failed: 1, waiting: 0, credits: 3400, cost_not_charged: 212.5, month_spent: 3400, last_run_at: null }],
            by_agent: [{ key: "sran.bank-reconciliation", name: "กระทบยอดธนาคาร", runs: 12, succeeded: 9, completed_open: 2, failed: 1, waiting: 0, users: 1, credits: 3400, cost_not_charged: 212.5, month_spent: 3400, last_run_at: null }],
          })
        : json({ org: { org_id: "o", name: "จีบีเทค", access: "list", domains: [], user_monthly_credits: 0, contact_name: "", contact_email: "", updated_by: "", updated_at: "" }, members: [], departments: [] });
    render(<AdminPage />);
    fireEvent.click(await screen.findByTestId("am-admin-tab-spend"));
    const user = await screen.findByTestId("am-admin-spend-user");
    expect(user.textContent).toContain("สำเร็จ / เสร็จแล้วยังไม่ผ่านเกณฑ์ / ไม่สำเร็จ / รอคำตอบ");
    expect(user.textContent).toContain(NOT_CHARGED);
    const row = within(user).getByTestId("am-admin-spend-row");
    expect(within(row).getByTestId("am-admin-spend-outcomes").textContent).toBe("9 / 2 / 1 / 0");
    expect(within(row).getByTestId("am-admin-spend-not-charged").textContent).toContain("212.5");
    const total = screen.getByTestId("am-admin-spend-not-charged-total");
    expect(total.textContent).toContain(NOT_CHARGED);
    expect(total.textContent).toContain("212.5");
  });

  it("the admins' spend of a server older than this shows what it showed", async () => {
    handler = (path) =>
      path.startsWith("/agent-market/admin/spend")
        ? json({
            days: 30,
            since: "",
            total_credits: 1050,
            catalog_samples_credits: 2400,
            by_user: [{ key: "u1", email: "a@gbtech.test", name: "เอ", department: "บัญชี", runs: 2, succeeded: 1, failed: 1, waiting: 0, credits: 1000, month_spent: 1000, last_run_at: null }],
            by_agent: [],
          })
        : json({ org: { org_id: "o", name: "จีบีเทค", access: "list", domains: [], user_monthly_credits: 0, contact_name: "", contact_email: "", updated_by: "", updated_at: "" }, members: [], departments: [] });
    render(<AdminPage />);
    fireEvent.click(await screen.findByTestId("am-admin-tab-spend"));
    const user = await screen.findByTestId("am-admin-spend-user");
    expect(user.textContent).toContain("สำเร็จ / ไม่สำเร็จ / รอคำตอบ");
    expect(user.textContent).toContain("1 / 1 / 0");
    expect(screen.queryByTestId("am-admin-spend-not-charged-total")).toBeNull();
  });
});
