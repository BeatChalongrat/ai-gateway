#!/usr/bin/env node
// FIX130 C1–C4 (+ A3/B2 views) in the REAL page — a production build of web-chat served on
// http://localhost:13002 against the TEST stack (chat-edge :14000, gateway :18080).
// Chromium runs behind a forward proxy we own, so the SSE socket to chat-edge can be
// destroyed for real (a TCP reset, not setOffline) — NIGHT2 live2/d10ui.js method.
//
//   node scripts/probes/FIX130/ts_ux.js <tag> <case>[,<case>…]
//   cases: blank blip wedge files banner pill kb thinking memory
// Screenshots + JSON → results/ux/<tag>/.
"use strict";
const http = require("node:http");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("./ts_lib");

const PAGE = "http://localhost:13002";
const GW = "http://localhost:18080";
const EDGE_PORT = "14000";
const REPO = "C:/Users/Beat Beat/Desktop/ai-gateway";
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const COMPOSER = "textarea[data-shortcut=composer]";

// ---- forward proxy (only chat-edge sockets are tracked / cut) -------------------------
const edgeSockets = new Set();
let blocking = false;
const proxy = http.createServer((req, res) => {
  const u = new URL(req.url);
  const isEdge = u.port === EDGE_PORT;
  if (isEdge && blocking) { req.socket.destroy(); return; }
  const fwd = http.request({ host: u.hostname, port: u.port || 80, path: u.pathname + u.search, method: req.method, headers: req.headers }, (r) => {
    res.writeHead(r.statusCode, r.headers);
    r.pipe(res);
  });
  fwd.on("socket", (s) => { if (isEdge) { edgeSockets.add(s); s.on("close", () => edgeSockets.delete(s)); } });
  fwd.on("error", () => { try { res.destroy(); } catch {} });
  req.pipe(fwd);
});
proxy.on("connect", (req, sock) => { sock.end(); });
const cut = () => { let n = 0; for (const s of edgeSockets) { try { s.destroy(); } catch {} n++; } edgeSockets.clear(); return n; };

// ---- browser ------------------------------------------------------------------------------
let OUT = "";
async function open(who, { width = 1280, height = 800, portalMe = "pass" } = {}) {
  const tok = L.token(who);
  const browser = await chromium.launch({ headless: true, proxy: { server: "http://127.0.0.1:4530", bypass: "<-loopback>" } });
  const ctx = await browser.newContext({ viewport: { width, height }, locale: "th-TH" });
  await ctx.addCookies([{ name: "portal_access_token", value: tok, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const cors = { "access-control-allow-origin": PAGE, "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type,authorization,x-csrf-token", "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS" };
  await ctx.route(`${GW}/**`, async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    if (portalMe === "hang" && /\/portal\/me/.test(req.url())) return; // never settles
    try {
      const r = await fetch(req.url(), { method: req.method(), headers: { cookie: `portal_access_token=${tok}`, "content-type": "application/json" }, body: ["GET", "HEAD"].includes(req.method()) ? undefined : req.postData() ?? undefined });
      return route.fulfill({ status: r.status, headers: { ...cors, "content-type": r.headers.get("content-type") || "application/json" }, body: Buffer.from(await r.arrayBuffer()) });
    } catch (e) { return route.fulfill({ status: 502, headers: cors, body: String(e) }); }
  });
  // never let a probe browser reach the LIVE Portal dev server (a login bounce would carry a test cookie there)
  await ctx.route("http://localhost:3001/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<html><body>PORTAL-BLOCKED-BY-PROBE</body></html>" }));
  const page = await ctx.newPage();
  page.setDefaultTimeout(45000);
  return { browser, ctx, page, tok };
}
const shot = (page, name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });
const text = (page) => page.evaluate(() => document.body.innerText);
const hasStop = (page) => page.evaluate(() => [...document.querySelectorAll("button")].some((b) => /หยุด/.test((b.textContent || "") + (b.getAttribute("aria-label") || ""))));
async function send(page, msg) {
  await page.locator(COMPOSER).click();
  await page.locator(COMPOSER).fill(msg);
  await page.keyboard.press("Enter");
}
async function gotoChat(page, id) {
  await page.goto(`${PAGE}/chat/${id}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(COMPOSER, { timeout: 45000 });
  await L.wait(1500);
}

// ---- cases -----------------------------------------------------------------------------------
async function caseBlank() {
  const { browser, page } = await open("A", { portalMe: "hang" });
  const t0 = Date.now();
  await page.goto(`${PAGE}/chat`, { waitUntil: "domcontentloaded" });
  const samples = [];
  for (const s of [3, 10, 15]) {
    await L.wait(s * 1000 - (Date.now() - t0));
    const t = await text(page);
    samples.push({ at_s: s, chars: t.trim().length, retry: /ลองใหม่|ลองอีกครั้ง/.test(t), head: t.trim().slice(0, 120) });
    await shot(page, `blank-${s}s`);
  }
  await browser.close();
  return { samples, pass: samples[2].chars > 0 && samples[2].retry };
}

async function caseBlip() {
  const { browser, page, tok } = await open("A");
  const id = await L.newChat(tok, { model: "deepseek-v4-flash" });
  await gotoChat(page, id);
  await send(page, "[FIX130] เขียนเรียงความภาษาไทยเรื่องแม่น้ำโขง ยาวประมาณ 900 คำ 6 ย่อหน้า");
  await L.wait(6000);
  const t0 = Date.now();
  blocking = true;
  const cutN = cut();
  await shot(page, "blip-01-just-cut");
  await L.wait(5000); // the 5-second blip: every chat-edge socket is refused
  blocking = false;
  const tBack = Date.now();
  let recoveredAt = null;
  for (let i = 0; i < 40; i++) {
    await L.wait(3000);
    if (!(await hasStop(page))) { recoveredAt = Date.now(); break; }
  }
  await L.wait(2000);
  await shot(page, "blip-02-after");
  const msgs = (await L.messages(tok, id))?.messages || [];
  const server = msgs.filter((m) => m.role === "assistant").pop();
  const pageText = await text(page);
  const serverTail = (server?.content || "").replace(/\s+/g, " ").trim().slice(-60);
  const shown = pageText.replace(/\s+/g, " ").includes(serverTail.slice(-40));
  await browser.close();
  await L.deleteChat(tok, id);
  return { cut: cutN, recovered_s_after_blip: recoveredAt ? Math.round((recoveredAt - tBack) / 1000) : null, server_status: server?.status, server_chars: (server?.content || "").length,
    final_text_on_page: shown, pass: !!recoveredAt && shown && (recoveredAt - tBack) <= 60000 };
}

async function caseWedge() {
  const { browser, page, tok } = await open("A");
  const id = await L.newChat(tok, { model: "deepseek-v4-flash" });
  await gotoChat(page, id);
  execFileSync("docker", ["pause", "sran-test-backend-1"]);
  const t0 = Date.now();
  const timeline = [];
  try {
    await send(page, "[FIX130] สวัสดีครับ ตอบสั้นๆ");
    for (const s of [20, 45, 75, 100, 130, 160]) {
      await L.wait(Math.max(0, s * 1000 - (Date.now() - t0)));
      const t = await text(page);
      const failed = /ไม่สำเร็จ|สร้างคำตอบใหม่|ลองใหม่|ไม่ตอบสนอง|เชื่อมต่อ/.test(t) && !(await hasStop(page));
      timeline.push({ at_s: s, failed, stop: await hasStop(page), working: /กำลังประมวลผล/.test(t) });
      await shot(page, `wedge-${s}s`);
      if (failed) break;
    }
  } finally { execFileSync("docker", ["unpause", "sran-test-backend-1"]); }
  await browser.close();
  const firstFail = timeline.find((x) => x.failed);
  await L.wait(5000);
  await L.deleteChat(tok, id);
  return { timeline, failed_at_s: firstFail?.at_s ?? null, pass: !!firstFail && firstFail.at_s <= 100 };
}

async function caseFiles() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "fix130-files-"));
  const longName = "รายงานสรุปผลการดำเนินงานประจำไตรมาสที่สามของฝ่ายบริหารทรัพยากรบุคคลและงานพัฒนาองค์กรอย่างยั่งยืนครบถ้วน.txt";
  const mk = (n, body) => { const p = path.join(dir, n); fs.writeFileSync(p, body); return p; };
  const small = [1, 2, 3, 4].map((i) => mk(`fix130-note-${i}.txt`, `บันทึกทดสอบ ${i}\n`));
  const failing = mk("fix130-fail.txt", "this upload is forced to fail by the probe\n");
  const long = mk(longName, "ไฟล์ชื่อยาวภาษาไทย\n");
  const res = { longNameChars: [...longName].length, longNameBytes: Buffer.byteLength(longName) };
  const { browser, ctx, page, tok } = await open("A");
  // a forced upload failure for one named file (the same in the before and the after run)
  await ctx.route(`http://localhost:${EDGE_PORT}/**`, async (route) => {
    const req = route.request();
    if (req.method() === "POST" && /\/files/.test(req.url()) && (req.postDataBuffer() || Buffer.alloc(0)).includes(Buffer.from("fix130-fail.txt"))) {
      return route.fulfill({ status: 500, headers: { "access-control-allow-origin": PAGE, "access-control-allow-credentials": "true", "content-type": "application/json" }, body: JSON.stringify({ error: { code: "INTERNAL_ERROR", message: "forced by probe" } }) });
    }
    return route.continue();
  });
  const input = () => page.locator("input[type=file]").first();
  const chips = () => page.evaluate(() => document.body.innerText.match(/[^\n]*(อัปโหลดไม่สำเร็จ|ลองใหม่|ชื่อไฟล์)[^\n]*/g) || []);
  // (a) the long Thai filename alone
  let id = await L.newChat(tok);
  await gotoChat(page, id);
  await input().setInputFiles(long);
  await L.wait(6000);
  res.longName = { errorsOnScreen: await chips() };
  await shot(page, "files-a-long-thai-name");
  const rowsA = L.csql(`select count(*) from chat.conversation_files where conversation_id='${id}'`);
  res.longName.uploadedRows = Number(rowsA);
  await L.deleteChat(tok, id);
  // (b) five files, one failed → press Send
  id = await L.newChat(tok);
  await gotoChat(page, id);
  await input().setInputFiles([...small, failing]);
  await L.wait(6000);
  await page.locator(COMPOSER).fill("[FIX130] สรุปไฟล์ที่แนบให้หน่อย");
  await shot(page, "files-b-five-one-failed-before-send");
  await page.keyboard.press("Enter");
  await L.wait(5000);
  const sentB = (await L.messages(tok, id))?.messages?.filter((m) => m.role === "user").length || 0;
  res.fiveOneFailed = { userMessagesSent: sentB, screen: (await chips()).slice(0, 4), composerKeptText: (await page.locator(COMPOSER).inputValue()).length > 0 };
  await shot(page, "files-b-after-send");
  await L.deleteChat(tok, id);
  // (c) one failed + one good + text → press Send
  id = await L.newChat(tok);
  await gotoChat(page, id);
  await input().setInputFiles([small[0], failing]);
  await L.wait(5000);
  await page.locator(COMPOSER).fill("[FIX130] ไฟล์ครบไหม ตอบสั้นๆ");
  await page.keyboard.press("Enter");
  await L.wait(8000);
  const msgsC = (await L.messages(tok, id))?.messages || [];
  const userC = msgsC.filter((m) => m.role === "user");
  res.oneFailed = { userMessagesSent: userC.length, filesOnMessage: (userC[0]?.files || userC[0]?.attachments || []).length, screen: (await chips()).slice(0, 4) };
  await shot(page, "files-c-one-failed-after-send");
  await L.deleteChat(tok, id);
  await browser.close();
  res.pass = res.longName.uploadedRows === 1 && res.fiveOneFailed.userMessagesSent === 0 && res.fiveOneFailed.screen.length > 0 && res.oneFailed.userMessagesSent === 0;
  return res;
}

async function longChat(tok) {
  // Typhoon-S (16k context): five long user turns push the oldest ones out of what is sent.
  const id = await L.newChat(tok, { model: "Typhoon-S-ThaiLLM-8B-Instruct" });
  const para = "ข้อความทดสอบความยาวสำหรับการตรวจสอบการตัดบริบทของแชทยาว ".repeat(70);
  for (let i = 1; i <= 5; i++) await L.turn(tok, id, `[FIX130] ส่วนที่ ${i}: ${para}\nตอบคำเดียวว่า รับทราบ`, { timeoutMs: 180000 });
  return id;
}

async function caseBanner() {
  const tok = L.token("A");
  const id = await longChat(tok);
  const out = { id };
  for (const [w, h] of [[360, 740], [1280, 800]]) {
    const { browser, page } = await open("A", { width: w, height: h });
    await gotoChat(page, id);
    await L.wait(2500);
    const t = await text(page);
    out[`w${w}`] = {
      bannerText: (t.match(/[^\n]*(แชทยาว|ส่งให้ผู้ช่วย|ข้อความเก่า)[^\n]*/g) || []).slice(0, 3),
      visible: await page.evaluate(() => [...document.querySelectorAll("body *")].some((e) => /แชทยาว|ส่งให้ผู้ช่วย|ข้อความเก่า/.test(e.textContent || "") && e.children.length === 0 && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0)),
    };
    await shot(page, `banner-${w}`);
    await browser.close();
  }
  out.keep = id;
  out.pass = out.w360.visible && out.w1280.visible;
  return out;
}

async function casePill(id) {
  const { browser, page, tok } = await open("A", { width: 360, height: 740 });
  if (!id) { id = await L.newChat(tok); await L.turn(tok, id, "[FIX130] ตอบสั้นๆ ว่า สวัสดี"); await L.turn(tok, id, "[FIX130] ตอบสั้นๆ ว่า ขอบคุณ"); }
  await gotoChat(page, id);
  await L.wait(2500);
  const geo = await page.evaluate(() => {
    const pill = [...document.querySelectorAll("[aria-label]")].find((e) => /การใช้งานและเครดิต|เครดิต/.test(e.getAttribute("aria-label") || ""));
    const times = [...document.querySelectorAll("time, [data-testid*=time], [class*=timestamp]")].filter((e) => e.getBoundingClientRect().height > 0);
    const last = times[times.length - 1];
    const r = (e) => e && (({ x, y, width, height }) => ({ x: Math.round(x), y: Math.round(y), w: Math.round(width), h: Math.round(height) }))(e.getBoundingClientRect());
    const a = r(pill), b = r(last);
    const overlap = !!(a && b && a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h);
    return { pill: a, lastTime: b, lastTimeText: last?.textContent?.trim() || null, overlap };
  });
  await shot(page, "pill-360");
  await browser.close();
  return { id, ...geo, pass: !!geo.pill && !!geo.lastTime && !geo.overlap };
}

async function caseKb() {
  const { browser, page, tok } = await open("A");
  const id = await L.newChat(tok);
  await L.turn(tok, id, "[FIX130] ตอบสั้นๆ ว่า สวัสดี");
  await gotoChat(page, id);
  const composerKb = await page.evaluate(() => [...document.querySelectorAll("button")].some((b) => /ฐานความรู้/.test((b.textContent || "") + (b.getAttribute("aria-label") || "") + (b.getAttribute("title") || ""))));
  await shot(page, "kb-01-composer");
  await page.goto(`${PAGE}/chat/knowledge`, { waitUntil: "domcontentloaded" });
  await L.wait(4000);
  const kbPage = (await text(page)).trim().slice(0, 400);
  await shot(page, "kb-02-knowledge-page");
  const avail = await L.api(tok, "/knowledge-bases");
  const create = await L.api(tok, "/knowledge-bases", { method: "POST", body: JSON.stringify({ name: "[FIX130] kb probe" }) });
  let upload = null;
  if (create.body?.id) {
    const fd = new FormData();
    fd.append("file", new Blob(["เอกสารทดสอบฐานความรู้ FIX130: รหัสลับของโครงการคือ ม่วงทอง-42"], { type: "text/plain" }), "fix130-kb.txt");
    const r = await fetch(`${L.API}/knowledge-bases/${create.body.id}/documents`, { method: "POST", headers: { Authorization: `Bearer ${tok}` }, body: fd });
    upload = { status: r.status, body: (await r.text()).slice(0, 300) };
    await L.api(tok, `/knowledge-bases/${create.body.id}`, { method: "DELETE" });
  }
  await browser.close();
  await L.deleteChat(tok, id);
  return { composerKbButton: composerKb, kbPage, list: { status: avail.status, body: JSON.stringify(avail.body).slice(0, 200) }, create: { status: create.status, body: JSON.stringify(create.body).slice(0, 200) }, upload };
}

async function caseThinking() {
  const { browser, page, tok } = await open("A");
  const id = await L.newChat(tok, { model: "qwen3.6-35b-a3b" });
  await gotoChat(page, id);
  await send(page, "[FIX130] 2+3 เท่ากับเท่าไร ตอบสั้นๆ");
  for (let i = 0; i < 40 && (await hasStop(page)); i++) await L.wait(2000);
  await L.wait(2500);
  const t = await text(page);
  const res = { thinkingInAnswer: /Thinking Process|<\/think>|thinking process/i.test(t), thinkingBox: /วิเคราะห์|คิด|Thinking|Analyzed/i.test(t) };
  await shot(page, "thinking-01-answer");
  const btn = page.locator("button[aria-expanded]").filter({ hasText: /วิเคราะห์|คิด|Analy|Think/i }).first();
  if (await btn.count()) { await btn.click(); await L.wait(800); res.expanded = true; await shot(page, "thinking-02-expanded"); }
  await browser.close();
  await L.deleteChat(tok, id);
  res.pass = !res.thinkingInAnswer;
  return res;
}

async function caseMemory() {
  // B holds an ACTIVE memory added by hand; a memory-ON chat uses it → the answer carries the chip.
  const tokB = L.token("B");
  const m = await L.api(tokB, "/memories", { method: "POST", body: JSON.stringify({ scope_type: "global_user", memory_type: "explicit_fact", content: "ผู้ใช้ชื่อเล่นว่า ภูผา เป็นวิศวกรโยธาอยู่เชียงใหม่" }) });
  const { browser, page } = await open("B");
  const id = await L.newChat(tokB, { memory: true });
  await gotoChat(page, id);
  await send(page, "[FIX130] ฉันชื่อเล่นอะไร ตอบสั้นๆ");
  for (let i = 0; i < 40 && (await hasStop(page)); i++) await L.wait(2000);
  await L.wait(2000);
  const t = await text(page);
  const res = { chip: (t.match(/[^\n]*ใช้ความจำ[^\n]*/g) || []).slice(0, 2) };
  await shot(page, "memory-01-answer-chip");
  const chip = page.getByText(/ใช้ความจำ/).first();
  if (await chip.count()) { await chip.click(); await L.wait(800); await shot(page, "memory-02-chip-open"); }
  await page.goto(`${PAGE}/chat/memory`, { waitUntil: "domcontentloaded" });
  await L.wait(4000);
  res.memoryPage = (await text(page)).slice(0, 600);
  await shot(page, "memory-03-page");
  await browser.close();
  await L.deleteChat(tokB, id);
  await L.api(tokB, `/memories/${m.body?.id}`, { method: "DELETE" });
  res.pass = res.chip.length > 0;
  return res;
}

(async () => {
  L.assertTestStack();
  const tag = process.argv[2] || "run";
  const cases = (process.argv[3] || "blank").split(",");
  OUT = path.join(L.OUT, "ux", tag);
  fs.mkdirSync(OUT, { recursive: true });
  await new Promise((r) => proxy.listen(4530, "127.0.0.1", r));
  const res = {};
  let bannerChat = null;
  for (const c of cases) {
    const t0 = Date.now();
    try {
      if (c === "blank") res.blank = await caseBlank();
      else if (c === "blip") res.blip = await caseBlip();
      else if (c === "wedge") res.wedge = await caseWedge();
      else if (c === "files") res.files = await caseFiles();
      else if (c === "banner") { res.banner = await caseBanner(); bannerChat = res.banner.keep; }
      else if (c === "pill") res.pill = await casePill(bannerChat);
      else if (c === "kb") res.kb = await caseKb();
      else if (c === "thinking") res.thinking = await caseThinking();
      else if (c === "memory") res.memory = await caseMemory();
    } catch (e) { res[c] = { error: String(e && e.stack || e).slice(0, 800) }; }
    if (res[c]) res[c].ms = Date.now() - t0;
    console.log(c, res[c]?.pass === true ? "PASS" : res[c]?.pass === false ? "FAIL" : "INFO", JSON.stringify(res[c]).slice(0, 500));
    fs.writeFileSync(path.join(OUT, "result.json"), JSON.stringify(res, null, 2));
  }
  if (bannerChat) await L.deleteChat(L.token("A"), bannerChat);
  proxy.close();
})().catch((e) => { console.error(e); process.exit(1); });
