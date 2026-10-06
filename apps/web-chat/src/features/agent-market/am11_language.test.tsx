// AM11 — Agent Market follows the app's language.
//
// AM4 had the whole surface in Thai whatever the app's language; a person
// whose app is in English then read an English app with a Thai island in it.
// Now there is an English dictionary beside the Thai one and the surface
// reads in the app's language, like every other page:
//   * in English, not one Thai letter of the app's own is on the screen — in
//     the text, or in a placeholder, title, aria-label or alt;
//   * in Thai, the Thai words are there as they always were;
//   * switching the language re-labels what is mounted, without a reload.
// Three places are read each way: the market page, a workspace card (the
// files card of a finished run) and the agent's settings page.
//
// Everything that is not the app's own words — an agent's name, a file's
// name, a period's label, a server's sentence — is the person's or the
// package's and is shown as it arrives. The fixtures here keep all of it in
// Latin letters, so that any Thai letter found in English is the app's.
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
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

import { LocaleScope, useI18nStore, type Locale } from "@/lib/i18n";

import { agentShortName, formatDate, formatDateTime, formatDuration, periodLabel } from "./format";
import { amDictionaries, tAm } from "./i18n";
import { MarketPage } from "./market/MarketPage";
import { SettingsPage } from "./settings/SettingsPage";
import { checkTyped } from "./typed";
import type { OutputsPayload } from "./types";
import { ErrorPanel, Thai } from "./ui";
import { resetAgentMarketForTest } from "./useAgentMarket";
import { WorkspaceContext } from "./workspace/context";
import { OutputsCard } from "./workspace/OutputsCard";
import { splitNotice } from "./workspace/ThreadView";

const THAI = /[\u0E00-\u0E7F]/;
const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

/** Every word a person reads on the page: the text, and the four attributes a reader or a pointer shows. */
function wordsOnScreen(): string[] {
  const out: string[] = [document.body.textContent ?? ""];
  for (const el of Array.from(document.body.querySelectorAll("*"))) {
    for (const attr of ["placeholder", "title", "aria-label", "alt"]) {
      const v = el.getAttribute(attr);
      if (v) out.push(v);
    }
  }
  return out;
}
const thaiOnScreen = () => wordsOnScreen().filter((w) => THAI.test(w));

const say = (key: keyof typeof amDictionaries.th, locale: Locale, vars?: Record<string, string>) => {
  const raw = (amDictionaries[locale] as Record<string, string>)[key]!;
  return vars ? raw.replace(/\{(\w+)\}/g, (_, n: string) => vars[n] ?? `{${n}}`) : raw;
};

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  raw.mockImplementation(async (path: string) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true }, role: "member" });
    if (path === "/agent-market/catalog")
      return json({
        agents: [
          {
            slug: "sran.financial-report",
            version: "1.3.0",
            name: "Month-end financial report",
            status: "published",
            manifest: {
              summary: "Reads the general ledger and produces the month-end report.",
              icon: "chart",
              inputs: [{ role: "primary", label: "General ledger", required: true, accept: [".xlsx"] }],
              outputs: [{ format: "docx", label: "Report" }],
              permissions: { default: "read_only" },
            },
          },
        ],
      });
    if (path === "/agent-market/installs")
      return json({ installs: [{ id: "i-1", slug: "sran.financial-report", title: "Month-end close", created_at: "2026-09-01T03:00:00Z", last_run: { created_at: "2026-09-28T03:05:00Z" } }] });
    if (path === "/agent-market/shared") return json({ shared: [] });
    if (path === "/agent-market/installs/i-1")
      return json({
        install: { id: "i-1", slug: "sran.financial-report", title: "Month-end close", definition_version: "1.3.0", created_at: "2026-09-01T03:00:00Z" },
        manifest: { outputs: [{ format: "docx", label: "Report" }] },
        defaults: [{ key: "currency", value: "THB", source: "install", label: "Currency", note: "", updated_at: "2026-09-28T03:05:00Z" }],
        budget: { credits: 30000, spent_this_month: 1200 },
      });
    if (path === "/agent-market/installs/i-1/defaults") return json({ defaults: [], declared: [] });
    if (path === "/agent-market/theme") return json({ theme: null, version: 0, presets: [] });
    // whatever else a section asks for: the server's own sentence, in Latin letters
    return json({ error: { code: "NOT_FOUND", message: "No such thing here." } }, 404);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const outputs: OutputsPayload = {
  run_id: "r-1",
  period_label: "September 2026",
  credits: 1200,
  artifacts: [
    { id: "a1", name: "report_v1.docx", format: "docx", size: 2048, version: 1, quality: { checks: [{ name: "docx.figures", label: "Totals check", ok: true, values: 61 }] } },
    { id: "a2", name: "figures_v1.xlsx", format: "xlsx", size: 4096, version: 1 },
  ],
  model: { uses: "text", model: "qwen3.8-27b-fp8", self_hosted: true },
};

const filesCard = () =>
  render(
    <WorkspaceContext.Provider value={{ installId: "i-1", merge: () => {}, admin: true }}>
      <OutputsCard outputs={outputs} />
    </WorkspaceContext.Provider>,
  );

describe("AM11 — in English, not one Thai letter of the app's own", () => {
  beforeEach(() => useI18nStore.setState({ locale: "en", hydrated: true }));

  it("the market page", async () => {
    render(<MarketPage />);
    const grid = await screen.findByTestId("am-market-grid");
    const mine = await screen.findByTestId("am-my-agent");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Agent Market");
    expect(screen.getByRole("searchbox").getAttribute("placeholder")).toBe("Search, e.g. invoice, month-end close");
    expect(screen.getByRole("group", { name: "Categories" })).toBeTruthy();
    expect(within(grid).getByTestId("am-card-feed").textContent).toContain("You send General ledger");
    expect(within(grid).getByTestId("am-card-get").textContent).toContain("You get");
    // the agents that are planned are the app's own words too, each a card of its own
    expect(within(grid).getAllByTestId("am-soon-card").length).toBeGreaterThan(0);
    expect(grid.textContent).toContain("Coming soon");
    expect(mine.textContent).toContain("Last run 28 Sep 2026");
    expect(thaiOnScreen()).toEqual([]);
  });

  it("a workspace card: the files of a finished run", () => {
    filesCard();
    const card = screen.getByTestId("am-outputs");
    expect(card.textContent).toContain("Files from the September 2026 run");
    expect(screen.getByTestId("am-outputs-seen-by").textContent).toContain("the commentary was written by qwen3.8-27b-fp8 (in the organisation)");
    expect(screen.getByTestId("am-outputs-details-body").textContent).toContain("Files reopened and rechecked — figures agreeing with the calculation: 61");
    // AM11b: the files card says nothing of what the run cost
    expect(card.textContent).not.toMatch(/credit/i);
    expect(card.textContent).toContain("Source of figures");
    expect(thaiOnScreen()).toEqual([]);
  });

  it("the settings page, from its header to its last section", async () => {
    render(<SettingsPage installId="i-1" />);
    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Remembered answers and settings");
    expect(screen.getByRole("link", { name: "Back to workspace" })).toBeTruthy();
    expect(screen.getByTestId("am-settings-dashboard").textContent).toBe("Overview");
    // AM11b: the settings page says nothing of credits or a budget's figures
    expect(screen.queryByTestId("am-budget")).toBeNull();
    for (const heading of ["Remembered answers", "Set at installation", "Uninstall"]) {
      expect(screen.getByRole("heading", { level: 2, name: heading })).toBeTruthy();
    }
    await waitFor(() => expect(raw.mock.calls.some((c) => c[0] === "/agent-market/theme")).toBe(true));
    expect(thaiOnScreen()).toEqual([]);
  });
});

describe("AM11 — in Thai, the Thai words, as they always were", () => {
  beforeEach(() => useI18nStore.setState({ locale: "th", hydrated: true }));

  it("the market page", async () => {
    render(<MarketPage />);
    const grid = await screen.findByTestId("am-market-grid");
    expect(screen.getByRole("searchbox").getAttribute("placeholder")).toBe("ค้นหา เช่น ใบแจ้งหนี้ ปิดงบ");
    expect(screen.getByRole("group", { name: "หมวดหมู่" })).toBeTruthy();
    expect(within(grid).getByTestId("am-card-feed").textContent).toContain("ส่งให้ General ledger");
    expect(grid.textContent).toContain("เร็ว ๆ นี้");
    // the date follows the language too: the Buddhist era in Thai
    expect((await screen.findByTestId("am-my-agent")).textContent).toContain("2569");
  });

  it("a workspace card: the files of a finished run", () => {
    filesCard();
    const card = screen.getByTestId("am-outputs");
    expect(card.textContent).toContain("ไฟล์จากรอบ September 2026");
    expect(screen.getByTestId("am-outputs-seen-by").textContent).toContain("คำบรรยายเขียนโดย qwen3.8-27b-fp8 (ในองค์กร)");
    expect(screen.getByTestId("am-outputs-details-body").textContent).toContain("เปิดไฟล์ตรวจซ้ำ: ตัวเลข 61 ค่าตรงกับที่คำนวณ");
    expect(card.textContent).toContain("ที่มาของตัวเลข");
  });

  it("the settings page header", async () => {
    render(<SettingsPage installId="i-1" />);
    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("ค่าที่จำไว้และการตั้งค่า");
    expect(screen.getByRole("link", { name: "กลับห้องทำงาน" })).toBeTruthy();
    expect(screen.queryByTestId("am-budget")).toBeNull();
  });
});

describe("AM11 — switching the language re-labels what is mounted, without a reload", () => {
  beforeEach(() => useI18nStore.setState({ locale: "th", hydrated: true }));

  it("the files card: Thai, then English, then Thai again — the same card, never remounted", () => {
    filesCard();
    const card = screen.getByTestId("am-outputs");
    expect(card.textContent).toContain(say("outputs.title", "th", { period: "September 2026" }));
    expect(thaiOnScreen().length).toBeGreaterThan(0);

    act(() => useI18nStore.getState().setLocale("en"));
    expect(screen.getByTestId("am-outputs")).toBe(card);
    expect(card.textContent).toContain(say("outputs.title", "en", { period: "September 2026" }));
    expect(thaiOnScreen()).toEqual([]);

    act(() => useI18nStore.getState().setLocale("th"));
    expect(screen.getByTestId("am-outputs")).toBe(card);
    expect(card.textContent).toContain("ไฟล์จากรอบ September 2026");
  });

  it("the market page: its search box, its categories and its cards change where they stand", async () => {
    render(<MarketPage />);
    const grid = await screen.findByTestId("am-market-grid");
    const search = screen.getByRole("searchbox");
    expect(search.getAttribute("placeholder")).toBe("ค้นหา เช่น ใบแจ้งหนี้ ปิดงบ");
    const requests = raw.mock.calls.length;

    act(() => useI18nStore.getState().setLocale("en"));
    expect(screen.getByRole("searchbox")).toBe(search);
    expect(search.getAttribute("placeholder")).toBe("Search, e.g. invoice, month-end close");
    expect(screen.getByTestId("am-market-grid")).toBe(grid);
    expect(grid.textContent).toContain("Coming soon");
    expect(thaiOnScreen()).toEqual([]);
    // the words changed; nothing was fetched again to change them
    expect(raw.mock.calls.length).toBe(requests);
  });
});

describe("AM11 — the language is the app's, or the scope's a part of the screen stands in", () => {
  it("a LocaleScope still sets the language of what is inside it, Agent Market's words included", () => {
    useI18nStore.setState({ locale: "en", hydrated: true });
    render(
      <>
        <ErrorPanel message="outside" onRetry={() => {}} />
        <LocaleScope locale="th">
          <ErrorPanel message="inside" onRetry={() => {}} />
        </LocaleScope>
      </>,
    );
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Try again", "ลองใหม่"]);
  });

  it("the wrapper AM5 put around the chat's components sets no language any more", () => {
    useI18nStore.setState({ locale: "en", hydrated: true });
    render(
      <Thai>
        <ErrorPanel message="x" onRetry={() => {}} />
      </Thai>,
    );
    expect(screen.getByRole("button").textContent).toBe("Try again");
  });
});

describe("AM11 — numbers, dates and examples follow the language; what a person types is read as before", () => {
  const at = "2026-09-28T03:05:00Z";

  it("a date is Buddhist-era in Thai and 28 Sep 2026 in English (GLOSSARY.md §4)", () => {
    expect(formatDate(at, "en")).toBe("28 Sep 2026");
    expect(formatDate(at, "th")).toContain("2569");
    expect(formatDateTime(at, "en")).toMatch(/^28 Sep 2026, \d\d:\d\d$/);
    expect(formatDateTime(at, "th")).toContain("2569");
  });

  it("a duration takes its units from the dictionary of the language in use", () => {
    useI18nStore.setState({ locale: "en" });
    expect(formatDuration(72_000, tAm)).toBe("1 min 12 s");
    expect(formatDuration(3_720_000, tAm)).toBe("1 h 2 min");
    useI18nStore.setState({ locale: "th" });
    expect(formatDuration(72_000, tAm)).toBe("1 นาที 12 วินาที");
  });

  it("the example in a date hint is in the language's own form, and either form is accepted in either language", () => {
    for (const locale of ["th", "en"] as const) {
      useI18nStore.setState({ locale });
      const example = /\d\d\/\d\d\/\d{4}/.exec(tAm("typed.hint.date"))?.[0];
      expect(example).toBe(locale === "th" ? "15/09/2569" : "15/09/2026");
      // parsing is not the language's business: both years are the same day, in both languages
      expect(checkTyped("date", "15/09/2569")).toEqual({ ok: true, value: "15/09/2569" });
      expect(checkTyped("date", "15/09/2026")).toEqual({ ok: true, value: "15/09/2026" });
      expect(checkTyped("date", "31/02/2026")).toEqual({ ok: false, reason: "typed.err.date" });
      expect(checkTyped("money", "12,850.70 บาท")).toEqual({ ok: true, value: "12850.70" });
    }
  });
});

describe("AM11 — a sentence the server wrote in the run's language is handled as its Thai one is", () => {
  it("the notice that ends in a link loses its ending in either language, and nothing else", () => {
    expect(splitNotice("จำไว้เป็นค่าตั้งต้นรอบหน้า · แก้ได้")).toBe("จำไว้เป็นค่าตั้งต้นรอบหน้า");
    expect(splitNotice("Remembered as the default for the next run · can be changed")).toBe("Remembered as the default for the next run");
    expect(splitNotice("The period can be changed in the settings.")).toBe("The period can be changed in the settings.");
  });
});

describe("AM11 — an agent is called by the name its package gives for the screen's language", () => {
  const m = { name: "รายงานผลประกอบการ + สไลด์ผู้บริหาร", name_en: "Financial Report + Executive Deck", short_name: "รายงานผลประกอบการ" };

  it("Thai: its short name; English: its English name (a package has no short one in English)", () => {
    expect(agentShortName(m, "th", "ชื่อที่ถืออยู่")).toBe("รายงานผลประกอบการ");
    expect(agentShortName(m, "en", "held")).toBe("Financial Report + Executive Deck");
  });

  it("a package with no English name: the name the caller holds, then its own", () => {
    const thaiOnly = { name: m.name, short_name: m.short_name };
    expect(agentShortName(thaiOnly, "en", "My copy")).toBe("My copy");
    expect(agentShortName(thaiOnly, "en")).toBe(m.name);
    expect(agentShortName(null, "th", "ชื่อที่ถืออยู่")).toBe("ชื่อที่ถืออยู่");
  });
});

describe("AM11 — a run's period is said in the screen's language", () => {
  it("a month is formatting: กันยายน 2569 / September 2026, whatever the package called it in Thai", () => {
    expect(periodLabel("2026-09", "th")).toBe("กันยายน 2569");
    expect(periodLabel("2026-09", "en")).toBe("September 2026");
    expect(periodLabel("2026-09", "en", "กันยายน 2569")).toBe("September 2026");
    expect(periodLabel("2026-09", "th", "กันยายน 2569")).toBe("กันยายน 2569");
  });

  it("a package's own name for the period — a quarter, a year — is kept as it said it", () => {
    expect(periodLabel("2026-09", "en", "ไตรมาส 3/2569")).toBe("ไตรมาส 3/2569");
    expect(periodLabel("2026-Q3", "en", "ไตรมาส 3/2569")).toBe("ไตรมาส 3/2569");
    expect(periodLabel("2026-Q3", "en")).toBe("2026-Q3");
    expect(periodLabel(null, "th")).toBe("");
  });
});

describe("AM11 — a code is the engine's name for a thing, never what a person reads", () => {
  it("a step the page has no words for is still named in words, in both languages", async () => {
    const { stepLabel } = await import("./runs/RunDetailPage");
    for (const locale of ["th", "en"] as const) {
      useI18nStore.setState({ locale });
      for (const id of ["check:template.recalculate", "check:pdf", "some_new_step"]) {
        const label = stepLabel(id, tAm);
        expect(label).not.toContain(id);
        expect(label).not.toMatch(/[a-z]+[_:.][a-z]/);
        expect(/[ก-๙]/.test(label)).toBe(locale === "th");
      }
    }
    useI18nStore.setState({ locale: "th" });
    expect(stepLabel("render", tAm)).toBe(tAm("step.render"));
  });
});
