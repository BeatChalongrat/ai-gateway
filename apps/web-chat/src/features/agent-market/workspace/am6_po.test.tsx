// AM6 part A — the invoice ↔ PO agent on the screen.
//
//   * How deep every line was tested is never hidden: the files card of a
//     round says, above the files, how many lines were checked against a
//     receipt (three ways) and how many against the order only (two ways) —
//     and, when any were two ways, that those lines were not checked against
//     a receipt, in the warning colour. Then the three counts with their
//     value, the reasons, and that no invoice was approved by the agent.
//   * A receipt confirmed in this system is offered, never required: a
//     folded section lists the order lines billed without one, and a person
//     who knows the goods came may say so — all of it, or how much.
//   * The catalog and the agent's pages say whom the agent is made for and
//     whose law and tax it follows.
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

import { AgentDetailPage } from "../market/AgentDetailPage";
import { DatasheetPage } from "../market/DatasheetPage";
import { MarketPage } from "../market/MarketPage";
import type { Manifest, OutputsPayload, ReceiptOffer } from "../types";
import { resetAgentMarketForTest } from "../useAgentMarket";
import { WorkspaceContext, type WorkspaceCtx } from "./context";
import { OutputsCard } from "./OutputsCard";

const RUN = "3b9f3c1e-8a52-4f6e-9d1b-0c2f7e5a4b61";
const TWO_WAY_NOTE = "บรรทัดที่ตรวจ 2 ทางเทียบปริมาณกับที่สั่งซื้อเท่านั้น ไม่ได้ตรวจกับใบรับของ";
const NO_APPROVAL = "ไม่มีใบแจ้งหนี้ใดถูกอนุมัติจ่ายโดย agent การอนุมัติและการจ่ายเป็นของคน";
const RECEIPTS = "ยืนยันการรับของ (ไม่บังคับ)";

const summary = {
  kind: "po_matching",
  period_label: "กันยายน 2569",
  invoices: 12,
  lines: 40,
  passed: 7,
  blocked: 3,
  person: 2,
  three_way: 22,
  two_way: 15,
  unlinked: 3,
  value_passed: "184500.00",
  value_blocked: "56200.50",
  value_person: "12000.00",
  by_reason: [
    { code: "price_over_po", label: "ราคาต่อหน่วยสูงกว่า PO เกินเกณฑ์", outcome: "blocked", invoices: 2, lines: 3, value: "40200.50" },
    { code: "possible_duplicate", label: "อาจซ้ำกับใบแจ้งหนี้ที่ส่งมาก่อน", outcome: "person", invoices: 1, lines: 1, value: "12000.00" },
  ],
  receipt_offers: 2,
  not_included: [],
};
const outputs = (s: Record<string, unknown> = {}): OutputsPayload => ({
  run_id: RUN,
  period_label: "กันยายน 2569",
  artifacts: [{ id: "a1", name: "invoice-po-matching_2569-09_tables_v1.xlsx", format: "xlsx", size: 40960, version: 1 }],
  summary: { ...summary, ...s },
  model: { uses: "none" },
});
const offers: ReceiptOffer[] = [
  { akey: "PO6909001#l:10", key: "pomatch_receipt:PO6909001#l:10", po: "PO6909001", line_no: 10, item_code: "A-100", description: "กระดาษ A4 80 แกรม", ordered: "50", unit: "รีม", billed: "50", file: "ใบสั่งซื้อ_2569-09.xlsx", sheet: "PO", row: 5 },
  { akey: "PO6909004#l:20", key: "pomatch_receipt:PO6909004#l:20", po: "PO6909004", line_no: 20, item_code: "T-220", description: "หมึกพิมพ์ดำ", ordered: "12", unit: "กล่อง", billed: "12", file: "ใบสั่งซื้อ_2569-09.xlsx", sheet: "PO", row: 9 },
];

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
const callsTo = (path: string, method = "GET") =>
  raw.mock.calls.filter((c) => c[0] === path && ((c[1] as RequestInit | undefined)?.method ?? "GET") === method);
const bodyOf = (c: unknown[]) => JSON.parse(String((c[1] as RequestInit).body));

const ws: WorkspaceCtx = { installId: "i-1", merge: () => {}, threadId: null };
const inWorkspace = (node: React.ReactNode) => render(<WorkspaceContext.Provider value={ws}>{node}</WorkspaceContext.Provider>);

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  handler = (path, init) => {
    if (path === `/agent-market/runs/${RUN}/computed`) return json({ period_label: "กันยายน 2569", receipt_offers: offers });
    if (path === `/agent-market/runs/${RUN}/receipts` && init?.method === "POST") return json({ run: { id: RUN, status: "running", outcome: "running" } }, 202);
    return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  };
  raw.mockImplementation(async (path: string, init?: RequestInit) =>
    path === "/agent-market/availability"
      ? json({ available: true, runner: { healthy: true }, role: "member", pricing: { credits_per_usd: 3000000, thb_per_usd: 35 } })
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

describe("AM6 — the depth of the match is never hidden", () => {
  it("says how every line was tested, and warns when a line was tested two ways", () => {
    inWorkspace(<OutputsCard outputs={outputs()} />);
    const depth = screen.getByTestId("am-po-depth");
    expect(depth.textContent).toBe("ตรวจ 3 ทาง 22 บรรทัด · ตรวจ 2 ทาง 15 บรรทัด · ยังไม่ได้ตรวจกับ PO 3 บรรทัด");
    const warning = screen.getByTestId("am-po-two-way");
    expect(warning.textContent).toBe(TWO_WAY_NOTE);
    expect(warning.className).toContain("text-status-warning");
    // directly under the depth line
    expect(depth.nextElementSibling).toBe(warning);
  });

  it("says it above the files", () => {
    inWorkspace(<OutputsCard outputs={outputs()} />);
    const depth = screen.getByTestId("am-po-depth");
    const file = screen.getByTestId("am-artifact");
    expect(depth.compareDocumentPosition(file) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("am-po-counts").compareDocumentPosition(file) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a round with every line tested three ways says so, and gives no warning", () => {
    inWorkspace(<OutputsCard outputs={outputs({ three_way: 40, two_way: 0, unlinked: 0 })} />);
    expect(screen.getByTestId("am-po-depth").textContent).toBe("ตรวจ 3 ทาง 40 บรรทัด · ตรวจ 2 ทาง 0 บรรทัด");
    expect(screen.queryByTestId("am-po-two-way")).toBeNull();
  });

  it("the three counts with their value in baht", () => {
    inWorkspace(<OutputsCard outputs={outputs()} />);
    const counts = within(screen.getByTestId("am-po-counts")).getAllByTestId("am-po-count");
    expect(counts.map((c) => c.getAttribute("data-status"))).toEqual(["passed", "blocked", "person"]);
    expect(counts[0]!.textContent).toContain("ผ่าน · พร้อมเสนออนุมัติ");
    expect(counts[0]!.textContent).toContain("7 ฉบับ");
    expect(counts[0]!.textContent).toContain("184,500.00 บาท");
    expect(counts[1]!.textContent).toContain("ติดเงื่อนไข");
    expect(counts[1]!.textContent).toContain("3 ฉบับ");
    expect(counts[1]!.textContent).toContain("56,200.50 บาท");
    expect(counts[2]!.textContent).toContain("ต้องให้คนตัดสิน");
    expect(counts[2]!.textContent).toContain("2 ฉบับ");
    expect(counts[2]!.textContent).toContain("12,000.00 บาท");
    // the reconciliation's and the report's numbers are not this agent's
    expect(screen.queryByTestId("am-outputs-kpis")).toBeNull();
  });

  it("the reasons, then that the approving and the paying are a person's", () => {
    inWorkspace(<OutputsCard outputs={outputs()} />);
    const reasons = within(screen.getByTestId("am-po-reasons")).getAllByTestId("am-po-reason");
    expect(reasons).toHaveLength(2);
    expect(reasons[0]!.textContent).toContain("ราคาต่อหน่วยสูงกว่า PO เกินเกณฑ์");
    expect(reasons[0]!.textContent).toContain("2 ฉบับ · 40,200.50 บาท");
    expect(reasons[1]!.textContent).toContain("อาจซ้ำกับใบแจ้งหนี้ที่ส่งมาก่อน");
    expect(document.body.textContent).not.toContain("price_over_po");
    expect(screen.getByTestId("am-po-no-approval").textContent).toBe(NO_APPROVAL);
  });

  it("another agent's files card has none of it", () => {
    inWorkspace(<OutputsCard outputs={{ run_id: "r", artifacts: [], summary: { revenue: "100.00", total_expenses: "40.00", net_profit: "60.00" }, model: { uses: "text" } }} />);
    expect(screen.queryByTestId("am-po-depth")).toBeNull();
    expect(screen.queryByTestId("am-po-no-approval")).toBeNull();
    expect(screen.queryByTestId("am-receipts")).toBeNull();
  });
});

describe("AM6 — a receipt confirmed here is offered, never required", () => {
  it("is a folded section; nothing is read until it is opened", async () => {
    inWorkspace(<OutputsCard outputs={outputs()} />);
    const section = screen.getByTestId("am-receipts");
    const toggle = within(section).getByRole("button", { name: RECEIPTS });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(callsTo(`/agent-market/runs/${RUN}/computed`)).toHaveLength(0);
    // it is not a question: nothing asks, nothing waits for it
    expect(screen.queryByTestId("am-question-card")).toBeNull();
    expect(section.getAttribute("role")).not.toBe("alert");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const rows = await within(section).findAllByTestId("am-receipt-row");
    expect(callsTo(`/agent-market/runs/${RUN}/computed`)).toHaveLength(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain("PO PO6909001 · บรรทัด 10");
    expect(rows[0]!.textContent).toContain("กระดาษ A4 80 แกรม");
    expect(rows[0]!.textContent).toContain("สั่งซื้อ 50 รีม");
    expect(rows[1]!.textContent).toContain("สั่งซื้อ 12 กล่อง");
  });

  it("“ของมาครบ” confirms all of it, and the round computes again", async () => {
    inWorkspace(<OutputsCard outputs={outputs()} />);
    fireEvent.click(within(screen.getByTestId("am-receipts")).getByRole("button", { name: RECEIPTS }));
    const rows = await screen.findAllByTestId("am-receipt-row");
    fireEvent.click(within(rows[0]!).getByRole("button", { name: "ของมาครบ" }));
    await waitFor(() => expect(callsTo(`/agent-market/runs/${RUN}/receipts`, "POST")).toHaveLength(1));
    expect(bodyOf(callsTo(`/agent-market/runs/${RUN}/receipts`, "POST")[0]!)).toEqual({ key: "pomatch_receipt:PO6909001#l:10", quantity: "all" });
    const done = await screen.findByTestId("am-receipt-done");
    expect(done.textContent).toContain("กำลังคำนวณใหม่ด้วยใบรับของนี้");
  });

  it("“มาบางส่วน” asks how much, and sends the number typed", async () => {
    inWorkspace(<OutputsCard outputs={outputs()} />);
    fireEvent.click(within(screen.getByTestId("am-receipts")).getByRole("button", { name: RECEIPTS }));
    const row = (await screen.findAllByTestId("am-receipt-row"))[1]!;
    fireEvent.click(within(row).getByRole("button", { name: "มาบางส่วน" }));
    const qty = within(row).getByLabelText("จำนวนที่ได้รับ") as HTMLInputElement;
    fireEvent.change(qty, { target: { value: "0" } });
    fireEvent.click(within(row).getByRole("button", { name: "ยืนยันจำนวนนี้" }));
    expect(within(row).getByRole("alert").textContent).toContain("ใส่จำนวนที่ได้รับเป็นตัวเลขมากกว่า 0");
    expect(callsTo(`/agent-market/runs/${RUN}/receipts`, "POST")).toHaveLength(0);
    fireEvent.change(qty, { target: { value: "8" } });
    fireEvent.click(within(row).getByRole("button", { name: "ยืนยันจำนวนนี้" }));
    await waitFor(() => expect(callsTo(`/agent-market/runs/${RUN}/receipts`, "POST")).toHaveLength(1));
    expect(bodyOf(callsTo(`/agent-market/runs/${RUN}/receipts`, "POST")[0]!)).toEqual({ key: "pomatch_receipt:PO6909004#l:20", quantity: "8" });
    expect(await screen.findByTestId("am-receipt-done")).toBeTruthy();
  });

  it("a refusal is said at the line, in the server's words", async () => {
    const base = handler;
    handler = (path, init) =>
      path === `/agent-market/runs/${RUN}/receipts`
        ? json({ error: { code: "NOT_OFFERED", message: "บรรทัดนี้ไม่ได้อยู่ในรายการที่รอยืนยันการรับของของรอบนี้" } }, 409)
        : base(path, init);
    inWorkspace(<OutputsCard outputs={outputs()} />);
    fireEvent.click(within(screen.getByTestId("am-receipts")).getByRole("button", { name: RECEIPTS }));
    const row = (await screen.findAllByTestId("am-receipt-row"))[0]!;
    fireEvent.click(within(row).getByRole("button", { name: "ของมาครบ" }));
    expect((await within(row).findByRole("alert")).textContent).toContain("บรรทัดนี้ไม่ได้อยู่ในรายการที่รอยืนยันการรับของของรอบนี้");
    expect(screen.queryByTestId("am-receipt-done")).toBeNull();
  });

  it("no line waiting for a receipt: no section", () => {
    inWorkspace(<OutputsCard outputs={outputs({ receipt_offers: 0 })} />);
    expect(screen.queryByTestId("am-receipts")).toBeNull();
    expect(screen.getByTestId("am-po-depth")).toBeTruthy();
  });
});

describe("AM6 — whom an agent is made for", () => {
  const po: Manifest = {
    id: "sran.invoice-po-matching",
    name: "ตรวจใบกำกับภาษีกับใบสั่งซื้อ",
    icon: "receipt",
    category: "finance",
    summary: "ส่งใบสั่งซื้อ ใบรับของ และใบแจ้งหนี้มา แล้วได้ผลตรวจเป็น Excel",
    inputs: [{ role: "po", label: "ใบสั่งซื้อ (PO)", required: true, accept: [".xlsx"] }],
    outputs: [{ format: "xlsx", label: "ผลตรวจ .xlsx", default: true }],
    permissions: { default: "read_only" },
    scope: "generic",
    jurisdiction: "TH",
  };
  const item = { slug: "sran.invoice-po-matching", version: "1.0.0", name: "ตรวจใบกำกับภาษีกับใบสั่งซื้อ", manifest: po, status: "published", scope: "generic", jurisdiction: "TH" };

  it("the catalog card", async () => {
    handler = (path) => {
      if (path === "/agent-market/catalog") return json({ agents: [item, { ...item, slug: "acme.close", name: "ปิดงบของบริษัทเรา", manifest: { ...po, scope: "org-specific", jurisdiction: undefined }, scope: "org-specific", jurisdiction: undefined }] });
      if (path === "/agent-market/installs") return json({ installs: [] });
      if (path === "/agent-market/shared") return json({ shared: [] });
      return json({}, 404);
    };
    render(<MarketPage />);
    const cards = await screen.findAllByTestId("am-agent-card");
    const scope = (c: HTMLElement) => within(c).getByTestId("am-scope").textContent;
    expect(scope(cards[0]!)).toContain("ใช้ได้กับทุกบริษัท");
    expect(scope(cards[0]!)).toContain("กฎหมายและภาษีของไทย");
    expect(scope(cards[1]!)).toContain("เฉพาะองค์กรนี้");
    expect(scope(cards[1]!)).not.toContain("กฎหมายและภาษีของไทย");
  });

  it("the agent's page and its data sheet", async () => {
    handler = (path) => {
      if (path === "/agent-market/catalog/sran.invoice-po-matching") return json(item);
      if (path === "/agent-market/catalog/sran.invoice-po-matching/datasheet")
        return json({
          slug: item.slug, version: "1.0.0", name: item.name, summary: "", reads: [], limits: null, produces: [], models: [], no_model: [], storage: null, retention: [], isolation: [],
          permissions: null, never: [], cannot: "", status: "published", runner: null, generated_at: "2026-09-29T00:00:00Z", scope: "generic", jurisdiction: "TH",
        });
      if (path === "/agent-market/installs") return json({ installs: [] });
      return json({}, 404);
    };
    const detail = render(<AgentDetailPage slug="sran.invoice-po-matching" />);
    const line = await screen.findByTestId("am-scope");
    expect(line.textContent).toContain("ใช้ได้กับทุกบริษัท");
    expect(line.textContent).toContain("กฎหมายและภาษีของไทย");
    detail.unmount();
    render(<DatasheetPage slug="sran.invoice-po-matching" />);
    const sheet = await screen.findByTestId("am-datasheet");
    expect(within(sheet).getByTestId("am-scope").textContent).toContain("ใช้ได้กับทุกบริษัท · กฎหมายและภาษีของไทย");
  });

  it("an agent from a server that does not say is shown as before", async () => {
    handler = (path) => {
      if (path === "/agent-market/catalog") return json({ agents: [{ ...item, manifest: { ...po, scope: undefined, jurisdiction: undefined }, scope: undefined, jurisdiction: undefined }] });
      if (path === "/agent-market/installs") return json({ installs: [] });
      if (path === "/agent-market/shared") return json({ shared: [] });
      return json({}, 404);
    };
    render(<MarketPage />);
    const card = (await screen.findAllByTestId("am-agent-card"))[0]!;
    expect(within(card).queryByTestId("am-scope")).toBeNull();
  });
});
