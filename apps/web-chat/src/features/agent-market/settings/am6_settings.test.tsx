// AM6 part A — the settings an agent declares, on its settings page.
//
// The invoice ↔ PO agent declares its tolerances and whether the company
// issues goods receipts (manifest install.settings). The page renders what
// the server sends under "ค่าที่ใช้ตรวจ", by kind, without knowing the agent:
// a choice as radios, a number as a text field with its range under it, a
// text as a text area, the setting's help under its field, and "ยังไม่ได้ตั้ง"
// for one that is not set. The server checks every value; its sentence is
// shown at the field. The bank agent's own rows for match_* stay as they were.
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
  usePathname: () => "/chat/agents/i/i-1/settings",
}));

import { useI18nStore } from "@/lib/i18n";

import type { DeclaredSetting, Default } from "../types";
import { resetAgentMarketForTest } from "../useAgentMarket";
import { SettingsPage } from "./SettingsPage";

const GR_HELP =
  "ถ้าออก: สินค้าที่ถูกเรียกเก็บโดยยังไม่มีใบรับของเป็นข้อยกเว้นที่ต้องติดตาม ถ้าไม่ออก: ไม่มีใบรับของเป็นเรื่องปกติ ตรวจ 2 ทางและไม่ถามเรื่องนี้ ถ้ายังไม่ได้ตั้ง agent จะถามครั้งเดียวในรอบแรก โดยเสนอคำตอบจากไฟล์ที่ส่งมา";
const BAD = "ราคาต่อหน่วยสูงกว่า PO ได้ไม่เกิน (ร้อยละ) ต้องอยู่ระหว่าง 0 ถึง 50";

const declared: DeclaredSetting[] = [
  { key: "ap_gr_practice", label: "บริษัทออกใบรับของหรือไม่", kind: "choice", min: null, max: null, default: null, help: GR_HELP,
    choices: [{ value: "yes", label: "ออกใบรับของ" }, { value: "no", label: "ไม่ออกใบรับของ" }], max_length: null, value: null, set: false },
  { key: "ap_price_tolerance_pct", label: "ราคาต่อหน่วยสูงกว่า PO ได้ไม่เกิน (ร้อยละ)", kind: "percent", min: 0, max: 50, default: "2.00", help: "", choices: null, max_length: null, value: "2.00", set: true },
  { key: "ap_price_tolerance_abs", label: "ส่วนต่างราคารวมต่อบรรทัดไม่เกิน (บาท)", kind: "money", min: 0, max: 1000000000, default: "500.00", help: "ต้องผ่านทั้งร้อยละและจำนวนเงิน", choices: null, max_length: null, value: "500.00", set: true },
  { key: "ap_date_window_days", label: "ใบแจ้งหนี้ลงวันที่ก่อนวันที่ PO ได้ไม่เกิน (วัน)", kind: "int", min: 0, max: 366, default: "0", help: "", choices: null, max_length: null, value: "0", set: true },
  { key: "ap_fuzzy_threshold", label: "ความคล้ายของชื่อสินค้าอย่างน้อย (0.50–1.00)", kind: "ratio", min: 0.5, max: 1, default: "0.80", help: "", choices: null, max_length: null, value: "0.80", set: true },
  { key: "ap_unit_conversions", label: "การแปลงหน่วยนับ", kind: "text", min: null, max: null, default: null, help: "บรรทัดละหนึ่งรายการ เช่น 1 กล่อง = 12 ชิ้น", choices: null, max_length: 2000, value: null, set: false },
];
const def = (key: string, value: unknown, label = key): Default => ({ key, value, source: "install", label, note: "", updated_at: "2026-09-29T03:00:00Z" });
const installDefaults: Default[] = [
  def("formats", ["xlsx"], "รูปแบบไฟล์"),
  def("ap_price_tolerance_pct", "2.00", "ap_price_tolerance_pct"),
  def("ap_price_tolerance_abs", "500.00", "ap_price_tolerance_abs"),
];

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
type Handler = (path: string, init?: RequestInit) => unknown;
let handler: Handler;
let current: { defaults: Default[]; declared: DeclaredSetting[] | undefined };
const callsTo = (path: string, method = "GET") =>
  raw.mock.calls.filter((c) => c[0] === path && ((c[1] as RequestInit | undefined)?.method ?? "GET") === method);
const bodyOf = (c: unknown[]) => JSON.parse(String((c[1] as RequestInit).body));

function page(): Handler {
  return (path, init) => {
    if (path === "/agent-market/installs/i-1")
      return json({ install: { id: "i-1", slug: "sran.invoice-po-matching", title: "ตรวจใบแจ้งหนี้กับ PO", definition_version: "1.0.0", created_at: "" }, manifest: { outputs: [{ format: "xlsx", label: "ผลตรวจ" }] }, defaults: current.defaults, budget: { credits: 0, spent_this_month: 0 } });
    if (path === "/agent-market/installs/i-1/defaults") return json({ defaults: current.defaults, declared: current.declared });
    if (path.startsWith("/agent-market/installs/i-1/defaults/") && init?.method === "PUT") return json({ saved: true });
    if (path === "/agent-market/theme") return json({ theme: null, version: 0, presets: [] });
    return json({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }, 404);
  };
}

beforeEach(() => {
  resetAgentMarketForTest();
  raw.mockReset();
  current = { defaults: installDefaults, declared };
  handler = page();
  raw.mockImplementation(async (path: string, init?: RequestInit) =>
    path === "/agent-market/availability" ? json({ available: true, runner: { healthy: true }, role: "member" }) : handler(path, init),
  );
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
  useI18nStore.setState({ locale: "th" });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const row = async (key: string) => {
  const section = await screen.findByTestId("am-declared");
  await waitFor(() => expect(section.querySelector(`[data-key="${key}"]`)).toBeTruthy());
  return section.querySelector(`[data-key="${key}"]`) as HTMLElement;
};

describe("AM6 — ค่าที่ใช้ตรวจ", () => {
  it("is a section of every setting the agent declares, in its order", async () => {
    render(<SettingsPage installId="i-1" />);
    const section = await screen.findByTestId("am-declared");
    expect(within(section).getByRole("heading", { level: 2 }).textContent).toBe("ค่าที่ใช้ตรวจ");
    await waitFor(() => expect(within(section).getAllByTestId("am-declared-row")).toHaveLength(6));
    expect(within(section).getAllByTestId("am-declared-row").map((r) => r.getAttribute("data-key"))).toEqual(declared.map((d) => d.key));
  });

  it("a declared setting is not listed a second time among the install's settings", async () => {
    render(<SettingsPage installId="i-1" />);
    await row("ap_price_tolerance_pct");
    const generic = screen.getAllByTestId("am-default-row").map((r) => r.getAttribute("data-key"));
    expect(generic).toContain("formats");
    expect(generic).not.toContain("ap_price_tolerance_pct");
    expect(generic).not.toContain("ap_price_tolerance_abs");
  });

  it("a choice is radios; one not set says so, and that the agent asks once", async () => {
    render(<SettingsPage installId="i-1" />);
    const r = await row("ap_gr_practice");
    const radios = within(r).getAllByRole("radio") as HTMLInputElement[];
    expect(radios.map((x) => (x.closest("label") as HTMLElement).textContent)).toEqual(["ออกใบรับของ", "ไม่ออกใบรับของ"]);
    expect(radios.some((x) => x.checked)).toBe(false);
    expect(within(r).getByTestId("am-declared-state").textContent).toContain("ยังไม่ได้ตั้ง");
    expect(within(r).getByTestId("am-declared-state").textContent).toContain("agent จะถามครั้งเดียวในรอบแรก");
    expect(within(r).getByTestId("am-declared-help").textContent).toBe(GR_HELP);
    fireEvent.click(radios[1]!);
    fireEvent.click(within(r).getByRole("button", { name: "บันทึก" }));
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/defaults/ap_gr_practice", "PUT")).toHaveLength(1));
    expect(bodyOf(callsTo("/agent-market/installs/i-1/defaults/ap_gr_practice", "PUT")[0]!)).toEqual({ value: "no" });
  });

  it("a number is a text field with its range under it, and is saved as typed", async () => {
    render(<SettingsPage installId="i-1" />);
    const r = await row("ap_price_tolerance_pct");
    const field = within(r).getByLabelText("ราคาต่อหน่วยสูงกว่า PO ได้ไม่เกิน (ร้อยละ)") as HTMLInputElement;
    expect(field.tagName).toBe("INPUT");
    expect(field.type).toBe("text");
    expect(field.inputMode).toBe("decimal");
    expect(field.value).toBe("2.00");
    expect(within(r).getByTestId("am-declared-hint").textContent).toBe("ใส่ได้ตั้งแต่ 0 ถึง 50");
    expect(within(r).queryByTestId("am-declared-state")).toBeNull();
    fireEvent.change(field, { target: { value: "3.5" } });
    fireEvent.click(within(r).getByRole("button", { name: "บันทึก" }));
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/defaults/ap_price_tolerance_pct", "PUT")).toHaveLength(1));
    expect(bodyOf(callsTo("/agent-market/installs/i-1/defaults/ap_price_tolerance_pct", "PUT")[0]!)).toEqual({ value: "3.5" });
    // the page reads the settings again, from the server
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/defaults").length).toBeGreaterThan(1));
  });

  it("money, whole numbers and ratios: their range, and the help", async () => {
    render(<SettingsPage installId="i-1" />);
    const money = await row("ap_price_tolerance_abs");
    expect(within(money).getByTestId("am-declared-hint").textContent).toBe("ใส่ได้ตั้งแต่ 0 ถึง 1,000,000,000");
    expect(within(money).getByTestId("am-declared-help").textContent).toBe("ต้องผ่านทั้งร้อยละและจำนวนเงิน");
    const days = await row("ap_date_window_days");
    expect((within(days).getByRole("textbox") as HTMLInputElement).inputMode).toBe("numeric");
    expect(within(days).getByTestId("am-declared-hint").textContent).toBe("ใส่ได้ตั้งแต่ 0 ถึง 366");
    const ratio = await row("ap_fuzzy_threshold");
    expect(within(ratio).getByTestId("am-declared-hint").textContent).toBe("ใส่ได้ตั้งแต่ 0.5 ถึง 1");
  });

  it("what is not a number is not sent", async () => {
    render(<SettingsPage installId="i-1" />);
    const r = await row("ap_price_tolerance_pct");
    fireEvent.change(within(r).getByRole("textbox"), { target: { value: "สองจุดห้า" } });
    fireEvent.click(within(r).getByRole("button", { name: "บันทึก" }));
    expect(within(r).getByRole("alert").textContent).toBe("ใส่เป็นตัวเลข");
    expect(callsTo("/agent-market/installs/i-1/defaults/ap_price_tolerance_pct", "PUT")).toHaveLength(0);
  });

  it("the server's refusal is said at the field", async () => {
    const base = page();
    handler = (path, init) =>
      path === "/agent-market/installs/i-1/defaults/ap_price_tolerance_pct" && init?.method === "PUT"
        ? json({ error: { code: "BAD_SETTING", message: BAD } }, 400)
        : base(path, init);
    render(<SettingsPage installId="i-1" />);
    const r = await row("ap_price_tolerance_pct");
    fireEvent.change(within(r).getByRole("textbox"), { target: { value: "75" } });
    fireEvent.click(within(r).getByRole("button", { name: "บันทึก" }));
    expect((await within(r).findByRole("alert")).textContent).toBe(BAD);
    const other = await row("ap_price_tolerance_abs");
    expect(within(other).queryByRole("alert")).toBeNull();
  });

  it("a text is a text area with its length, not set", async () => {
    render(<SettingsPage installId="i-1" />);
    const r = await row("ap_unit_conversions");
    const area = within(r).getByLabelText("การแปลงหน่วยนับ") as HTMLTextAreaElement;
    expect(area.tagName).toBe("TEXTAREA");
    expect(area.maxLength).toBe(2000);
    expect(within(r).getByTestId("am-declared-state").textContent).toBe("ยังไม่ได้ตั้ง");
    expect(within(r).getByTestId("am-declared-help").textContent).toBe("บรรทัดละหนึ่งรายการ เช่น 1 กล่อง = 12 ชิ้น");
  });

  it("an agent that declares nothing has no such section; the bank agent's rows stay", async () => {
    current = { defaults: [def("match_date_window_days", "3", "ช่วงวันที่ที่ใช้จับคู่ (วัน)")], declared: undefined };
    render(<SettingsPage installId="i-1" />);
    const rows = await screen.findAllByTestId("am-default-row");
    expect(rows.map((r) => r.getAttribute("data-key"))).toEqual(["match_date_window_days"]);
    await waitFor(() => expect(callsTo("/agent-market/installs/i-1/defaults")).toHaveLength(1));
    expect(screen.queryByTestId("am-declared")).toBeNull();
  });
});
