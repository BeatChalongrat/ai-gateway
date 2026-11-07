// AM10 F — the Agent Market page reads in the language the admin chose.
//
// An agent's manifest carries its name twice (`name`, Thai; `name_en`). The
// page showed the Thai one whatever the language was: in the catalogue, and
// in every list that names an agent by a slug and a name (egress, runs,
// spend). And every refusal was shown as chat-edge wrote it — Thai, in an
// English screen. A refusal carries a code; the codes whose sentence is the
// same every time now read from the dictionary, and anything else is still
// shown as received: the server's sentence is not ours to translate.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({
  api: {
    org: vi.fn(),
    catalog: vi.fn(),
    putCatalog: vi.fn(),
    remakeSample: vi.fn(),
    egress: vi.fn(),
    putEgress: vi.fn(),
    runs: vi.fn(),
    openRun: vi.fn(),
    run: vi.fn(),
    removeEvidence: vi.fn(),
    spend: vi.fn(),
    mail: vi.fn(),
    putMail: vi.fn(),
    testMail: vi.fn(),
    artifactHref: (runId: string, id: string) => `/api/chat-edge/agent-market/runs/${runId}/artifacts/${id}/download`,
    fileHref: (runId: string, id: string) => `/api/chat-edge/agent-market/runs/${runId}/files/${id}/download`,
  },
}));
vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: api };
});
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/agent-market",
}));

import { amErrorMessage, amKnownErrorCodes } from "../../lib/agentMarketApi";
import { ChatEdgeApiError } from "../../lib/chatEdgeApi";
import { I18nProvider, type DictKey } from "../../lib/i18n";
import { dictionaries, modules } from "../../lib/i18n/dict";
import { ToastProvider } from "../../components/ui/Toast";

import AgentEgressCard from "./_sections/AgentEgress";
import CatalogSection from "./_sections/Catalog";
import MailSection from "./_sections/Mail";
import RunsSection from "./_sections/Runs";
import SpendSection from "./_sections/Spend";
import AdminRunPage from "./runs/[id]/page";

type Locale = "en" | "th";

function renderIn(locale: Locale, el: React.ReactNode) {
  window.localStorage.setItem("sran-admin-locale", locale);
  return render(
    <I18nProvider>
      <ToastProvider>{el}</ToastProvider>
    </I18nProvider>,
  );
}

const tOf =
  (locale: Locale) =>
  (key: DictKey, vars?: Record<string, string | number>): string =>
    dictionaries[locale][key].replace(/\{(\w+)\}/g, (whole, name: string) => (vars?.[name] !== undefined ? String(vars[name]) : whole));

const refusal = (code: string, message: string, status = 400) => new ChatEdgeApiError(status, { error: { code, message } });

const TH = "รายงานผลประกอบการ";
const EN = "Financial Report + Executive Deck";

const CATALOG = {
  admin: true,
  agents: [
    { slug: "sran.financial-report", version: "1.2.0", name: TH, manifest: { name_en: EN }, status: "published", installs: 4, sample: null },
    { slug: "sran.old-agent", version: "0.9.0", name: "เอเจนต์รุ่นเก่า", manifest: {}, status: "unpublished", installs: 0, sample: null },
  ],
};

const RUN = {
  id: "run-1",
  agent: TH,
  slug: "sran.financial-report",
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

const EGRESS = { slug: "sran.financial-report", name: TH, org: true, agent: false, agent_default: true, accounting: true, effective: false, words: "" };

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  api.catalog.mockResolvedValue(CATALOG);
  api.org.mockResolvedValue({ org: {}, members: [], departments: [], pricing: { credits_per_usd: 3_000_000, thb_per_usd: 35 } });
});
afterEach(cleanup);

// ── a. an agent's name ───────────────────────────────────────────────────

describe("an agent is named in the language of the page", () => {
  it("catalogue: name_en in English, name in Thai, name when the manifest has no name_en", async () => {
    renderIn("en", <CatalogSection canDisable />);
    const rows = await screen.findAllByTestId("am-catalog-row");
    await waitFor(() => expect(rows[0].textContent).toContain(EN));
    expect(rows[0].textContent).not.toContain(TH);
    expect(rows[1].textContent).toContain("เอเจนต์รุ่นเก่า");
    cleanup();

    renderIn("th", <CatalogSection canDisable />);
    const thRows = await screen.findAllByTestId("am-catalog-row");
    await waitFor(() => expect(thRows[0].textContent).toContain(TH));
    expect(thRows[0].textContent).not.toContain(EN);
  });

  it("egress: a row carries only a name and a slug — the catalogue supplies name_en, and the saved line uses it", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [EGRESS] });
    api.putEgress.mockResolvedValue({ slug: EGRESS.slug, egress: { ...EGRESS, agent: true, effective: true } });
    renderIn("en", <AgentEgressCard orgVersion="a" />);
    const row = await screen.findByTestId(`am-egress-${EGRESS.slug}`);
    await waitFor(() => expect(row.textContent).toContain(EN));
    expect(row.textContent).not.toContain(TH);
    fireEvent.click(screen.getByTestId(`am-egress-toggle-${EGRESS.slug}`));
    await waitFor(() => expect(screen.getByTestId("am-note").textContent).toContain(EN));
    cleanup();

    renderIn("th", <AgentEgressCard orgVersion="b" />);
    const thRow = await screen.findByTestId(`am-egress-${EGRESS.slug}`);
    await waitFor(() => expect(thRow.textContent).toContain(TH));
    expect(thRow.textContent).not.toContain(EN);
  });

  it("runs: the same lookup", async () => {
    api.runs.mockResolvedValue({ runs: [RUN], grant_minutes: 60 });
    renderIn("en", <RunsSection />);
    const row = await screen.findByTestId("am-run");
    await waitFor(() => expect(row.textContent).toContain(EN));
    expect(row.textContent).not.toContain(TH);
    cleanup();

    renderIn("th", <RunsSection />);
    const thRow = await screen.findByTestId("am-run");
    await waitFor(() => expect(thRow.textContent).toContain(TH));
    expect(thRow.textContent).not.toContain(EN);
  });

  it("spend by agent: the same lookup (the row's key is the slug)", async () => {
    api.spend.mockResolvedValue({
      days: 30,
      since: "",
      by_user: [],
      by_agent: [{ key: "sran.financial-report", name: TH, runs: 2, succeeded: 2, failed: 0, waiting: 0, users: 1, credits: 10, month_spent: 10, last_run_at: null }],
      total_credits: 10,
      catalog_samples_credits: 0,
    });
    renderIn("en", <SpendSection />);
    const table = await screen.findByTestId("am-spend-agent");
    await waitFor(() => expect(table.textContent).toContain(EN));
    expect(table.textContent).not.toContain(TH);
    cleanup();

    renderIn("th", <SpendSection />);
    const thTable = await screen.findByTestId("am-spend-agent");
    await waitFor(() => expect(thTable.textContent).toContain(TH));
  });

  it("a list without the catalogue still names its agents (the name it was given)", async () => {
    api.catalog.mockRejectedValue(new ChatEdgeApiError(403, { error: { code: "NOT_PUBLISHER", message: "no" } }));
    api.runs.mockResolvedValue({ runs: [RUN], grant_minutes: 60 });
    renderIn("en", <RunsSection />);
    expect((await screen.findByTestId("am-run")).textContent).toContain(TH);
  });
});

// ── b. a refusal ─────────────────────────────────────────────────────────

describe("a refusal reads in the chosen language when its code is known", () => {
  it("a known code: the dictionary's sentence, English or Thai — not the server's Thai in an English screen", () => {
    const err = refusal("AUDIT_UNAVAILABLE", "ตอนนี้บันทึกประวัติการเปลี่ยนแปลงของผู้ดูแลไม่ได้", 503);
    const en = amErrorMessage(err, "fallback", tOf("en"));
    const th = amErrorMessage(err, "fallback", tOf("th"));
    expect(en).toBe(dictionaries.en["am.err.AUDIT_UNAVAILABLE"]);
    expect(th).toBe(dictionaries.th["am.err.AUDIT_UNAVAILABLE"]);
    expect(en).not.toMatch(/[฀-๿]/);
    expect(th).toMatch(/[฀-๿]/);
  });

  it("a request the page should never send says its code in both languages", () => {
    const en = amErrorMessage(refusal("BAD_JSON", "คำขอนี้ไม่ถูกต้อง (รหัส “BAD_JSON”)"), "fallback", tOf("en"));
    expect(en).toContain("BAD_JSON");
    expect(en).not.toMatch(/[฀-๿]/);
  });

  it("an unknown code shows the server's sentence as received, in either language", () => {
    const err = refusal("SOMETHING_NEW", "ยังเผยแพร่ไม่ได้ — ไม่มีอะไรเปลี่ยน", 409);
    expect(amErrorMessage(err, "fallback", tOf("en"))).toBe("ยังเผยแพร่ไม่ได้ — ไม่มีอะไรเปลี่ยน");
    expect(amErrorMessage(err, "fallback", tOf("th"))).toBe("ยังเผยแพร่ไม่ได้ — ไม่มีอะไรเปลี่ยน");
  });

  it("a code whose sentence carries the server's own data stays the server's sentence", () => {
    // which thing was not found, which value was wrong, who to ask: only the server knows
    for (const code of ["NOT_FOUND", "BAD_VALUE", "NOT_ADMIN", "NOT_PUBLISHER", "RUNNER_DOWN", "CAPABILITY_MISSING", "MAIL_RECIPIENT"]) {
      expect(amKnownErrorCodes(), code).not.toContain(code);
      expect(amErrorMessage(refusal(code, "ประโยคของเซิร์ฟเวอร์"), "fallback", tOf("en"))).toBe("ประโยคของเซิร์ฟเวอร์");
    }
    // and two whose sentence chat-edge wrote for the round's owner, not for this page
    for (const code of ["ROUND_IN_PROGRESS", "MAIL_FAILED"]) {
      expect(amKnownErrorCodes(), code).not.toContain(code);
    }
  });

  it("no body, or not a chat-edge error: the caller's fallback", () => {
    expect(amErrorMessage(new ChatEdgeApiError(502, "<html>bad gateway</html>"), "fallback", tOf("en"))).toBe("fallback");
    expect(amErrorMessage(new Error("boom"), "fallback", tOf("en"))).toBe("fallback");
  });

  it("every mapped code has a sentence of its own in both languages", () => {
    const codes = amKnownErrorCodes();
    expect(codes.length).toBeGreaterThan(10);
    for (const code of codes) {
      const en = amErrorMessage(refusal(code, "เซิร์ฟเวอร์"), "fallback", tOf("en"));
      const th = amErrorMessage(refusal(code, "เซิร์ฟเวอร์"), "fallback", tOf("th"));
      expect(en, code).not.toBe("เซิร์ฟเวอร์");
      expect(en, code).not.toMatch(/[฀-๿]/);
      expect(th, code).toMatch(/[฀-๿]/);
      expect(en, code).not.toMatch(/\{\w+\}/);
      expect(th, code).not.toMatch(/\{\w+\}/);
    }
  });

  it("on the page: a refused catalogue action reads in English, then in Thai; an unknown code as received", async () => {
    api.putCatalog.mockRejectedValue(refusal("AUDIT_UNAVAILABLE", "ตอนนี้บันทึกประวัติการเปลี่ยนแปลงของผู้ดูแลไม่ได้", 503));
    renderIn("en", <CatalogSection canDisable />);
    fireEvent.click((await screen.findAllByTestId("am-unpublish"))[0]);
    await waitFor(() => expect(screen.getByTestId("am-note").textContent).toBe(dictionaries.en["am.err.AUDIT_UNAVAILABLE"]));
    cleanup();

    renderIn("th", <CatalogSection canDisable />);
    fireEvent.click((await screen.findAllByTestId("am-unpublish"))[0]);
    await waitFor(() => expect(screen.getByTestId("am-note").textContent).toBe(dictionaries.th["am.err.AUDIT_UNAVAILABLE"]));
    cleanup();

    api.putCatalog.mockRejectedValue(refusal("X", "ยังเผยแพร่ไม่ได้ — ไม่มีอะไรเปลี่ยน", 409));
    renderIn("en", <CatalogSection canDisable />);
    fireEvent.click((await screen.findAllByTestId("am-unpublish"))[0]);
    await waitFor(() => expect(screen.getByTestId("am-note").textContent).toBe("ยังเผยแพร่ไม่ได้ — ไม่มีอะไรเปลี่ยน"));
  });

  it("on the page: a load that failed with a known code reads in English", async () => {
    api.catalog.mockRejectedValue(refusal("AGENT_MARKET_BUSY", "ระบบ agent มีงานค้างมากในขณะนี้", 503));
    renderIn("en", <CatalogSection canDisable />);
    expect((await screen.findByRole("alert")).textContent).toContain(dictionaries.en["am.err.AGENT_MARKET_BUSY"]);
  });
});

// ── c. what the server formats for a Thai reader ─────────────────────────

describe("dates the server wrote out for a Thai reader", () => {
  it("runs: the round's period is a month — said in English in English, as received in Thai", async () => {
    api.runs.mockResolvedValue({ runs: [RUN, { ...RUN, id: "run-2", period_label: "ไตรมาส 3/2569" }], grant_minutes: 60 });
    renderIn("en", <RunsSection />);
    const rows = await screen.findAllByTestId("am-run");
    await waitFor(() => expect(rows[0].textContent).toContain("September 2026"));
    expect(rows[0].textContent).not.toContain("กันยายน");
    // a label that is not a month of a year is the round's own words
    expect(rows[1].textContent).toContain("ไตรมาส 3/2569");
    cleanup();

    renderIn("th", <RunsSection />);
    const thRows = await screen.findAllByTestId("am-run");
    await waitFor(() => expect(thRows[0].textContent).toContain("กันยายน 2569"));
  });

  it("an opened run: the access log's time comes from the timestamp in English, the server's label in Thai", async () => {
    const DETAIL = {
      run: { id: "run-1", status: "succeeded", outcome: "completed", period: "2026-09", credits_spent: 1, model_used: "qwen", model_self_hosted: true, created_at: "2026-09-20T02:00:00Z" },
      steps: [],
      artifacts: [],
      files: [],
      install: { id: "i1", title: "รายงานการเงิน", slug: "sran.financial-report" },
      access_log: [{ admin_name: "Boss", admin_email: "boss@example.invalid", reason: "ตรวจยอดที่ไม่ตรง", opened_at: "2026-09-20T02:10:00Z", at_label: "20 ก.ย. 2569 09:10 น." }],
    };
    api.run.mockResolvedValue(DETAIL);
    renderIn("en", <AdminRunPage params={{ id: "run-1" }} />);
    const log = await screen.findByTestId("am-run-access-log");
    await waitFor(() => expect(log.textContent).toMatch(/20 Sept? 2026/));
    expect(log.textContent).not.toContain("ก.ย.");
    // the reason is what the admin typed: it stays
    expect(log.textContent).toContain("ตรวจยอดที่ไม่ตรง");
    cleanup();

    renderIn("th", <AdminRunPage params={{ id: "run-1" }} />);
    const thLog = await screen.findByTestId("am-run-access-log");
    await waitFor(() => expect(thLog.textContent).toContain("20 ก.ย. 2569 09:10 น."));
  });

  it("an opened run that needs a reason: the request reads in English", async () => {
    api.run.mockRejectedValue(refusal("ACCESS_REASON_NEEDED", "ต้องกด “เปิดดูพร้อมเหตุผล” ก่อน", 403));
    renderIn("en", <AdminRunPage params={{ id: "run-1" }} />);
    const card = await screen.findByTestId("am-run-reason");
    expect(card.textContent).toContain(dictionaries.en["am.err.ACCESS_REASON_NEEDED"]);
    expect(card.textContent).not.toContain("ต้องกด");
  });

  it("mail: the reason the relay is not ready carries no code — it is shown as received", async () => {
    api.mail.mockResolvedValue({
      settings: { host: "", port: 587, tls_mode: "starttls", username: "", sender: "" },
      password_set: false,
      password_env: "AGENT_MARKET_SMTP_PASSWORD",
      ready: false,
      not_ready: "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล — กรอกเซิร์ฟเวอร์ พอร์ต และอีเมลผู้ส่ง แล้วกดบันทึก",
    });
    renderIn("en", <MailSection defaultTo="boss@example.invalid" />);
    const band = await screen.findByTestId("am-mail-not-ready");
    expect(band.textContent).toContain(dictionaries.en["am.mail.notReadyTitle"]);
    expect(band.textContent).toContain("ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล");
    // …while a refused test message does carry one
    api.testMail.mockRejectedValue(refusal("MAIL_NOT_CONFIGURED", "ยังไม่ได้ตั้งค่าเซิร์ฟเวอร์อีเมล", 409));
    fireEvent.click(screen.getByTestId("am-mail-test"));
    await waitFor(() => expect(screen.getByTestId("am-mail-test-note").textContent).toBe(dictionaries.en["am.err.MAIL_NOT_CONFIGURED"]));
  });
});

// The Accounts page's words (AM9) were composed into the dictionary but left
// out of its per-module checks.
describe("the accounts module is inside the dictionary's per-module checks", () => {
  it("is listed with the rest, inside its own prefix, the same keys in both languages", () => {
    expect(Object.keys(modules)).toContain("accounts");
    expect(Object.keys(modules.accounts.en).filter((k) => !k.startsWith("acct."))).toEqual([]);
    expect(Object.keys(modules.accounts.th).sort()).toEqual(Object.keys(modules.accounts.en).sort());
  });
});
