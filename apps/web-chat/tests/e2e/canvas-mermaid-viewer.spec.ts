// The Mermaid canvas viewer in a real browser: the srcdoc the canvas panel
// builds, in the same sandbox the panel gives it (allow-scripts only, an
// opaque origin), with the self-hosted mermaid.min.js served from the app's
// /canvas-libs/ path. A diagram far wider than the pane fits on open; the
// toolbar, the wheel, a drag and the keyboard zoom, pan and fit; the level
// is shown. No backend and no dev server page are needed (the library is
// served by route interception), so this runs anywhere Playwright runs.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test, type Frame, type Page } from "@playwright/test";

import { buildMermaidSrcDoc } from "../../src/components/canvas/canvasUtil";

const ORIGIN = "http://canvas.test";
const MERMAID = readFileSync(resolve(__dirname, "../../public/canvas-libs/mermaid.min.js"));

// a long chain: far wider than any pane
const WIDE = "flowchart LR\n" + Array.from({ length: 40 }, (_, i) => `  N${i}[ขั้นที่ ${i + 1}] --> N${i + 1}`).join("\n");

async function open(page: Page, width = 900, height = 600): Promise<Frame> {
  await page.setViewportSize({ width, height });
  await page.route(`${ORIGIN}/canvas-libs/mermaid.min.js`, (r) => r.fulfill({ body: MERMAID, contentType: "text/javascript" }));
  await page.route(`${ORIGIN}/`, (r) => r.fulfill({ body: "<!doctype html><html><body style=\"margin:0\"></body></html>", contentType: "text/html" }));
  await page.goto(`${ORIGIN}/`);
  await page.evaluate(
    ({ doc, w, h }) => {
      const f = document.createElement("iframe");
      f.setAttribute("sandbox", "allow-scripts");
      f.style.cssText = `border:0;width:${w}px;height:${h}px`;
      f.srcdoc = doc;
      document.body.appendChild(f);
    },
    { doc: buildMermaidSrcDoc(WIDE, ORIGIN), w: width, h: height },
  );
  const frame = page.frames().find((f) => f !== page.mainFrame())!;
  await expect(frame.locator("#view svg")).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => Number(await frame.locator("#stage").getAttribute("data-zoom"))).toBeGreaterThan(0);
  return frame;
}

const zoom = async (f: Frame) => Number(await f.locator("#stage").getAttribute("data-zoom"));
const shift = async (f: Frame) => f.locator("#view").evaluate((el) => (el as HTMLElement).style.transform);

test("a diagram far wider than the pane fits on open, with its level shown", async ({ page }) => {
  const f = await open(page);
  const z = await zoom(f);
  expect(z).toBeLessThan(100);
  await expect(f.locator("#zoom-level")).toHaveText(`${z}%`);
  const svg = await f.locator("#view svg").boundingBox();
  expect(svg!.x).toBeGreaterThanOrEqual(0);
  expect(svg!.x + svg!.width).toBeLessThanOrEqual(900 + 1);
  await page.screenshot({ path: "test-results/canvas-mermaid-fit.png" });
});

test("the buttons, the wheel, a drag and the keyboard zoom, pan and fit", async ({ page }) => {
  const f = await open(page);
  const fit = await zoom(f);
  await f.locator("#zoom-in").click();
  expect(await zoom(f)).toBeGreaterThan(fit);
  await f.locator("#zoom-out").click();
  await f.locator("#zoom-out").click();
  expect(await zoom(f)).toBeLessThan(fit);
  await f.locator("#zoom-fit").click();
  expect(await zoom(f)).toBe(fit);

  // the wheel zooms around the pointer
  await page.mouse.move(450, 300);
  await page.mouse.wheel(0, -400);
  await expect.poll(() => zoom(f)).toBeGreaterThan(fit);

  // a drag pans
  const before = await shift(f);
  await page.mouse.move(450, 300);
  await page.mouse.down();
  await page.mouse.move(250, 260, { steps: 6 });
  await page.mouse.up();
  expect(await shift(f)).not.toBe(before);
  await page.screenshot({ path: "test-results/canvas-mermaid-zoomed.png" });

  // the keyboard, while the canvas has focus
  await f.locator("#stage").focus();
  await page.keyboard.press("0");
  expect(await zoom(f)).toBe(fit);
  await page.keyboard.press("+");
  expect(await zoom(f)).toBeGreaterThan(fit);
  await page.keyboard.press("-");
  await page.keyboard.press("-");
  expect(await zoom(f)).toBeLessThan(fit);
});

test("a narrower pane fits again until the person zooms", async ({ page }) => {
  const f = await open(page, 900, 600);
  const wide = await zoom(f);
  await page.evaluate(() => {
    (document.querySelector("iframe") as HTMLIFrameElement).style.width = "450px";
  });
  await expect.poll(() => zoom(f)).toBeLessThan(wide);
});
