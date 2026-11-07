// AM1 — the sidebar entry is absent unless the feature is on.
//   * flag off (/api/agent-market says enabled:false) → renders nothing and
//     makes NO chat-edge request at all;
//   * no fetch / a failing fetch → renders nothing, throws nothing;
//   * flag on + account allowed → the market link and my installed agents.
// AM3 — "Agent ของฉัน" survives twenty agents:
//   * a collapsible section with the count, its state saved per user (PUT /prefs);
//   * pinned first, then recently used, about five, "ดูทั้งหมด" for the rest;
//   * a run waiting for an answer is never hidden: a badge with the count on
//     the section (collapsed too) and on the agent, even past the cap;
//   * the sidebar's search text (Ctrl+K) shows matching agents.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  usePathname: () => "/chat/agents/i/i-1",
}));

import { useI18nStore } from "@/lib/i18n";

import { AgentRailSection } from "./AgentRailSection";
import { amDictionaries } from "./i18n";
import type { RailInstall } from "./types";
import { resetAgentMarketForTest } from "./useAgentMarket";
import { forgetHeldOrderForTest } from "./useRail";

const json = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) });

const row = (id: string, title: string, extra: Partial<RailInstall> = {}): RailInstall => ({
  id,
  slug: `sran.${id}`,
  title,
  icon: "chart",
  version: "1.0.0",
  pinned_at: null,
  last_used_at: null,
  waiting: 0,
  disabled: false,
  ...extra,
});

let rail: { installs: RailInstall[]; waiting_total: number; prefs: Record<string, boolean> };

function routeWith(path: string, init?: RequestInit) {
  if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true } });
  if (path === "/agent-market/rail") return json(rail);
  if (path === "/agent-market/prefs" && init?.method === "PUT") return json({ prefs: JSON.parse(String(init.body)) });
  if (path === "/agent-market/catalog")
    return json({
      agents: [
        { slug: "sran.expense-audit", version: "1.1.0", name: "ตรวจการเบิกค่าใช้จ่าย", status: "published", manifest: { short_name: "ตรวจค่าใช้จ่าย", icon: "expense" } },
        { slug: "sran.i-1", version: "1.0.0", name: "ปิดงบการเงิน", status: "published", manifest: { short_name: "ปิดงบการเงิน" } },
      ],
    });
  return json({ error: { code: "NOT_FOUND", message: "x" } }, 404);
}

beforeEach(() => {
  resetAgentMarketForTest();
  forgetHeldOrderForTest(); // (AM24c: each case a new page)
  raw.mockReset();
  useI18nStore.setState({ locale: "th" });
  rail = { installs: [], waiting_total: 0, prefs: {} };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function flagOn() {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ enabled: true }) }));
  raw.mockImplementation(async (path: string, init?: RequestInit) => routeWith(path, init));
}

describe("AgentRailSection", () => {
  it("flag off: renders nothing and never calls chat-edge", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ enabled: false }) });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<AgentRailSection />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/agent-market");
    // give the (absent) second step a chance to run
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
    expect(raw).not.toHaveBeenCalled();
  });

  it("no fetch at all (a test or an old shell): renders nothing and does not throw", async () => {
    vi.stubGlobal("fetch", undefined);
    const { container } = render(<AgentRailSection />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
    expect(raw).not.toHaveBeenCalled();
  });

  it("a failing flag request: renders nothing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to parse URL")));
    const { container } = render(<AgentRailSection />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
    expect(raw).not.toHaveBeenCalled();
  });

  it("flag on but the account is not allowed: renders nothing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ enabled: true }) }));
    raw.mockResolvedValue(json({ available: false, runner: { healthy: true } }));
    const { container } = render(<AgentRailSection />);
    await waitFor(() => expect(raw).toHaveBeenCalledWith("/agent-market/availability", expect.anything()));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.innerHTML).toBe("");
  });

  it("available: the market link and the installed agents, a waiting run counted", async () => {
    flagOn();
    rail = { installs: [row("i-1", "ปิดงบการเงิน", { waiting: 1, last_used_at: "2026-09-27T01:00:00Z" })], waiting_total: 1, prefs: {} };
    render(<AgentRailSection />);
    const section = await screen.findByTestId("agent-rail-section");
    expect(section.querySelector('a[href="/chat/agents"]')?.textContent).toContain("Agent Market");
    const item = await screen.findByTestId("agent-rail-install");
    expect(item.getAttribute("href")).toBe("/chat/agents/i/i-1");
    expect(item.getAttribute("aria-current")).toBe("page");
    expect(item.textContent).toContain("ปิดงบการเงิน");
    expect(item.textContent).toContain("รอคำตอบของคุณ");
    expect(screen.getByTestId("agent-rail-toggle").textContent).toContain("Agent ของฉัน (1)");
    expect(screen.getByTestId("agent-rail-waiting-total").textContent).toContain("1");
  });

  it("the workspace's own list of agents is named the same way (not “AGENT ของฉัน”)", () => {
    expect(amDictionaries.th["ws.myAgents"]).toBe("Agent ของฉัน");
    // AM11: the surface follows the app's language — there is an English dictionary beside the Thai one,
    // and the two name the list the same way in each language
    expect(Object.keys(amDictionaries).sort()).toEqual(["en", "th"]);
    expect(amDictionaries.en["ws.myAgents"]).toBe("My agents");
    expect(amDictionaries.en["market.mine"]).toBe(amDictionaries.en["ws.myAgents"]);
    expect(amDictionaries.th["market.mine"]).toBe(amDictionaries.th["ws.myAgents"]);
  });

  it("AM11: the section follows the app's language — Thai, English, and back, without a reload", async () => {
    flagOn();
    // the agent's own name is the person's data; kept in Latin letters here so every Thai letter on the screen is the app's
    rail = { installs: [row("i-1", "Month-end close", { waiting: 1 })], waiting_total: 1, prefs: {} };
    render(<AgentRailSection />);
    const toggle = await screen.findByTestId("agent-rail-toggle");
    expect(toggle.textContent).toContain("Agent ของฉัน (1)");
    act(() => useI18nStore.getState().setLocale("en"));
    expect(screen.getByTestId("agent-rail-toggle").textContent).toContain("My agents (1)");
    const section = screen.getByTestId("agent-rail-section");
    expect(section.getAttribute("aria-label")).toBe("Agent Market and installed agents");
    expect(section.textContent ?? "").not.toMatch(/[฀-๿]/);
    act(() => useI18nStore.getState().setLocale("th"));
    expect(screen.getByTestId("agent-rail-toggle").textContent).toContain("Agent ของฉัน (1)");
    expect(screen.getByTestId("agent-rail-section").getAttribute("aria-label")).toBe("Agent Market และ agent ที่ติดตั้ง");
  });

  it("pinned first, then recently used, about five — never alphabetical; a waiting agent past the cap still shows", async () => {
    flagOn();
    const t = (h: number) => `2026-09-27T${String(h).padStart(2, "0")}:00:00Z`;
    rail = {
      installs: [
        row("a", "ก ใช้นานแล้ว", { last_used_at: t(1) }),
        row("b", "ข ใช้ล่าสุด", { last_used_at: t(9) }),
        row("c", "ค ปักหมุดทีหลัง", { pinned_at: t(5), last_used_at: t(2) }),
        row("d", "ง ปักหมุดก่อน", { pinned_at: t(3), last_used_at: t(1) }),
        row("e", "จ", { last_used_at: t(8) }),
        row("f", "ฉ", { last_used_at: t(7) }),
        row("g", "ช", { last_used_at: t(6) }),
        row("h", "ซ รอคำตอบ", { last_used_at: t(0), waiting: 2 }),
      ],
      waiting_total: 2,
      prefs: {},
    };
    render(<AgentRailSection />);
    await screen.findAllByTestId("agent-rail-install");
    const titles = screen.getAllByTestId("agent-rail-install").map((a) => a.textContent ?? "");
    expect(titles.map((x) => x.slice(0, 1))).toEqual(["ง", "ค", "ข", "จ", "ฉ", "ซ"]);
    expect(titles[5]).toContain("2");
    const all = screen.getByTestId("agent-rail-all");
    expect(all.getAttribute("href")).toBe("/chat/agents/mine");
    expect(all.textContent).toContain("8");
  });

  it("collapsed: remembered per user, the waiting count on the header, and the waiting agent still listed", async () => {
    flagOn();
    rail = {
      installs: [row("x", "ไม่มีอะไรรอ", { last_used_at: "2026-09-27T09:00:00Z" }), row("y", "มีรอบรอคำตอบ", { waiting: 3 })],
      waiting_total: 3,
      prefs: { rail_collapsed: true },
    };
    render(<AgentRailSection />);
    const toggle = await screen.findByTestId("agent-rail-toggle");
    await waitFor(() => expect(toggle.getAttribute("aria-expanded")).toBe("false"));
    expect(screen.getByTestId("agent-rail-waiting-total").textContent).toContain("3");
    const rows = screen.getAllByTestId("agent-rail-install");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.textContent).toContain("มีรอบรอคำตอบ");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await waitFor(() => expect(screen.getAllByTestId("agent-rail-install")).toHaveLength(2));
    const put = raw.mock.calls.find((c) => c[0] === "/agent-market/prefs");
    expect(put && JSON.parse(String((put[1] as RequestInit).body))).toEqual({ rail_collapsed: false });
  });

  it("pin from the row", async () => {
    flagOn();
    rail = { installs: [row("i-1", "ปิดงบการเงิน")], waiting_total: 0, prefs: {} };
    raw.mockImplementation(async (path: string, init?: RequestInit) =>
      path === "/agent-market/installs/i-1/pin" ? json({ pinned_at: "2026-09-28T00:00:00Z" }) : routeWith(path, init),
    );
    render(<AgentRailSection />);
    const pin = await screen.findByTestId("agent-rail-pin");
    expect(pin.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(pin);
    await waitFor(() => expect(screen.getByTestId("agent-rail-pin").getAttribute("aria-pressed")).toBe("true"));
    expect(raw).toHaveBeenCalledWith("/agent-market/installs/i-1/pin", expect.objectContaining({ method: "PUT" }));
  });

  it("the sidebar's search text: installed agents first, then catalog agents not installed", async () => {
    flagOn();
    rail = { installs: [row("i-1", "ปิดงบการเงิน")], waiting_total: 0, prefs: {} };
    const { rerender } = render(<AgentRailSection search="" />);
    await screen.findByTestId("agent-rail-install");
    rerender(<AgentRailSection search="ตรวจ" />);
    const hit = await screen.findByTestId("agent-search-catalog");
    expect(hit.getAttribute("href")).toBe("/chat/agents/sran.expense-audit");
    expect(screen.queryByTestId("agent-search-installed")).toBeNull();
    rerender(<AgentRailSection search="ปิดงบ" />);
    const mine = await screen.findByTestId("agent-search-installed");
    expect(mine.getAttribute("href")).toBe("/chat/agents/i/i-1");
    expect(screen.queryByTestId("agent-search-catalog")).toBeNull();
    rerender(<AgentRailSection search="ไม่มีแน่นอน" />);
    expect(within(await screen.findByTestId("agent-search-none")).getByText(/ไม่พบ agent/)).toBeTruthy();
  });
});
