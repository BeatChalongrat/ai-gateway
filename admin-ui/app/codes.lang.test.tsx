// A stored code is never what a person reads. A walk of the Thai screen found
// twelve printed as they are stored on the admin app's own pages: an alert's
// severity and what became of it (critical, warning, info · delivered,
// grouped, log_only) and a DLP detector's category (credential, financial,
// identity, network, pii, secret). Each is a word now, in the page's
// language; the code stays what the page filters by.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock("../lib/api", async () => {
  const real = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...real, api };
});
// next/font is a build-time loader; under vitest it is only a class name
vi.mock("next/font/google", () => ({ Outfit: () => ({ className: "outfit", style: {} }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

import { I18nProvider } from "../lib/i18n";
import { dictionaries } from "../lib/i18n/dict";
import { ALERT_DELIVERY, ALERT_SEVERITY, DETECTION_TYPE, DLP_CATEGORY, profileName } from "../lib/codeWords";
import AlertEventsPage from "./alerts/events/page";
import DlpCoveragePage from "./dlp-coverage/page";

const inLanguage = (locale: "en" | "th", el: React.ReactNode) => {
  localStorage.setItem("sran-admin-locale", locale);
  return render(<I18nProvider>{el}</I18nProvider>);
};

// The stored code as the walk read it on the page: in lower case, a word of
// its own — not part of another word ("Information" holds "nfo", not "info"),
// and not the English word for it, which begins with a capital ("Financial").
const asAWord = (code: string) => new RegExp(`(^|[^A-Za-z_])${code}([^A-Za-z_]|$)`);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});
afterEach(cleanup);

const events = {
  items: [
    { id: "e1", rule_id: null, alert_type: "chat_failing", severity: "critical", message: "m1", delivery_status: "delivered", fired_at: null, tenant_id: null, project_id: null },
    { id: "e2", rule_id: null, alert_type: "disk_low", severity: "warning", message: "m2", delivery_status: "grouped", fired_at: null, tenant_id: null, project_id: null },
    { id: "e3", rule_id: null, alert_type: "backup_done", severity: "info", message: "m3", delivery_status: "log_only", fired_at: null, tenant_id: null, project_id: null },
  ],
};

const detector = (name: string, category: string) => ({
  name, category, severity: "HIGH", mask_token: "[X]", enabled: true, method: "regex", coverage_note: "", example_patterns: [], gaps: [],
});
const CATEGORIES = ["credential", "financial", "identity", "network", "pii", "secret"];
const coverage = {
  summary: {
    total: CATEGORIES.length,
    by_category: Object.fromEntries(CATEGORIES.map((c) => [c, { count: 1, detectors: [`det_${c}x`] }])),
  },
  detectors: CATEGORIES.map((c) => detector(`det_${c}x`, c)),
  known_gaps: [{ category: "pii", description: "d", reason: "r", priority: "high" }],
};

describe("the alert events page", () => {
  it.each([
    ["th", ["วิกฤต", "เตือน", "แจ้งให้ทราบ", "ส่งแล้ว", "ไม่ส่งซ้ำ (เหตุการณ์เดิม)", "บันทึกไว้ ไม่ได้ส่ง"]],
    ["en", ["Critical", "Warning", "Information", "Sent", "Not sent again (same incident)", "Recorded, not sent"]],
  ] as const)("says each severity and each delivery state in words (%s)", async (locale, words) => {
    api.get.mockResolvedValue(events);
    const { container } = inLanguage(locale, <AlertEventsPage />);
    await screen.findAllByText(words[0]);
    const table = screen.getByRole("table");
    for (const w of words) expect(within(table).getAllByText(w).length).toBeGreaterThan(0);
    const shown = container.textContent ?? "";
    for (const code of ["critical", "warning", "info", "delivered", "grouped", "log_only"]) {
      expect(shown, `the stored code "${code}" is on the page`).not.toMatch(asAWord(code));
    }
  });

  it("filters by the code while showing the word", async () => {
    api.get.mockResolvedValue(events);
    inLanguage("th", <AlertEventsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "วิกฤต" }));
    await waitFor(() => expect(screen.queryByText("m2")).toBeNull());
    expect(screen.getByText("m1")).toBeTruthy();
  });

  it("the detail of an event says the same words", async () => {
    api.get.mockResolvedValue(events);
    inLanguage("th", <AlertEventsPage />);
    fireEvent.click(await screen.findByText("m3"));
    // the dialog is drawn into <body>, after the page
    const title = await screen.findByText(dictionaries.th["obs.alerts.detailTitle"]);
    const dialog = title.parentElement!.parentElement!;
    expect(within(dialog).getByText("แจ้งให้ทราบ")).toBeTruthy();
    expect(within(dialog).getByText("บันทึกไว้ ไม่ได้ส่ง")).toBeTruthy();
    expect(dialog.textContent).not.toMatch(asAWord("log_only"));
  });
});

describe("the DLP coverage page", () => {
  it.each([
    ["th", ["ข้อมูลเข้าระบบ", "การเงิน", "เลขประจำตัว", "ที่อยู่เครือข่าย", "ข้อมูลส่วนบุคคล", "รหัสลับ"]],
    ["en", ["Credentials", "Financial", "Identity numbers", "Network addresses", "Personal data", "Secrets"]],
  ] as const)("names each category in words (%s)", async (locale, words) => {
    api.get.mockResolvedValue(coverage);
    const { container } = inLanguage(locale, <DlpCoveragePage />);
    await screen.findAllByText(words[0]);
    for (const w of words) expect(screen.getAllByText(w).length).toBeGreaterThan(0);
    // the detectors' own names are identifiers and stay; the category beside them is a word
    const shown = (container.textContent ?? "").replace(/det_[a-z]+x/g, "");
    for (const code of CATEGORIES) expect(shown, `the stored code "${code}" is on the page`).not.toMatch(asAWord(code));
  });

  it("a search finds a category by its word as well as by its code", async () => {
    api.get.mockResolvedValue(coverage);
    inLanguage("th", <DlpCoveragePage />);
    await screen.findAllByText("การเงิน");
    const box = screen.getByPlaceholderText(dictionaries.th["infra.coverage.searchPlaceholder"]) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "การเงิน" } });
    await waitFor(() => expect(screen.queryByText("det_piix")).toBeNull());
    expect(screen.getByText("det_financialx")).toBeTruthy();
    fireEvent.change(box, { target: { value: "network" } });
    await waitFor(() => expect(screen.getByText("det_networkx")).toBeTruthy());
    expect(screen.queryByText("det_financialx")).toBeNull();
  });
});

describe("every code the gateway can store has a word in both languages", () => {
  // backend/app/services/alert_channels.py (severity), alert_service.py and
  // alert_pager.py (what became of an alert), dlp/detectors/*.py (category)
  const stored = {
    severity: ["critical", "warning", "info", "low"],
    delivery: ["pending", "delivered", "failed", "log_only", "grouped", "hold", "maintenance"],
    category: ["identity", "financial", "credential", "secret", "pii", "network", "content_policy"],
    // backend/app/models/custom_detector.py
    detection: ["regex", "keyword_list", "semantic"],
  };
  it.each([
    ["severity", ALERT_SEVERITY],
    ["delivery", ALERT_DELIVERY],
    ["category", DLP_CATEGORY],
    ["detection", DETECTION_TYPE],
  ] as const)("%s", (kind, words) => {
    for (const code of stored[kind]) {
      const key = words[code];
      expect(key, `${kind} "${code}" has no word`).toBeTruthy();
      for (const locale of ["en", "th"] as const) {
        const word = dictionaries[locale][key];
        expect(word, `${locale}.${key}`).toBeTruthy();
        expect(word, `${locale}.${key} is the stored code itself`).not.toBe(code);
      }
    }
  });
});

// The same walk read English terms inside Thai sentences. These were translations that had been
// missed (what stays in English on the Thai screen is a name: a tenant, a project, a provider, a
// model, an example host, a setting's name).
describe("what the Thai screen still said in English and should not have", () => {
  const th = dictionaries.th as Record<string, string>;
  const thai = Object.entries(th);

  it.each([
    ["API Key", "the page it names is คีย์ API"],
    ["audit log", "the sidebar calls that page ประวัติการทำงานของผู้ดูแล"],
    ["manual override", ""],
    ["discovery sync", ""],
    ["provider:model", "a format to type, said in the reader's words"],
    ["production", ""],
  ])("no Thai entry says %s", (term) => {
    const holding = thai.filter(([, v]) => v.includes(term)).map(([k]) => k);
    expect(holding, `Thai entries that still say "${term}"`).toEqual([]);
  });

  it("the sidebar and the page call the API keys page by one name", () => {
    expect(th["nav.apiKeys"]).toBe(th["tenancy.apiKeys.title"]);
  });

  it("a built-in policy profile is named in the page's language; a renamed one keeps its name", () => {
    const t = (key: string) => th[key];
    expect(profileName({ profile_id: "default", name: "Default" }, t as never)).toBe("ค่าตั้งต้น");
    expect(profileName({ profile_id: "strict", name: "Strict" }, t as never)).toBe("เข้มงวด");
    expect(profileName({ profile_id: "strict", name: "Finance — strict" }, t as never)).toBe("Finance — strict");
    expect(profileName({ profile_id: "team-a", name: "Team A" }, t as never)).toBe("Team A");
  });
});
