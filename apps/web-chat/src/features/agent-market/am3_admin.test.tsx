// AM3 — sharing inside the organisation, and the organisation's admins:
//   * the share dialog suggests colleagues, says plainly the colleague must
//     sign in with that email and nothing is public, and gives the link;
//   * the shared view shows the server's refusal plainly (NOT_SHARED), and a
//     share that may review offers the sign-off;
//   * admins: access (switch, domains, contact, budget, people), departments,
//     spend per person and per agent, the catalog (disable needs a reason),
//     reported problems; a publisher sees the catalog tab only.
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
  usePathname: () => "/chat/agents/admin",
}));

import { useI18nStore } from "@/lib/i18n";

import { AdminPage } from "./admin/AdminPage";
import { ShareButton } from "./share/ShareButton";
import { SharedRunPage } from "./share/SharedRunPage";
import { resetAgentMarketForTest } from "./useAgentMarket";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
let availability: Record<string, unknown>;
const callsTo = (path: string, method?: string) =>
  raw.mock.calls.filter((c) => c[0] === path && (!method || ((c[1] as RequestInit | undefined)?.method ?? "GET") === method));
const bodyOf = (c: unknown[]) => JSON.parse(String((c[1] as RequestInit).body));

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  availability = { available: true, runner: { healthy: true }, role: "admin", admin: true, publisher: true };
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) =>
    path === "/agent-market/availability" ? json(availability) : handler(path, init),
  );
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("sharing a run", () => {
  it("suggests colleagues, gives the link, lists and revokes the share", async () => {
    let shares: unknown[] = [];
    handler = (path, init) => {
      if (path.startsWith("/agent-market/people")) return json({ people: [{ email: "manager@gbtech.test", name: "ผู้จัดการ" }] });
      if (path === "/agent-market/runs/r-1/shares" && init?.method === "POST") {
        const s = { id: "s-1", run_id: "r-1", recipient_email: "manager@gbtech.test", can_review: true, created_at: "", expires_at: "2026-10-28T00:00:00Z", revoked_at: null, last_opened_at: null, opens: 0 };
        shares = [s];
        return json({ share: s, path: "/chat/agents/shared/s-1" });
      }
      if (path === "/agent-market/runs/r-1/shares") return json({ shares });
      if (path === "/agent-market/shares/s-1") return json({ revoked: true });
      return json({}, 404);
    };
    render(<ShareButton runId="r-1" />);
    fireEvent.click(screen.getByTestId("am-share-open"));
    const dialog = screen.getByTestId("am-share-dialog");
    expect(dialog.textContent).toContain("ต้องเข้าสู่ระบบด้วยอีเมลที่คุณใส่");
    expect(dialog.textContent).toContain("ไม่มีลิงก์สาธารณะ");
    fireEvent.change(screen.getByTestId("am-share-email"), { target: { value: "man" } });
    const suggestion = await within(await screen.findByTestId("am-share-people")).findByText("ผู้จัดการ · manager@gbtech.test");
    fireEvent.click(suggestion);
    fireEvent.click(screen.getByTestId("am-share-review"));
    fireEvent.click(screen.getByTestId("am-share-send"));
    expect((await screen.findByTestId("am-share-link")).textContent).toContain("/chat/agents/shared/s-1");
    const post = callsTo("/agent-market/runs/r-1/shares", "POST")[0]!;
    expect(bodyOf(post)).toEqual({ email: "manager@gbtech.test", can_review: true, expires_days: 30 });
    const list = await screen.findByTestId("am-share-list");
    expect(list.textContent).toContain("manager@gbtech.test");
    fireEvent.click(within(list).getByTestId("am-share-revoke"));
    await waitFor(() => expect(screen.queryByTestId("am-share-list")).toBeNull());
  });

  it("the shared view: the refusal as written; a share that may review offers the sign-off", async () => {
    handler = () => json({ error: { code: "NOT_SHARED", message: "ลิงก์นี้ไม่ได้แชร์ให้บัญชีนี้ หรือถูกยกเลิก/หมดอายุแล้ว — ขอให้เจ้าของรอบงานแชร์ให้อีเมลของคุณอีกครั้ง" } }, 403);
    // the recipient may have no Agent Market access of their own
    availability = { available: false };
    const first = render(<SharedRunPage shareId="s-1" />);
    const refused = await screen.findByTestId("am-shared-refused");
    expect(refused.textContent).toContain("เปิดลิงก์นี้ไม่ได้");
    expect(refused.textContent).toContain("ขอให้เจ้าของรอบงานแชร์ให้อีเมลของคุณอีกครั้ง");
    first.unmount();

    handler = (path) =>
      path === "/agent-market/shared/s-2"
        ? json({
            share: { id: "s-2", run_id: "r-1", recipient_email: "cfo@x.test", can_review: true, created_at: "", expires_at: null, revoked_at: null, last_opened_at: null, opens: 1 },
            owner: "สมหญิง บัญชี",
            agent: { title: "ปิดงบการเงิน", name: "รายงานการเงิน", slug: "sran.financial-report", version: "1.2.0" },
            run: { id: "r-1", status: "succeeded", period: "2026-09", created_at: "", finished_at: "2026-09-28T03:00:00Z", assumptions: [] },
            artifacts: [{ id: "a-1", format: "docx", name: "report_v1.docx", version: 1, size: 100, created_at: "" }],
            exceptions: [
              { id: "e-1", run_id: "r-1", severity: "review", reason_code: "over_claim_limit", status: "resolved", created_at: "", source_ref: { subject: "C02", decision: { label: "อนุมัติเป็นกรณียกเว้น" } } },
            ],
            reviews: [],
          })
        : json({}, 404);
    resetAgentMarketForTest();
    render(<SharedRunPage shareId="s-2" />);
    const view = await screen.findByTestId("am-shared");
    expect(view.textContent).toContain("สมหญิง บัญชี แชร์ให้คุณ");
    expect(within(await screen.findByTestId("am-shared-files")).getByText("report_v1.docx")).toBeTruthy();
    expect(screen.getByTestId("am-shared-exceptions").textContent).toContain("ตัดสินว่า: อนุมัติเป็นกรณียกเว้น");
    expect(screen.getByTestId("am-signoff-open")).toBeTruthy();
    expect(screen.getByTestId("am-problem-open")).toBeTruthy();
  });
});

describe("the admins' page", () => {
  const org = {
    org: { org_id: "default", name: "", access: "list", domains: ["gbtech.test"], user_monthly_credits: 3000000, contact_name: "ฝ่ายไอที", contact_email: "it@gbtech.test", updated_by: "", updated_at: "" },
    members: [
      { email: "owner@gbtech.test", display_name: "เจ้าของ", department: "", role: "admin", monthly_credits: null, added_by: "bootstrap", env_admin: true, user_id: "u" },
      { email: "a@gbtech.test", display_name: "เอ", department: "บัญชี", role: "member", monthly_credits: 500000, added_by: "owner@gbtech.test" },
    ],
    departments: [{ name: "บัญชี", monthly_credits: 5000000, contact_name: "หัวหน้าบัญชี", contact_email: "", members: 1 }],
  };

  it("admin: five tabs; the access tab saves the switch and adds a person; a refusal is shown as written", async () => {
    handler = (path, init) => {
      if (path === "/agent-market/admin/org" && init?.method === "PUT") return json({ org: org.org, resumed: 0 });
      if (path === "/agent-market/admin/org") return json(org);
      if (path === "/agent-market/admin/members/new%40gbtech.test") return json({ member: {}, created: true, resumed: 0 });
      if (path === "/agent-market/admin/members/a%40gbtech.test")
        return json({ error: { code: "LAST_ADMIN", message: "ต้องมีผู้ดูแลอย่างน้อยหนึ่งคนเสมอ — เพิ่มผู้ดูแลคนใหม่ก่อน" } }, 409);
      return json({}, 404);
    };
    render(<AdminPage />);
    const access = await screen.findByTestId("am-admin-access");
    expect(["access", "departments", "spend", "catalog", "problems"].every((x) => screen.queryByTestId(`am-admin-tab-${x}`))).toBe(true);
    const people = within(access).getByTestId("am-admin-people");
    expect(people.textContent).toContain("ผู้ดูแลจากการตั้งค่าระบบ");
    fireEvent.click(screen.getByTestId("am-access-everyone"));
    fireEvent.click(screen.getByTestId("am-admin-org-save"));
    await waitFor(() => expect(callsTo("/agent-market/admin/org", "PUT")).toHaveLength(1));
    expect(bodyOf(callsTo("/agent-market/admin/org", "PUT")[0]!)).toMatchObject({ access: "everyone", domains: ["gbtech.test"], user_monthly_credits: "3000000" });

    fireEvent.change(screen.getByTestId("am-admin-add-email"), { target: { value: "new@gbtech.test" } });
    fireEvent.click(screen.getByTestId("am-admin-add-save"));
    await waitFor(() => expect(callsTo("/agent-market/admin/members/new%40gbtech.test", "PUT")).toHaveLength(1));
    expect(bodyOf(callsTo("/agent-market/admin/members/new%40gbtech.test", "PUT")[0]!)).toEqual({ display_name: "", department: "", role: "member", monthly_credits: null });

    // a row's change refused by the server: its sentence, as written
    const row = within(people).getAllByTestId("am-admin-person")[1]!;
    fireEvent.change(within(row).getByLabelText("บทบาทของ a@gbtech.test"), { target: { value: "admin" } });
    fireEvent.click(within(row).getByText("บันทึก"));
    expect((await screen.findByText(/ต้องมีผู้ดูแลอย่างน้อยหนึ่งคนเสมอ/)).textContent).toContain("เพิ่มผู้ดูแลคนใหม่ก่อน");
  });

  it("spend per person and per agent, samples on their own line", async () => {
    handler = (path) =>
      path.startsWith("/agent-market/admin/spend")
        ? json({
            days: 30,
            since: "",
            total_credits: 1050,
            catalog_samples_credits: 2400,
            by_user: [{ key: "u1", email: "a@gbtech.test", name: "เอ", department: "บัญชี", runs: 2, succeeded: 1, failed: 1, waiting: 0, credits: 1000, month_spent: 1000, monthly_limit: 3000000, last_run_at: null }],
            by_agent: [{ key: "sran.financial-report", name: "รายงานการเงิน", runs: 3, succeeded: 2, failed: 1, waiting: 0, users: 2, credits: 1050, month_spent: 1050, last_run_at: null }],
          })
        : json(org);
    render(<AdminPage />);
    fireEvent.click(await screen.findByTestId("am-admin-tab-spend"));
    const user = await screen.findByTestId("am-admin-spend-user");
    expect(user.textContent).toContain("เอ");
    expect(user.textContent).toContain("a@gbtech.test · บัญชี");
    expect(screen.getByTestId("am-admin-spend-agent").textContent).toContain("2 คนใช้");
    expect(screen.getByTestId("am-admin-spend-samples").textContent).toContain("2,400");
    fireEvent.click(screen.getByText("90 วัน"));
    await waitFor(() => expect(raw.mock.calls.some((c) => c[0] === "/agent-market/admin/spend?days=90")).toBe(true));
  });

  it("the catalog: disabling for everyone needs a reason; problems close with a note", async () => {
    handler = (path, init) => {
      if (path === "/agent-market/admin/catalog")
        return json({
          admin: true,
          agents: [{ slug: "sran.expense-audit", version: "1.1.0", name: "ตรวจการเบิก", manifest: {}, status: "published", installs: 3, sample: { run_id: "s", version: "1.1.0", status: "succeeded", credits: 1, seconds: 1 } }],
        });
      if (path === "/agent-market/admin/catalog/sran.expense-audit" && init?.method === "PUT") {
        const b = JSON.parse(String(init.body));
        return b.reason ? json({ state: { slug: "x", status: "disabled", reason: b.reason }, stopped_runs: 2 }) : json({ error: { code: "REASON_REQUIRED", message: "การปิด agent ให้ทุกคนต้องบอกเหตุผลสั้น ๆ — ใส่เหตุผลแล้วกดอีกครั้ง" } }, 400);
      }
      if (path.startsWith("/agent-market/admin/problems?"))
        return json({
          problems: [{ id: "p-1", ref: "AB12CD", email: "a@gbtech.test", slug: "sran.expense-audit", version: "1.1.0", run_status: "failed", run_reason: "compute_failed", step: "compute", step_error: "KeyError", page: "/chat/agents/runs/r", text: "ยอดไม่ตรง", status: "open", admin_note: "", closed_by: "", created_at: "2026-09-28T00:00:00Z", run_id: "r-1" }],
        });
      if (path === "/agent-market/admin/problems/p-1/close") return json({ problem: {} });
      return json(org);
    };
    render(<AdminPage />);
    fireEvent.click(await screen.findByTestId("am-admin-tab-catalog"));
    const row = await screen.findByTestId("am-admin-catalog-row");
    expect(row.textContent).toContain("ติดตั้ง 3 คน");
    fireEvent.click(within(row).getByTestId("am-admin-disable"));
    expect((await within(row).findByTestId("am-admin-note")).textContent).toContain("ต้องบอกเหตุผล");
    fireEvent.change(within(row).getByTestId("am-admin-disable-reason"), { target: { value: "กำลังแก้กฎวันหยุด" } });
    fireEvent.click(within(row).getByTestId("am-admin-disable"));
    await waitFor(() => expect(within(row).getByTestId("am-admin-note").textContent).toContain("หยุดงานที่ยังไม่เริ่ม 2 รอบ"));

    fireEvent.click(screen.getByTestId("am-admin-tab-problems"));
    const p = await screen.findByTestId("am-admin-problem");
    expect(p.textContent).toContain("#AB12CD");
    expect(p.textContent).toContain("ขั้นตอนที่หยุด: compute");
    fireEvent.click(within(p).getByTestId("am-admin-problem-close"));
    await waitFor(() => expect(raw.mock.calls.some((c) => c[0] === "/agent-market/admin/problems/p-1/close")).toBe(true));
  });

  it("a publisher sees the catalog tab only; a member sees no admin page", async () => {
    availability = { available: true, role: "publisher", admin: false, publisher: true };
    handler = (path) => (path === "/agent-market/admin/catalog" ? json({ agents: [], admin: false }) : json({}, 404));
    const pub = render(<AdminPage />);
    expect(await screen.findByTestId("am-admin-tab-catalog")).toBeTruthy();
    expect(screen.queryByTestId("am-admin-tab-access")).toBeNull();
    pub.unmount();
    resetAgentMarketForTest();
    availability = { available: true, role: "member", admin: false, publisher: false };
    render(<AdminPage />);
    expect(await screen.findByTestId("am-admin-denied")).toBeTruthy();
  });
});
