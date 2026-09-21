#!/usr/bin/env node
// FIX122 Part A — what a long conversation does when it opens, when it is
// scrolled, and when older messages load. Measured from outside the app, per
// animation frame, on a running page.
//
//   node scripts/probes/fix122/long-thread.js <tag> <convId>[,<convId>…]
//   PAGE / API / EDGE as in lib.js;  cdp:<port> as PAGE drives the desktop.
//
// Per conversation:
//   open      frames from the first frame the thread exists until the newest
//             message is on screen; how many of those frames showed no row at
//             all ("blank"); where the view ended up.
//   wheel     one small wheel-up (120 px): how far the view actually moved.
//   older     scroll to the top, load older messages (as many pages as
//             PAGES, default 3): per page, how far the row the reader was
//             looking at moved on screen — worst single frame and final.
// Output: scripts/probes/fix122/results/<tag>-long-thread.json
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { REPO, PAGE, token, openContext, chromium, wait } = require("./lib");

const OUT = path.join(__dirname, "results");
const PAGES = Number(process.env.PAGES || 3);

// Installed before any page script: samples every frame from navigation.
function sampler() {
  const S = (window.__lt = { frames: [], t0: performance.now() });
  const tick = () => {
    const spacer = document.querySelector('[data-testid="turn-spacer"]');
    const el = spacer ? spacer.parentElement : null;
    if (el) {
      const rows = el.querySelectorAll("[data-msg-index]");
      let lastIdx = -1, lastVisible = false, lastText = "";
      const vr = el.getBoundingClientRect();
      for (const r of rows) {
        const i = Number(r.dataset.msgIndex);
        if (i > lastIdx) {
          lastIdx = i;
          const b = r.getBoundingClientRect();
          lastVisible = b.bottom > vr.top && b.top < vr.bottom;
          lastText = (r.textContent || "").slice(0, 40);
        }
      }
      let visibleRows = 0;
      for (const r of rows) {
        const b = r.getBoundingClientRect();
        if (b.bottom > vr.top && b.top < vr.bottom && b.height > 0) visibleRows++;
      }
      S.frames.push({ t: Math.round(performance.now() - S.t0), rows: rows.length, visibleRows, lastIdx, lastVisible, lastText, scrollTop: Math.round(el.scrollTop), scrollHeight: el.scrollHeight, clientHeight: el.clientHeight });
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

// The row the reader is looking at: first row whose bottom is below the
// viewport top. Tracked by its TEXT (indexes shift when older rows arrive).
function landmark() {
  const el = document.querySelector('[data-testid="turn-spacer"]').parentElement;
  const top = el.getBoundingClientRect().top;
  for (const r of el.querySelectorAll("[data-msg-index]")) {
    const b = r.getBoundingClientRect();
    if (b.bottom > top + 1) return { text: (r.textContent || "").slice(0, 30), y: b.top - top };
  }
  return null;
}
function trackLandmark(text) {
  const el = document.querySelector('[data-testid="turn-spacer"]').parentElement;
  const T = (window.__track = { ys: [], stop: false });
  const tick = () => {
    if (T.stop) return;
    const top = el.getBoundingClientRect().top;
    let y = null;
    for (const r of el.querySelectorAll("[data-msg-index]")) {
      if ((r.textContent || "").slice(0, 30) === text) { y = r.getBoundingClientRect().top - top; break; }
    }
    T.ys.push(y);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

async function measure(page, convId) {
  const out = { convId };
  const base = PAGE.startsWith("cdp:") ? new URL(page.url()).origin : PAGE;
  await page.goto(`${base}/chat/${convId}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="turn-spacer"]', { timeout: 60000, state: "attached" });
  await wait(2500);
  const frames = await page.evaluate(() => window.__lt.frames);
  const firstShown = frames.findIndex((f) => f.lastVisible);
  const settled = frames[frames.length - 1];
  out.open = {
    framesSampled: frames.length,
    blankFrames: frames.filter((f) => f.visibleRows === 0).length,
    framesUntilNewestVisible: firstShown,
    msUntilNewestVisible: firstShown >= 0 ? frames[firstShown].t - frames[0].t : null,
    newestVisibleAtEnd: settled.lastVisible,
    visibleRowsAtEnd: settled.visibleRows,
    distanceFromBottomAtEnd: settled.scrollHeight - settled.clientHeight - settled.scrollTop,
    newestText: settled.lastText,
    first5: frames.slice(0, 5),
  };

  // one small wheel-up
  const box = await page.evaluate(() => { const r = document.querySelector('[data-testid="turn-spacer"]').parentElement.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await page.mouse.move(box.x, box.y);
  const before = await page.evaluate(() => document.querySelector('[data-testid="turn-spacer"]').parentElement.scrollTop);
  const lmBefore = await page.evaluate(landmark);
  await page.evaluate(trackLandmark, lmBefore ? lmBefore.text : "");
  await page.mouse.wheel(0, -120);
  await wait(900);
  const after = await page.evaluate(() => { window.__track.stop = true; const el = document.querySelector('[data-testid="turn-spacer"]').parentElement; return { top: el.scrollTop, ys: window.__track.ys, visibleRows: window.__lt.frames[window.__lt.frames.length - 1].visibleRows }; });
  const ys = after.ys.filter((y) => y !== null);
  out.wheel = { scrollTopBefore: Math.round(before), scrollTopAfter: Math.round(after.top), moved: Math.round(after.top - before), landmarkMoved: lmBefore && ys.length ? Math.round(ys[ys.length - 1] - lmBefore.y) : null, landmarkLostFrames: after.ys.length - ys.length, visibleRowsAfter: after.visibleRows };

  // older pages
  out.older = [];
  for (let p = 0; p < PAGES; p++) {
    const btn = page.getByRole("button", { name: /โหลดข้อความก่อนหน้า|Load earlier/ });
    if (!(await btn.count())) break;
    await page.evaluate(() => { document.querySelector('[data-testid="turn-spacer"]').parentElement.scrollTop = 0; });
    await page.mouse.wheel(0, -50);
    await wait(500);
    const lm = await page.evaluate(landmark);
    if (!lm) break;
    const rowsBefore = await page.evaluate(() => window.__lt.frames[window.__lt.frames.length - 1].lastIdx + 1);
    await page.evaluate(trackLandmark, lm.text);
    await btn.click();
    await page.waitForFunction((n) => window.__lt.frames[window.__lt.frames.length - 1].lastIdx + 1 > n, rowsBefore, { timeout: 30000 }).catch(() => {});
    await wait(1200);
    const res = await page.evaluate(() => { window.__track.stop = true; return { ys: window.__track.ys, total: window.__lt.frames[window.__lt.frames.length - 1].lastIdx + 1, scrollTop: document.querySelector('[data-testid="turn-spacer"]').parentElement.scrollTop }; });
    const seen = res.ys.filter((y) => y !== null);
    const deltas = seen.map((y) => y - lm.y);
    out.older.push({
      page: p + 1, rowsBefore, rowsAfter: res.total, landmark: lm.text,
      landmarkYBefore: Math.round(lm.y), landmarkYAfter: seen.length ? Math.round(seen[seen.length - 1]) : null,
      finalShiftPx: seen.length ? Math.round(deltas[deltas.length - 1] * 10) / 10 : null,
      worstFrameShiftPx: seen.length ? Math.round(Math.max(...deltas.map(Math.abs)) * 10) / 10 : null,
      framesLandmarkNotInDom: res.ys.length - seen.length, frames: res.ys.length, scrollTopAfter: Math.round(res.scrollTop),
    });
  }
  return out;
}

(async () => {
  const [tag, ids] = process.argv.slice(2);
  if (!tag || !ids) { console.error("usage: long-thread.js <tag> <convId,convId…>"); process.exit(2); }
  fs.mkdirSync(OUT, { recursive: true });
  let browser, ctx, page;
  if (PAGE.startsWith("cdp:")) {
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${PAGE.slice(4)}`);
    ctx = browser.contexts()[0];
    page = ctx.pages()[0];
    await page.addInitScript(sampler);
  } else {
    ({ browser, ctx } = await openContext(token()));
    await ctx.addInitScript(sampler);
    page = await ctx.newPage();
  }
  const results = [];
  for (const id of ids.split(",")) {
    const r = await measure(page, id);
    results.push(r);
    console.log(JSON.stringify({ conv: id, open: { blankFrames: r.open.blankFrames, framesUntilNewestVisible: r.open.framesUntilNewestVisible, newestVisibleAtEnd: r.open.newestVisibleAtEnd, fromBottom: r.open.distanceFromBottomAtEnd, newest: r.open.newestText }, wheel: r.wheel, older: r.older.map((o) => ({ p: o.page, rows: `${o.rowsBefore}→${o.rowsAfter}`, final: o.finalShiftPx, worst: o.worstFrameShiftPx, lost: o.framesLandmarkNotInDom })) }));
    await page.screenshot({ path: path.join(OUT, `${tag}-${id.slice(0, 8)}.png`) }).catch(() => {});
  }
  fs.writeFileSync(path.join(OUT, `${tag}-long-thread.json`), JSON.stringify({ tag, page: PAGE, at: new Date().toISOString(), results }, null, 2));
  await browser.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
