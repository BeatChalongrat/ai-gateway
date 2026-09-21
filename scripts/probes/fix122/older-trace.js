#!/usr/bin/env node
// FIX122 Part A — frame-by-frame trace of ONE "load earlier": scrollTop,
// scrollHeight, the landmark row's y, and which rows are in the DOM.
//   node scripts/probes/fix122/older-trace.js <convId> [pagesBefore]
"use strict";
const { PAGE, token, openContext, wait } = require("./lib");

(async () => {
  const [convId, pagesBefore = "0"] = process.argv.slice(2);
  const { browser, ctx } = await openContext(token());
  const page = await ctx.newPage();
  await page.goto(`${PAGE}/chat/${convId}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="turn-spacer"]', { state: "attached", timeout: 60000 });
  await wait(2000);
  const btn = page.getByRole("button", { name: /โหลดข้อความก่อนหน้า|Load earlier/ });
  const toTop = async () => {
    const box = await page.evaluate(() => { const r = document.querySelector('[data-testid="turn-spacer"]').parentElement.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
    await page.mouse.move(box.x, box.y);
    await page.evaluate(() => { document.querySelector('[data-testid="turn-spacer"]').parentElement.scrollTop = 0; });
    await page.mouse.wheel(0, -50);
    await wait(600);
  };
  for (let i = 0; i < Number(pagesBefore); i++) { await toTop(); await btn.click(); await wait(2000); }
  await toTop();
  await page.evaluate(() => {
    const el = document.querySelector('[data-testid="turn-spacer"]').parentElement;
    const top = el.getBoundingClientRect().top;
    let text = null;
    for (const r of el.querySelectorAll("[data-msg-index]")) { if (r.getBoundingClientRect().bottom > top + 1) { text = (r.textContent || "").slice(0, 30); break; } }
    const T = (window.__tr = { text, frames: [], stop: false });
    const tick = () => {
      if (T.stop) return;
      let y = null; const idx = [];
      for (const r of el.querySelectorAll("[data-msg-index]")) { idx.push(Number(r.dataset.msgIndex)); if ((r.textContent || "").slice(0, 30) === text) y = Math.round(r.getBoundingClientRect().top - top); }
      T.frames.push({ t: Math.round(performance.now()), st: Math.round(el.scrollTop), sh: el.scrollHeight, y, rows: idx.length ? `${Math.min(...idx)}-${Math.max(...idx)}(${idx.length})` : "-" });
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await btn.click();
  await wait(2500);
  const tr = await page.evaluate(() => { window.__tr.stop = true; return window.__tr; });
  console.log("landmark:", tr.text);
  let prev = "";
  for (const f of tr.frames) {
    const line = `st=${f.st} sh=${f.sh} y=${f.y} rows=${f.rows}`;
    if (line !== prev) console.log(f.t, line);
    prev = line;
  }
  await browser.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
