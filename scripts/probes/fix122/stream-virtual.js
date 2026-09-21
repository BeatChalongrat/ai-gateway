#!/usr/bin/env node
// FIX122 Part A — "do not break": a live turn inside a VIRTUALIZED thread.
//
//   node scripts/probes/fix122/stream-virtual.js <tag> <convId>
//   MODEL is whatever the conversation is bound to.
//
// Turn 1 (follow): send, do nothing. At the end the view must be at the
//   bottom with the newest row on screen.
// Turn 2 (reader): send, and once text is streaming wheel up 700 px, select
//   a sentence in a row on screen, and watch until the turn ends: the view
//   must not be dragged back down, the row being read must not move, and the
//   selection must survive.
// Also samples the composer: its value in the frame after Enter.
// Output: scripts/probes/fix122/results/<tag>-stream-virtual.json
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { REPO, PAGE, token, openContext, chromium, wait } = require("./lib");

const OUT = path.join(__dirname, "results");
const STOP = '[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="หยุด"]';
const COMPOSER = 'textarea[data-shortcut="composer"]';
const PROMPT1 = process.env.PROMPT1 || "[FIX122] เขียนเรียงความภาษาไทยประมาณ 300 คำ เรื่องประวัติรถไฟไทย แบ่งเป็น 4 ย่อหน้า";
const PROMPT2 = process.env.PROMPT2 || "[FIX122] เขียนต่ออีก 300 คำ เรื่องรถไฟความเร็วสูง แบ่งเป็น 4 ย่อหน้า";

const geo = () => {
  const el = document.querySelector('[data-testid="turn-spacer"]').parentElement;
  const rows = [...el.querySelectorAll("[data-msg-index]")];
  const top = el.getBoundingClientRect().top;
  const last = rows.reduce((a, r) => (Number(r.dataset.msgIndex) > Number(a?.dataset.msgIndex ?? -1) ? r : a), null);
  const lb = last ? last.getBoundingClientRect() : null;
  return { st: Math.round(el.scrollTop), fromBottom: Math.round(el.scrollHeight - el.clientHeight - el.scrollTop), rows: rows.length, lastIdx: last ? Number(last.dataset.msgIndex) : -1, lastOnScreen: lb ? lb.bottom > top && lb.top < top + el.clientHeight : false, spacer: parseFloat(document.querySelector('[data-testid="turn-spacer"]').style.height) || 0 };
};

async function send(page, text) {
  await page.click(COMPOSER);
  await page.fill(COMPOSER, text);
  await page.keyboard.press("Enter");
  // the composer in the frame after Enter, and ~1 s later (draft restore window)
  const v0 = await page.evaluate((sel) => new Promise((r) => requestAnimationFrame(() => r(document.querySelector(sel)?.value ?? null))), COMPOSER);
  await wait(1200);
  const v1 = await page.evaluate((sel) => document.querySelector(sel)?.value ?? null, COMPOSER);
  return { composerNextFrame: v0, composerAfter1s: v1 };
}

async function waitStreamingText(page, minChars, timeoutMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const n = await page.evaluate(() => { const el = document.querySelector('[data-testid="turn-spacer"]').parentElement; const rows = [...el.querySelectorAll("[data-msg-index]")]; const last = rows.reduce((a, r) => (Number(r.dataset.msgIndex) > Number(a?.dataset.msgIndex ?? -1) ? r : a), null); return last ? (last.textContent || "").length : 0; });
    if (n >= minChars) return true;
    if (!(await page.locator(STOP).count())) return false;
    await wait(500);
  }
  return false;
}
const waitEnd = async (page, timeoutMs) => { const t0 = Date.now(); while (Date.now() - t0 < timeoutMs) { if (!(await page.locator(STOP).count())) return true; await wait(500); } return false; };

(async () => {
  const [tag, convId] = process.argv.slice(2);
  fs.mkdirSync(OUT, { recursive: true });
  let browser, ctx, page;
  if (PAGE.startsWith("cdp:")) { browser = await chromium.connectOverCDP(`http://127.0.0.1:${PAGE.slice(4)}`); ctx = browser.contexts()[0]; page = ctx.pages()[0]; }
  else { ({ browser, ctx } = await openContext(token())); page = await ctx.newPage(); }
  const base = PAGE.startsWith("cdp:") ? new URL(page.url()).origin : PAGE;
  await page.goto(`${base}/chat/${convId}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="turn-spacer"]', { state: "attached", timeout: 60000 });
  await wait(2500);
  const out = { tag, convId, at: new Date().toISOString(), opened: await page.evaluate(geo) };

  // ── turn 1: follow ─────────────────────────────────────────────────
  const t1 = Date.now();
  out.turn1 = { ...(await send(page, PROMPT1)) };
  out.turn1.afterSend = await page.evaluate(geo);
  out.turn1.gotText = await waitStreamingText(page, 400, 300000);
  out.turn1.midStream = await page.evaluate(geo);
  out.turn1.ended = await waitEnd(page, 300000);
  await wait(800);
  out.turn1.atEnd = await page.evaluate(geo);
  out.turn1.seconds = Math.round((Date.now() - t1) / 1000);
  await page.screenshot({ path: path.join(OUT, `${tag}-turn1-end.png`) });

  // ── turn 2: a reader who scrolled up ───────────────────────────────
  const t2 = Date.now();
  out.turn2 = { ...(await send(page, PROMPT2)) };
  out.turn2.gotText = await waitStreamingText(page, 150, 300000);
  const box = await page.evaluate(() => { const r = document.querySelector('[data-testid="turn-spacer"]').parentElement.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await page.mouse.move(box.x, box.y);
  for (let i = 0; i < 7; i++) { await page.mouse.wheel(0, -100); await wait(40); }
  await wait(500);
  // select inside the first row that starts on screen, and start the watch
  out.turn2.watchStart = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="turn-spacer"]').parentElement;
    const top = el.getBoundingClientRect().top;
    const row = [...el.querySelectorAll("[data-msg-index]")].find((r) => r.getBoundingClientRect().top >= top && r.getBoundingClientRect().top < top + el.clientHeight - 80);
    if (!row) return null;
    const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.textContent.trim().length > 12 ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP) });
    const node = walker.nextNode();
    let selected = "";
    if (node) { const range = document.createRange(); range.setStart(node, 0); range.setEnd(node, Math.min(12, node.textContent.length)); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range); selected = sel.toString(); }
    const key = (row.textContent || "").slice(0, 30);
    const W = (window.__watch = { key, selected, y0: row.getBoundingClientRect().top - top, st0: el.scrollTop, maxStIncrease: 0, maxYShift: 0, selectionLostAt: null, frames: 0, rowGoneFrames: 0, stop: false });
    const tick = () => {
      if (W.stop) return;
      W.frames++;
      W.maxStIncrease = Math.max(W.maxStIncrease, el.scrollTop - W.st0);
      const r = [...el.querySelectorAll("[data-msg-index]")].find((x) => (x.textContent || "").slice(0, 30) === key);
      if (r) W.maxYShift = Math.max(W.maxYShift, Math.abs(r.getBoundingClientRect().top - top - W.y0)); else W.rowGoneFrames++;
      if (W.selectionLostAt === null && getSelection().toString() !== selected) W.selectionLostAt = W.frames;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    return { key, selected, y0: Math.round(W.y0), st0: Math.round(W.st0) };
  });
  out.turn2.geoWhileReading = await page.evaluate(geo);
  out.turn2.ended = await waitEnd(page, 300000);
  await wait(800);
  out.turn2.watch = await page.evaluate(() => { const W = window.__watch; if (!W) return null; W.stop = true; return { frames: W.frames, maxScrollTopIncreasePx: Math.round(W.maxStIncrease), maxRowShiftPx: Math.round(W.maxYShift * 10) / 10, rowGoneFrames: W.rowGoneFrames, selectionLostAtFrame: W.selectionLostAt, selectionAtEnd: getSelection().toString(), selected: W.selected }; });
  out.turn2.atEnd = await page.evaluate(geo);
  out.turn2.pill = await page.evaluate(() => !!document.querySelector('button[aria-label*="ล่าสุด"], button[aria-label*="latest"]') || [...document.querySelectorAll("button")].some((b) => /ข้อความใหม่|New messages/.test(b.textContent || "")));
  out.turn2.seconds = Math.round((Date.now() - t2) / 1000);
  await page.screenshot({ path: path.join(OUT, `${tag}-turn2-end.png`) });

  fs.writeFileSync(path.join(OUT, `${tag}-stream-virtual.json`), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
