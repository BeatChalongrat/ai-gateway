// E2E — Batch 1 quick wins (W1 drafts, W2 image download/zoom, W3
// vision gating) exercised against the real app in a real browser.
// Same contract as chat-happy-path.spec.ts: gateway (:8080) and
// chat-edge (:4000) are mocked with route interception, no services.

import { expect, test, type Page, type Route } from "@playwright/test";

const GATEWAY = "http://localhost:8080";
const API = "http://localhost:4000";

const VISION_MODEL = {
  id: "claude-sonnet-4",
  name: "Claude Sonnet 4",
  provider: "anthropic",
  description: "mock vision model",
  context_window: 200000,
  capabilities: ["chat", "vision"],
  icon: "anthropic",
  category: "fast",
  allowed: true,
};

const TEXT_MODEL = {
  ...VISION_MODEL,
  id: "text-only-1",
  name: "Text Only",
  description: "mock text-only model",
  capabilities: ["chat"],
};

const CONV_VISION = {
  id: "b1-conv-vision",
  title: "Vision chat",
  model_id: VISION_MODEL.id,
  message_count: 2,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const CONV_TEXT = {
  ...CONV_VISION,
  id: "b1-conv-text",
  title: "Text-only chat",
  model_id: TEXT_MODEL.id,
};

const IMAGE_FILE = {
  id: "b1-file-img",
  filename: "generated-cat.png",
  content_type: "image/png",
  file_size_bytes: 68,
  extraction_status: "completed",
  created_at: "2026-01-01T00:00:00Z",
};

// 1×1 transparent PNG.
const PNG_BYTES = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

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

/** Wires the standard mock backend. Returns counters the tests assert on. */
async function mockBackend(page: Page) {
  const hits = { imageDownloads: 0, uploads: 0 };

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

    if (path === "/api/v1/models") {
      return json(route, { models: [VISION_MODEL, TEXT_MODEL] });
    }
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
    if (path === "/api/v1/conversations" && req.method() === "GET") {
      return json(route, {
        conversations: [CONV_VISION, CONV_TEXT],
        has_more: false,
        next_cursor: null,
      });
    }
    if (path === `/api/v1/files/${IMAGE_FILE.id}/download`) {
      hits.imageDownloads++;
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        headers: corsHeaders(route),
        body: PNG_BYTES,
      });
    }
    if (path.endsWith("/files") && req.method() === "POST") {
      hits.uploads++;
      return json(route, { id: "up-1", extraction_status: "completed" });
    }
    if (path === `/api/v1/conversations/${CONV_VISION.id}/messages` && req.method() === "GET") {
      return json(route, {
        has_more: false,
        messages: [
          {
            id: "m-u1",
            role: "user",
            content: "here is a cat picture",
            status: "completed",
            created_at: "2026-01-01T00:00:01Z",
            files: [IMAGE_FILE],
          },
          {
            id: "m-a1",
            role: "assistant",
            content: "A fine cat indeed.",
            status: "completed",
            created_at: "2026-01-01T00:00:05Z",
          },
        ],
      });
    }
    if (path === `/api/v1/conversations/${CONV_TEXT.id}/messages` && req.method() === "GET") {
      return json(route, { has_more: false, messages: [] });
    }
    if (path === `/api/v1/conversations/${CONV_VISION.id}`) {
      return json(route, CONV_VISION);
    }
    if (path === `/api/v1/conversations/${CONV_TEXT.id}`) {
      return json(route, CONV_TEXT);
    }
    return json(route, {});
  });

  return hits;
}

test("W1 — composer draft survives switch + reload, per conversation", async ({ page }) => {
  await mockBackend(page);

  await page.goto(`/chat/${CONV_TEXT.id}`);
  const composer = page.locator("textarea");
  await expect(composer).toBeVisible();

  await composer.fill("three paragraphs of unsent thought");
  // Client-side switch to the other conversation (sidebar link).
  await page.getByText("Vision chat").click();
  await expect(page).toHaveURL(new RegExp(`/chat/${CONV_VISION.id}$`));
  await expect(composer).toHaveValue("");

  // Back — the draft is simply there again.
  await page.getByText("Text-only chat").click();
  await expect(page).toHaveURL(new RegExp(`/chat/${CONV_TEXT.id}$`));
  await expect(composer).toHaveValue("three paragraphs of unsent thought");

  // Hard reload (pagehide flush covers the sub-debounce window).
  await page.reload();
  await expect(page.locator("textarea")).toHaveValue(
    "three paragraphs of unsent thought",
  );
});

test("W2 — image downloads from thumb and lightbox reuse the loaded bytes", async ({ page }) => {
  const hits = await mockBackend(page);

  await page.goto(`/chat/${CONV_VISION.id}`);
  const thumbDownload = page.getByTestId("image-thumb-download");
  await expect(thumbDownload).toBeAttached();
  // AuthedImage's initial load (dev StrictMode double-mounts effects, so
  // this can be 2 on the e2e dev server — the assertion that matters is
  // the DELTA: downloads and the lightbox must add zero fetches).
  await expect.poll(() => hits.imageDownloads).toBeGreaterThanOrEqual(1);
  const baselineFetches = hits.imageDownloads;

  // Thumb hover download → correct filename, no extra fetch.
  const download1Promise = page.waitForEvent("download");
  await thumbDownload.click({ force: true }); // hover-revealed control
  const download1 = await download1Promise;
  expect(download1.suggestedFilename()).toBe("generated-cat.png");

  // Open the lightbox from the thumb; its download control works too.
  await page.getByTitle(/generated-cat\.png/).click();
  const lightboxDownload = page.getByTestId("lightbox-download");
  await expect(lightboxDownload).toBeVisible();
  const download2Promise = page.waitForEvent("download");
  await lightboxDownload.click();
  const download2 = await download2Promise;
  expect(download2.suggestedFilename()).toBe("generated-cat.png");

  // Zoom: wheel in → transform scales and the reset control appears.
  const img = page.locator('div[role="dialog"] img');
  await img.dispatchEvent("wheel", { deltaY: -120 });
  await expect(img).toHaveCSS("cursor", "grab");
  await page.getByTestId("lightbox-zoom-reset").click();
  await expect(img).toHaveCSS("cursor", "zoom-in");

  // Escape still closes with the new controls present.
  await page.keyboard.press("Escape");
  await expect(page.locator('div[role="dialog"]')).toHaveCount(0);

  // Neither download nor the lightbox re-fetched the bytes.
  expect(hits.imageDownloads).toBe(baselineFetches);
});

test("W3 — non-vision model blocks pasted images and strips them from the picker", async ({ page }) => {
  const hits = await mockBackend(page);

  await page.goto(`/chat/${CONV_TEXT.id}`);
  const composer = page.locator("textarea");
  await expect(composer).toBeVisible();

  // Picker accept list carries no image types on a text-only model.
  const accept = await page
    .locator('input[type="file"]')
    .getAttribute("accept");
  expect(accept).not.toContain("image/png");
  expect(accept).not.toContain(".png");

  // Pasting an image is rejected with a toast naming a capable model,
  // and no upload is fired.
  await composer.evaluate((el) => {
    const dt = new DataTransfer();
    dt.items.add(
      new File([new Uint8Array([137, 80, 78, 71])], "cat.png", {
        type: "image/png",
      }),
    );
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true }));
  });
  await expect(
    page.getByText(/switch to Claude Sonnet 4/),
  ).toBeVisible();
  expect(hits.uploads).toBe(0);

  // On a vision model, the picker accepts images again.
  await page.getByText("Vision chat").click();
  await expect(page).toHaveURL(new RegExp(`/chat/${CONV_VISION.id}$`));
  const acceptVision = await page
    .locator('input[type="file"]')
    .getAttribute("accept");
  expect(acceptVision).toContain("image/png");
});
