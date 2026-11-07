import type { Page } from "@playwright/test";

export const transcript = (count = 8) => Array.from({ length: count }, (_, i) => ({
  id: `scroll-seed-${i}`, role: i % 2 ? "assistant" : "user",
  content: i % 2 ? "A previous reply. ".repeat(45) : `Previous question ${i}`,
  status: "completed", created_at: "2026-01-01T00:00:00Z",
}));

export async function installScrollFixture(page: Page, count = 8, appOrigin = "http://localhost:3102", olderCount = 0) {
  await page.addInitScript(() => {
    localStorage.setItem("sran-chat-locale", "en");
    const original = window.fetch.bind(window);
    const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
    const encoder = new TextEncoder();
    const fixture = {
      streams,
      emit(index: number, event: string, data: unknown) {
        streams[index].enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      },
      complete(index = streams.length - 1) {
        fixture.emit(index, "message_complete", {
          message_id: `scroll-answer-${index}`, token_usage: { input: 4, output: 12 },
        });
        streams[index].close();
      },
    };
    Object.assign(window, { __scrollFixture: fixture });
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (init?.method === "POST" && /\/conversations\/[^/]+\/(messages|regenerate|edit)/.test(url)) {
        const index = streams.length;
        const body = new ReadableStream<Uint8Array>({
          start(controller) { streams.push(controller); },
        });
        init.signal?.addEventListener("abort", () => {
          streams[index].error(new DOMException("Aborted", "AbortError"));
        }, { once: true });
        fixture.emit(index, "message_start", {
          message_id: `scroll-answer-${index}`, user_message_id: `scroll-user-${index}`,
          conversation_id: "scroll-test",
        });
        return new Response(body, { headers: { "content-type": "text/event-stream" } });
      }
      return original(input, init);
    };
  });
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.origin === appOrigin ||
        (url.hostname === "localhost" && ["3102", "3104"].includes(url.port))) {
      return route.continue();
    }
    const path = url.pathname;
    const conv = { id: "scroll-test", title: "Scroll test", model_id: "scroll-model", message_count: count,
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
    let body: unknown = {};
    if (path === "/portal/me") body = { id: "scroll-reader", email: "scroll@example.com", tenant_id: "scroll-tenant", tenant_role: "member" };
    else if (path.endsWith("/models")) body = { models: [{ id: "scroll-model", name: "Test", provider: "mock", capabilities: ["chat"], allowed: true, context_window: 200000 }] };
    else if (path.endsWith("/projects")) body = { projects: [] };
    else if (path.endsWith("/usage/me")) body = { credits: { total: 1000, used: 0, remaining: 1000 }, usage_by_model: [] };
    else if (path.endsWith("/conversations")) body = req.method() === "POST" ? conv : { conversations: [conv], has_more: false };
    else if (path.endsWith("/messages")) body = url.searchParams.has("before")
      ? { messages: transcript(olderCount).map((m) => ({ ...m, id: `older-${m.id}` })), has_more: false }
      : { messages: transcript(count), has_more: olderCount > 0 };
    else if (/\/conversations\/[^/]+$/.test(path)) body = conv;
    await route.fulfill({ status: req.method() === "OPTIONS" ? 204 : 200,
      headers: { "access-control-allow-origin": req.headers().origin || "*", "access-control-allow-credentials": "true",
        "access-control-allow-headers": "content-type, authorization, x-user-id, x-csrf-token",
        "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS" },
      contentType: "application/json", body: req.method() === "OPTIONS" ? "" : JSON.stringify(body) });
  });
}

export async function emitScrollEvent(page: Page, event: string, data: unknown, index = -1) {
  await page.evaluate(({ event, data, index }) => {
    const f = (window as unknown as { __scrollFixture: {
      streams: unknown[]; emit(i: number, event: string, data: unknown): void;
    } }).__scrollFixture;
    f.emit(index < 0 ? f.streams.length - 1 : index, event, data);
  }, { event, data, index });
}

export async function completeScrollReply(page: Page) {
  await page.evaluate(() => (window as unknown as { __scrollFixture: { complete(): void } }).__scrollFixture.complete());
}

export function scrollSnapshot() {
  const spacer = document.querySelector<HTMLElement>('[data-testid="turn-spacer"]')!;
  const el = spacer.parentElement!;
  const column = el.firstElementChild as HTMLElement;
  const er = el.getBoundingClientRect();
  const cr = column.getBoundingClientRect();
  const rows = el.querySelectorAll<HTMLElement>("[data-msg-index]");
  const last = rows[rows.length - 1];
  const padding = parseFloat(getComputedStyle(el).paddingBottom);
  const naturalContent = cr.bottom - er.top - el.clientTop + el.scrollTop + padding;
  // Read-only baseline diagnostics: refs begin at the scroller ref in ChatThread.
  type Fiber = { return: Fiber | null; alternate: Fiber | null; type?: { name?: string };
    stateNode?: { current?: Fiber }; memoizedState: { memoizedState: unknown; next: Fiber["memoizedState"] } | null };
  const key = Object.keys(el).find((k) => k.startsWith("__reactFiber$"));
  let fiber: Fiber | null = key ? (el as unknown as Record<string, Fiber>)[key] : null;
  const ref = (value: unknown) => (value as { current?: unknown } | null)?.current;
  while (fiber) {
    let ownsScroller = false;
    if (typeof fiber.type === "function") {
      for (let h = fiber.memoizedState; h; h = h.next) {
        if (ref(h.memoizedState) === el) { ownsScroller = true; break; }
      }
    }
    if (ownsScroller) break;
    fiber = fiber.return;
  }
  if (fiber) {
    let root = fiber;
    while (root.return) root = root.return;
    if (root.stateNode?.current !== root && fiber.alternate) fiber = fiber.alternate;
  }
  const hooks: unknown[] = [];
  for (let h = fiber?.memoizedState; h; h = h.next) hooks.push(h.memoizedState);
  const start = hooks.findIndex((h) => ref(h) === el);
  const readRef = (offset: number) => start < 0 ? null : ref(hooks[start + offset]);
  return {
    H: el.clientHeight, width: cr.width, A: readRef(12), T: el.scrollTop, C: naturalContent,
    scrollHeight: el.scrollHeight, spacerStyle: parseFloat(spacer.style.height) || 0,
    spacerRendered: spacer.getBoundingClientRect().height,
    lastBottom: last ? last.getBoundingClientRect().bottom - er.top - el.clientTop : 0,
    anchorActive: readRef(11), holdTop: readRef(13), follow: readRef(2),
    programmatic: readRef(7), userIntent: readRef(17),
    readerLandmark: readRef(24),
    farFromBottom: start < 0 ? null : hooks[start + 3], pendingNew: start < 0 ? null : hooks[start + 4],
    plainArrow: !!document.querySelector('button[aria-label="Scroll to latest"], button[aria-label="Jump to latest"]'),
    newPill: Array.from(document.querySelectorAll("button")).some((b) => /New messages|ข้อความใหม่/.test(b.textContent || "")),
  };
}
