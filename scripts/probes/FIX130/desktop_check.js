#!/usr/bin/env node
// FIX130 — desktop check over CDP (the installed e54 launched with
// --remote-debugging-port=9333; the app's own signed-in session).
//
//   node scripts/probes/FIX130/desktop_check.js <tag>
//
// signed in (composer present, not a login page) · page build · a TEMPORARY
// chat asks what the assistant remembers about the owner: with FIX130 a
// temporary chat reads no memory, so the answer has nothing and no
// "ใช้ความจำ" chip shows. Nothing asks the model to remember; the chat is
// [FIX130]-prefixed and deleted by id at the end.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const REPO = "C:/Users/Beat Beat/Desktop/ai-gateway";
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const OUT = path.join(REPO, "scripts", "probes", "FIX130", "results", "desktop");
const COMPOSER = 'textarea[data-shortcut="composer"]';
const STOP = 'button[aria-label*="Stop"], button[aria-label*="หยุด"], [data-testid="stop-button"]';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const sql = (q) => execFileSync("docker", ["exec", "ai-gateway-postgres-webchat", "psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-F", "|", "-c", q], { encoding: "utf8" }).trim();

(async () => {
  const tag = process.argv[2] || "run";
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
  const ctx = browser.contexts()[0];
  const page = ctx.pages().find((p) => /localhost:3002/.test(p.url())) || ctx.pages()[0];
  const out = { ua: await page.evaluate(() => navigator.userAgent) };
  await page.waitForSelector(COMPOSER, { timeout: 60000 });
  out.signedIn = true;
  out.pageBuild = await page.evaluate(async () => (await (await fetch("/api/version")).json()).build);
  await page.screenshot({ path: path.join(OUT, `${tag}-01-signed-in.png`) });
  // a temporary chat: the sidebar's "แชทชั่วคราว"
  await page.getByText(/^(แชทชั่วคราว|Temporary chat)$/).first().click();
  await wait(2500);
  await page.locator(COMPOSER).fill("[FIX130] บอกทุกอย่างที่คุณจำเกี่ยวกับฉันได้ ตอบสั้นๆ เป็นข้อๆ ถ้าไม่มีข้อมูลเลยให้ตอบว่า \"ไม่มีข้อมูล\"");
  await page.keyboard.press("Enter");
  const t0 = Date.now();
  await wait(2000);
  while (Date.now() - t0 < 120000 && (await page.locator(STOP).count())) await wait(700);
  await wait(2500);
  const convId = (/\/chat\/([0-9a-f-]{36})/.exec(page.url()) || [])[1] || null;
  const body = await page.evaluate(() => document.body.innerText);
  out.convId = convId;
  out.row = convId ? sql(`select is_temporary, memory_enabled, coalesce(title,'') from chat.conversations where id='${convId}'`) : null;
  out.answerTail = body.slice(-500);
  out.memoryChip = /ใช้ความจำ\s*\d|Used\s+\d+\s+(saved\s+)?memor/i.test(body);
  // FIX130 (e55) — a temporary chat is not offered past-chat search either.
  out.pastChatSearch = /Search(ed|ing) past chats|ค้นหาแชทที่ผ่านมา|ค้นแชทเก่า/i.test(body);
  out.sources = convId ? Number(sql(`select count(*) from chat.response_context_sources where conversation_id='${convId}' and source_type='saved_memory'`)) : null;
  out.ms = Date.now() - t0;
  await page.screenshot({ path: path.join(OUT, `${tag}-02-temporary-answer.png`) });
  // delete by id through the page's own session
  if (convId) {
    out.deleted = await page.evaluate(async (id) => {
      const base = (window.__SRAN_API_BASE__ || "http://localhost:4000/api/v1");
      const r = await fetch(`${base}/conversations/${id}`, { method: "DELETE", credentials: "include" });
      return r.status;
    }, convId);
    out.rowAfter = sql(`select is_deleted from chat.conversations where id='${convId}'`);
  }
  out.pass = out.signedIn && out.row && out.row.startsWith("t|f") && !out.memoryChip && out.sources === 0 && !out.pastChatSearch;
  fs.writeFileSync(path.join(OUT, `${tag}.json`), JSON.stringify(out, null, 2));
  console.log(out.pass ? "PASS" : "FAIL", JSON.stringify({ ...out, answerTail: out.answerTail.slice(-160) }));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
