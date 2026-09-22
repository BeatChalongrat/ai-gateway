process.env.PAGE = process.env.PAGE || "http://localhost:3002"; process.env.API = process.env.API || "http://localhost:4000/api/v1"; process.env.EDGE = process.env.EDGE || "ai-gateway-chat-edge-api";
const { PAGE, token, openContext, wait } = require("../fix122/lib");
(async () => {
  const [width, conv, out] = [Number(process.argv[2]), process.argv[3], process.argv[4]];
  const { browser, ctx } = await openContext(token(), { viewport: { width, height: 740 } });
  const page = await ctx.newPage();
  await page.goto(`${PAGE}/chat/${conv}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 60000 }); await wait(2500);
  await page.screenshot({ path: out });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
