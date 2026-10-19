// AM24c — nothing moves under the pointer. The owner clicked between his agents and the list went "ไปๆ มาๆ":
//   * the rail's order is settled at the page's first read and held for the page's life — a click, the workspace's
//     own read, two polls that bring new recency and a return to the window move no row, while titles, counts and
//     badges stay fresh; a new page (a reload) arranges them by recency again;
//   * pin and unpin, install and uninstall move rows, because the person asked — and only the row asked about;
//   * "opened" means opened for every row: what a person opened stays open behind them, what they closed stays
//     closed, and an agent with no recorded state opens once, the first time they go into it.
// Every case here was red on the code before AM24c; the pin case — red there only for where an unpinned row goes — is the
// thing that must still move, red only when the hold forgets the pin.
// The rows' vertical offsets, measured in a browser, are in tests/e2e/am24c-rail-holds-still.spec.ts.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { raw, nav } = vi.hoisted(() => ({ raw: vi.fn(), nav: { pathname: "/chat/agents/i/a" } }));
vi.mock("@/lib/api", () => ({
  api: { raw },
  CHAT_API_BASE: "http://chat-edge.test/api/v1",
  sharedRefresh: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(""),
}));

import { useI18nStore } from "@/lib/i18n";

import { AgentRailSection } from "./AgentRailSection";
import type { RailInstall } from "./types";
import { notifyInstallsChanged, resetAgentMarketForTest } from "./useAgentMarket";
import { forgetHeldOrderForTest, togglePin } from "./useRail";

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(body) });
const at = (m: number) => `2026-10-19T08:${String(m).padStart(2, "0")}:00Z`;
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

/** What GET /rail answers now — a test changes it between reads, as the server would. */
let installs: RailInstall[];
const railReads = () => raw.mock.calls.filter((c) => c[0] === "/agent-market/rail").length;

beforeEach(() => {
  resetAgentMarketForTest();
  forgetHeldOrderForTest();
  raw.mockReset();
  useI18nStore.setState({ locale: "en" });
  nav.pathname = "/chat/agents/i/a";
  window.localStorage.clear();
  window.sessionStorage.clear();
  installs = [
    row("a", "Alpha", { last_used_at: at(40) }),
    row("b", "Bravo", { last_used_at: at(30) }),
    row("c", "Charlie", { last_used_at: at(20) }),
    row("d", "Delta", { last_used_at: at(10) }),
  ];
  raw.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "/agent-market/availability") return json({ available: true, runner: { healthy: true } });
    if (path === "/agent-market/rail")
      return json({ installs, waiting_total: installs.reduce((n, r) => n + r.waiting, 0), prefs: {}, shared_unopened: 0 });
    if (path.endsWith("/pin")) return json({ pinned_at: init?.method === "PUT" ? "2026-10-19T09:00:00Z" : null });
    if (path.endsWith("/threads")) return json({ threads: [] });
    return json({ error: { code: "NOT_FOUND", message: "x" } }, 404);
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ enabled: true }) })));
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** The agents in the sidebar, top to bottom, by install id. */
const order = () => screen.getAllByTestId("agent-rail-install").map((a) => (a.getAttribute("href") ?? "").split("/").pop());
const rowOf = (id: string) =>
  screen.getAllByTestId("agent-rail-row").find((li) => within(li).getByTestId("agent-rail-install").getAttribute("href") === `/chat/agents/i/${id}`)!;
const isOpen = (id: string) => rowOf(id).getAttribute("data-chats-open") === "true";
const badge = (id: string) => within(rowOf(id)).queryByTestId("agent-rail-waiting")?.textContent ?? "";
const go = (id: string, rerender: (ui: ReactElement) => void) => {
  nav.pathname = `/chat/agents/i/${id}`;
  rerender(<AgentRailSection />);
};
const set = (id: string, change: Partial<RailInstall>) => {
  installs = installs.map((r) => (r.id === id ? { ...r, ...change } : r));
};

describe("AM24c: the order is settled once and held", () => {
  it("a click moves only the highlight; two polls with new recency and a return to the window move no row — the badges are fresh", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { rerender } = render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));

    go("c", rerender);
    expect(order()).toEqual(["a", "b", "c", "d"]);
    expect(screen.getAllByTestId("agent-rail-install")[2]!.getAttribute("aria-current")).toBe("page");

    // the server now says Charlie was opened just now — the rail's order rule would put it first — and Bravo waits
    set("c", { last_used_at: at(59) });
    set("b", { waiting: 1 });
    const reads = railReads();
    act(() => void vi.advanceTimersByTime(30_000));
    await waitFor(() => expect(badge("b")).toContain("1"));
    set("a", { waiting: 2 });
    act(() => void vi.advanceTimersByTime(30_000));
    await waitFor(() => expect(badge("a")).toContain("2"));
    expect(railReads()).toBe(reads + 2);
    expect(order()).toEqual(["a", "b", "c", "d"]);

    // alt-tab away and back: the rail is read again; the badge goes, the rows stay
    set("a", { waiting: 0 });
    set("d", { last_used_at: at(58), title: "Delta (renamed)" });
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(badge("a")).toBe(""));
    expect(screen.getByText("Delta (renamed)")).toBeTruthy();
    expect(order()).toEqual(["a", "b", "c", "d"]);
  });

  it("the workspace's own read of the rail a second after arriving (what moved the row on live) moves no row", async () => {
    const { rerender } = render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));
    // WorkspacePage: the server marks Charlie opened, its first round loads, and 800 ms later the list is read again
    go("c", rerender);
    set("c", { last_used_at: at(59) });
    const reads = railReads();
    act(() => notifyInstallsChanged());
    await waitFor(() => expect(railReads()).toBe(reads + 1));
    await new Promise((r) => setTimeout(r, 50));
    expect(order()).toEqual(["a", "b", "c", "d"]);
  });

  it("the order lives as long as the page: the rail mounted again keeps it, a new page takes the recency, nothing is stored", async () => {
    const first = render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));
    set("d", { last_used_at: at(59) });
    first.unmount();

    // the sidebar mounted again in the same page
    render(<AgentRailSection />);
    await waitFor(() => expect(railReads()).toBe(2));
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));

    // a reload: a new page
    cleanup();
    forgetHeldOrderForTest();
    render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["d", "a", "b", "c"]));
    // the order is not kept anywhere a reload would find it (the open/closed state is, on purpose)
    const kept = (st: Storage) => Array.from({ length: st.length }, (_, i) => st.key(i));
    expect(kept(window.sessionStorage)).toEqual([]);
    expect(kept(window.localStorage).filter((k) => k !== "am.rail.chats")).toEqual([]);
  });

  it("a new install arrives in a poll: it appears, at the end, and no existing row moves", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));
    // installed and opened on another device a moment ago, while Charlie was used here
    installs = [row("e", "Echo", { last_used_at: at(59) }), ...installs];
    set("c", { last_used_at: at(58) });
    act(() => void vi.advanceTimersByTime(30_000));
    await waitFor(() => expect(order()).toContain("e"));
    expect(order()).toEqual(["a", "b", "c", "d", "e"]);
  });

  it("past the cap of five: a new install is listed after the five, and the sixth stays behind 'see all'", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    installs = [...installs, row("e", "Echo", { last_used_at: at(5) }), row("f", "Foxtrot", { last_used_at: at(1) })];
    render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d", "e"]));
    expect(screen.getByTestId("agent-rail-all").textContent).toContain("6");
    installs = [row("g", "Golf", { last_used_at: at(59) }), ...installs];
    act(() => void vi.advanceTimersByTime(30_000));
    await waitFor(() => expect(order()).toContain("g"));
    expect(order()).toEqual(["a", "b", "c", "d", "e", "g"]);
    expect(screen.getByTestId("agent-rail-all").textContent).toContain("7");
  });

  it("an agent past the five that starts waiting for an answer is listed after the others — never hidden, nothing moves", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    installs = [...installs, row("e", "Echo", { last_used_at: at(5) }), row("f", "Foxtrot", { last_used_at: at(1) })];
    render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d", "e"]));
    set("f", { waiting: 1 });
    set("e", { last_used_at: at(59) });
    act(() => void vi.advanceTimersByTime(30_000));
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d", "e", "f"]));
    expect(badge("f")).toContain("1");
    // answered: it stays where it is for the session — a row does not leave under the pointer either
    set("f", { waiting: 0 });
    act(() => void vi.advanceTimersByTime(30_000));
    await waitFor(() => expect(badge("f")).toBe(""));
    expect(order()).toEqual(["a", "b", "c", "d", "e", "f"]);
  });

  it("the agent on screen, reached through 'see all', is listed after the others at once", async () => {
    installs = [...installs, row("e", "Echo", { last_used_at: at(5) }), row("f", "Foxtrot", { last_used_at: at(1) })];
    const { rerender } = render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d", "e"]));
    go("f", rerender);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d", "e", "f"]));
    expect(rowOf("f").querySelector('[aria-current="page"]')).toBeTruthy();
    go("a", rerender);
    expect(order()).toEqual(["a", "b", "c", "d", "e", "f"]);
  });

  it("an uninstall: that row goes and the others keep their places", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));
    installs = installs.filter((r) => r.id !== "b");
    set("d", { last_used_at: at(59) });
    act(() => void vi.advanceTimersByTime(30_000));
    await waitFor(() => expect(order()).not.toContain("b"));
    expect(order()).toEqual(["a", "c", "d"]);
  });

  it("pin: the pinned agent moves to the top at once — the things that must move still move; unpin leaves it below the pins", async () => {
    render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));
    fireEvent.click(within(rowOf("c")).getByTestId("agent-rail-pin"));
    expect(order()).toEqual(["c", "a", "b", "d"]);
    set("c", { pinned_at: "2026-10-19T09:00:00Z" });
    await waitFor(() => expect(within(rowOf("c")).getByTestId("agent-rail-pin").getAttribute("aria-pressed")).toBe("true"));
    expect(order()).toEqual(["c", "a", "b", "d"]);

    // the workspace's own pin (WorkspacePage: togglePin, then the list read again) moves the sidebar's row too
    set("d", { pinned_at: "2026-10-19T09:05:00Z" });
    await act(async () => {
      await togglePin("d", false);
      notifyInstallsChanged();
    });
    await waitFor(() => expect(order()).toEqual(["c", "d", "a", "b"]));

    // unpin: the row goes to the first place below the pins — the person's pointer is on it
    set("c", { pinned_at: null });
    fireEvent.click(within(rowOf("c")).getByTestId("agent-rail-pin"));
    await waitFor(() => expect(within(rowOf("c")).getByTestId("agent-rail-pin").getAttribute("aria-pressed")).toBe("false"));
    expect(order()).toEqual(["d", "c", "a", "b"]);
  });
});

describe("AM24c: opened means opened, for every row", () => {
  it("an agent opened stays open after the person goes to another", async () => {
    const { rerender } = render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));
    // Alpha is on screen with no recorded state: it opens, once, and that is recorded
    await waitFor(() => expect(isOpen("a")).toBe(true));
    go("b", rerender);
    expect(isOpen("b")).toBe(true);
    expect(isOpen("a")).toBe(true);
    // one opened by its arrow while elsewhere stays open when the person goes into it
    fireEvent.click(within(rowOf("d")).getByTestId("agent-rail-chats-toggle"));
    expect(isOpen("d")).toBe(true);
    go("d", rerender);
    expect(isOpen("d")).toBe(true);
  });

  it("the agent on screen, collapsed, stays collapsed when the person goes elsewhere and when they come back", async () => {
    const { rerender } = render(<AgentRailSection />);
    await waitFor(() => expect(isOpen("a")).toBe(true));
    fireEvent.click(within(rowOf("a")).getByTestId("agent-rail-chats-toggle"));
    expect(isOpen("a")).toBe(false);
    go("b", rerender);
    expect(isOpen("a")).toBe(false);
    go("a", rerender);
    expect(isOpen("a")).toBe(false);
  });

  it("an agent never opened and not on screen stays closed; what was recorded survives a reload", async () => {
    const first = render(<AgentRailSection />);
    await waitFor(() => expect(isOpen("a")).toBe(true));
    expect(isOpen("c")).toBe(false);
    fireEvent.click(within(rowOf("a")).getByTestId("agent-rail-chats-toggle"));
    fireEvent.click(within(rowOf("c")).getByTestId("agent-rail-chats-toggle"));
    first.unmount();
    forgetHeldOrderForTest(); // a reload
    nav.pathname = "/chat";
    render(<AgentRailSection />);
    await waitFor(() => expect(order()).toEqual(["a", "b", "c", "d"]));
    expect(isOpen("a")).toBe(false);
    expect(isOpen("c")).toBe(true);
    expect(isOpen("b")).toBe(false);
  });
});
