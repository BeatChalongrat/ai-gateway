// FIX128 D4 — what overflows at a given width (read-only: opens /chat and one chat, types nothing).
process.env.PAGE = process.env.PAGE || "http://localhost:3002"; process.env.API = process.env.API || "http://localhost:4000/api/v1"; process.env.EDGE = process.env.EDGE || "ai-gateway-chat-edge-api";
const { PAGE, token, openContext, wait } = require("../fix122/lib");
(async () => {
  const width = Number(process.argv[2] || 360), conv = process.argv[3];
  const { browser, ctx } = await openContext(token(), { viewport: { width, height: 740 } });
  const page = await ctx.newPage();
  const out = {};
  for (const url of [`${PAGE}/chat`, conv ? `${PAGE}/chat/${conv}` : null].filter(Boolean)) {
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 60000 }); await wait(2500);
    out[url.replace(PAGE, "")] = await page.evaluate((w) => {
      const doc = document.documentElement;
      const wide = [];
      for (const el of document.querySelectorAll("body *")) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const cs = getComputedStyle(el);
        if (cs.position === "fixed" && (r.right <= 0 || r.left >= w)) continue; // off-canvas drawers
        if (r.right > w + 1 && !el.closest('[aria-hidden="true"]')) {
          wide.push({ tag: el.tagName.toLowerCase(), cls: (el.className?.baseVal ?? el.className ?? "").toString().slice(0, 90), right: Math.round(r.right), testid: el.getAttribute("data-testid") });
        }
      }
      // keep only outermost offenders
      return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth, offenders: wide.slice(0, 12) };
    }, width);
  }
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
