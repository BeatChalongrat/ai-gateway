process.env.PAGE = "http://localhost:3002"; process.env.API = "http://localhost:4000/api/v1"; process.env.EDGE = "ai-gateway-chat-edge-api";
const { PAGE, token, openContext, wait } = require("../fix122/lib");
(async () => {
  const id = process.argv[2];
  const { browser, ctx } = await openContext(token());
  const page = await ctx.newPage();
  await page.goto(`${PAGE}/chat/${id}`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForSelector("[data-msg-index], [data-testid=\"error-banner\"], main", { timeout: 120000 }); console.log("url", page.url(), "rows", await page.locator("[data-msg-index]").count(), "text", (await page.locator("main").textContent().catch(()=>""))?.slice(0,200));
  await wait(3000);
  const card = await page.evaluate(() => { const rows = [...document.querySelectorAll("[data-msg-index]")]; const r = rows[rows.length - 1]; return { rows: rows.length, alerts: r.querySelectorAll('[role="alert"]').length, alertText: r.querySelector('[role="alert"]')?.textContent?.slice(0, 120) ?? null, errorLabel: r.querySelector('[data-testid="error-label"]')?.textContent ?? null }; });
  console.log(JSON.stringify(card));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
