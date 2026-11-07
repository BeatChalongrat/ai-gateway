// AM12 — an agent that works on the web, on the web chat's screens.
//   * the choice card shows the comparison it asks about above the choice: one row a candidate, what
//     is not known written as "ไม่ทราบ" and never left blank, nothing marked as the one to take, the
//     sources that were not used with why;
//   * a row opens to what else the page said, what is still not known and why, the values a reader
//     gave that were not used, and the picture cut from the saved page — fetched with the session,
//     from the run's own evidence;
//   * the answer that goes out is the option the person clicked, like any card;
//   * the workspace says before anything is typed that the agent may not search yet, in the server's
//     sentence; an agent that works from a request has no "drop your file here";
//   * the run page lists where the run went, and why an address was not opened.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw } = vi.hoisted(() => ({ raw: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));

import { useI18nStore } from "@/lib/i18n";

import { SourcesTab } from "../runs/SourcesTab";
import type { CardPayload, Comparison, Manifest, Question, WebCall } from "../types";
import { AgentQuestionCard } from "./AgentQuestionCard";
import { comparisonOf } from "./Comparison";
import { EmptyDrop } from "./EmptyDrop";
import { SeenBy } from "./OutputsCard";
import { WebBanner } from "./WebBanner";

const comparison: Comparison = {
  item: { key: "i1", name: "กระดานไวท์บอร์ดแม่เหล็ก 120 x 240 ซม.", quantity: "6", unit: "แผ่น" },
  candidates: [
    {
      no: 1, key: "s1.1", host: "www.baan-stationery.test", url: "http://www.baan-stationery.test/p/wb-120240-m", kind: "own_site",
      observed_at: "2026-10-06T11:42:22.814Z", name: "กระดานไวท์บอร์ดแม่เหล็ก ขนาด 120 x 240 ซม.", model: { quote: "WB-120240-M", block: 3 },
      price: { display: "฿3,200.00", amount: "3200.00", currency: "THB", block: 4 }, subtotal: "19200.00", net: "17943.93", vat_part: "1256.07",
      total: "19200.00", vat: { says: "included", quote: "ราคารวมภาษีมูลค่าเพิ่มแล้ว" }, shipping: { quote: "จัดส่งฟรีในกรุงเทพฯ และปริมณฑล" },
      warranty: { quote: "รับประกันผิวกระดาน 2 ปี" }, tax_id: { quote: "0105559876543" }, open: [], source: "s1",
      picture: { name: "evidence_abafcb275c103bd3b8b78b6d70acc3ec.jpg", width: 1002, height: 334 },
      words: { kind: "เว็บไซต์ของผู้ขายเอง", vat: "รวมภาษีมูลค่าเพิ่มแล้ว", on_list: "ไม่อยู่ในรายชื่อผู้ขายขององค์กร", requirements: "ตรง 2 จาก 2 ข้อ", open: [], not_found: [] },
    },
    {
      no: 2, key: "s2.1", host: "shop.ruam-raan.test", url: "http://shop.ruam-raan.test/item/mb-2412", kind: "marketplace",
      name: "ไวท์บอร์ดแม่เหล็ก 120x240 ซม. ขอบอะลูมิเนียม", price: null, subtotal: null, net: null, vat_part: null, total: null, vat: null,
      open: ["price", "vat"], source: "s2",
      words: {
        kind: "ตลาดออนไลน์", vat: "ไม่ทราบ", on_list: "ไม่อยู่ในรายชื่อผู้ขายขององค์กร", requirements: "หน้าเว็บไม่ได้ระบุ",
        open: [
          { code: "price", what: "ราคา", why: "ไม่พบราคาที่ตรวจซ้ำได้ในหน้าที่บันทึกไว้ ต้องถามผู้ขาย" },
          { code: "vat", what: "ภาษีมูลค่าเพิ่ม", why: "หน้าเว็บไม่ได้ระบุว่าราคารวมหรือยังไม่รวมภาษีมูลค่าเพิ่ม" },
        ],
        not_found: [{ field: "ราคาที่อ่านได้", why: "ตัวเลขนี้ถูกขีดฆ่าในหน้าเว็บ จึงไม่ใช่ราคาที่เสนอขาย" }],
      },
    },
  ],
  dropped: [
    { host: "www.norobots.test", url: "http://www.norobots.test/p/board", why: "robots_disallow",
      words: "เว็บไซต์นี้ประกาศไม่ให้โปรแกรมอัตโนมัติอ่านหน้านี้ จึงไม่เปิด ข้อที่ประกาศ: “Disallow: /”" },
  ],
  sorted_by: "fewest_open",
  round: { used: { searches: 1, pages: 3 }, caps: { searches: 4, pages: 8 }, lines: ["ถึงเพดานจำนวนหน้าของรอบนี้แล้ว จึงมีตัวเลือกน้อยกว่าที่อาจหาได้"] },
};

const pick: Question = {
  key: "proc_pick:i1", code: "proc_pick", severity: "blocking", header: "เลือกตัวเลือก",
  question: "พบ 2 ตัวเลือกสำหรับ “กระดานไวท์บอร์ดแม่เหล็ก 120 x 240 ซม.” ครับ ตารางนี้เป็นรายการให้เลือก ไม่ใช่คำแนะนำ จะร่างใบขอซื้อจากตัวเลือกไหน",
  options: [
    { label: "ตัวเลือกที่ 1", description: "www.baan-stationery.test · ฿3,200.00", value: "s1.1" },
    { label: "ตัวเลือกที่ 2", description: "shop.ruam-raan.test", value: "s2.1" },
    { label: "ไม่เลือกรายใด", description: "ได้ตารางเทียบอย่างเดียว ยังไม่ร่างใบขอซื้อ", value: "none" },
  ],
  default: "none", default_label: "ไม่เลือกรายใด", evidence: { comparison },
};
const card: CardPayload = { card_id: "card-9", run_id: "run-9", kind: "questions", status: "open", blocking: true, attempts: 0, questions: [pick] };

const picture = new Blob(["jpeg"], { type: "image/jpeg" });

beforeEach(() => {
  raw.mockReset();
  raw.mockImplementation(async (path: string) =>
    String(path).includes("/evidence/") || String(path).endsWith("/picture")
      ? { ok: true, status: 200, blob: async () => picture, text: async () => "" }
      : { ok: true, status: 200, text: async () => JSON.stringify({ changed: true }) },
  );
  useI18nStore.setState({ locale: "th" });
  URL.createObjectURL = vi.fn(() => "blob:evidence");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);

describe("the choice card of a run on the web", () => {
  it("shows the comparison above the choice: a row a candidate, what is not known said as that", () => {
    render(<AgentQuestionCard card={card} />);
    const table = screen.getByTestId("am-comparison");
    const rows = within(table).getAllByTestId("am-candidate");
    expect(rows.map((r) => r.getAttribute("data-candidate"))).toEqual(["s1.1", "s2.1"]);
    expect(rows[0]!.textContent).toContain("www.baan-stationery.test");
    expect(rows[0]!.textContent).toContain("฿3,200.00");
    expect(rows[0]!.textContent).toContain("รวมภาษีมูลค่าเพิ่มแล้ว");
    expect(rows[0]!.textContent).toContain("THB 19,200.00");
    // the second has no price that could be found again: three cells say so, none is empty and none is a figure
    const unknown = within(rows[1]!).getAllByTestId("am-unknown").map((n) => n.textContent);
    expect(unknown).toEqual(["ไม่ทราบ", "ไม่ทราบ", "ไม่ทราบ"]);
    expect(rows[1]!.textContent).toContain("2 จุด");
    expect(rows[0]!.textContent).toContain("ไม่มี");
    // it says what the order is, and that it is not advice
    expect(screen.getByTestId("am-comparison-order").textContent).toContain("ไม่ใช่การจัดอันดับ");
    // AM13: … and, where a person decides, that this card is not in the order of price even when they asked
    // for that order — the Excel file is
    expect(screen.getByTestId("am-comparison-order").textContent).toContain("การ์ดนี้ไม่เรียงตามราคา");
    expect(screen.getByTestId("am-comparison-order").textContent).toContain("ไฟล์ Excel ที่ได้จะเรียงตามราคา");
    // the table comes before the choice
    const choice = screen.getAllByTestId("question-option")[0]!;
    expect(table.compareDocumentPosition(choice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("marks no candidate as the one to take", () => {
    render(<AgentQuestionCard card={card} />);
    // what a skipped card records ("none") is not shown as a recommendation, and the table is not dumped as key/values
    expect(screen.queryByTestId("am-default-line")).toBeNull();
    expect(screen.queryByTestId("am-question-details")).toBeNull();
    const text = screen.getByTestId("am-question-card").textContent ?? "";
    for (const word of ["ค่าแนะนำ", "ดีที่สุด", "ควรเลือก", "คุ้มที่สุด"]) expect(text).not.toContain(word);
  });

  it("lists the sources that were not used with why, and what the run could not do", () => {
    render(<AgentQuestionCard card={card} />);
    const dropped = screen.getByTestId("am-comparison-dropped");
    expect(dropped.textContent).toContain("แหล่งที่ไม่ได้ใช้ (1)");
    expect(dropped.textContent).toContain("www.norobots.test");
    expect(dropped.textContent).toContain("ประกาศไม่ให้โปรแกรมอัตโนมัติอ่านหน้านี้");
    expect(screen.getByTestId("am-comparison-limits").textContent).toContain("ถึงเพดานจำนวนหน้า");
    expect(screen.getByTestId("am-comparison").textContent).toContain("ค้นหา 1 ครั้ง เปิด 3 หน้า");
  });

  it("a row opens to the evidence: the picture from the saved page, what is open and why, what was not used", async () => {
    render(<AgentQuestionCard card={card} />);
    const toggles = screen.getAllByTestId("am-candidate-toggle");
    expect(toggles[0]!.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggles[0]!);
    const details = screen.getByTestId("am-candidate-details");
    expect(details.textContent).toContain("http://www.baan-stationery.test/p/wb-120240-m");
    expect(details.textContent).toContain("0105559876543");
    expect(details.textContent).toContain("THB 1,256.07");
    // a field the page said nothing of is "not known", not blank
    expect(details.textContent).toContain("ระยะเวลาส่ง" + "ไม่ทราบ");
    const img = (await screen.findByTestId("am-evidence-picture")).querySelector("img")!;
    expect(img.getAttribute("src")).toBe("blob:evidence");
    expect(raw.mock.calls.some((c) => c[0] === "/agent-market/runs/run-9/evidence/evidence_abafcb275c103bd3b8b78b6d70acc3ec.jpg")).toBe(true);
    // the page's address is text: nothing in the details is a link out
    expect(details.querySelector("a")).toBeNull();

    fireEvent.click(toggles[1]!);
    const second = screen.getByTestId("am-candidate-details");
    expect(within(second).getByTestId("am-candidate-open").textContent).toContain("ไม่พบราคาที่ตรวจซ้ำได้ในหน้าที่บันทึกไว้");
    expect(within(second).getByTestId("am-candidate-not-found").textContent).toContain("ถูกขีดฆ่า");
    expect(screen.getAllByTestId("am-candidate-details")).toHaveLength(1); // one open at a time
  });

  it("the picture that cannot be loaded says so", async () => {
    raw.mockImplementation(async () => ({ ok: false, status: 410, text: async () => JSON.stringify({ error: { code: "WEB_PAGE_EXPIRED", message: "ลบแล้ว" } }) }));
    render(<AgentQuestionCard card={card} />);
    fireEvent.click(screen.getAllByTestId("am-candidate-toggle")[0]!);
    expect((await screen.findByTestId("am-evidence-picture-failed")).textContent).toContain("โหลดภาพหลักฐานไม่ได้");
  });

  it("the choice goes out as the option that was clicked", async () => {
    render(<AgentQuestionCard card={card} />);
    expect(screen.getAllByTestId("question-option")).toHaveLength(3);
    fireEvent.click(screen.getAllByTestId("question-option")[0]!);
    fireEvent.click(screen.getByTestId("question-submit"));
    await waitFor(() => expect(raw.mock.calls.some((c) => c[0] === "/agent-market/cards/card-9/answer")).toBe(true));
    const call = raw.mock.calls.find((c) => c[0] === "/agent-market/cards/card-9/answer")!;
    expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ answers: { "proc_pick:i1": { labels: ["ตัวเลือกที่ 1"] } } });
  });

  it("in English the screen's own words are English; what a page printed stays as it was printed", () => {
    useI18nStore.setState({ locale: "en" });
    render(<AgentQuestionCard card={card} />);
    const table = screen.getByTestId("am-comparison");
    expect(table.textContent).toContain("Comparison of candidates");
    expect(table.textContent).toContain("Unit price as shown");
    expect(within(table).getAllByTestId("am-unknown")[0]!.textContent).toBe("Not known");
    expect(table.textContent).toContain("กระดานไวท์บอร์ดแม่เหล็ก ขนาด 120 x 240 ซม.");
  });

  it("evidence that is not a comparison is left to the generic evidence view", () => {
    expect(comparisonOf({ file: "GL.xlsx" })).toBeNull();
    expect(comparisonOf(null)).toBeNull();
    expect(comparisonOf({ comparison: { candidates: "x" } })).toBeNull();
    expect(comparisonOf({ comparison })).toBe(comparison);
  });
});

describe("the workspace of an agent that works on the web", () => {
  it("says the agent may not search yet, in the server's sentence", () => {
    const words = "agent นี้ยังค้นหาไม่ได้ครับ ผู้ดูแลระบบยังไม่ได้เปิดให้ agent นี้ค้นหาและเปิดหน้าเว็บนอกองค์กร";
    render(<WebBanner web={{ uses: true, open: false, search: true, why: "agent_closed", words }} />);
    const banner = screen.getByTestId("am-web-banner");
    expect(banner.getAttribute("data-web")).toBe("closed");
    expect(banner.textContent).toContain("agent นี้ยังค้นหาไม่ได้");
    expect(banner.textContent).toContain(words);
    // said once: the server's sentence already opens with it, so the screen's own short line stands down
    expect(banner.textContent!.split("ยังค้นหาไม่ได้").length - 1).toBe(1);
    cleanup();
    render(<WebBanner web={{ uses: true, open: false, search: true, why: "agent_closed" }} />);
    expect(screen.getByTestId("am-web-banner").textContent).toBe("agent นี้ยังค้นหาไม่ได้");
  });

  it("with no search provider it says links can be pasted; when all is open it says nothing", () => {
    const { rerender } = render(<WebBanner web={{ uses: true, open: true, search: false, words: "วางลิงก์หน้าสินค้าที่สนใจมาในข้อความ" }} />);
    expect(screen.getByTestId("am-web-banner").getAttribute("data-web")).toBe("no-search");
    expect(screen.getByTestId("am-web-banner").textContent).toContain("วางลิงก์");
    rerender(<WebBanner web={{ uses: true, open: true, search: true }} />);
    expect(screen.queryByTestId("am-web-banner")).toBeNull();
    rerender(<WebBanner web={{ uses: false, open: false, search: false }} />);
    expect(screen.queryByTestId("am-web-banner")).toBeNull();
    rerender(<WebBanner web={undefined} />);
    expect(screen.queryByTestId("am-web-banner")).toBeNull();
  });

  it("an empty workspace says what to type, not what file to drop", () => {
    const manifest: Manifest = { id: "sran.procurement", starts_from: "request", inputs: [] };
    render(<EmptyDrop manifest={manifest} onPick={() => {}} />);
    expect(screen.getByTestId("am-empty-request")).toBeTruthy();
    expect(screen.getByTestId("am-empty-sentence").textContent).toBe("พิมพ์สิ่งที่ต้องการซื้อ");
    expect(screen.queryByTestId("am-empty-pick")).toBeNull();
    expect(screen.queryByTestId("am-empty-drop")).toBeNull();
  });
});

describe("where a run on the web went (the run page)", () => {
  const calls: WebCall[] = [
    { run_id: "run-9", kind: "search", source: "", host: "", url: "กระดานไวท์บอร์ดแม่เหล็ก 120 x 240", outcome: "answered", reason: "", bytes: 0, results: 6,
      provider: "tavily", started_at: "2026-10-06T11:42:20Z", duration_ms: 800 },
    { run_id: "run-9", kind: "fetch", source: "s01", host: "www.baan-stationery.test", url: "http://www.baan-stationery.test/p/wb-120240-m", outcome: "captured",
      reason: "", bytes: 5446, results: 0, provider: "", started_at: "2026-10-06T11:42:22Z", duration_ms: 1500, used: true },
    { run_id: "run-9", kind: "fetch", source: "s02", host: "www.old-page.test", url: "http://www.old-page.test/x", outcome: "captured", reason: "", bytes: 100,
      results: 0, provider: "", started_at: "2026-10-06T11:42:24Z", duration_ms: 900, used: false, purged_at: "2026-10-13T00:00:00Z" },
    { run_id: "run-9", kind: "fetch", source: "s03", host: "members.closed-shop.test", url: "http://members.closed-shop.test/account", outcome: "refused",
      reason: "credential", words: "หน้านี้ต้องเข้าสู่ระบบหรือกรอกข้อมูลชำระเงิน จึงไม่เปิดต่อ", bytes: 0, results: 0, provider: "",
      started_at: "2026-10-06T11:42:26Z", duration_ms: 300 },
  ];

  it("lists every search and every address, and why one was not opened", () => {
    render(<SourcesTab runId="run-9" calls={calls} />);
    const rows = screen.getAllByTestId("am-source-row");
    expect(rows.map((r) => r.getAttribute("data-outcome"))).toEqual(["answered", "captured", "captured", "refused"]);
    expect(rows[0]!.textContent).toContain("กระดานไวท์บอร์ดแม่เหล็ก 120 x 240");
    expect(rows[0]!.textContent).toContain("ได้ 6 ผลการค้นหา");
    expect(rows[1]!.textContent).toContain("เป็นหลักฐานของตัวเลือก");
    expect(rows[2]!.textContent).toContain("ลบเมื่อครบกำหนดเก็บ");
    expect(rows[3]!.textContent).toContain("ไม่ได้เปิด");
    expect(rows[3]!.textContent).toContain("ต้องเข้าสู่ระบบ");
    // only a page that is still kept offers its picture
    expect(screen.getAllByTestId("am-source-picture")).toHaveLength(1);
  });

  it("shows a saved page as its picture, fetched from the run's own sources", async () => {
    render(<SourcesTab runId="run-9" calls={calls} />);
    fireEvent.click(screen.getByTestId("am-source-picture"));
    expect((await screen.findByTestId("am-evidence-picture")).querySelector("img")!.getAttribute("src")).toBe("blob:evidence");
    expect(raw.mock.calls.some((c) => c[0] === "/agent-market/runs/run-9/sources/s01/picture")).toBe(true);
  });

  it("a run that went nowhere says so", () => {
    render(<SourcesTab runId="run-9" calls={[]} />);
    expect(screen.getByText("รอบนี้ไม่ได้ค้นหาหรือเปิดหน้าเว็บ")).toBeTruthy();
  });
});

// The files card always says what a model saw. For an agent on the web a model read the request and
// the pages — the card names it, and never says the line of an agent whose model only writes prose
// ("no model saw the source files"), which is not true of this one.
describe("the files card of a round on the web: which model read", () => {
  const seen = () => screen.getByTestId("am-outputs-seen-by").textContent ?? "";

  it("says what read the request and the pages, and where each ran — naming no model (AM23)", () => {
    render(
      <SeenBy
        outputs={{
          run_id: "r-1",
          artifacts: [],
          model: { uses: "web", choice: "auto", web: [{ model: "deepseek-v4-flash", self_hosted: false }, { model: "qwen3.8-27b-fp8", self_hosted: true }] },
        }}
      />,
    );
    expect(seen()).toContain("คำขอและหน้าเว็บที่บันทึกไว้อ่านโดยผู้ช่วย AI ขององค์กร (นอกองค์กร · ในองค์กร)");
    expect(seen()).not.toMatch(/deepseek|qwen/);
    expect(seen()).toContain("ตรวจซ้ำกับหน้าที่บันทึกไว้ด้วยโปรแกรม");
    expect(seen()).not.toContain("ไม่มีโมเดลใดเห็นไฟล์ต้นฉบับ");
    expect(seen()).not.toContain("กฎของ agent");
  });

  it("a round no model answered in (the sample runs on pages kept with the agent) says that, and no more", () => {
    render(<SeenBy outputs={{ run_id: "r-1", artifacts: [], model: { uses: "web", choice: "auto", web: [] } }} />);
    expect(seen()).toBe("รอบนี้ไม่มีโมเดลใดอ่านคำขอหรือหน้าเว็บ");
  });

  it("says so when the model that read is not the one the person chose", () => {
    render(
      <SeenBy
        outputs={{
          run_id: "r-1",
          artifacts: [],
          model: { uses: "web", choice: "deepseek-v4-flash", chosen_model: "deepseek-v4-flash", not_chosen: true, web: [{ model: "qwen3.8-27b-fp8", self_hosted: true }] },
        }}
      />,
    );
    expect(seen()).toContain("รอบนี้ไม่ได้ใช้โมเดลที่คุณเลือกไว้");
    expect(seen()).not.toMatch(/deepseek|qwen/);
  });

  it("in English", () => {
    useI18nStore.setState({ locale: "en" });
    render(<SeenBy outputs={{ run_id: "r-1", artifacts: [], model: { uses: "web", web: [{ model: "deepseek-v4-flash", self_hosted: false }] } }} />);
    expect(seen()).toContain("The request and the saved web pages were read by the organisation's AI assistant (outside the organisation)");
    expect(seen()).not.toContain("deepseek");
    expect(seen()).not.toMatch(/[฀-๿]/);
  });
});
