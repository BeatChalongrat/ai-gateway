// AM3 — the catalog a stranger reads, before installing:
//   * the agent's page says who it is for, how it works, which files it needs
//     (with the REAL sample files to download) and what it produces (with the
//     real outputs it made), roughly what a run costs in baht and how long it
//     takes, and — next to the install button — which models see the
//     documents and whether each runs inside the organisation;
//   * a paused agent says why and offers no install;
//   * the data sheet lists reads, models, storage and how long each kind of
//     file is kept.
import { cleanup, render, screen, within } from "@testing-library/react";
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
  usePathname: () => "/chat/agents/sran.invoice-processing",
}));

import { useI18nStore } from "@/lib/i18n";

import { AsksPanel } from "./install/panels";
import { AgentDetailPage } from "./market/AgentDetailPage";
import { DatasheetPage } from "./market/DatasheetPage";
import { costLine } from "./market/trust";
import { railOrder, searchAgents } from "./rail";
import type { Datasheet, Manifest, RailInstall } from "./types";
import { resetAgentMarketForTest } from "./useAgentMarket";
import { tAm } from "./i18n";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });

// The invoice agent as the AM3 package describes itself (manifest guide block).
const invoice: Manifest = {
  id: "sran.invoice-processing",
  icon: "receipt",
  summary: "ส่งใบกำกับภาษี แล้วได้ทะเบียนเอกสารซื้อ",
  inputs: [{ role: "documents", label: "ใบกำกับภาษี ใบเสร็จ", required: true, accept: [".pdf", ".xml"] }],
  outputs: [{ format: "xlsx", label: "ทะเบียน .xlsx", default: true }],
  permissions: { default: "read_only", denied: ["email_send", "payment"] },
  cannot: "ไม่บันทึกเข้าระบบบัญชี",
  typical_run_seconds: 90,
  guide: {
    for: "ฝ่ายบัญชีเจ้าหนี้ที่บันทึกใบกำกับภาษีซื้อทุกเดือน",
    how: ["ส่งไฟล์ PDF หลายไฟล์พร้อมกัน", "ตอบคำถามถ้า agent ไม่มั่นใจ", "ดาวน์โหลดทะเบียน"],
    inputs: { documents: { good: ["PDF ที่ออกจากระบบของผู้ขาย"], avoid: ["รูปถ่ายเอียงหรือมืด"], sample: ["sample-data/ใบกำกับภาษี_IV6909-0015.pdf"] } },
    outputs: { xlsx: "ทะเบียน 6 แผ่น ทุกค่าบอกหน้าและตำแหน่งที่อ่าน" },
    questions: "จะถามเมื่อค่าที่อ่านได้ไม่มั่นใจ",
    wrong_file: "ถ้าไม่ใช่ใบกำกับภาษี agent จะบอกและไม่บันทึกไฟล์นั้น",
    never: ["ไม่ส่งอีเมลแทนคุณ"],
    time_note: "สแกนใช้เวลาราว 1 นาทีต่อหน้า",
  },
};

const sheet: Datasheet = {
  slug: "sran.invoice-processing",
  version: "1.1.0",
  name: "อ่านใบกำกับภาษี",
  summary: "ส่งใบกำกับภาษี",
  reads: [{ role: "documents", label: "ใบกำกับภาษี ใบเสร็จ", required: true, accept: [".pdf"] }],
  limits: { max_file_mb: 20, max_files: 30 },
  produces: [{ format: "xlsx", label: "ทะเบียน .xlsx" }],
  models: [
    { capability: "model.vision", sees: "ภาพหน้าเอกสารที่เป็นสแกนหรือรูปถ่ายเท่านั้น", model: "qwen3.8-27b-fp8", role: "vision", self_hosted: true },
    { capability: "model.text", sees: "ตารางที่คำนวณแล้ว", model: "deepseek-v4-flash", role: "fallback", self_hosted: false },
  ],
  no_model: ["คำบรรยายและสรุปในไฟล์เขียนด้วยกฎของ agent ไม่มีโมเดลภาษาใดเห็นข้อมูลของคุณ"],
  storage: { backend: "local", prefix: "agent-market/" },
  retention: [
    { what: "ไฟล์ที่อัปโหลดแต่ไม่ได้ส่งให้ agent", keep: 48, unit: "hours" },
    { what: "ไฟล์ต้นฉบับ ผลลัพธ์ และประวัติของรอบที่เสร็จ", keep: 0, unit: "while_installed" },
    { what: "สรุปเอกสารสำหรับตรวจเอกสารซ้ำ", keep: 730, unit: "days" },
  ],
  isolation: ["ทุกขั้นตอนทำงานในกล่องแยก (sandbox) ที่ไม่มีเครือข่าย"],
  permissions: { default: "read_only", denied: ["email_send"] },
  never: ["ไม่ส่งอีเมลแทนคุณ"],
  cannot: "ไม่บันทึกเข้าระบบบัญชี",
  status: "published",
  runner: { healthy: true, version: "aa11048" },
  generated_at: "2026-09-28T03:00:00Z",
};

let status = "published";

function route(path: string) {
  if (path === "/agent-market/availability")
    return json({ available: true, runner: { healthy: true }, role: "member", pricing: { credits_per_usd: 3000000, thb_per_usd: 35 } });
  if (path === "/agent-market/catalog/sran.invoice-processing")
    return json({
      slug: "sran.invoice-processing",
      version: "1.1.0",
      name: "อ่านใบกำกับภาษี",
      manifest: invoice,
      status,
      status_reason: status === "disabled" ? "กำลังแก้ยอด VAT" : "",
      sample: { run_id: "s-1", version: "1.1.0", status: "succeeded", credits: 2400, seconds: 42 },
      stats: { runs: 5, users: 2, median_credits: 3000, median_seconds: 60, per_document: 900, documents: 12 },
    });
  if (path === "/agent-market/catalog/sran.invoice-processing/sample")
    return json({
      sample: { run_id: "s-1", version: "1.1.0", status: "succeeded", credits: 2400, seconds: 42 },
      artifacts: [{ id: "a-1", format: "xlsx", name: "invoice_2026-09_tables_v1.xlsx", version: 1, size: 20480, created_at: "" }],
      files: [
        { id: "f-1", name: "ใบกำกับภาษี_IV6909-0015.pdf", size: 10240 },
        { id: "f-2", name: "eTax_ใบเสร็จ.xml", size: 2048 },
      ],
    });
  if (path === "/agent-market/catalog/sran.invoice-processing/datasheet") return json(sheet);
  if (path === "/agent-market/installs") return json({ installs: [] });
  return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
}

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  raw.mockImplementation(async (path: string) => route(path));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ enabled: true }) }));
  useI18nStore.setState({ locale: "th" });
  status = "published";
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the agent's page, for a stranger", () => {
  it("says who, how, what files (with real samples), what comes back, cost — and who sees the documents next to install", async () => {
    render(<AgentDetailPage slug="sran.invoice-processing" />);
    expect(await screen.findByText("ฝ่ายบัญชีเจ้าหนี้ที่บันทึกใบกำกับภาษีซื้อทุกเดือน")).toBeTruthy();
    expect(within(screen.getByTestId("am-guide-how")).getAllByRole("listitem")).toHaveLength(3);
    const inputs = screen.getByTestId("am-guide-inputs");
    expect(inputs.textContent).toContain("PDF ที่ออกจากระบบของผู้ขาย");
    expect(inputs.textContent).toContain("รูปถ่ายเอียงหรือมืด");
    // the sample file its guide names sits under that input; the other sample below
    expect(await within(inputs).findByText("ใบกำกับภาษี_IV6909-0015.pdf")).toBeTruthy();
    expect(within(screen.getByTestId("am-sample-inputs")).getByText("eTax_ใบเสร็จ.xml")).toBeTruthy();
    expect(within(screen.getByTestId("am-sample-outputs")).getByText("invoice_2026-09_tables_v1.xlsx")).toBeTruthy();
    expect(screen.getByTestId("am-guide-outputs").textContent).toContain("ทะเบียน 6 แผ่น");
    expect(screen.getByTestId("am-guide-never").textContent).toContain("ไม่ส่งอีเมลแทนคุณ");
    expect(screen.getByText("ถ้าไม่ใช่ใบกำกับภาษี agent จะบอกและไม่บันทึกไฟล์นั้น")).toBeTruthy();

    const before = screen.getByTestId("am-before-install");
    const models = await within(before).findAllByTestId("am-model");
    expect(models).toHaveLength(2);
    expect(models[0]!.textContent).toContain("qwen3.8-27b-fp8");
    expect(within(models[0]!).getByTestId("am-model-where").textContent).toBe("ทำงานในองค์กร");
    expect(within(models[1]!).getByTestId("am-model-where").textContent).toBe("ผู้ให้บริการภายนอก");
    const cost = within(before).getByTestId("am-cost").textContent ?? "";
    expect(cost).toContain("2,400 เครดิต"); // the real sample run
    expect(cost).toContain("≈ 0.03 บาท"); // 2400 / 3,000,000 × 35
    expect(cost).toContain("42 วินาที");
    expect(cost).toContain("5 รอบ"); // the organisation's own median (≥ 3 runs)
    expect(cost).toContain("1 นาทีต่อหน้า");
    // the install button is inside the same box, after the models
    // AM4: one click with the recommended settings, the step-by-step wizard one link away
    expect(within(before).getByTestId("am-detail-install").textContent).toContain("ใช้ค่าแนะนำทั้งหมด");
    expect(within(before).getByTestId("am-detail-install-custom").getAttribute("href")).toBe("/chat/agents/install/sran.invoice-processing");
    expect(within(before).getByTestId("am-datasheet-link").getAttribute("href")).toBe("/chat/agents/sran.invoice-processing/datasheet");
  });

  it("a paused agent says why and offers no install", async () => {
    status = "disabled";
    render(<AgentDetailPage slug="sran.invoice-processing" />);
    expect((await screen.findByTestId("am-detail-disabled")).textContent).toContain("กำลังแก้ยอด VAT");
    expect(screen.queryByTestId("am-detail-install")).toBeNull();
  });
});

describe("what the agent asks for", () => {
  it("names the files this agent makes, not a report and deck it never makes", () => {
    render(<AsksPanel manifest={invoice} />);
    expect(document.body.textContent).toContain("ทะเบียน .xlsx");
    expect(document.body.textContent).not.toContain("สไลด์");
  });
});

describe("the data sheet", () => {
  it("reads, models with where they run, storage and retention", async () => {
    render(<DatasheetPage slug="sran.invoice-processing" />);
    const ds = await screen.findByTestId("am-datasheet");
    expect(ds.textContent).toContain("ไฟล์ละไม่เกิน 20 MB · ครั้งละไม่เกิน 30 ไฟล์");
    expect(within(ds).getAllByTestId("am-model-where").map((b) => b.textContent)).toEqual(["ทำงานในองค์กร", "ผู้ให้บริการภายนอก"]);
    expect(ds.textContent).toContain("เซิร์ฟเวอร์ขององค์กร");
    const ret = within(ds).getByTestId("am-ds-retention");
    expect(ret.textContent).toContain("48 ชั่วโมง");
    expect(ret.textContent).toContain("ตลอดที่ยังติดตั้ง agent");
    expect(ret.textContent).toContain("730 วัน");
    expect(ds.textContent).toContain("ส่วนประมวลผลทำงานอยู่ (aa11048)");
  });
});

describe("rail order and search (pure)", () => {
  const r = (id: string, extra: Partial<RailInstall> = {}): RailInstall => ({
    id,
    slug: id,
    title: id,
    icon: "",
    version: "1",
    pinned_at: null,
    last_used_at: null,
    waiting: 0,
    disabled: false,
    ...extra,
  });
  it("never alphabetical: recency decides, pins come first in the order they were pinned", () => {
    const rows = [r("aaa", { last_used_at: "2026-01-01T00:00:00Z" }), r("zzz", { last_used_at: "2026-09-01T00:00:00Z" }), r("mmm", { pinned_at: "2026-02-01T00:00:00Z" })];
    expect(railOrder(rows).shown.map((x) => x.id)).toEqual(["mmm", "zzz", "aaa"]);
  });
  it("search finds by title and slug; catalog agents already installed are not offered twice", () => {
    const hits = searchAgents("zz", [r("zzz")], [
      { slug: "zzz", version: "1", name: "Z", manifest: {} },
      { slug: "zz-2", version: "1", name: "ZZ two", manifest: {} },
    ]);
    expect(hits.installed.map((x) => x.id)).toEqual(["zzz"]);
    expect(hits.catalog.map((x) => x.slug)).toEqual(["zz-2"]);
    expect(searchAgents("  ", [r("zzz")], []).installed).toEqual([]);
  });
  it("a card's one-line cost", () => {
    useI18nStore.setState({ locale: "th" });
    expect(costLine(tAm, { run_id: "x", version: "1", status: "succeeded", credits: 3_000_000, seconds: 90 }, null)).toBe("ตัวอย่างจริง ≈ 35.00 บาท · 1 นาที 30 วินาที");
    expect(costLine(tAm, { run_id: "x", version: "1", status: "failed", credits: 10, seconds: 1 }, null)).toBeNull();
  });
});
