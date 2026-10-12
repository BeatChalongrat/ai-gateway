#!/usr/bin/env node
// AM15 — a run that waits for its model, read on the built web client the way a person reads it
// (CARD-RULES §6b: a test that builds the card itself does not prove the page shows it). Test stack
// only, the test stack's own account, a browser of its own.
//
//   look <install id> <name>    opens the agent's page, waits for the newest run's card, and writes down
//                               what is on the screen: the card's title, every notice, whether a files
//                               card is there — and a screenshot (results/walk/<name>.png)
//
//   SMOKE_TARGET=test node scripts/probes/AM15/walk.js look <install id> bank-waiting
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") { console.error("walk.js runs on the test stack only (SMOKE_TARGET=test)"); process.exit(2); }
const OUT = path.join(__dirname, "results", "walk");
fs.mkdirSync(OUT, { recursive: true });
const WEB = process.env.AM15_WEB || "http://localhost:13002";
const squash = (s) => String(s || "").replace(/\s+/g, " ").trim();

async function look(install, name) {
  let chromium = null;
  for (const d of [path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"), path.join(L.ROOT, "apps", "desktop", "node_modules", "playwright-core")]) {
    try { ({ chromium } = require(d)); break; } catch { /* next */ }
  }
  if (!chromium) throw new Error("playwright-core not found (apps/web-chat/node_modules)");
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, locale: "th-TH" });
    await ctx.addCookies([{ name: "portal_access_token", value: L.mintPortalToken(L.TS_USER, 1800), domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax", secure: false }]);
    const page = await ctx.newPage();
    await page.goto(`${WEB}/chat/agents/i/${install}`, { waitUntil: "load" });
    await page.locator('[data-testid="am-progress-title"]').last().waitFor({ state: "visible", timeout: 90000 });
    await L.wait(2500);
    const seen = await page.evaluate(() => {
      const text = (el) => (el ? el.innerText.replace(/\s+/g, " ").trim() : "");
      const all = (sel) => [...document.querySelectorAll(sel)].map(text).filter(Boolean);
      return {
        lang: document.documentElement.lang,
        titles: all('[data-testid="am-progress-title"]'),
        notices: all('[data-testid="am-notice"], [data-testid="am-warning-notice"], [role="status"], [role="alert"]'),
        files_cards: document.querySelectorAll('[data-testid="am-outputs"], [data-testid="am-outputs-card"]').length,
        downloads: all('a[download], [data-testid^="am-artifact"]').length,
        body: text(document.querySelector("main") || document.body),
      };
    });
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false });
    const out = {
      at: new Date().toISOString(), url: page.url(), lang: seen.lang, title: seen.titles[seen.titles.length - 1] || "", titles: seen.titles,
      says_model_away: /ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้|cannot be reached right now/.test(seen.body),
      says_model_back: /ติดต่อโมเดลได้แล้ว|can be reached again/.test(seen.body),
      says_waiting_for_your_answer: /รอคำตอบของคุณ|Waiting for your answer/.test(seen.body),
      says_unread: /อ่านข้อความที่พิมพ์มาไม่สำเร็จ|could not be read/.test(seen.body),
      says_done_as_asked: /ทำตามที่ขอแล้ว|Done as asked/.test(seen.body),
      says_finished: /เสร็จแล้วครับ/.test(seen.body),
      files_cards: seen.files_cards, downloads: seen.downloads,
      away_sentence: squash((/หยุดรอไว้ก่อนครับ[^\n]{0,260}/.exec(seen.body) || [""])[0]),
      back_sentence: squash((/ติดต่อโมเดลได้แล้ว[^\n]{0,80}/.exec(seen.body) || [""])[0]),
      screenshot: path.join("scripts", "probes", "AM15", "results", "walk", `${name}.png`),
    };
    fs.writeFileSync(path.join(OUT, `${name}.json`), JSON.stringify({ ...out, body: seen.body.slice(-2500) }, null, 1));
    console.log(JSON.stringify(out, null, 1));
  } finally {
    await browser.close();
  }
}

const [cmd, install, name] = process.argv.slice(2);
if (cmd !== "look" || !install) { console.error("usage: SMOKE_TARGET=test node scripts/probes/AM15/walk.js look <install id> <name>"); process.exit(2); }
look(install, name || "look").catch((e) => { console.error(String(e && e.message || e)); process.exit(1); });
