// E2E — Batch 2 T1: GPT-style math delimiters over a real streamed
// turn. The SSE body deliberately splits `\` from `[` across
// content_delta frames (the chunk-boundary case) and mixes display
// math, inline math, a code fence containing backslash-brackets, and
// currency — the rendered result must typeset the math and leave
// everything else byte-literal. Backends mocked as in
// chat-happy-path.spec.ts.

import { expect, test, type Route } from "@playwright/test";

const GATEWAY = "http://localhost:8080";
const API = "http://localhost:4000";
const CONV_ID = "t1-math-conv";

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

// The reply, cut into deltas at adversarial boundaries: frame 3 ends in
// a lone backslash, frame 4 starts with "[".
const DELTAS = [
  "Here is display math:\n\n\\[\nE = mc^2\n\\]\n\n",
  "and inline \\(a+b\\) in a sentence. ",
  "Splitting the backslash \\",
  "[x^2\\] across frames. Code stays code:\n\n",
  "```csharp\n[property: JsonPropertyName(\"x\")] // \\[not math\\]\n```\n\n",
  // NOTE: a two-dollar sentence like "between $100 and $200" is eaten by
  // remark-math's single-$ inline parsing — a PRE-EXISTING false positive
  // (audit A4, unrelated to the backslash normalizer, which never touches
  // $). The single-$ price below is the pre-existing safe shape; the
  // normalizer's own no-touch guarantee for the two-dollar case is pinned
  // at unit level in mathDelimiters.test.ts.
  "The price is $150 today.",
];

const SSE_BODY =
  `event: message_start\ndata: ${JSON.stringify({
    message_id: "srv-math-1",
    conversation_id: CONV_ID,
  })}\n\n` +
  DELTAS.map(
    (d) => `event: content_delta\ndata: ${JSON.stringify({ delta: d })}\n\n`,
  ).join("") +
  `event: message_complete\ndata: ${JSON.stringify({
    message_id: "srv-math-1",
    token_usage: { input: 4, output: 64 },
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

test("streamed \\[…\\]/\\(…\\) math renders as KaTeX; fences and currency stay literal", async ({ page }) => {
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
      return json(route, {
        id: CONV_ID,
        title: "New conversation",
        model_id: MODEL.id,
        message_count: 0,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      });
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
    return json(route, {});
  });

  await page.goto("/chat");
  const composer = page.locator("textarea");
  await expect(composer).toBeVisible();
  await composer.fill("show me the math");
  await composer.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/chat/${CONV_ID}$`));

  // Stream completes; the standalone \[…\] typeset as DISPLAY math.
  await expect(page.locator(".katex-display").first()).toBeVisible();

  // Inline \(a+b\) and the frame-split \[x^2\] typeset too (≥3 KaTeX
  // renders in the message: display block + inline + split display).
  await expect
    .poll(async () => page.locator(".katex").count())
    .toBeGreaterThanOrEqual(3);

  // The fence's content stayed byte-literal — attribute brackets AND the
  // backslash-bracket comment render as code, never as math.
  const code = page.locator("pre code").first();
  await expect(code).toContainText('[property: JsonPropertyName("x")]');
  await expect(code).toContainText("\\[not math\\]");

  // Currency untouched.
  await expect(page.getByText("The price is $150 today.")).toBeVisible();

  // Nothing leaked raw delimiters into the prose (the math got
  // converted, not echoed): the visible text outside the fence has no
  // literal "\[".
  const proseHasRaw = await page.evaluate(() => {
    const bubbles = Array.from(document.querySelectorAll("p"));
    return bubbles.some((p) => p.textContent?.includes("\\["));
  });
  expect(proseHasRaw).toBe(false);
});
