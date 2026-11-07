// AM24c — nothing moves under the pointer, measured in a browser. The owner clicked between his agents in the sidebar
// and the list went "ไปๆ มาๆ": the agent he arrived at expanded and the one he left collapsed, so rows above the
// pointer moved in the same frame; and about a second later the row he had clicked jumped to the top — the server
// wrote its last_opened_at, and the workspace re-read the rail once its first round loaded (on live every install
// has one, its self-test) — as did the 30-second poll and his return to the window. jsdom has no layout, so this spec
// measures where the rows are (getBoundingClientRect via boundingBox) in the real app, with a server that behaves
// as live's does:
//   * click row 3: rows 1 and 2 are at the same vertical offset — the agent left behind stays open, the one arrived
//     at opens below — after the workspace's own re-read of the rail has come and gone;
//   * the window regains focus with new recency and a waiting badge in the answer: every row is where it was, and
//     the badges are fresh (the section header's count and a row's badge change no height);
//   * the order lives as long as the page: a reload arranges the agents by recency again.
// The gate runs it (lane web_rail_browser). Like the other specs here, every backend call is mocked: no services.

import { expect, test, type Page, type Route } from "@playwright/test";

const APP = "http://localhost:3102";
const at = (m: number) => `2026-01-01T08:${String(m).padStart(2, "0")}:00Z`;
const row = (id: string, title: string, last: number, waiting = 0) => ({
  id,
  slug: `sran.${id}`,
  title,
  icon: "chart",
  version: "1.0.0",
  pinned_at: null,
  last_used_at: at(last),
  waiting,
  disabled: false,
});
const chat = (id: string | null, title: string) => ({
  id,
  title,
  created_at: at(1),
  last_at: at(2),
  rounds: 1,
  waiting: false,
  pinned_at: null,
  named: true,
});

/** What GET /rail answers now; the spec changes it as the server would. */
let installs = [row("a", "Alpha", 40), row("b", "Bravo", 30), row("c", "Charlie", 20), row("d", "Delta", 10)];
let railReads = 0;
/** Each agent's chat holds one finished round — on live, the self-test every install runs. */
const selfTest = (id: string) => ({
  id: `p-${id}`,
  install_id: id,
  kind: "progress",
  run_id: `r-${id}`,
  payload: { run_id: `r-${id}`, kind: "self_test", status: "succeeded", outcome: "passed" },
  rev: 1,
  created_at: at(1),
});

async function mockServices(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("sran-chat-locale", "en");
  });
  await page.route("**/*", async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (url.origin === APP) {
      if (path === "/api/agent-market") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ enabled: true }) });
      return route.continue();
    }
    let status = 200;
    let body: unknown = {};
    if (path === "/portal/me") body = { id: "am24c-reader", email: "am24c@example.com", tenant_id: "am24c-tenant", tenant_role: "member" };
    else if (path.endsWith("/models")) body = { models: [{ id: "m", name: "Test", provider: "mock", capabilities: ["chat"], allowed: true, context_window: 200000 }] };
    else if (path.endsWith("/projects")) body = { projects: [] };
    else if (path.endsWith("/usage/me")) body = { credits: { total: 1000, used: 0, remaining: 1000 }, usage_by_model: [] };
    else if (path.endsWith("/conversations")) body = { conversations: [], has_more: false };
    else if (path.endsWith("/memories/pending-count")) body = { count: 0 };
    else if (path.endsWith("/agent-market/availability")) body = { available: true, runner: { healthy: true } };
    else if (path.endsWith("/agent-market/rail")) {
      if (req.method() === "GET") railReads += 1;
      body = { installs, waiting_total: installs.reduce((n, r) => n + r.waiting, 0), prefs: {}, shared_unopened: 0 };
    } else if (/\/agent-market\/installs\/[a-d]\/threads$/.test(path)) body = { threads: [chat(null, "First chat"), chat("t-2", "Second chat")] };
    else if (/\/agent-market\/installs\/[a-d]\/thread$/.test(path)) body = { items: [selfTest(path.split("/").at(-2)!)], cursor: 1 };
    else if (path.endsWith("/opened") && req.method() === "POST") {
      // as live's server does: opening an agent makes it the most recently used
      const id = path.split("/").at(-2);
      installs = installs.map((r) => (r.id === id ? { ...r, last_used_at: new Date().toISOString() } : r));
      body = { ok: true };
    } else if (path.endsWith("/opened")) body = { ok: true };
    else if (path.includes("/agent-market/")) {
      status = 404;
      body = { error: { code: "NOT_FOUND", message: "not in this spec" } };
    }
    await route.fulfill({
      status: req.method() === "OPTIONS" ? 204 : status,
      headers: {
        "access-control-allow-origin": req.headers().origin || "*",
        "access-control-allow-credentials": "true",
        "access-control-allow-headers": "content-type, authorization, x-user-id, x-csrf-token",
        "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
      },
      contentType: "application/json",
      body: req.method() === "OPTIONS" ? "" : JSON.stringify(body),
    });
  });
}

const chatsOf = (page: Page, name: string) => page.locator(`ul[data-testid="agent-chat-rows"][aria-label*="${name}"] [data-testid="agent-chat-row"]`);
const rowLi = (page: Page, id: string) =>
  page.getByTestId("agent-rail-row").filter({ has: page.locator(`a[data-testid="agent-rail-install"][href="/chat/agents/i/${id}"]`) });

/** Each row's top edge, in CSS pixels from the top of the window. */
async function tops(page: Page): Promise<Record<string, number>> {
  const out: Record<string, number> = {};
  for (const id of ["a", "b", "c", "d"]) {
    const box = await rowLi(page, id).boundingBox();
    out[id] = box ? Math.round(box.y * 10) / 10 : NaN;
  }
  return out;
}

test("AM24c: a click and a return to the window move no row above the pointer; a reload is a new order", async ({ page }) => {
  installs = [row("a", "Alpha", 40), row("b", "Bravo", 30), row("c", "Charlie", 20), row("d", "Delta", 10)];
  railReads = 0;
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockServices(page);
  await page.goto("/chat/agents/i/a");
  await expect(page.getByTestId("agent-rail-install")).toHaveCount(4);
  // Alpha, on screen, shows its chats
  await expect(rowLi(page, "a")).toHaveAttribute("data-chats-open", "true");
  await expect(chatsOf(page, "Alpha")).toHaveCount(2);
  const before = await tops(page);

  // click row 3; the server makes Charlie the most recently used, and the workspace reads the rail again
  const reads = railReads;
  await page.locator('a[data-testid="agent-rail-install"][href="/chat/agents/i/c"]').click();
  await expect(page).toHaveURL(/\/chat\/agents\/i\/c$/);
  await expect(rowLi(page, "c")).toHaveAttribute("data-chats-open", "true");
  await expect(chatsOf(page, "Charlie")).toHaveCount(2);
  await expect.poll(() => railReads, { timeout: 15_000 }).toBeGreaterThan(reads);
  await page.waitForTimeout(800); // what that read would move has moved
  const afterClick = await tops(page);
  expect({ a: afterClick.a, b: afterClick.b }).toEqual({ a: before.a, b: before.b });
  // the one left behind is as it was
  expect(await rowLi(page, "a").getAttribute("data-chats-open")).toBe("true");
  await expect(chatsOf(page, "Alpha")).toHaveCount(2);

  // alt-tab away and back: Bravo now waits for an answer, and Charlie is still the most recently used
  installs = installs.map((r) => (r.id === "b" ? { ...r, waiting: 1 } : r));
  await page.evaluate(() => {
    window.dispatchEvent(new Event("blur"));
    window.dispatchEvent(new Event("focus"));
  });
  await expect(rowLi(page, "b").getByTestId("agent-rail-waiting")).toBeVisible();
  await expect(page.getByTestId("agent-rail-waiting-total")).toBeVisible();
  const afterFocus = await tops(page);
  expect(afterFocus).toEqual(afterClick);
  const order = await page.getByTestId("agent-rail-install").evaluateAll((els) => els.map((e) => e.getAttribute("href")));
  expect(order).toEqual(["/chat/agents/i/a", "/chat/agents/i/b", "/chat/agents/i/c", "/chat/agents/i/d"]);

  // a reload is a new page: recency again — Charlie, opened last, comes first
  await page.reload();
  await expect(page.getByTestId("agent-rail-install")).toHaveCount(4);
  await expect
    .poll(() => page.getByTestId("agent-rail-install").evaluateAll((els) => els.map((e) => e.getAttribute("href"))))
    .toEqual(["/chat/agents/i/c", "/chat/agents/i/a", "/chat/agents/i/b", "/chat/agents/i/d"]);
});
