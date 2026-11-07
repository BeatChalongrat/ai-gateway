// E2E — FIX62's three composer guarantees, clicked in a real browser.
//
//   C1  🎨 on at a capable model → switch to a model that cannot make
//       images → the mode drops by itself, with the reason in a toast, and
//       the button is disabled with the reason on it.
//   C2  an explicit image request typed at a model that cannot make images
//       is held: the notice names the way out, the text stays, and NO
//       request leaves the browser (the messages endpoint is never hit);
//       sending the same text again lets it through as a plain question.
//   C3  a turn that fails (the stream ends in an error frame) does not
//       poison the chat: the next turn in the same conversation answers.
//
// Same contract as chat-happy-path.spec.ts: gateway (:8080) and chat-edge
// (:4000) are mocked with route interception; the page, the composer and
// the store are the real ones. The desktop opens this same page, so what
// is clicked here is the composer both clients share — but only the web
// path is driven by this file.

import { expect, test, type Page, type Route } from "@playwright/test";

const GATEWAY = "http://localhost:8080";
const API = "http://localhost:4000";

const CAPABLE = {
  id: "tooly-1",
  name: "Tooly",
  provider: "test",
  description: "mock model that can use tools and make images",
  context_window: 128000,
  capabilities: ["chat", "function_calling", "image_generation_via_tools"],
  icon: "openai",
  category: "fast",
  allowed: true,
};

const TEXT_ONLY = {
  ...CAPABLE,
  id: "text-only-1",
  name: "Text Only",
  description: "mock model without tools",
  capabilities: ["chat"],
};

const CONV = {
  id: "fix63-conv",
  title: "FIX63 chat",
  model_id: CAPABLE.id,
  message_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

function corsHeaders(route: Route): Record<string, string> {
  const origin = route.request().headers()["origin"] ?? "http://localhost:3102";
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-credentials": "true",
    "access-control-allow-headers": "content-type, authorization, x-user-id",
    "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", headers: corsHeaders(route), body: JSON.stringify(body) });
}

function sse(route: Route, body: string) {
  return route.fulfill({
    status: 200,
    headers: { ...corsHeaders(route), "content-type": "text/event-stream", "cache-control": "no-cache" },
    body,
  });
}

const okStream = (n: number, text: string) =>
  `event: message_start\ndata: ${JSON.stringify({ message_id: `srv-${n}`, conversation_id: CONV.id })}\n\n` +
  `event: content_delta\ndata: ${JSON.stringify({ delta: text })}\n\n` +
  `event: message_complete\ndata: ${JSON.stringify({ message_id: `srv-${n}`, token_usage: { input: 4, output: 12 } })}\n\n`;

const errorStream = (n: number) =>
  `event: message_start\ndata: ${JSON.stringify({ message_id: `srv-${n}`, conversation_id: CONV.id })}\n\n` +
  `event: error\ndata: ${JSON.stringify({ code: "upstream_bad_response", message: "The AI provider rejected the request.", retryable: false })}\n\n`;

/** Wires the mock backend; returns counters the tests assert on. */
async function mockBackend(page: Page, opts: { modelId?: string; streams?: string[] } = {}) {
  const hits = { messagePosts: 0 };
  const conv = { ...CONV, model_id: opts.modelId ?? CAPABLE.id };
  const streams = [...(opts.streams ?? [])];

  await page.route(`${GATEWAY}/**`, async (route) => {
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: corsHeaders(route) });
    const path = new URL(route.request().url()).pathname;
    if (path === "/portal/me") {
      return json(route, { id: "user-e2e", email: "e2e@example.com", tenant_id: "tenant-e2e", tenant_role: "member" });
    }
    return json(route, {});
  });

  await page.route(`${API}/**`, async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: corsHeaders(route) });
    const path = new URL(req.url()).pathname;

    if (path === "/api/v1/models") return json(route, { models: [CAPABLE, TEXT_ONLY] });
    if (path === "/api/v1/projects") return json(route, { projects: [] });
    if (path === "/api/v1/memories/pending-count") return json(route, { count: 0 });
    if (path === "/api/v1/usage/me") {
      return json(route, { credits: { total: 1000, used: 10, remaining: 990, reset_date: null }, usage_by_model: [], total_credits_used: 10, period: "lifetime" });
    }
    if (path === "/api/v1/conversations" && req.method() === "GET") {
      return json(route, { conversations: [conv], has_more: false, next_cursor: null });
    }
    if (path === `/api/v1/conversations/${CONV.id}/messages` && req.method() === "POST") {
      hits.messagePosts++;
      const body = streams.shift() ?? okStream(hits.messagePosts, "fallback answer");
      return sse(route, body);
    }
    if (path === `/api/v1/conversations/${CONV.id}/messages` && req.method() === "GET") {
      return json(route, { has_more: false, messages: [] });
    }
    if (path === `/api/v1/conversations/${CONV.id}/generate-title`) return json(route, { title: CONV.title });
    if (path === `/api/v1/conversations/${CONV.id}`) {
      // A model switch PATCHes the conversation; the mock must remember it
      // or the next GET reverts the picker to the old model.
      if (req.method() === "PATCH") {
        try {
          const body = JSON.parse(req.postData() || "{}");
          if (typeof body.model_id === "string") conv.model_id = body.model_id;
        } catch { /* ignore */ }
      }
      return json(route, conv);
    }
    return json(route, {});
  });

  return hits;
}

async function pickModel(page: Page, name: string) {
  await page.locator('button[aria-haspopup="listbox"]').first().click();
  await page.getByTestId("model-row-name").filter({ hasText: name }).first().click();
}

test("C1 — image mode drops itself, with the reason, when the model cannot make images", async ({ page }) => {
  await mockBackend(page);
  await page.goto(`/chat/${CONV.id}`);
  await expect(page.locator("textarea")).toBeVisible();

  const imageBtn = page.getByTestId("image-mode-button");
  await expect(imageBtn).toBeEnabled();
  await imageBtn.click();
  await expect(imageBtn).toHaveAttribute("aria-pressed", "true");

  // the toast lives 2.5 s and the picker interaction can outlast it, so
  // record every toast text as it appears instead of racing it
  await page.evaluate(() => {
    const w = window as unknown as { __toasts: string[] };
    w.__toasts = [];
    new MutationObserver(() => {
      for (const el of document.querySelectorAll('[role="status"],[role="alert"]')) {
        const t = el.textContent ?? "";
        if (t && !w.__toasts.includes(t)) w.__toasts.push(t);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  });

  await pickModel(page, TEXT_ONLY.name);

  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __toasts: string[] }).__toasts.join(" | ")))
    .toContain(`Image mode turned off — ${TEXT_ONLY.name} can't use tools`);
  // FIX65 — a model that cannot make images has no 🎨 at all (the picker
  // curates vendor rows to a floor and labels the operator's own, so an
  // absent button is the honest shape); FIX47.6's disabled button is gone.
  await expect(imageBtn).toHaveCount(0);
});

test("C2 — an image request at a text-only model is held, nothing leaves the browser, and the same text goes through on the second send", async ({ page }) => {
  const hits = await mockBackend(page, { modelId: TEXT_ONLY.id, streams: [okStream(1, "I can describe a cat in words.")] });
  await page.goto(`/chat/${CONV.id}`);
  const composer = page.locator("textarea");
  await expect(composer).toBeVisible();
  await expect(page.getByTestId("image-mode-button")).toHaveCount(0); // FIX65: no button at a text-only model

  await composer.fill("ทำรูปแมว");
  await composer.press("Enter");

  const notice = page.getByTestId("image-intent-blocked");
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("can't make images");
  await expect(notice).toContainText(TEXT_ONLY.name);
  await expect(composer).toHaveValue("ทำรูปแมว");
  // give any stray request time to appear — none must
  await page.waitForTimeout(500);
  expect(hits.messagePosts).toBe(0);

  await composer.press("Enter");
  await expect(page.getByText("I can describe a cat in words.")).toBeVisible();
  expect(hits.messagePosts).toBe(1);
  await expect(notice).toHaveCount(0);
});

test("C3 — a failed turn does not poison the chat: the next turn answers", async ({ page }) => {
  const hits = await mockBackend(page, { streams: [errorStream(1), okStream(2, "Second turn answered fine.")] });
  await page.goto(`/chat/${CONV.id}`);
  const composer = page.locator("textarea");
  await expect(composer).toBeVisible();

  await composer.fill("first turn, will fail");
  await composer.press("Enter");
  // the message bubble carries the error (a toast repeats it; either is fine)
  await expect(page.getByText("The AI provider rejected this request", { exact: false }).first()).toBeVisible();

  await composer.fill("second turn");
  await composer.press("Enter");
  await expect(page.getByText("Second turn answered fine.")).toBeVisible();
  expect(hits.messagePosts).toBe(2);
});
