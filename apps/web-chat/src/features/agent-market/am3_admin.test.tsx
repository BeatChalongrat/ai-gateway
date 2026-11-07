// AM3 — sharing inside the organisation:
//   * the share dialog suggests colleagues, says plainly the colleague must
//     sign in with that email and nothing is public, and gives the link;
//   * the shared view shows the server's refusal plainly (NOT_SHARED), and a
//     share that may review offers the sign-off.
// (AM7: the organisation's admins' page moved to the admin app — its tests
// are admin-ui/app/agent-market/page.am7.test.tsx.)
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
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
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
