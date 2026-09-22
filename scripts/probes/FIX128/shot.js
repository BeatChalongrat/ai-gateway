process.env.PAGE = process.env.PAGE || "http://localhost:3002"; process.env.API = process.env.API || "http://localhost:4000/api/v1"; process.env.EDGE = process.env.EDGE || "ai-gateway-chat-edge-api";
const { PAGE, token, openContext, wait } = require("../fix122/lib");
(async () => {
  const [width, conv, out] = [Number(process.argv[2]), process.argv[3], process.argv[4]];
  const { browser, ctx } = await openContext(token(), { viewport: { width, height: 740 } });
  const page = await ctx.newPage();
  await page.goto(`${PAGE}/chat/${conv}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 60000 }); await wait(2500);
  await page.screenshot({ path: out });
  const geo = await page.evaluate(() => {
    const send = document.querySelector('button[aria-label*="Send"], button[aria-label*="ส่ง"], button[aria-label*="Stop"], button[aria-label*="หยุด"]');
    const r = send?.getBoundingClientRect();
    return { viewport: innerWidth, send: r ? { left: Math.round(r.left), right: Math.round(r.right) } : null, sendInside: !!r && r.left >= 0 && r.right <= innerWidth, scrollWidth: document.documentElement.scrollWidth };
  });
  console.log(JSON.stringify(geo));
  // keyboard: from the top of the document the first Tab is the skip link;
  // Enter on it focuses the composer
  await page.evaluate(() => { (document.activeElement)?.blur?.(); window.scrollTo(0, 0); });
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => document.activeElement?.getAttribute("data-testid"));
  await page.keyboard.press("Enter");
  const afterSkip = await page.evaluate(() => document.activeElement?.getAttribute("data-shortcut"));
  console.log(JSON.stringify({ firstTab: first, afterEnter: afterSkip }));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
