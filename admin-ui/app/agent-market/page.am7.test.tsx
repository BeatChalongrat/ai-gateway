// AM7 — Agent Market's admin page in admin-ui (moved from the chat app's
// /chat/agents/admin). The page renders only the sections chat-edge names
// for the signed-in account, shows the organisation's settings exactly as
// the server holds them, keeps the disable control to those who may use it,
// never offers a password field for the mail relay, and says calmly when
// Agent Market is switched off.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api, nav } = vi.hoisted(() => ({
  api: {
    me: vi.fn(),
    org: vi.fn(),
    // AM9: the Models tab also lists each agent's egress under the organisation's switch
    egress: vi.fn(async () => ({ org: false, agents: [] })),
    putEgress: vi.fn(),
    putOrg: vi.fn(),
    putMember: vi.fn(),
    deleteMember: vi.fn(),
    putDepartment: vi.fn(),
    deleteDepartment: vi.fn(),
    spend: vi.fn(),
    problems: vi.fn(),
    closeProblem: vi.fn(),
    catalog: vi.fn(),
    putCatalog: vi.fn(),
    remakeSample: vi.fn(),
    runs: vi.fn(),
    openRun: vi.fn(),
    run: vi.fn(),
    removeEvidence: vi.fn(),
    artifactHref: (runId: string, id: string) => `/api/chat-edge/agent-market/runs/${runId}/artifacts/${id}/download`,
    fileHref: (runId: string, id: string) => `/api/chat-edge/agent-market/runs/${runId}/files/${id}/download`,
    mail: vi.fn(),
    putMail: vi.fn(),
    testMail: vi.fn(),
  },
  nav: { search: "", replace: vi.fn(), push: vi.fn() },
}));

vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: api };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: nav.push, back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(nav.search),
  usePathname: () => "/agent-market",
}));

import { ChatEdgeApiError } from "../../lib/chatEdgeApi";
import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";

import AgentMarketPage from "./page";
import AdminRunPage from "./runs/[id]/page";

// ── fixtures ─────────────────────────────────────────────────────────────

const ADMIN_SECTIONS = ["access", "departments", "spend", "models", "catalog", "problems", "runs"];

const ADMIN_ME = {
  email: "boss@example.invalid",
  name: "Boss",
  admin_role: "admin",
  role: "admin",
  org_id: "org-1",
  sections: ADMIN_SECTIONS,
  can_disable: true,
};

const PUBLISHER_ME = {
  email: "pim@example.invalid",
  name: "Pim",
  admin_role: "agent_publisher",
  role: "publisher",
  org_id: "org-1",
  sections: ["catalog", "problems"],
  can_disable: false,
};

const ORG = {
  org: {
    org_id: "org-1",
    name: "บริษัท ทดสอบ จำกัด",
    access: "list",
    domains: ["example.invalid", "branch.example.invalid"],
    user_monthly_credits: 1_500_000,
    contact_name: "คุณสมศรี",
    contact_email: "somsri@example.invalid",
    updated_by: "boss@example.invalid",
    updated_at: "2026-09-01T03:00:00Z",
    documents_may_leave: true,
    retention_years: 7,
    storage_warn_mb: 4096,
  },
  members: [
    {
      email: "somchai@example.invalid",
      user_id: "u1",
      display_name: "สมชาย",
      department: "บัญชี",
      role: "member",
      monthly_credits: null,
      added_by: "boss@example.invalid",
    },
    {
      email: "root@example.invalid",
      display_name: "Root",
      department: "",
      role: "admin",
      monthly_credits: null,
      added_by: "env",
      env_admin: true,
    },
  ],
  departments: [{ name: "บัญชี", monthly_credits: 3_000_000, contact_name: "", contact_email: "", members: 1 }],
  pricing: { credits_per_usd: 3_000_000, thb_per_usd: 35 },
};

const CATALOG = {
  admin: true,
  agents: [
    { slug: "financial_report", version: "1.2.0", name: "รายงานการเงิน", status: "published", installs: 4, sample: null },
    { slug: "invoice_po", version: "1.0.1", name: "ใบกำกับภาษีกับ PO", status: "disabled", status_reason: "กำลังแก้", installs: 1, sample: null },
  ],
};

const PROBLEM = {
  id: "p1",
  ref: "A1B2",
  email: "somchai@example.invalid",
  run_id: "11111111-2222-3333-4444-555555555555",
  slug: "financial_report",
  version: "1.2.0",
  run_status: "failed",
  run_reason: "parse_failed",
  step: "compute",
  step_error: "KeyError: 'revenue'",
  page: "/chat/agents/runs/1111",
  text: "ยอดรวมไม่ตรง",
  status: "open",
  admin_note: "",
  closed_by: "",
  created_at: "2026-09-20T02:00:00Z",
};

const RUN_META = {
  id: "run-1",
  agent: "รายงานการเงิน",
  slug: "financial_report",
  person: "สมชาย",
  email: "somchai@example.invalid",
  status: "succeeded",
  outcome: "completed",
  reason: "",
  period_label: "กันยายน 2569",
  credits: 300_000,
  model: "qwen3.8-27b-fp8",
  model_self_hosted: true,
  created_at: "2026-09-20T02:00:00Z",
  open_now: false,
  times_opened: 0,
};

const MAIL_UNSET = {
  settings: { host: "", port: 587, tls_mode: "starttls", username: "", sender: "" },
  password_set: false,
  password_env: "AGENT_MARKET_SMTP_PASSWORD",
  ready: false,
  not_ready: "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล — ใส่ชื่อเซิร์ฟเวอร์และอีเมลผู้ส่งก่อน",
};

function renderPage() {
  return render(
    <I18nProvider>
      <ToastProvider>
        <AgentMarketPage />
      </ToastProvider>
    </I18nProvider>,
  );
}

const input = (id: string) => screen.getByTestId(id) as HTMLInputElement;

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = "";
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
  api.org.mockResolvedValue(ORG);
  api.catalog.mockResolvedValue(CATALOG);
  api.problems.mockResolvedValue({ problems: [PROBLEM] });
  api.runs.mockResolvedValue({ runs: [RUN_META], grant_minutes: 60 });
  api.spend.mockResolvedValue({ days: 30, since: "", by_user: [], by_agent: [], total_credits: 0, catalog_samples_credits: 0 });
  api.mail.mockResolvedValue(MAIL_UNSET);
  api.putOrg.mockImplementation(async (patch: Record<string, unknown>) => ({ org: { ...ORG.org, ...patch }, resumed: 0 }));
  api.putCatalog.mockResolvedValue({ state: { slug: "x", status: "disabled", reason: "" }, stopped_runs: 0 });
  api.testMail.mockImplementation(async (to: string) => ({ sent: true, to }));
});
afterEach(cleanup);

// ── a. the sections an account may open ──────────────────────────────────

describe("section tabs (AM7)", () => {
  it("an admin sees a tab for every section in me.sections, in order, and the access section first", async () => {
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    await screen.findByTestId("am-tab-access");
    const tabs = screen.getAllByRole("tab").map((el) => el.getAttribute("data-testid"));
    expect(tabs).toEqual(ADMIN_SECTIONS.map((s) => `am-tab-${s}`));
    expect(screen.queryByTestId("am-tab-mail")).toBeNull();
    expect(screen.getByTestId("am-tab-access")).toHaveAttribute("aria-selected", "true");
    await screen.findByTestId("am-access");
  });

  it("the mail tab appears once the server lists it", async () => {
    api.me.mockResolvedValue({ ...ADMIN_ME, sections: [...ADMIN_SECTIONS, "mail"] });
    renderPage();
    await screen.findByTestId("am-tab-mail");
    expect(screen.getAllByRole("tab")).toHaveLength(8);
  });

  it("an agent_publisher sees exactly the catalog and problems tabs, with the Thai labels", async () => {
    window.localStorage.setItem("sran-admin-locale", "th");
    api.me.mockResolvedValue(PUBLISHER_ME);
    renderPage();
    await screen.findByTestId("am-tab-catalog");
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((el) => el.getAttribute("data-testid"))).toEqual(["am-tab-catalog", "am-tab-problems"]);
    await waitFor(() => expect(tabs.map((el) => el.textContent)).toEqual(["แคตตาล็อก", "ปัญหาที่แจ้ง"]));
    for (const hidden of ["access", "departments", "spend", "models", "runs", "mail"]) {
      expect(screen.queryByTestId(`am-tab-${hidden}`)).toBeNull();
    }
    // the first allowed section opens, and the admin-only loaders never ran
    await screen.findByTestId("am-catalog");
    expect(api.org).not.toHaveBeenCalled();
    expect(api.runs).not.toHaveBeenCalled();
  });

  it("opens the section named in ?section= and writes a picked one back to the URL", async () => {
    nav.search = "section=models";
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    await screen.findByTestId("am-models");
    expect(screen.getByTestId("am-tab-models")).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByTestId("am-tab-catalog"));
    expect(nav.replace).toHaveBeenCalledWith("/agent-market?section=catalog", { scroll: false });
    await screen.findByTestId("am-catalog");
  });

  it("a ?section= the account may not open falls back to its first section", async () => {
    nav.search = "section=access";
    api.me.mockResolvedValue(PUBLISHER_ME);
    renderPage();
    await screen.findByTestId("am-catalog");
    expect(screen.queryByTestId("am-access")).toBeNull();
  });
});

// ── b. the organisation's settings, exactly as the server holds them ─────

describe("access and models show the org row as returned (AM7)", () => {
  it("access: every input holds the value amAdmin.org() returned", async () => {
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    await screen.findByTestId("am-access");
    expect(input("am-org-name").value).toBe(ORG.org.name);
    expect(input("am-access-list").checked).toBe(true);
    expect(input("am-access-off").checked).toBe(false);
    expect(input("am-access-everyone").checked).toBe(false);
    expect(input("am-org-domains").value).toBe("example.invalid, branch.example.invalid");
    expect(input("am-org-budget").value).toBe(String(ORG.org.user_monthly_credits));
    expect(input("am-org-contact-name").value).toBe(ORG.org.contact_name);
    expect(input("am-org-contact-email").value).toBe(ORG.org.contact_email);
    // 1,500,000 credits / 3,000,000 × 35 = 17.50 baht
    expect(screen.getByTestId("am-access").textContent).toContain("17.50");
    // the pointer to the chat users page
    const line = screen.getByTestId("am-chat-users-line");
    expect(within(line).getByRole("link")).toHaveAttribute("href", "/chat-users");
  });

  it("access: an env admin row can be neither removed nor given another role", async () => {
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    const rows = await screen.findAllByTestId("am-person");
    const root = rows.find((r) => r.textContent?.includes("root@example.invalid"))!;
    const other = rows.find((r) => r.textContent?.includes("somchai@example.invalid"))!;
    expect(within(root).queryByTestId("am-person-remove")).toBeNull();
    expect(within(root).getByLabelText(/Role of root/)).toBeDisabled();
    expect(within(other).getByTestId("am-person-remove")).toBeInTheDocument();
    expect(within(other).getByLabelText(/Role of somchai/)).not.toBeDisabled();
  });

  it("access: saving sends the organisation fields to putOrg", async () => {
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    await screen.findByTestId("am-access");
    fireEvent.click(input("am-access-everyone"));
    fireEvent.change(input("am-org-budget"), { target: { value: "2,000,000" } });
    fireEvent.click(screen.getByTestId("am-org-save"));
    await waitFor(() =>
      expect(api.putOrg).toHaveBeenCalledWith({
        name: ORG.org.name,
        access: "everyone",
        domains: ["example.invalid", "branch.example.invalid"],
        user_monthly_credits: "2000000",
        contact_name: ORG.org.contact_name,
        contact_email: ORG.org.contact_email,
      }),
    );
  });

  it("models: the switch, years and warning hold the org's values; save sends exactly those three fields", async () => {
    nav.search = "section=models";
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    await screen.findByTestId("am-models");
    expect(input("am-leave-on").checked).toBe(true);
    expect(input("am-leave-off").checked).toBe(false);
    expect(input("am-retention-years").value).toBe("7");
    expect(input("am-storage-warn").value).toBe("4096");
    expect(screen.getByTestId("am-models-changed").textContent).toContain("boss@example.invalid");
    expect(within(screen.getByTestId("am-models-audit")).getByRole("link")).toHaveAttribute("href", "/audit-log");

    fireEvent.click(input("am-leave-off"));
    fireEvent.change(input("am-retention-years"), { target: { value: "10" } });
    fireEvent.click(screen.getByTestId("am-models-save"));
    await waitFor(() => expect(api.putOrg).toHaveBeenCalledTimes(1));
    expect(api.putOrg).toHaveBeenCalledWith({ documents_may_leave: false, retention_years: 10, storage_warn_mb: 4096 });
  });

  it("models: years outside 1–30 are refused before anything is sent", async () => {
    nav.search = "section=models";
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    await screen.findByTestId("am-models");
    fireEvent.change(input("am-retention-years"), { target: { value: "31" } });
    fireEvent.click(screen.getByTestId("am-models-save"));
    await screen.findByRole("alert");
    expect(api.putOrg).not.toHaveBeenCalled();
  });
});

// ── c. who may disable an agent ──────────────────────────────────────────

describe("catalog disable controls (AM7)", () => {
  it("absent when can_disable is false (publish/unpublish/sample stay)", async () => {
    api.me.mockResolvedValue(PUBLISHER_ME);
    renderPage();
    const rows = await screen.findAllByTestId("am-catalog-row");
    expect(rows).toHaveLength(2);
    expect(screen.queryByTestId("am-disable")).toBeNull();
    expect(screen.queryByTestId("am-disable-reason")).toBeNull();
    expect(screen.queryByTestId("am-enable")).toBeNull();
    expect(screen.getByTestId("am-unpublish")).toBeInTheDocument();
    expect(screen.getAllByTestId("am-remake-sample")).toHaveLength(2);
  });

  it("present when can_disable is true; a reason is required before disabling", async () => {
    nav.search = "section=catalog";
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    await screen.findAllByTestId("am-catalog-row");
    expect(screen.getByTestId("am-disable")).toBeInTheDocument();
    expect(screen.getByTestId("am-enable")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("am-disable"));
    expect(api.putCatalog).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("am-disable-reason"), { target: { value: "ยอดภาษีผิด" } });
    fireEvent.click(screen.getByTestId("am-disable"));
    await waitFor(() => expect(api.putCatalog).toHaveBeenCalledWith("financial_report", { status: "disabled", reason: "ยอดภาษีผิด" }));
  });

  it("shows the server's sentence when an action is refused", async () => {
    nav.search = "section=catalog";
    api.me.mockResolvedValue(ADMIN_ME);
    api.putCatalog.mockRejectedValue(new ChatEdgeApiError(409, { error: { code: "X", message: "ยังเผยแพร่ไม่ได้ — ไม่มีอะไรเปลี่ยน" } }));
    renderPage();
    await screen.findAllByTestId("am-catalog-row");
    fireEvent.click(screen.getByTestId("am-unpublish"));
    expect(await screen.findByText("ยังเผยแพร่ไม่ได้ — ไม่มีอะไรเปลี่ยน")).toBeInTheDocument();
  });
});

// ── d. switched off / not allowed ────────────────────────────────────────

describe("when the page cannot open (AM7)", () => {
  it("Agent Market switched off (404) → a calm notice, no tabs, no crash", async () => {
    api.me.mockRejectedValue(new ChatEdgeApiError(404, { error: { code: "NOT_FOUND", message: "not found" } }));
    renderPage();
    const notice = await screen.findByTestId("am-off");
    expect(notice.textContent).toMatch(/not switched on|ยังไม่ได้เปิด/);
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(api.org).not.toHaveBeenCalled();
  });

  it("a refused account (403) → says this account cannot open the page", async () => {
    api.me.mockRejectedValue(new ChatEdgeApiError(403, { error: { code: "NOT_ADMIN", message: "no" } }));
    renderPage();
    await screen.findByTestId("am-denied");
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });
});

// ── e. the mail relay ────────────────────────────────────────────────────

describe("mail section (AM7 phase 5)", () => {
  it("unconfigured: shows the not_ready sentence, never a password input, and sends a test to the typed address", async () => {
    nav.search = "section=mail";
    api.me.mockResolvedValue({ ...ADMIN_ME, sections: [...ADMIN_SECTIONS, "mail"] });
    const { container } = renderPage();
    await screen.findByTestId("am-mail");
    expect(screen.getByTestId("am-mail-not-ready").textContent).toContain(MAIL_UNSET.not_ready);
    expect(screen.getByTestId("am-mail-password").textContent).toContain("AGENT_MARKET_SMTP_PASSWORD");
    expect(container.ownerDocument.querySelector('input[type="password"]')).toBeNull();
    for (const el of Array.from(container.ownerDocument.querySelectorAll("input"))) {
      expect(`${el.name} ${el.id} ${el.getAttribute("aria-label") ?? ""} ${el.dataset.testid ?? ""}`).not.toMatch(/pass/i);
    }

    const to = input("am-mail-test-to");
    expect(to.value).toBe(ADMIN_ME.email);
    fireEvent.change(to, { target: { value: "finance@example.invalid" } });
    fireEvent.click(screen.getByTestId("am-mail-test"));
    await waitFor(() => expect(api.testMail).toHaveBeenCalledWith("finance@example.invalid"));
    expect((await screen.findByTestId("am-mail-test-note")).textContent).toContain("finance@example.invalid");
  });

  it("a refused test shows the server's sentence", async () => {
    nav.search = "section=mail";
    api.me.mockResolvedValue({ ...ADMIN_ME, sections: [...ADMIN_SECTIONS, "mail"] });
    api.testMail.mockRejectedValue(new ChatEdgeApiError(422, { error: { code: "MAIL_NOT_READY", message: "ส่งไม่ได้ — ยังไม่ได้ตั้งเซิร์ฟเวอร์" } }));
    renderPage();
    await screen.findByTestId("am-mail");
    fireEvent.click(screen.getByTestId("am-mail-test"));
    expect(await screen.findByText("ส่งไม่ได้ — ยังไม่ได้ตั้งเซิร์ฟเวอร์")).toBeInTheDocument();
  });
});

// ── f. problems: the run link is an admin's ──────────────────────────────

describe("problems (AM7)", () => {
  it("an agent_publisher reads the report but gets no run link", async () => {
    nav.search = "section=problems";
    api.me.mockResolvedValue(PUBLISHER_ME);
    renderPage();
    await screen.findByTestId("am-problem");
    expect(screen.getByTestId("am-problem-step-error").textContent).toBe("KeyError: 'revenue'");
    expect(screen.queryByTestId("am-problem-run")).toBeNull();
  });

  it("an admin gets a link to the run's page", async () => {
    nav.search = "section=problems";
    api.me.mockResolvedValue(ADMIN_ME);
    renderPage();
    const link = await screen.findByTestId("am-problem-run");
    expect(link).toHaveAttribute("href", `/agent-market/runs/${PROBLEM.run_id}`);
  });
});

// ── runs: content opens with a reason ────────────────────────────────────

describe("runs (AM7)", () => {
  it("opening a run needs a reason of 8+ characters, then goes to the run's page", async () => {
    nav.search = "section=runs";
    api.me.mockResolvedValue(ADMIN_ME);
    api.openRun.mockResolvedValue({ opened: true, expires_at: "2026-09-20T03:00:00Z" });
    renderPage();
    fireEvent.click(await screen.findByTestId("am-run-open"));
    fireEvent.change(await screen.findByTestId("am-reason-text"), { target: { value: "short" } });
    fireEvent.click(screen.getByTestId("am-reason-go"));
    expect(await screen.findByTestId("am-reason-error")).toBeInTheDocument();
    expect(api.openRun).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("am-reason-text"), { target: { value: "ตรวจยอดที่ผู้ใช้แจ้งว่าไม่ตรง" } });
    fireEvent.click(screen.getByTestId("am-reason-go"));
    await waitFor(() => expect(api.openRun).toHaveBeenCalledWith("run-1", "ตรวจยอดที่ผู้ใช้แจ้งว่าไม่ตรง"));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/agent-market/runs/run-1"));
  });

  it("removing a finished run's files early takes a reason and says how many went", async () => {
    nav.search = "section=runs";
    api.me.mockResolvedValue(ADMIN_ME);
    api.removeEvidence.mockResolvedValue({ removed_objects: 3 });
    renderPage();
    fireEvent.click(await screen.findByTestId("am-run-remove"));
    fireEvent.change(await screen.findByTestId("am-reason-text"), { target: { value: "ลูกค้าขอลบตามสัญญา" } });
    fireEvent.click(screen.getByTestId("am-reason-go"));
    await waitFor(() => expect(api.removeEvidence).toHaveBeenCalledWith("run-1", "ลูกค้าขอลบตามสัญญา"));
    expect((await screen.findByTestId("am-runs-note")).textContent).toMatch(/3/);
  });
});

// ── the run page ─────────────────────────────────────────────────────────

describe("an opened run (AM7)", () => {
  const DETAIL = {
    run: { id: "run-1", status: "succeeded", outcome: "completed", period: "2026-09", credits_spent: 300_000, model_used: "qwen", model_self_hosted: true, created_at: "2026-09-20T02:00:00Z" },
    steps: [{ id: "s1", seq: 1, name: "compute", kind: "script", status: "succeeded" }],
    artifacts: [{ id: "a1", format: "xlsx", name: "report.xlsx", version: 1, size: 2048, created_at: "2026-09-20T02:05:00Z" }],
    files: [{ id: "f1", name: "gl.csv", size: 512 }],
    install: { id: "i1", title: "รายงานการเงิน", slug: "financial_report" },
    access_log: [{ admin_name: "Boss", admin_email: "boss@example.invalid", reason: "ตรวจยอดที่ไม่ตรง", opened_at: "2026-09-20T02:10:00Z", at_label: "20 ก.ย. 09:10" }],
  };

  function renderRun() {
    return render(
      <I18nProvider>
        <ToastProvider>
          <AdminRunPage params={{ id: "run-1" }} />
        </ToastProvider>
      </I18nProvider>,
    );
  }

  it("asks for a reason on ACCESS_REASON_NEEDED, opens the run, then shows its files with proxy download links", async () => {
    api.run
      .mockRejectedValueOnce(new ChatEdgeApiError(403, { error: { code: "ACCESS_REASON_NEEDED", message: "ต้องกด “เปิดดูพร้อมเหตุผล” ก่อน" } }))
      .mockResolvedValue(DETAIL);
    api.openRun.mockResolvedValue({ opened: true, expires_at: "" });
    renderRun();
    await screen.findByTestId("am-run-reason");
    expect(screen.getByText("ต้องกด “เปิดดูพร้อมเหตุผล” ก่อน")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("am-reason-text"), { target: { value: "ตรวจยอดที่ไม่ตรง" } });
    fireEvent.click(screen.getByTestId("am-reason-go"));
    await waitFor(() => expect(api.openRun).toHaveBeenCalledWith("run-1", "ตรวจยอดที่ไม่ตรง"));

    await screen.findByTestId("am-run-detail");
    expect(api.run).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("am-run-admin-view")).toBeInTheDocument();
    expect(screen.getByTestId("am-run-access-log").textContent).toContain("ตรวจยอดที่ไม่ตรง");
    const input = within(screen.getByTestId("am-run-inputs")).getByRole("link");
    expect(input).toHaveAttribute("href", "/api/chat-edge/agent-market/runs/run-1/files/f1/download");
    expect(input).toHaveAttribute("download");
    const output = within(screen.getByTestId("am-run-outputs")).getByRole("link");
    expect(output).toHaveAttribute("href", "/api/chat-edge/agent-market/runs/run-1/artifacts/a1/download");
    expect(screen.getByTestId("am-run-back")).toHaveAttribute("href", "/agent-market?section=runs");
  });

  it("another refusal shows the server's sentence, not the reason form", async () => {
    api.run.mockRejectedValue(new ChatEdgeApiError(404, { error: { code: "NOT_FOUND", message: "ไม่พบรอบนี้" } }));
    renderRun();
    expect(await screen.findByText("ไม่พบรอบนี้")).toBeInTheDocument();
    expect(screen.queryByTestId("am-run-reason")).toBeNull();
  });
});
