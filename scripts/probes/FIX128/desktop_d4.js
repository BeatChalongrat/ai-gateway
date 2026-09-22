// FIX128 D4 on the desktop (CDP 9333): send button inside the window, skip link, arrow keys.
const path = require("node:path");
const { chromium } = require(path.join(__dirname, "..", "..", "..", "apps", "web-chat", "node_modules", "playwright-core"));
(async () => {
  const b = await chromium.connectOverCDP("http://127.0.0.1:9333");
  const page = b.contexts()[0].pages().find((p) => /localhost:3002/.test(p.url()));
  await page.goto("http://localhost:3002/chat", { waitUntil: "domcontentloaded" });
  await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 60000 }); await new Promise((r) => setTimeout(r, 2000));
  const geo = await page.evaluate(() => { const s = document.querySelector('button[aria-label*="Send"], button[aria-label*="ส่ง"]'); const r = s.getBoundingClientRect(); return { width: innerWidth, sendInside: r.left >= 0 && r.right <= innerWidth, scrollWidth: document.documentElement.scrollWidth }; });
  await page.evaluate(() => { const el = document.createElement("span"); el.tabIndex = -1; document.body.prepend(el); el.focus(); });
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
  const rows = page.locator("[data-conversation-id]"); await rows.first().focus();
  const r0 = await page.evaluate(() => document.activeElement.getAttribute("data-conversation-id"));
  await page.keyboard.press("ArrowDown");
  const r1 = await page.evaluate(() => document.activeElement.getAttribute("data-conversation-id"));
  console.log(JSON.stringify({ ...geo, firstTab: first, arrowDownMoved: !!r1 && r0 !== r1 }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
