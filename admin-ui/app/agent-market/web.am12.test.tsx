// AM12 — what an admin holds over agents that work on the web: the ceilings
// of one run, the sites never opened, the organisation's suppliers, where
// each run went; and the per-agent switch said in words that fit an agent
// that searches (it sends no documents).
//   * the tab says whether there is a search provider and a page fetcher, and
//     that an agent is off until it is turned on on the Models tab;
//   * a ceiling left blank goes to chat-edge as null (the agent's own), a
//     number as that number; a refusal is said in the page's language;
//   * a site added to the list, a supplier added, edited and removed, each go
//     to chat-edge as exactly that;
//   * a run's addresses that were not opened are listed with the reason in
//     words, in the page's language — never as a code.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { api } = vi.hoisted(() => ({
  api: {
    web: vi.fn(), webCalls: vi.fn(), putWebPolicy: vi.fn(), putWebDeny: vi.fn(), deleteWebDeny: vi.fn(), putWebSupplier: vi.fn(),
    deleteWebSupplier: vi.fn(), egress: vi.fn(), putEgress: vi.fn(), catalog: vi.fn(),
  },
}));
vi.mock("../../lib/agentMarketApi", async () => {
  const real = await vi.importActual<typeof import("../../lib/agentMarketApi")>("../../lib/agentMarketApi");
  return { ...real, amAdmin: api };
});

import { I18nProvider } from "../../lib/i18n";
import { ToastProvider } from "../../components/ui/Toast";
import { ChatEdgeApiError } from "../../lib/chatEdgeApi";
import { amKnownErrorCodes, type AdminWeb, type AgentEgressRow, type WebRunRow } from "../../lib/agentMarketApi";

import AgentEgressCard from "./_sections/AgentEgress";
import WebSection from "./_sections/Web";

const wrap = (el: React.ReactNode) => render(<I18nProvider><ToastProvider>{el}</ToastProvider></I18nProvider>);

const SLUG = "sran.procurement";
const caps = { searches: 4, pages: 8, pages_per_host: 3, seconds: 300, source_mb: 10, results: 8 };
const web = (o: Partial<AdminWeb> = {}): AdminWeb => ({
  policy: { max_searches: null, max_pages: null, max_seconds: null, max_source_mb: null, unused_keep_days: 7 },
  deny: [],
  suppliers: [],
  agents: [{ slug: SLUG, name: "จัดซื้อ", org: true, agent: false, agent_default: true, accounting: false, web: true, effective: false, words: "", caps }],
  ceiling: { searches: 8, pages: 16, pages_per_host: 4, seconds: 600, source_mb: 10, results: 10 },
  search: false,
  fetcher: true,
  ...o,
});
const runs: WebRunRow[] = [
  { run_id: "run-1", slug: SLUG, agent: "จัดซื้อ", user: "Somchai", started_at: "2026-10-06T11:42:20Z", searches: 1, fetches: 5,
    reached: ["www.baan-stationery.test", "shop.ruam-raan.test"], bytes: 5_446_000, search_credits: 24000,
    refused: [{ host: "www.norobots.test", reason: "robots_disallow",
      words: { th: "เว็บไซต์นี้ประกาศไม่ให้โปรแกรมอัตโนมัติอ่านหน้านี้ จึงไม่เปิด", en: "This site states that automated programs may not read this page, so it was not opened." } }] },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.catalog.mockResolvedValue({ agents: [] });
  api.web.mockResolvedValue(web());
  api.webCalls.mockResolvedValue({ days: 30, runs });
});
afterEach(cleanup);

describe("the web tab", () => {
  it("says what is there to search and fetch with, and that the agent is off until turned on on the Models tab", async () => {
    wrap(<WebSection />);
    expect((await screen.findByTestId("am-web-search")).getAttribute("data-on")).toBe("false");
    expect(screen.getByTestId("am-web-search").textContent).toMatch(/no search provider|ยังไม่ได้ตั้งผู้ให้บริการค้นหา/);
    expect(screen.getByTestId("am-web-fetcher").getAttribute("data-on")).toBe("true");
    const agent = screen.getByTestId(`am-web-agent-${SLUG}`);
    expect(agent.getAttribute("data-on")).toBe("false");
    expect(agent.textContent).toMatch(/Off|ปิดอยู่/);
    expect(agent.textContent).toMatch(/Models tab|แท็บโมเดล/);
    expect(agent.textContent).toMatch(/4 searches · 8 pages · 300 seconds · 10 MB|ค้นหา 4 ครั้ง · 8 หน้า · 300 วินาที · 10 MB/);
    // there is no switch here, and no list of sites an agent is allowed to open
    expect(within(screen.getByTestId("am-web")).queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.getByTestId("am-web").textContent).toMatch(/no list of sites the agent is allowed to open|ไม่มีรายชื่อเว็บไซต์ที่อนุญาต/);
  });

  it("a ceiling left blank is the agent's own (null); a number is that number", async () => {
    api.putWebPolicy.mockResolvedValue({ policy: { max_searches: 2, max_pages: null, max_seconds: null, max_source_mb: null, unused_keep_days: 14 } });
    wrap(<WebSection />);
    fireEvent.change(await screen.findByTestId("am-web-policy-max_searches"), { target: { value: "2" } });
    fireEvent.change(screen.getByTestId("am-web-policy-unused_keep_days"), { target: { value: "14" } });
    // the most the platform allows is the hint in each box
    expect((screen.getByTestId("am-web-policy-max_pages") as HTMLInputElement).placeholder).toMatch(/16/);
    fireEvent.click(screen.getByTestId("am-web-policy-save"));
    await waitFor(() =>
      expect(api.putWebPolicy).toHaveBeenCalledWith({ max_searches: 2, max_pages: null, max_seconds: null, max_source_mb: null, unused_keep_days: 14 }),
    );
    expect((await screen.findByTestId("am-web-policy-note")).textContent).toMatch(/Saved|บันทึกแล้ว/);
  });

  it("a number outside what is allowed is refused in the page's own words", async () => {
    api.putWebPolicy.mockRejectedValue(new ChatEdgeApiError(400, { error: { code: "BAD_WEB_POLICY", message: "ตัวเลขอยู่นอกช่วง" } }));
    wrap(<WebSection />);
    fireEvent.change(await screen.findByTestId("am-web-policy-max_pages"), { target: { value: "99" } });
    fireEvent.click(screen.getByTestId("am-web-policy-save"));
    const note = await screen.findByTestId("am-web-policy-note");
    expect(note.textContent).toMatch(/outside what is allowed|อยู่นอกช่วงที่กำหนด/);
    expect(note.textContent).not.toContain("BAD_WEB_POLICY");
    for (const code of ["BAD_HOST", "BAD_WEB_POLICY", "BAD_TAX_ID", "SUPPLIER_EXISTS"]) expect(amKnownErrorCodes()).toContain(code);
  });

  it("a site is added to the never-opened list and taken off it", async () => {
    const denied = [{ host: "never-open.example", note: "ไม่ซื้อจากที่นี่", added_by: "boss@example.invalid", added_at: "2026-10-06T10:00:00Z" }];
    api.putWebDeny.mockResolvedValue({ deny: denied });
    api.deleteWebDeny.mockResolvedValue({ deny: [] });
    wrap(<WebSection />);
    const add = (await screen.findByTestId("am-web-deny-add")) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("am-web-deny-host"), { target: { value: " https://www.never-open.example/p/1 " } });
    fireEvent.click(add);
    await waitFor(() => expect(api.putWebDeny).toHaveBeenCalledWith("https://www.never-open.example/p/1", ""));
    const row = await screen.findByTestId("am-web-denied");
    expect(row.textContent).toContain("never-open.example"); // what the server kept: the site, not the address typed
    fireEvent.click(within(row).getByRole("button"));
    await waitFor(() => expect(api.deleteWebDeny).toHaveBeenCalledWith("never-open.example"));
    await waitFor(() => expect(screen.queryByTestId("am-web-denied")).toBeNull());
  });

  it("a supplier is added, edited by its id, and removed", async () => {
    const one = { id: "sup-1", name: "บริษัท ตัวอย่างการค้า จำกัด", tax_id: "0105556012345", website: "supplier.example", note: "" };
    api.putWebSupplier.mockResolvedValue({ suppliers: [one] });
    api.deleteWebSupplier.mockResolvedValue({ suppliers: [] });
    wrap(<WebSection />);
    fireEvent.change(await screen.findByTestId("am-web-sup-name"), { target: { value: one.name } });
    fireEvent.change(screen.getByTestId("am-web-sup-tax"), { target: { value: one.tax_id } });
    fireEvent.click(screen.getByTestId("am-web-sup-save"));
    await waitFor(() => expect(api.putWebSupplier).toHaveBeenCalledWith({ name: one.name, tax_id: one.tax_id, website: "", note: "" }));
    const row = await screen.findByTestId("am-web-supplier");
    expect(row.textContent).toContain("0105556012345");
    const [edit, remove] = within(row).getAllByRole("button");
    fireEvent.click(edit!);
    expect((screen.getByTestId("am-web-sup-name") as HTMLInputElement).value).toBe(one.name);
    fireEvent.click(screen.getByTestId("am-web-sup-save"));
    await waitFor(() => expect(api.putWebSupplier).toHaveBeenLastCalledWith({ id: "sup-1", name: one.name, tax_id: one.tax_id, website: "supplier.example", note: "" }));
    fireEvent.click(remove!);
    await waitFor(() => expect(api.deleteWebSupplier).toHaveBeenCalledWith("sup-1"));
  });

  it("where each run went: the sites it reached, and why an address was not opened — in words, never as a code", async () => {
    wrap(<WebSection />);
    const run = await screen.findByTestId("am-web-run");
    expect(run.textContent).toContain("www.baan-stationery.test · shop.ruam-raan.test");
    expect(run.textContent).toContain("Somchai");
    const refused = within(run).getByTestId("am-web-run-refused");
    expect(refused.textContent).toContain("www.norobots.test");
    expect(refused.textContent).toMatch(/automated programs may not read this page|ประกาศไม่ให้โปรแกรมอัตโนมัติอ่านหน้านี้/);
    expect(run.textContent).not.toContain("robots_disallow");
    expect(api.webCalls).toHaveBeenCalledWith(30);
  });

  it("with nothing listed and no run, each part says so", async () => {
    api.web.mockResolvedValue(web({ agents: [] }));
    api.webCalls.mockResolvedValue({ days: 30, runs: [] });
    wrap(<WebSection />);
    const page = await screen.findByTestId("am-web");
    await waitFor(() => expect(page.textContent).toMatch(/No run has searched|ไม่มีรอบใดค้นหา/));
    expect(page.textContent).toMatch(/No site is listed|ยังไม่มีเว็บไซต์ในรายการ/);
    expect(page.textContent).toMatch(/No supplier is listed|ยังไม่มีผู้ขายในรายการ/);
    expect(page.textContent).toMatch(/No agent in the catalog works on the web|ไม่มี agent ในแค็ตตาล็อกที่ทำงานบนเว็บ/);
  });
});

describe("the per-agent switch of an agent that works on the web (Models tab)", () => {
  const row = (o: Partial<AgentEgressRow>): AgentEgressRow => ({
    slug: SLUG, name: "จัดซื้อ", org: true, agent: false, agent_default: true, accounting: false, web: true, effective: false, words: "", ...o,
  });

  it("is off until an admin turns it on, and says searching — not documents", async () => {
    api.egress.mockResolvedValue({ org: true, agents: [row({})] });
    api.putEgress.mockResolvedValue({ slug: SLUG, egress: row({ agent: true, agent_default: false, effective: true }) });
    wrap(<AgentEgressCard orgVersion="a" />);
    const toggle = (await screen.findByTestId(`am-egress-toggle-${SLUG}`)) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    const now = screen.getByTestId(`am-egress-now-${SLUG}`);
    expect(now.textContent).toMatch(/Does not search and opens no web page|ไม่ค้นหา และไม่เปิดหน้าเว็บใด/);
    expect(screen.getByTestId(`am-egress-${SLUG}`).textContent).toMatch(/default for an agent that works on the web|ค่าตั้งต้นของ agent ที่ทำงานบนเว็บ/);
    fireEvent.click(toggle);
    await waitFor(() => expect(api.putEgress).toHaveBeenCalledWith(SLUG, true));
    await waitFor(() => expect(now.textContent).toMatch(/May search and open web pages outside the organisation|ค้นหาและเปิดหน้าเว็บนอกองค์กรได้/));
    expect(now.textContent).not.toMatch(/outside model|โมเดลนอกองค์กร/);
  });
});
