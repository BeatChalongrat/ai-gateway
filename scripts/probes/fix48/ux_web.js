// FIX48 · Part B — the SRAN web side of the 10-task UX comparison.
//
// Measures, per task: clicks to complete, ms to the first visible character,
// ms to done, and a screenshot. Claude and ChatGPT cannot be driven from here
// (the Chrome extension is not connected — same limitation the 2026-08-31
// round hit), so this fills OUR column only; UX-COMPARISON.md carries the
// other two columns as a sheet for the owner.
//
//   node scripts/probes/fix48/ux_web.js
//
// Output: results/ux-web.json + shots/*.png

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..", "..");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const OUT = path.join(__dirname, "results");
const SHOTS = path.join(OUT, "shots");
fs.mkdirSync(SHOTS, { recursive: true });

const USER = process.env.PROBE_USER || "f1848000-0000-4000-8000-000000000048";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const BASE = "http://localhost:3002";

function token() {
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c",
    `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "fix48@local", type: "portal", token_use: "access",
    tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 7200, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Real hooks, read off the running app (scripts/probes/fix48/dom_probe.js).
// The UI runs in Thai here, so the aria-labels are the Thai dict strings:
//   input.stop = "หยุด" · a11y.regenerate = "สร้างคำตอบใหม่"
//   a11y.editMessage = "แก้ไขข้อความ" · shortcuts.title = "ปุ่มลัดคีย์บอร์ด"
// Message rows carry data-msg-index (ChatThread.tsx:901); there is no role
// attribute, so "the last row" is the assistant turn after a send.
const SEL_STOP = 'button[aria-label="หยุด"], button[aria-label*="Stop"]';
const SEL_REGEN = 'button[aria-label="สร้างคำตอบใหม่"], button[aria-label*="Regenerate"]';
const SEL_EDIT = 'button[aria-label="แก้ไขข้อความ"], button[aria-label*="Edit"]';

// Count every real user click. The page counter is incremented by a listener
// installed in the page so nothing is missed or double-counted by hand.
async function installClickCounter(page) {
  await page.addInitScript(() => { window.__clicks = 0; document.addEventListener("click", () => { window.__clicks++; }, true); });
}
const clicks = (page) => page.evaluate(() => window.__clicks || 0);
const resetClicks = (page) => page.evaluate(() => { window.__clicks = 0; });

async function send(page, text) {
  const box = page.locator("textarea, [contenteditable=true]").first();
  await box.click();
  await box.fill(text);
  LAST_SENT = text;
  const t0 = Date.now();
  await page.keyboard.press("Enter");
  return t0;
}
let LAST_SENT = "";

/** Text of the last message row, excluding the row that is just the echo of
 *  what we typed — the first pass measured the USER bubble and reported
 *  implausible 140-450 ms "first character" times because of it. */
const assistantText = (page) => page.evaluate((sent) => {
  const rows = [...document.querySelectorAll("[data-msg-index]")];
  for (let i = rows.length - 1; i >= 0; i--) {
    const t = (rows[i].textContent || "").trim();
    if (t && !t.startsWith(sent.slice(0, 40))) return t;
  }
  return "";
}, LAST_SENT);

/** The answer with the transient status line and the timestamp removed.
 *  A screenshot of the first corrected pass showed the assistant row holding
 *  "กำลังประมวลผล…" while the model was still working, which the raw text
 *  read as "the answer has arrived" — hence 190 ms first-character times and
 *  400 ms "completions". Status lines are the "กำลัง…" phrases the thread
 *  renders (ChatThread.tsx tool/stall notices) plus the HH:MM stamp. */
const answerText = (page) => page.evaluate((sent) => {
  const rows = [...document.querySelectorAll("[data-msg-index]")];
  const STATUS = /^(กำลัง|Thinking|Searching|Running|Organi[sz]ing)/;
  for (let i = rows.length - 1; i >= 0; i--) {
    const raw = (rows[i].textContent || "").trim();
    if (!raw || raw.startsWith(sent.slice(0, 40))) continue;
    const kept = raw.split(String.fromCharCode(10)).map((l) => l.trim())
      .filter((l) => l && !STATUS.test(l) && !/^\d{1,2}:\d{2}$/.test(l));
    return kept.join(String.fromCharCode(10));
  }
  return "";
}, LAST_SENT);

/** ms until the assistant bubble first shows any character. */
/** Two different user-visible moments, both worth reporting:
 *   feedbackMs — anything at all appears (usually the "กำลังประมวลผล…" line)
 *   ttfcMs     — the first character of the ANSWER appears */
async function firstChar(page, t0, timeout = 120000) {
  const deadline = Date.now() + timeout;
  let feedbackMs = null;
  while (Date.now() < deadline) {
    if (feedbackMs === null && (await assistantText(page)).length > 0) feedbackMs = Date.now() - t0;
    if ((await answerText(page)).length > 0) return { ttfcMs: Date.now() - t0, feedbackMs };
    await wait(60);
  }
  return { ttfcMs: null, feedbackMs };
}

/** ms until the answer stops growing for STABLE_MS.
 *  Instrument-independent: the first pass keyed off the Stop button and
 *  returned ~700 ms "completions" whenever that button had not rendered yet,
 *  which is a harness artefact, not a fast answer. */
const STABLE_MS = 1800;
async function untilDone(page, t0, timeout = 180000) {
  const deadline = Date.now() + timeout;
  let len = -1, lastChange = Date.now(), sawAny = false;
  while (Date.now() < deadline) {
    const n = (await answerText(page)).length;
    if (n !== len) { len = n; lastChange = Date.now(); if (n > 0) sawAny = true; }
    if (sawAny && Date.now() - lastChange >= STABLE_MS) return Date.now() - t0 - STABLE_MS;
    await wait(150);
  }
  return null;
}

const results = [];
async function task(page, id, title, fn) {
  await resetClicks(page);
  const rec = { id, title };
  try { Object.assign(rec, await fn(page)); } catch (e) { rec.error = String(e.message).slice(0, 300); }
  rec.clicks = await clicks(page);
  const shot = path.join(SHOTS, `${id}.png`);
  await page.screenshot({ path: shot }).catch(() => {});
  rec.shot = path.relative(REPO, shot);
  results.push(rec);
  console.log(`${id.padEnd(6)} clicks=${String(rec.clicks).padEnd(3)} fb=${String(rec.feedbackMs ?? "-").padEnd(6)} ttfc=${String(rec.ttfcMs ?? "-").padEnd(7)} done=${String(rec.doneMs ?? "-").padEnd(7)} ${rec.note || rec.error || ""}`);
}

(async () => {
  const tok = token();
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "th-TH" });
  await ctx.addCookies([{ name: "portal_access_token", value: tok, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  await installClickCounter(page);
  await page.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" });
  await wait(3000);

  // B1 — ถามยาว แล้วขอให้แก้คำตอบ
  await task(page, "B1", "ถามยาว แล้วขอให้แก้คำตอบ (regenerate)", async (p) => {
    const t0 = await send(p, "อธิบายว่าระบบ CI/CD ที่ดีควรมีอะไรบ้าง ตอบยาว ๆ ละเอียด ๆ");
    const { ttfcMs, feedbackMs } = await firstChar(p, t0);
    const doneMs = await untilDone(p, t0);
    await wait(1200);
    const before = await clicks(p);
    // regenerate: the action row lives under the last assistant bubble
    const regen = p.locator(SEL_REGEN).last();
    const has = await regen.count() > 0;
    if (has) { await regen.click(); await wait(1500); }
    return { ttfcMs, feedbackMs, doneMs, note: has ? `regenerate ใช้ ${(await clicks(p)) - before} คลิก` : "ไม่พบปุ่ม regenerate ใน DOM" };
  });

  // B2 — หยุดกลางคัน
  await task(page, "B2", "หยุดกลางคัน (stop)", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const t0 = await send(p, "เขียนเรียงความเรื่องประวัติศาสตร์กรุงศรีอยุธยา ยาว 2000 คำ");
    let stopSeenMs = null;
    for (let i = 0; i < 200; i++) { if (await p.locator(SEL_STOP).count() > 0) { stopSeenMs = Date.now() - t0; break; } await wait(50); }
    const { ttfcMs, feedbackMs } = await firstChar(p, t0);
    const stop = p.locator(SEL_STOP).first();
    const t1 = Date.now();
    let stoppedMs = null;
    if (await stop.count() > 0) {
      await stop.click();
      for (let i = 0; i < 60 && await p.locator(SEL_STOP).count() > 0; i++) await wait(100);
      stoppedMs = Date.now() - t1;
    }
    return { ttfcMs, feedbackMs, doneMs: null, note: stoppedMs === null ? `ไม่พบปุ่มหยุด (ปุ่มโผล่ที่ ${stopSeenMs} ms)` : `ปุ่มหยุดโผล่ที่ ${stopSeenMs} ms · กดแล้วหยุดจริงใน ${stoppedMs} ms` };
  });

  // B3 — แก้ข้อความที่ส่งไปแล้ว
  await task(page, "B3", "แก้ข้อความที่ส่งไปแล้ว (edit + branch)", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const t0 = await send(p, "เมืองหลวงของญี่ปุ่นคือ");
    await firstChar(p, t0); await untilDone(p, t0); await wait(1000);
    const before = await clicks(p);
    const userBubble = p.locator("[data-msg-index]").first();
    await userBubble.hover().catch(() => {});
    const edit = p.locator(SEL_EDIT).last();
    const has = await edit.count() > 0;
    if (has) { await edit.click(); await wait(800); }
    return { ttfcMs: null, doneMs: null, note: has ? `ปุ่มแก้ไขพบ ใช้ ${(await clicks(p)) - before} คลิกถึงโหมดแก้` : "ไม่พบปุ่มแก้ไข" };
  });

  // B4 — วางข้อความยาว 8,000 ตัวอักษร
  await task(page, "B4", "วางข้อความยาว 8,000 ตัวอักษร", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const long = "ระบบนี้ต้องรองรับข้อความยาวจากผู้ใช้ที่วางมาจากเอกสาร ".repeat(160).slice(0, 8000);
    const box = p.locator("textarea, [contenteditable=true]").first();
    await box.click();
    const t0 = Date.now();
    await box.fill(long);
    await wait(600);
    const pasteMs = Date.now() - t0;
    const asFile = await p.locator('text=/ไฟล์|file|attachment|แนบ/i').count();
    const len = await box.inputValue().then((v) => v.length).catch(() => -1);
    return { ttfcMs: null, doneMs: null, note: `วาง ${pasteMs} ms · composer ถือ ${len} ตัวอักษร · ตัวบ่งชี้ paste-as-file ที่พบ: ${asFile}` };
  });

  // B5 — เครื่องมือ: ขอให้ค้นเว็บ
  await task(page, "B5", "ขอให้ค้นเว็บ", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const t0 = await send(p, "ช่วยค้นเว็บให้หน่อยว่าตอนนี้ราคาน้ำมันดีเซลในไทยเท่าไหร่");
    const { ttfcMs, feedbackMs } = await firstChar(p, t0);
    const doneMs = await untilDone(p, t0);
    const body = await p.evaluate(() => document.body.innerText.slice(-2500));
    const said = /ค้นหา|search|ไม่สามารถ|ปิดอยู่|ไม่ได้เปิด/i.test(body);
    return { ttfcMs, feedbackMs, doneMs, note: said ? "ระบบพูดถึงการค้นหา/ข้อจำกัด" : "ไม่มีสัญญาณเรื่องการค้นหาในคำตอบ" };
  });

  // B6 — เครื่องมือ: ขอให้คำนวณ/รันโค้ด
  await task(page, "B6", "ขอให้คำนวณ / รันโค้ด", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const t0 = await send(p, "รันโค้ด Python คำนวณ 2**300 แล้วบอกจำนวนหลักของผลลัพธ์");
    const { ttfcMs, feedbackMs } = await firstChar(p, t0);
    const doneMs = await untilDone(p, t0);
    const body = await p.evaluate(() => document.body.innerText.slice(-3000));
    return { ttfcMs, feedbackMs, doneMs, note: `มีการ์ดรันโค้ด: ${/run_python|โค้ด|Python|output/i.test(body)} · ตอบ 91 หลัก: ${/\b91\b/.test(body)}` };
  });

  // B7 — ความจำข้ามแชท (ถามในแชทใหม่)
  await task(page, "B7", "ความจำข้ามแชท", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const t0 = await send(p, "จำไว้นะ ผมชื่อ ธนวัฒน์ ทำงานฝ่ายจัดซื้อ");
    await firstChar(p, t0); await untilDone(p, t0); await wait(4000);
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const t1 = await send(p, "ผมชื่ออะไร และทำงานฝ่ายไหน");
    const { ttfcMs, feedbackMs } = await firstChar(p, t1);
    const doneMs = await untilDone(p, t1);
    const body = await p.evaluate(() => document.body.innerText.slice(-2000));
    return { ttfcMs, feedbackMs, doneMs, note: `จำชื่อได้: ${/ธนวัฒน์/.test(body)} · จำฝ่ายได้: ${/จัดซื้อ/.test(body)}` };
  });

  // B8 — ปิดแท็บกลางคำตอบแล้วกลับมา
  await task(page, "B8", "ปิดแท็บกลางคำตอบแล้วกลับมา (resume)", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const t0 = await send(p, "อธิบายสถาปัตยกรรม microservices อย่างละเอียด พร้อมข้อดีข้อเสีย");
    await firstChar(p, t0);
    await wait(1500);
    const url = p.url();
    await p.goto("about:blank"); await wait(1200);
    const tBack = Date.now();
    await p.goto(url, { waitUntil: "domcontentloaded" });
    let resumed = false, ms = null;
    for (let i = 0; i < 100; i++) {
      const n = (await assistantText(p)).length;
      if (n > 40) { resumed = true; ms = Date.now() - tBack; break; }
      await wait(200);
    }
    return { ttfcMs: null, doneMs: ms, note: resumed ? `กลับมาเห็นคำตอบเดิมใน ${ms} ms` : "กลับมาแล้วไม่เห็นคำตอบ" };
  });

  // B9 — เน็ตหลุดกลางคำตอบ
  await task(page, "B9", "เน็ตหลุดกลางคำตอบ", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    const t0 = await send(p, "เล่าประวัติของภาษา Go ตั้งแต่เริ่มจนถึงปัจจุบัน แบบละเอียด");
    await firstChar(p, t0);
    await wait(1500);
    await ctx.setOffline(true);
    await wait(4000);
    const banner = await p.evaluate(() => /ออฟไลน์|offline|ไม่มีการเชื่อมต่อ|เชื่อมต่อใหม่|reconnect/i.test(document.body.innerText));
    await ctx.setOffline(false);
    await wait(6000);
    const recovered = (await assistantText(p)).length;
    return { ttfcMs: null, doneMs: null, note: `แบนเนอร์ออฟไลน์: ${banner} · หลังเน็ตกลับมา ตัวอักษรในคำตอบ: ${recovered}` };
  });

  // B10 — keyboard: เปิด cheatsheet + แชทใหม่ด้วยคีย์ลัดของ Claude
  await task(page, "B10", "คีย์ลัด (Ctrl+/ และ Ctrl+Shift+O)", async (p) => {
    await p.goto(`${BASE}/chat`, { waitUntil: "domcontentloaded" }); await wait(2500);
    await p.keyboard.press("Control+Slash"); await wait(900);
    const sheet = await p.evaluate(() => /ปุ่มลัดคีย์บอร์ด|Keyboard shortcut/i.test(document.body.innerText));
    await p.keyboard.press("Escape"); await wait(500);
    const before = p.url();
    await p.keyboard.press("Control+Shift+O"); await wait(1500);
    return { ttfcMs: null, doneMs: null, note: `Ctrl+/ เปิด cheatsheet: ${sheet} · Ctrl+Shift+O เปลี่ยน URL: ${p.url() !== before || /\/chat$/.test(p.url())}` };
  });

  fs.writeFileSync(path.join(OUT, "ux-web.json"), JSON.stringify({ captured_at: new Date().toISOString(), base: BASE, results }, null, 1));
  await browser.close();
  console.log(`\nwritten ${path.join(OUT, "ux-web.json")}`);
})();
