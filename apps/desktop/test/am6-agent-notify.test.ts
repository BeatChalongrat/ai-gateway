// AM6 — a reminder reaches someone who has closed the window. Closing the
// window hides it to the tray and the page stops polling, so the HOST asks
// chat-edge for the Agent Market notifications this surface has not shown
// (GET /api/v1/agent-market/notifications?surface=desktop, with the portal
// token it already holds), shows each ONCE as an OS notification carrying the
// server's own title and body, tells the server it was shown, and a click
// opens the main window on the notification's page — a path of the page's
// own origin, checked before use.
//
// Driven with the real ChatEdgeClient over a stubbed fetch, a fake toaster
// and a fake window: no Electron, no network.
//
// LIMITS — what this test cannot see:
//   * whether Windows draws the toast, and whether a click on a toast that
//     has moved to the Action Center still reaches the app;
//   * index.ts itself (it imports 'electron'): its wiring is pinned by the
//     source-text check at the bottom, which cannot see what runs.
import { afterEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";

import { AGENTS_HOME, AgentNotifier, agentPageUrl, safeAgentLink } from "../src/main/agent-notify";
import { ChatEdgeClient } from "../src/main/chat-edge-client";
import type { Logger } from "../src/main/logger";

const EDGE = "https://edge.test:4000";
const PAGE = "https://page.test:3002";
const TOKEN = "tok-never-in-a-log";
const LIST_URL = `${EDGE}/api/v1/agent-market/notifications?surface=desktop`;

interface Row {
  id: string;
  kind: string;
  title: string;
  body: string;
  link: string;
  install_id: string;
  run_id: string | null;
  reminder_id: string | null;
  agent_name: string;
  created_at: string;
}

function row(n: number, link = `/chat/agents/i/inst-${n}?thread=th-${n}&run=run-${n}`): Row {
  return {
    id: `n-${n}`,
    kind: "reminder_due",
    title: `ถึงเวลาปิดงบ ก.ย. 2569 แล้ว (${n})`,
    body: "ต้องใช้ 3 ไฟล์นี้: บัญชีแยกประเภท · งบทดลอง · งบประมาณ",
    link,
    install_id: `inst-${n}`,
    run_id: `run-${n}`,
    reminder_id: `rem-${n}`,
    agent_name: "ปิดงบรายเดือน",
    created_at: "2026-09-05T02:00:00Z",
  };
}

interface Sent {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: string | null;
}

/** A chat-edge that lists what this surface has not been told was shown. */
function harness(rows: unknown[], opts: { token?: string | null; listStatus?: number; listThrows?: boolean; shownStatus?: number; windowVisible?: boolean; canShow?: boolean } = {}) {
  const server = {
    rows,
    shown: new Set<string>(),
    listStatus: opts.listStatus ?? 200,
    listThrows: opts.listThrows ?? false,
    shownStatus: opts.shownStatus ?? 204,
  };
  const requests: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init: RequestInit = {}) => {
      const method = (init.method ?? "GET").toUpperCase();
      const url = String(input);
      requests.push({ method, url, headers: (init.headers ?? {}) as Record<string, string>, body: typeof init.body === "string" ? init.body : null });
      if (method === "GET") {
        if (server.listThrows) throw new TypeError("fetch failed");
        if (server.listStatus !== 200) return new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "ไม่พบ" } }), { status: server.listStatus });
        const pending = server.rows.filter((r) => !server.shown.has((r as { id?: string } | null)?.id ?? ""));
        return new Response(JSON.stringify({ notifications: pending }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      const m = /\/notifications\/([^/]+)\/shown$/.exec(url);
      if (server.shownStatus === 204) {
        if (m) server.shown.add(decodeURIComponent(m[1]!));
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify({ error: { code: "INTERNAL", message: "x" } }), { status: server.shownStatus });
    }),
  );
  const session = { token: opts.token === undefined ? TOKEN : opts.token };
  const toasts: { title: string; body: string; click: () => void }[] = [];
  const win = { visible: opts.windowVisible ?? true, shows: 0, loaded: [] as string[] };
  const lines: string[] = [];
  const log = { info: (m: string) => lines.push(m), warn: (m: string) => lines.push(m), error: (m: string) => lines.push(m) } as unknown as Logger;
  const notifier = new AgentNotifier({
    feed: new ChatEdgeClient(EDGE, () => session.token, log),
    toaster: {
      show: (title, body, onClick) => {
        if (opts.canShow === false) return false;
        toasts.push({ title, body, click: onClick });
        return true;
      },
    },
    window: {
      show: () => {
        win.shows += 1;
        win.visible = true;
      },
      load: (url) => win.loaded.push(url),
    },
    pageOrigin: PAGE,
    isSignedIn: () => session.token !== null,
    log,
  });
  return {
    notifier,
    server,
    requests,
    session,
    toasts,
    win,
    lines,
    gets: () => requests.filter((r) => r.method === "GET"),
    posts: () => requests.filter((r) => r.method === "POST"),
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("AM6 — the host asks for the notifications and shows each once", () => {
  it("asks chat-edge for this surface's notifications with the session the host holds", async () => {
    const h = harness([]);
    await h.notifier.tick();
    expect(h.requests).toHaveLength(1);
    expect(h.requests[0]!.method).toBe("GET");
    expect(h.requests[0]!.url).toBe(LIST_URL);
    expect(h.requests[0]!.headers["Authorization"]).toBe(`Bearer ${TOKEN}`);
    expect(h.toasts).toHaveLength(0);
  });

  it("shows a notification with the server's title and body, exactly, and says it was shown", async () => {
    const r = row(1);
    const h = harness([r]);
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(1);
    expect(h.toasts[0]!.title).toBe(r.title);
    expect(h.toasts[0]!.body).toBe(r.body);
    expect(h.posts()).toHaveLength(1);
    expect(h.posts()[0]!.url).toBe(`${EDGE}/api/v1/agent-market/notifications/n-1/shown`);
    expect(h.posts()[0]!.headers["Authorization"]).toBe(`Bearer ${TOKEN}`);
    expect(JSON.parse(h.posts()[0]!.body ?? "null")).toEqual({ surface: "desktop" });
  });

  it("does not show it again at the next tick", async () => {
    const h = harness([row(1)]);
    await h.notifier.tick();
    await h.notifier.tick();
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(1);
    expect(h.gets()).toHaveLength(3);
    expect(h.posts()).toHaveLength(1);
  });

  it("does not show it again when the 'shown' POST failed, and says 'shown' again until the server has it", async () => {
    const h = harness([row(1)], { shownStatus: 500 });
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(1);
    expect(h.posts()).toHaveLength(1);
    // The server never heard: it lists the notification again.
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(1);
    expect(h.posts()).toHaveLength(2);
    h.server.shownStatus = 204;
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(1);
    expect(h.server.shown.has("n-1")).toBe(true);
    await h.notifier.tick();
    expect(h.posts()).toHaveLength(3); // the server has it: nothing more to say
  });

  it("does not show it again when the 'shown' POST never reached the server", async () => {
    const h = harness([row(1)]);
    const answer = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: unknown, init: RequestInit = {}) => {
        if ((init.method ?? "GET") === "POST") {
          h.requests.push({ method: "POST", url: String(input), headers: {}, body: null });
          throw new TypeError("fetch failed");
        }
        return answer(input as string, init);
      }),
    );
    await expect(h.notifier.tick()).resolves.toBeUndefined();
    await expect(h.notifier.tick()).resolves.toBeUndefined();
    expect(h.toasts).toHaveLength(1);
  });

  it("shows at most 3 in one tick; the rest wait for the next", async () => {
    const rows = [1, 2, 3, 4, 5].map((n) => row(n));
    const h = harness(rows);
    await h.notifier.tick();
    expect(h.toasts.map((t) => t.title)).toEqual(rows.slice(0, 3).map((r) => r.title));
    expect(h.posts().map((p) => p.url)).toEqual(["n-1", "n-2", "n-3"].map((id) => `${EDGE}/api/v1/agent-market/notifications/${id}/shown`));
    await h.notifier.tick();
    expect(h.toasts.map((t) => t.title)).toEqual(rows.map((r) => r.title));
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(5);
  });

  it("at most 3 in one tick also when every 'shown' POST fails", async () => {
    const h = harness([1, 2, 3, 4, 5].map((n) => row(n)), { shownStatus: 503 });
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(3);
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(5);
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(5);
  });

  it("a row without an id, a title or a body is not shown", async () => {
    const good = row(4);
    const h = harness([{ ...row(1), id: "" }, { ...row(2), title: 7 }, { id: "n-3", title: "x", link: "/chat/agents/i/3" }, null, "text", good]);
    await h.notifier.tick();
    expect(h.toasts.map((t) => t.title)).toEqual([good.title]);
    expect(h.posts().map((p) => p.url)).toEqual([`${EDGE}/api/v1/agent-market/notifications/n-4/shown`]);
  });

  it("when the OS cannot show a notification, nothing is reported as shown", async () => {
    const h = harness([row(1)], { canShow: false });
    await h.notifier.tick();
    expect(h.gets()).toHaveLength(1);
    expect(h.toasts).toHaveLength(0);
    expect(h.posts()).toHaveLength(0);
  });
});

describe("AM6 — when there is nothing to ask, or nobody to ask", () => {
  it("not signed in → no request", async () => {
    const h = harness([row(1)], { token: null });
    await h.notifier.tick();
    await h.notifier.tick();
    expect(h.requests).toHaveLength(0);
    expect(h.toasts).toHaveLength(0);
    expect(h.lines).toHaveLength(0);
    // The same host, signed in: the request is made.
    h.session.token = TOKEN;
    await h.notifier.tick();
    expect(h.gets()).toHaveLength(1);
  });

  it("404 (Agent Market off, or an older server) → nothing shown and no further request in this session of the app", async () => {
    const h = harness([row(1)], { listStatus: 404 });
    for (let i = 0; i < 5; i++) await h.notifier.tick();
    expect(h.requests).toHaveLength(1);
    expect(h.toasts).toHaveLength(0);
    expect(h.win.shows).toBe(0);
    expect(h.lines.length).toBeLessThanOrEqual(1); // never a line per tick
    // A server that answers later does not bring the poll back before a restart.
    h.server.listStatus = 200;
    await h.notifier.tick();
    h.notifier.onSignedIn();
    await h.notifier.tick();
    expect(h.requests).toHaveLength(1);
  });

  it.each([500, 502, 401, 429])("HTTP %i → nothing shown, asks again at the next tick", async (status) => {
    const h = harness([row(1)], { listStatus: status });
    await expect(h.notifier.tick()).resolves.toBeUndefined();
    await expect(h.notifier.tick()).resolves.toBeUndefined();
    expect(h.gets()).toHaveLength(2);
    expect(h.toasts).toHaveLength(0);
    expect(h.win.shows).toBe(0);
    h.server.listStatus = 200;
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(1);
  });

  it("the server cannot be reached → nothing shown, asks again at the next tick, never throws", async () => {
    const h = harness([row(1)], { listThrows: true });
    await expect(h.notifier.tick()).resolves.toBeUndefined();
    await expect(h.notifier.tick()).resolves.toBeUndefined();
    await expect(h.notifier.tick()).resolves.toBeUndefined();
    expect(h.gets()).toHaveLength(3);
    expect(h.toasts).toHaveLength(0);
    expect(h.lines.length).toBeLessThanOrEqual(1); // an outage is one line, not one a minute
    h.server.listThrows = false;
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(1);
  });

  it("no log line carries the token, a title or a body", async () => {
    const h = harness([row(1), row(2)], { shownStatus: 500 });
    await h.notifier.tick();
    h.server.listStatus = 500;
    await h.notifier.tick();
    h.server.listStatus = 404;
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(2);
    expect(h.lines.length).toBeGreaterThan(0);
    const all = h.lines.join("\n");
    expect(all).not.toContain(TOKEN);
    expect(all).not.toContain("ปิดงบ");
    expect(all).not.toContain("บัญชีแยกประเภท");
  });
});

const BAD_LINKS: [string, unknown][] = [
  ["another site", "https://evil.example/x"],
  ["a protocol-relative address", "//evil.example"],
  ["a protocol-relative address further in", "/chat/agents//evil.example"],
  ["dot segments that leave the agents pages", "/chat/agents/../../settings"],
  ["encoded dot segments that leave the agents pages", "/chat/agents/%2e%2e/%2E%2e/settings"],
  ["dot segments that end on the parent", "/chat/agents/.."],
  ["a backslash", "/chat/agents/i/1\\@evil"],
  ["a script", "javascript:alert(1)"],
  ["a newline", "/chat/agents/i/1\nhttps://evil.example"],
  ["a carriage return", "/chat/agents/i/1\r"],
  ["a tab", "/chat/agents/\ti/1"],
  ["a NUL", "/chat/agents/i/1\u0000"],
  ["a scheme in the query", "/chat/agents/i/1?next=https://evil.example"],
  ["a scheme without slashes in the query", "/chat/agents/i/1?next=mailto:a@evil.example"],
  ["a scheme in front of the path", "http:/chat/agents/i/1"],
  ["another page of the app", "/chat/settings"],
  ["a look-alike prefix", "/chat/agentsevil/i/1"],
  ["the agents home without a page under it", "/chat/agents"],
  ["a leading space", " /chat/agents/i/1"],
  ["a relative path", "chat/agents/i/1"],
  ["an empty link", ""],
  ["no link", undefined],
  ["null", null],
  ["a number", 42],
  ["an object", { href: "/chat/agents/i/1" }],
  ["a link longer than any page address", `/chat/agents/i/${"a".repeat(3000)}`],
];

describe("AM6 — the link is checked before it is used", () => {
  it("a path of the agents pages is kept as it came", () => {
    expect(safeAgentLink("/chat/agents/i/8f0e?thread=2b1c&run=77aa")).toBe("/chat/agents/i/8f0e?thread=2b1c&run=77aa");
    expect(safeAgentLink("/chat/agents/i/8f0e")).toBe("/chat/agents/i/8f0e");
  });

  it("dot segments are resolved first, and the result decides", () => {
    expect(safeAgentLink("/chat/agents/i/1/../2?thread=t")).toBe("/chat/agents/i/2?thread=t");
    expect(safeAgentLink("/chat/agents/./i/1")).toBe("/chat/agents/i/1");
    expect(safeAgentLink("/chat/agents/../../settings")).toBe(AGENTS_HOME);
    expect(safeAgentLink("/chat/agents/i/../../agents/i/1")).toBe("/chat/agents/i/1");
  });

  it.each(BAD_LINKS)("%s → the agents home", (_name, link) => {
    expect(AGENTS_HOME).toBe("/chat/agents");
    expect(safeAgentLink(link)).toBe("/chat/agents");
  });

  it.each(BAD_LINKS)("%s → the address stays on the page's origin", (_name, link) => {
    const url = agentPageUrl(PAGE, link);
    expect(url).toBe(`${PAGE}/chat/agents`);
    expect(new URL(url).origin).toBe(PAGE);
  });

  it("a page base that ends in a slash, or carries a path, still gives the page's origin + the link", () => {
    expect(agentPageUrl(`${PAGE}/`, "/chat/agents/i/1")).toBe(`${PAGE}/chat/agents/i/1`);
    expect(agentPageUrl(`${PAGE}/chat`, "/chat/agents/i/1")).toBe(`${PAGE}/chat/agents/i/1`);
  });
});

describe("AM6 — a click opens the main window on the notification's page", () => {
  it("a good link → the window is shown and navigated to the page's origin + the link", async () => {
    const r = row(1, "/chat/agents/i/8f0e?thread=2b1c&run=77aa");
    const h = harness([r], { windowVisible: false });
    await h.notifier.tick();
    expect(h.win.shows).toBe(0); // showing a notification does not raise the window
    expect(h.win.loaded).toEqual([]);
    h.toasts[0]!.click();
    expect(h.win.shows).toBe(1);
    expect(h.win.visible).toBe(true);
    expect(h.win.loaded).toEqual([`${PAGE}/chat/agents/i/8f0e?thread=2b1c&run=77aa`]);
  });

  it.each(BAD_LINKS)("%s → the window opens on /chat/agents and the link is never loaded", async (_name, link) => {
    const h = harness([{ ...row(1), link }], { windowVisible: false });
    await h.notifier.tick();
    expect(h.toasts).toHaveLength(1); // the reminder itself is still the server's to give
    h.toasts[0]!.click();
    expect(h.win.shows).toBe(1);
    expect(h.win.loaded).toEqual([`${PAGE}/chat/agents`]);
    expect(h.win.loaded.join(" ")).not.toMatch(/evil|javascript|settings|\\|\s/);
  });

  it("a link that normalises inside the agents pages is loaded in its normal form", async () => {
    const h = harness([row(1, "/chat/agents/i/1/../2?thread=t")]);
    await h.notifier.tick();
    h.toasts[0]!.click();
    expect(h.win.loaded).toEqual([`${PAGE}/chat/agents/i/2?thread=t`]);
  });

  it("each notification opens its own page", async () => {
    const h = harness([row(1), row(2)]);
    await h.notifier.tick();
    h.toasts[1]!.click();
    h.toasts[0]!.click();
    expect(h.win.loaded).toEqual([`${PAGE}/chat/agents/i/inst-2?thread=th-2&run=run-2`, `${PAGE}/chat/agents/i/inst-1?thread=th-1&run=run-1`]);
  });
});

// setImmediate stays real so that a Response body read under fake timers can finish.
const FAKED = ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] as const;
async function advance(ms: number): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
  for (let i = 0; i < 5; i++) await new Promise<void>((r) => setImmediate(r));
}

describe("AM6 — the poll runs for as long as the process is alive", () => {
  it("asks shortly after start-up and then every 60 seconds, while the window is hidden", async () => {
    vi.useFakeTimers({ toFake: [...FAKED] });
    const h = harness([], { windowVisible: false });
    h.notifier.start();
    expect(h.gets()).toHaveLength(0);
    await advance(30_000);
    expect(h.gets()).toHaveLength(1); // shortly after start-up
    await advance(10 * 60_000);
    expect(h.gets().length).toBeGreaterThanOrEqual(10);
    expect(h.gets().length).toBeLessThanOrEqual(11);
    expect(h.win.visible).toBe(false);
    expect(h.win.shows).toBe(0);
    h.notifier.stop();
  });

  it("a notification that falls due while the window is hidden is shown then", async () => {
    vi.useFakeTimers({ toFake: [...FAKED] });
    const h = harness([], { windowVisible: false });
    h.notifier.start();
    await advance(5 * 60_000);
    expect(h.toasts).toHaveLength(0);
    const r = row(9);
    h.server.rows.push(r);
    await advance(60_000);
    expect(h.toasts.map((t) => t.title)).toEqual([r.title]);
    expect(h.win.visible).toBe(false);
    await advance(5 * 60_000);
    expect(h.toasts).toHaveLength(1);
    h.notifier.stop();
  });

  it("asks shortly after sign-in, without waiting for the minute", async () => {
    vi.useFakeTimers({ toFake: [...FAKED] });
    const h = harness([row(1)], { token: null });
    h.notifier.start();
    await advance(5 * 60_000 + 1_000);
    expect(h.requests).toHaveLength(0);
    h.session.token = TOKEN;
    h.notifier.onSignedIn();
    await advance(10_000);
    expect(h.gets()).toHaveLength(1);
    expect(h.toasts).toHaveLength(1);
    h.notifier.stop();
  });

  it("signed out again → the requests stop; the timer does not", async () => {
    vi.useFakeTimers({ toFake: [...FAKED] });
    const h = harness([]);
    h.notifier.start();
    await advance(2 * 60_000);
    const before = h.gets().length;
    expect(before).toBeGreaterThanOrEqual(2);
    h.session.token = null;
    await advance(5 * 60_000);
    expect(h.gets()).toHaveLength(before);
    h.session.token = TOKEN;
    await advance(60_000);
    expect(h.gets()).toHaveLength(before + 1);
    h.notifier.stop();
  });

  it("404 ends the timer for this session of the app", async () => {
    vi.useFakeTimers({ toFake: [...FAKED] });
    const h = harness([row(1)], { listStatus: 404 });
    h.notifier.start();
    const atStart = h.lines.length;
    await advance(30 * 60_000);
    expect(h.requests).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
    expect(h.lines.length - atStart).toBeLessThanOrEqual(1);
  });

  it("stop() ends the polling", async () => {
    vi.useFakeTimers({ toFake: [...FAKED] });
    const h = harness([]);
    h.notifier.start();
    await advance(60_000);
    const before = h.gets().length;
    expect(before).toBeGreaterThanOrEqual(1);
    h.notifier.stop();
    expect(vi.getTimerCount()).toBe(0);
    await advance(10 * 60_000);
    expect(h.gets()).toHaveLength(before);
  });
});

describe("AM6 — wiring (source text)", () => {
  const MAIN = path.resolve(__dirname, "..", "src", "main");
  const read = (name: string) => (fs.existsSync(path.join(MAIN, name)) ? fs.readFileSync(path.join(MAIN, name), "utf8") : "");

  it("the poller has no Electron in it, opens no browser and shows no dialog", () => {
    const src = read("agent-notify.ts");
    expect(src).not.toBe("");
    expect(src).not.toMatch(/from ["']electron["']|require\(["']electron["']\)/);
    expect(src).not.toMatch(/openExternal|showMessageBox|showErrorBox/);
  });

  it("index.ts builds the poller on the chat-edge client, starts it, and stops it on quit", () => {
    const src = read("index.ts");
    expect(src).toMatch(/new AgentNotifier\(\{[\s\S]{0,200}feed:\s*chatEdge\b/);
    expect(src).toMatch(/agentNotifier\.start\(\)/);
    expect(src).toMatch(/agentNotifier\?\.stop\(\)/);
    expect(src).toMatch(/agentNotifier\?\.onSignedIn\(\)/);
  });

  it("index.ts shows the server's title and body and nothing else, and a click goes through the main window", () => {
    const src = read("index.ts");
    expect(src).toMatch(/new Notification\(\{\s*title,\s*body\s*\}\)/);
    expect(src).toMatch(/window:\s*\{\s*show:\s*\(\)\s*=>\s*showMainWindow\(\),\s*load:\s*\(url\)\s*=>\s*navigateMain\(url\)\s*\}/);
  });
});
