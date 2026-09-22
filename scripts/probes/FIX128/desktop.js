#!/usr/bin/env node
// FIX127 — desktop checks over CDP (the installed e53 launched with
// --remote-debugging-port=9333). Uses the app's own signed-in session.
//
//   node scripts/probes/FIX127/desktop.js <tag> <case,...>
//   c2   send a turn in a [FIX128] chat, reload the page (a fresh page, no
//        cursor), reopen the chat: the answer shows ONCE; the DB has one row.
//   c3   on the new-chat page attach a file (creates the draft chat), then
//        reload without sending: the host deletes the 0-message chat.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const REPO = path.resolve(__dirname, "..", "..", "..");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const OUT = path.join(__dirname, "results");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const COMPOSER = 'textarea[data-shortcut="composer"]';
const STOP = 'button[aria-label*="Stop"], button[aria-label*="หยุด"], [data-testid="stop-button"]';
const sql = (q) => execFileSync("docker", ["exec", "ai-gateway-postgres-webchat", "psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-F", "|", "-c", q], { encoding: "utf8" }).trim();
const noteChat = (tag, id) => fs.appendFileSync(path.join(OUT, `${tag}-chats.txt`), id + "\n");
const waitEnd = async (page, ms) => { const t0 = Date.now(); await wait(1500); while (Date.now() - t0 < ms) { if (!(await page.locator(STOP).count())) return Math.round((Date.now() - t0) / 1000); await wait(500); } return null; };
const convIdOf = (page) => (/\/chat\/([0-9a-f-]{36})/.exec(page.url()) || [])[1] || null;

async function pageOf(browser) {
  const ctx = browser.contexts()[0];
  const page = ctx.pages().find((p) => /localhost:3002/.test(p.url())) || ctx.pages()[0];
  await page.bringToFront();
  return page;
}

async function c2(tag, page) {
  await page.goto("http://localhost:3002/chat", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(COMPOSER, { timeout: 60000 }); await wait(1500);
  await page.fill(COMPOSER, "[FIX128] ตอบสั้นๆ: 7 คูณ 8 เท่ากับเท่าไร");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => /\/chat\/[0-9a-f-]{36}/.test(location.pathname), null, { timeout: 30000 });
  const id = convIdOf(page); noteChat(tag, id);
  const secs = await waitEnd(page, 180000);
  await wait(3000); // the import lands before the terminal; give the row a moment
  const count = () => page.evaluate(() => { const rows = [...document.querySelectorAll("main [data-msg-index]")]; return { rows: rows.length, texts: rows.map((r) => (r.textContent || "").slice(0, 40)) }; });
  const before = await count();
  // a hard navigation: a fresh page with no cursor asks the host `ui.ready`
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("main [data-msg-index]", { timeout: 60000 });
  await wait(4000); // HOSTED_RESUME_PROBE_MS = 700 + replay time, generously
  const after = await count();
  // and once more via the sidebar (another chat, then back)
  await page.goto("http://localhost:3002/chat", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(COMPOSER, { timeout: 60000 }); await wait(1000);
  await page.click(`[data-conversation-id="${id}"]`);
  await page.waitForSelector("main [data-msg-index]", { timeout: 60000 }); await wait(4000);
  const reopened = await count();
  const db = sql(`select role, status, length(content_masked) from chat.messages_masked where conversation_id='${id}' order by created_at`).split("\n");
  const out = { id, secs, before, after, reopened, db };
  out.pass = before.rows === 2 && after.rows === 2 && reopened.rows === 2 && db.length === 2;
  return out;
}

async function c3(tag, page) {
  await page.goto("http://localhost:3002/chat", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(COMPOSER, { timeout: 60000 }); await wait(1500);
  const f = path.join(OUT, "fix127-c3.txt"); fs.writeFileSync(f, "[FIX128] c3 attachment " + Date.now());
  await page.setInputFiles('input[type="file"]', [f]);
  await wait(3000); // the landing page eager-creates the draft conversation for the upload
  const id = await page.evaluate(() => { try { const s = JSON.parse(localStorage.getItem("sran-webchat-composer-drafts") || "{}"); return null; } catch { return null; } });
  // the draft conversation is the newest 0-message chat of this user
  const newest = sql(`select id from chat.conversations where user_id='56726cc2-47d2-4e81-95bd-4aba41d4d677' and created_at > now() - interval '2 minutes' order by created_at desc limit 1`);
  noteChat(tag, newest);
  const beforeRow = sql(`select is_deleted, message_count from chat.conversations where id='${newest}'`);
  await page.reload({ waitUntil: "domcontentloaded" }); // hard navigation without sending
  await page.waitForSelector(COMPOSER, { timeout: 60000 }); await wait(4000);
  const afterRow = sql(`select is_deleted, message_count from chat.conversations where id='${newest}'`);
  const out = { draft: newest, beforeRow, afterRow, ignored: id };
  out.pass = /^f\|0$/.test(beforeRow) && /^t\|0$/.test(afterRow);
  return out;
}

async function main() {
  const [tag, list] = process.argv.slice(2);
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
  const page = await pageOf(browser);
  const results = {};
  for (const c of list.split(",")) {
    const fn = { c2, c3 }[c];
    const t0 = Date.now();
    try { results[c] = await fn(tag, page); } catch (e) { results[c] = { error: String(e).slice(0, 400) }; }
    results[c].ms = Date.now() - t0;
    console.log(c, results[c].pass ? "PASS" : "FAIL", JSON.stringify(results[c]).slice(0, 700));
  }
  fs.writeFileSync(path.join(OUT, `${tag}-desktop.json`), JSON.stringify({ at: new Date().toISOString(), results }, null, 2));
  await browser.close(); // disconnects; the app keeps running
}
main().catch((e) => { console.error(e); process.exit(1); });
