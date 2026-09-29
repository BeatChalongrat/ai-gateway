// AM4 — make it simple:
//   * the whole surface is Thai, whatever the app's language;
//   * the workspace header has one obvious action ("ที่มาของตัวเลข") and
//     everything else in one menu; an empty workspace says in one loud
//     sentence what to drop in;
//   * the round-boundary chip asks instead of guessing;
//   * the files card always says which model saw the documents, and folds
//     checks, credits and model names for members (open for admins);
//   * the market is one grid of equal cards, "coming soon" visibly lighter;
//   * installing can end in one click.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw, push } = vi.hoisted(() => ({ raw: vi.fn(), push: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/chat/agents/i/i-1",
}));

import { useI18nStore } from "@/lib/i18n";

import { tAm } from "./i18n";
import { QuickInstall } from "./install/QuickInstall";
import { MarketPage } from "./market/MarketPage";
import { NumbersTab, scriptLabel } from "./runs/RunDetailPage";
import { describeValue } from "./settings/SettingsPage";
import type { Manifest, OutputsPayload, ThreadItem } from "./types";
import { resetAgentMarketForTest } from "./useAgentMarket";
import { WorkspaceContext, type WorkspaceCtx } from "./workspace/context";
import { dropSentence } from "./workspace/EmptyDrop";
import { OutputsCard } from "./workspace/OutputsCard";
import { IntentChip } from "./workspace/RoundParts";
import { WorkspacePage } from "./workspace/WorkspacePage";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
let admin = false;
const calls = (path: string) => raw.mock.calls.filter((c) => c[0] === path);

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  push.mockReset();
  admin = false;
  handler = () => json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  raw.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true }, admin, publisher: admin });
    if (path.includes("/events")) return new Promise(() => {});
    return handler(path, init);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  // Modal reads matchMedia for its mobile sheet; jsdom has none.
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  // AM4: the owner's app is in English — the agents must still read Thai
  useI18nStore.setState({ locale: "en" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const manifest: Manifest = {
  id: "sran.financial-report",
  name: "รายงานการเงิน",
  greeting: { text: "สวัสดีครับ", role: "ผู้ช่วยปิดงบการเงิน", tagline: "อ่านอย่างเดียว" },
  inputs: [
    { role: "primary", label: "บัญชีแยกประเภทของเดือนที่จะปิด", note: "จำเป็น (หรืองบทดลอง / งบกำไรขาดทุน)", required: true, accept: [".xlsx"] },
    { role: "budget", label: "งบประมาณของงวดนั้น", required: false, accept: [".xlsx"] },
  ],
  outputs: [{ format: "docx", label: "รายงาน", default: true }],
} as Manifest;

const ENGLISH_CHROME = /\b(My agents|Pin|Unpin|Report a problem|Overview|Settings|See every step|More files|Quality checks)\b/;

function workspaceHandler(items: ThreadItem[]): Handler {
  return (path) => {
    if (path === "/agent-market/installs") return json({ installs: [{ id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน", created_at: "" }] });
    if (path === "/agent-market/installs/i-1") return json({ install: { id: "i-1", slug: "sran.financial-report", title: "ปิดงบการเงิน" }, manifest, defaults: [], budget: { credits: 0, spent_this_month: 0 } });
    if (path.startsWith("/agent-market/installs/i-1/thread")) return json({ items, cursor: 99 });
    if (path === "/agent-market/installs/i-1/models") return json({ uses: "text", choice: "auto", auto_model: "", models: [], blocked_reason: "", hidden_reason: "", reads: "", note: "" });
    if (path === "/agent-market/installs/i-1/storage")
      return json({ storage: { uploads: 0, outputs: 0, total: 0, warn_bytes: 1, warn: false }, retention_years: 5, retention: "", not_archive: "", personal_delete: "" });
    if (path === "/agent-market/installs/i-1/budget") return json({ budget: { tight: null }, remaining: 1 });
    if (path === "/agent-market/installs/i-1/opened") return json({ ok: true });
    return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  };
}

const at = (s: number) => new Date(Date.UTC(2026, 8, 28, 3, 0, s)).toISOString();
const doneRound: ThreadItem[] = [
  { id: "g", install_id: "i-1", kind: "greeting", payload: { text: "สวัสดีครับ" }, rev: 1, created_at: at(0) },
  { id: "p", install_id: "i-1", kind: "progress", run_id: "r-1", payload: { run_id: "r-1", kind: "run", status: "succeeded" }, rev: 2, created_at: at(1) },
  {
    id: "o",
    install_id: "i-1",
    kind: "outputs",
    run_id: "r-1",
    payload: { run_id: "r-1", period_label: "กันยายน 2569", artifacts: [{ id: "a1", name: "report_v1.docx", format: "docx", size: 10, version: 1 }], model: { uses: "text", model: "qwen3.8-27b-fp8", self_hosted: true } },
    rev: 3,
    created_at: at(2),
  },
];

describe("the workspace, simple and Thai", () => {
  it("one obvious action in the header, the rest in one menu — Thai under an English app", async () => {
    handler = workspaceHandler(doneRound);
    render(<WorkspacePage installId="i-1" />);
    const action = await screen.findByTestId("am-ws-view-run");
    expect(action.textContent).toBe("ที่มาของตัวเลข");
    expect(action.getAttribute("href")).toBe("/chat/agents/runs/r-1?tab=numbers");
    // pin, overview, settings, report a problem: not on the header until the menu opens
    expect(screen.queryByTestId("am-ws-settings")).toBeNull();
    expect(screen.queryByTestId("am-ws-dashboard")).toBeNull();
    fireEvent.click(screen.getByTestId("am-ws-more"));
    const menu = screen.getByTestId("am-ws-menu");
    for (const label of ["แชทใหม่กับ agent นี้", "ปักหมุดไว้บนสุดของแถบด้านซ้าย", "ภาพรวมการทำงาน", "ตั้งค่า agent", "แจ้งปัญหา"]) {
      expect(within(menu).getByText(label)).toBeTruthy();
    }
    // the finished round closes with its divider and the next round one click away
    expect(screen.getByTestId("am-round-divider").textContent).toContain("รอบ กันยายน 2569 · เสร็จ");
    expect(screen.getByTestId("am-round-start").textContent).toContain("เริ่มรอบใหม่");
    expect(document.body.textContent ?? "").not.toMatch(ENGLISH_CHROME);
  });

  it("an empty workspace says in one sentence what to drop in", async () => {
    handler = workspaceHandler([doneRound[0]!]);
    render(<WorkspacePage installId="i-1" />);
    const sentence = await screen.findByTestId("am-empty-sentence");
    expect(sentence.textContent).toBe("ส่งบัญชีแยกประเภทของเดือนที่จะปิดมาได้เลย");
    expect(sentence.tagName).toBe("H2");
    expect(screen.queryByTestId("am-ws-view-run")).toBeNull();
    expect(dropSentence({ inputs: [] } as unknown as Manifest)).toBeNull();
  });
});

describe("the round-boundary chip", () => {
  const ctx: WorkspaceCtx = { installId: "i-1", merge: () => {} };
  const chip = (payload: Record<string, unknown>): ThreadItem => ({ id: "chip-1", install_id: "i-1", kind: "intent", payload, rev: 5, created_at: at(9) });

  it("asks — start a new round, or ask about the last one — and sends the choice", async () => {
    handler = (path) => (path === "/agent-market/intents/chip-1" ? json({ changed: true, choice: "new_round", run_id: "r-2" }) : json({}, 404));
    render(
      <WorkspaceContext.Provider value={ctx}>
        <IntentChip item={chip({ status: "open", files: [{ id: "f", name: "GL_10.xlsx", size: 1 }], last_round: { period_label: "กันยายน 2569" } })} />
      </WorkspaceContext.Provider>,
    );
    expect(screen.getByText("เริ่มรอบใหม่?")).toBeTruthy();
    expect(screen.getByTestId("am-intent").textContent).toContain("กันยายน 2569");
    fireEvent.click(screen.getByTestId("am-intent-new"));
    await waitFor(() => expect(calls("/agent-market/intents/chip-1")).toHaveLength(1));
    expect(JSON.parse(String((calls("/agent-market/intents/chip-1")[0]![1] as RequestInit).body))).toEqual({ choice: "new_round" });
  });

  it("someone about to delete the chat is told they need not", () => {
    render(
      <WorkspaceContext.Provider value={ctx}>
        <IntentChip item={chip({ status: "open", text: "ลบแชทแล้วเริ่มใหม่หมด", fresh: true })} />
      </WorkspaceContext.Provider>,
    );
    expect(screen.getByTestId("am-intent").textContent).toContain("ไม่ต้องลบแชทเพื่อเริ่มใหม่");
  });
});

describe("the files card", () => {
  const outputs: OutputsPayload = {
    run_id: "r-1",
    period_label: "กันยายน 2569",
    credits: 1200,
    artifacts: [{ id: "a1", name: "register_v1.xlsx", format: "xlsx", size: 10, version: 1, quality: { checks: [{ name: "x", label: "ตรวจยอดรวม", ok: true }] } }],
    summary: { models: [{ model: "qwen3.8-27b-fp8", self_hosted: true, documents: 2 }] },
    model: { uses: "vision", choice: "gpt-vision", chosen_model: "gpt-vision", not_chosen: true, vision_model: "qwen3.8-27b-fp8" },
  };

  it("always says which model saw the documents — and when another was used instead of the chosen one", () => {
    render(
      <WorkspaceContext.Provider value={{ installId: "i-1", merge: () => {} }}>
        <OutputsCard outputs={outputs} />
      </WorkspaceContext.Provider>,
    );
    const seen = screen.getByTestId("am-outputs-seen-by").textContent ?? "";
    expect(seen).toContain("qwen3.8-27b-fp8 (ในองค์กร)");
    expect(seen).toContain("คุณเลือก gpt-vision ไว้ แต่รอบนี้ใช้ qwen3.8-27b-fp8 แทน");
    // a member: checks and credits folded
    expect(screen.queryByTestId("am-outputs-details-body")).toBeNull();
  });

  it("an admin sees the details open", () => {
    render(
      <WorkspaceContext.Provider value={{ installId: "i-1", merge: () => {}, admin: true }}>
        <OutputsCard outputs={outputs} />
      </WorkspaceContext.Provider>,
    );
    expect(screen.getByTestId("am-outputs-details-body").textContent).toContain("ตรวจคุณภาพผ่าน 1 จาก 1");
  });

  it("digital documents: says no model saw them", () => {
    render(<OutputsCard outputs={{ run_id: "r-2", artifacts: [], model: { uses: "vision" } }} />);
    expect(screen.getByTestId("am-outputs-seen-by").textContent).toContain("ไม่มีโมเดลใดเห็นเอกสาร");
  });
});

describe("the market, one grid of equal cards", () => {
  it("every card has the same parts in the same places; coming soon is lighter", async () => {
    handler = (path) => {
      if (path === "/agent-market/catalog")
        return json({
          agents: [
            { slug: "sran.financial-report", version: "1.3.0", name: "ปิดงบการเงินรายเดือน", manifest: { summary: "ยาว ".repeat(80), icon: "report", outputs: [{ format: "docx" }], permissions: { default: "read_only" } } },
            { slug: "sran.invoice-processing", version: "1.2.0", name: "ทะเบียนซื้อ", manifest: { summary: "สั้น", icon: "invoice" } },
          ],
        });
      if (path === "/agent-market/installs") return json({ installs: [] });
      if (path === "/agent-market/shared") return json({ shared: [] });
      return json({}, 404);
    };
    render(<MarketPage />);
    const grid = await screen.findByTestId("am-market-grid");
    expect(grid.className).toContain("auto-rows-fr");
    const cards = [...within(grid).getAllByTestId("am-agent-card"), ...within(grid).getAllByTestId("am-soon-card")];
    expect(cards.length).toBeGreaterThan(2);
    for (const c of cards) {
      expect(within(c).getByTestId("am-card-tags")).toBeTruthy();
      expect(within(c).getByTestId("am-card-meta")).toBeTruthy();
      expect(within(c).getByTestId("am-card-actions")).toBeTruthy();
      expect(c.className).toContain("h-full");
      // tags, price/time and actions are one block pinned to the bottom: the same height on every card of a row
      // (the AM4 screen tour saw the tag rows 25 px apart when one title wrapped)
      const bottom = within(c).getByTestId("am-card-bottom");
      expect(bottom.className).toContain("mt-auto");
      for (const id of ["am-card-tags", "am-card-meta", "am-card-actions"]) expect(bottom.contains(within(c).getByTestId(id))).toBe(true);
    }
    for (const s of within(grid).getAllByTestId("am-soon-card")) {
      expect(s.className).toContain("opacity-60");
      expect(s.className).toContain("border-dashed");
    }
    for (const a of within(grid).getAllByTestId("am-agent-card")) {
      expect(a.className).not.toContain("opacity-60");
      expect(within(a).getByTestId("am-card-desc").className).toContain("line-clamp-3");
    }
  });
});

describe("installing in one click", () => {
  it("ใช้ค่าแนะนำทั้งหมด installs with the defaults, runs the sample self-test and opens the workspace", async () => {
    handler = (path) => (path === "/agent-market/installs" ? json({ install: { id: "i-9", slug: "sran.invoice-processing" }, created: true }) : json({}, 404));
    render(<QuickInstall slug="sran.invoice-processing" />);
    fireEvent.click(screen.getByTestId("am-detail-install"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/chat/agents/i/i-9"));
    const body = JSON.parse(String((calls("/agent-market/installs")[0]![1] as RequestInit).body));
    expect(body.self_test).toBe(true);
    expect(body.slug).toBe("sran.invoice-processing");
    expect(screen.getByTestId("am-detail-install-custom").getAttribute("href")).toBe("/chat/agents/install/sran.invoice-processing");
  });
});

// Found by the AM4 screen tour (results/ui/latin_words.json): two places still
// showed an English word to a person — the account group on "ที่มาของตัวเลข"
// (revenue, operating_expenses …) and the document theme in settings (navy).
describe("no English left where a person reads", () => {
  it("the accounts table names each group in Thai (the line it adds up to)", () => {
    render(
      <NumbersTab
        c={{
          lines: [
            { key: "revenue", label: "รายได้จากการขายและบริการ", current: "100" },
            { key: "operating_expenses", label: "ค่าใช้จ่ายในการขายและบริหาร", current: "40" },
          ],
          accounts: [
            { code: "4100-01", name: "รายได้จากการขายสินค้า", group: "revenue", current: "100", rows: 3 },
            { code: "5200-01", name: "เงินเดือน", group: "operating_expenses", current: "40", rows: 1 },
          ],
        } as never}
      />,
    );
    const table = screen.getByTestId("am-run-numbers");
    expect(table.textContent).toContain("ค่าใช้จ่ายในการขายและบริหาร");
    expect(table.textContent).not.toMatch(/revenue|operating_expenses/);
  });

  it("the document theme is named in Thai", () => {
    const d = { key: "theme", value: { id: "navy" }, source: "install", label: "ธีมเอกสาร" } as never;
    expect(describeValue(d, tAm)).toEqual(["กรมท่า"]);
    expect(describeValue({ key: "theme", value: { id: "teal" } } as never, tAm)).toEqual(["เขียวหยก"]);
  });
});

// The live walkthrough in the owner's (English) desktop app: the run page's
// step list said "fallback_narrative" for the narrative step of an agent whose
// words come from rules. A script's file name reads as a name; that did not.
describe("the run page's steps", () => {
  it("say how the words were written, in Thai", () => {
    expect(scriptLabel("fallback_narrative", tAm)).toBe(tAm("step.script.rules"));
    expect(tAm("step.script.rules")).toMatch(/[฀-๿]/);
    expect(scriptLabel("metrics.py@1.1.0", tAm)).toBe("metrics.py@1.1.0");
  });
});
