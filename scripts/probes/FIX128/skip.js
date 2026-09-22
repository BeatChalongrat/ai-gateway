process.env.PAGE = "http://localhost:3002"; process.env.API = "http://localhost:4000/api/v1"; process.env.EDGE = "ai-gateway-chat-edge-api";
const { PAGE, token, openContext, wait } = require("../fix122/lib");
(async () => {
  const { browser, ctx } = await openContext(token(), { viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  await page.goto(`${PAGE}/chat/f7a849e9-76f1-42a3-b56d-6073c7f8d8d8`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 60000 }); await wait(2000);
  console.log(await page.evaluate(() => {
    const a = document.querySelector('[data-testid="skip-to-composer"]');
    const all = [...document.querySelectorAll('a[href], button, [tabindex]:not([tabindex="-1"]), textarea, input')].filter(e => !e.disabled);
    return JSON.stringify({ exists: !!a, indexAmongFocusable: all.indexOf(a), first3: all.slice(0,3).map(e => e.getAttribute("data-testid") || e.tagName + ":" + (e.getAttribute("aria-label")||"").slice(0,30)) });
  }));
  // move the focus-navigation starting point to the very top of the page
  await page.evaluate(() => { const el = document.createElement("span"); el.id = "top-probe"; el.tabIndex = -1; document.body.prepend(el); el.focus(); });
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => document.activeElement?.getAttribute("data-testid") || document.activeElement?.tagName);
  const visible = await page.evaluate(() => { const r = document.activeElement.getBoundingClientRect(); return r.width > 10 && r.height > 10; });
  await page.keyboard.press("Enter");
  const after = await page.evaluate(() => document.activeElement?.getAttribute("data-shortcut"));
  // arrow keys in the sidebar list
  const rows = page.locator("[data-conversation-id]");
  await rows.first().focus();
  const r0 = await page.evaluate(() => document.activeElement.getAttribute("data-conversation-id"));
  await page.keyboard.press("ArrowDown");
  const r1 = await page.evaluate(() => document.activeElement.getAttribute("data-conversation-id"));
  console.log(JSON.stringify({ firstTab: first, skipLinkVisibleOnFocus: visible, afterEnter: after, arrowDownMoved: r0 !== r1 && !!r1 }));
  await browser.close();
})();
