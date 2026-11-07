// E2E happy path — login handoff (cookie session accepted via the
// mocked /portal/me) → send a message → streamed tokens render in the
// thread. The gateway (:8080) and chat-edge (:4000) are BOTH mocked
// with route interception, so this spec runs with no real services.

import { expect, test, type Route } from "@playwright/test";

const GATEWAY = "http://localhost:8080";
const API = "http://localhost:4000";
const CONV_ID = "e2e-conv-1";

const CONVERSATION = {
  id: CONV_ID,
  title: "New conversation",
  model_id: "claude-sonnet-4",
  message_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const MODEL = {
  id: "claude-sonnet-4",
  name: "Claude Sonnet 4",
  provider: "anthropic",
  description: "mock model",
  context_window: 200000,
  capabilities: ["chat"],
  icon: "anthropic",
  category: "fast",
  allowed: true,
};

// The assistant reply, streamed as SSE. One frame deliberately uses
// CRLF (\r\n\r\n) delimiters to exercise the E4 parser fix end-to-end.
const SSE_BODY =
  `event: message_start\ndata: ${JSON.stringify({
    message_id: "srv-msg-1",
    conversation_id: CONV_ID,
  })}\n\n` +
  `event: content_delta\ndata: ${JSON.stringify({ delta: "Hello from the " })}\n\n` +
  `event: content_delta\r\ndata: ${JSON.stringify({ delta: "mocked gateway!" })}\r\n\r\n` +
  `event: message_complete\ndata: ${JSON.stringify({
    message_id: "srv-msg-1",
    token_usage: { input: 4, output: 12 },
  })}\n\n`;

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
  return route.fulfill({
    status,
    contentType: "application/json",
    headers: corsHeaders(route),
    body: JSON.stringify(body),
  });
}

test("login handoff → send message → streamed tokens render", async ({ page }) => {
  // ── Gateway (identity authority) ─────────────────────────────
  await page.route(`${GATEWAY}/**`, async (route) => {
    if (route.request().method() === "OPTIONS") {
      return route.fulfill({ status: 204, headers: corsHeaders(route) });
    }
    const path = new URL(route.request().url()).pathname;
    if (path === "/portal/me") {
      return json(route, {
        id: "user-e2e",
        email: "e2e@example.com",
        tenant_id: "tenant-e2e",
        tenant_role: "member",
      });
    }
    return json(route, {});
  });

  // ── chat-edge API ────────────────────────────────────────────
  await page.route(`${API}/**`, async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") {
      return route.fulfill({ status: 204, headers: corsHeaders(route) });
    }
    const path = new URL(req.url()).pathname;

    if (path === "/api/v1/models") return json(route, { models: [MODEL] });
    if (path === "/api/v1/projects") return json(route, { projects: [] });
    if (path === "/api/v1/memories/pending-count") return json(route, { count: 0 });
    if (path === "/api/v1/usage/me") {
      return json(route, {
        credits: { total: 1000, used: 10, remaining: 990, reset_date: null },
        usage_by_model: [],
        total_credits_used: 10,
        period: "lifetime",
      });
    }
    if (path === "/api/v1/conversations" && req.method() === "POST") {
      return json(route, CONVERSATION);
    }
    if (path === "/api/v1/conversations" && req.method() === "GET") {
      return json(route, { conversations: [], has_more: false, next_cursor: null });
    }
    if (path === `/api/v1/conversations/${CONV_ID}/messages` && req.method() === "POST") {
      return route.fulfill({
        status: 200,
        headers: {
          ...corsHeaders(route),
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
        },
        body: SSE_BODY,
      });
    }
    if (path === `/api/v1/conversations/${CONV_ID}/messages` && req.method() === "GET") {
      return json(route, { messages: [], has_more: false });
    }
    if (path === `/api/v1/conversations/${CONV_ID}/generate-title`) {
      return json(route, { title: "Mocked chat" });
    }
    if (path === `/api/v1/conversations/${CONV_ID}`) {
      return json(route, { ...CONVERSATION, title: "Mocked chat", message_count: 2 });
    }
    return json(route, {});
  });

  // ── Login handoff: the cookie session resolves via /portal/me,
  //    so /chat renders instead of bouncing to the Portal login. ──
  await page.goto("/chat");
  const composer = page.locator("textarea");
  await expect(composer).toBeVisible();
  await expect(page).toHaveURL(/\/chat$/);

  // ── Send a message. ──────────────────────────────────────────
  await composer.fill("Hello mock model");
  await composer.press("Enter");

  // Lazy conversation create + navigation into the thread.
  await expect(page).toHaveURL(new RegExp(`/chat/${CONV_ID}$`));

  // User bubble renders optimistically.
  await expect(page.getByText("Hello mock model")).toBeVisible();

  // Streamed assistant tokens render (both LF and CRLF frames).
  await expect(page.getByText("Hello from the mocked gateway!")).toBeVisible();
});
