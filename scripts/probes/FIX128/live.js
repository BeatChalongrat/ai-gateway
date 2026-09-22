#!/usr/bin/env node
// FIX128 — live regression (from FIX127 live.js) on the running web page (:3002) as the test account.
//
//   node scripts/probes/FIX127/live.js <tag> <case>[,<case>…]
//   MODEL (default qwen3.8-27b-fp8)
//
// cases
//   a1      four chip removals (queued / in flight / body sent-no response /
//           uploaded) + send: the model must not see any of them, storage
//           must hold none of them; the file that WAS sent is seen.
//   a3      the chat is deleted from "another tab" (API) while text is typed;
//           the send keeps the text, shows the Thai notice + "ส่งในแชทใหม่",
//           and the button sends it in a new chat.
//   a4      a failed row with a code shows ONE reason (alert, no error-label).
//   b1      memory OFF chat: the first completed turn gets a model title.
//   d1      Enter while streaming queues; edit/delete/auto-send/send-now.
//   d2      scroll up in chat A, switch to B, back to A: the position is kept.
// Test chats: "[FIX128]" in the first message, memory OFF. Cleanup is by id
// (results/<tag>-chats.txt lists every chat this run created).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
process.env.PAGE = process.env.PAGE || "http://localhost:3002";
process.env.API = process.env.API || "http://localhost:4000/api/v1";
process.env.EDGE = process.env.EDGE || "ai-gateway-chat-edge-api";
const { PAGE, token, api, openContext, wait } = require("../fix122/lib");
const OUT = path.join(__dirname, "results");
const MODEL = process.env.MODEL || "qwen3.8-27b-fp8";
const STOP = 'button[aria-label*="Stop"], button[aria-label*="หยุด"], [data-testid="stop-button"]';
const COMPOSER = 'textarea[data-shortcut="composer"]';
const created = [];
const noteChat = (id) => { created.push(id); fs.appendFileSync(path.join(OUT, `${process.argv[2]}-chats.txt`), id + "\n"); };

async function newChat(tok, memory = false) {
  const c = await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ model_id: MODEL }) });
  if (!memory) await api(tok, `/conversations/${c.body.id}`, { method: "PATCH", body: JSON.stringify({ memory_enabled: false }) });
  noteChat(c.body.id);
  return c.body.id;
}
const waitEnd = async (page, ms) => { const t0 = Date.now(); await wait(1500); while (Date.now() - t0 < ms) { if (!(await page.locator(STOP).count())) return Math.round((Date.now() - t0) / 1000); await wait(500); } return null; };
const lastReplyText = (page) => page.evaluate(() => { const rows = [...document.querySelectorAll("[data-msg-index]")]; const r = rows[rows.length - 1]; return r ? (r.textContent || "") : ""; });
const serverFiles = async (tok, id) => ((await api(tok, `/conversations/${id}/files`)).body.files || []).map((f) => ({ id: f.id, name: f.filename, message_id: f.message_id ?? null }));
const storageNames = (convId) => { try { return execFileSync("docker", ["exec", process.env.EDGE, "sh", "-c", `ls /app/data/uploads/conversation-files/*/${convId}/ 2>/dev/null`], { encoding: "utf8" }).trim().split("\n").filter(Boolean); } catch { return []; } };
const dbFiles = (convId) => execFileSync("docker", ["exec", "ai-gateway-postgres-webchat", "psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-c", `select original_filename, message_id is not null from chat.conversation_files where conversation_id='${convId}' order by created_at`], { encoding: "utf8" }).trim();

async function open(ctx, id) {
  const page = await ctx.newPage();
  await page.goto(`${PAGE}/chat/${id}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(COMPOSER, { timeout: 30000 });
  await wait(800);
  return page;
}
async function send(page, text) {
  await page.fill(COMPOSER, text);
  await page.keyboard.press("Enter");
}
function tmpFile(name, body) { const p = path.join(OUT, name); fs.writeFileSync(p, body); return p; }

// ── a1 ─────────────────────────────────────────────────────────────────────
async function a1(tok, ctx) {
  const id = await newChat(tok);
  const page = await open(ctx, id);
  const big = tmpFile("fix127-big.txt", "REMOVED-QUEUED-TOKEN-QQQ1\n" + "x".repeat(6 * 1024 * 1024));
  const inflight = tmpFile("fix127-inflight.txt", "REMOVED-INFLIGHT-TOKEN-FFF2\n" + "y".repeat(2 * 1024 * 1024));
  const late = tmpFile("fix127-late.txt", "REMOVED-LATE-TOKEN-LLL3\n" + "z".repeat(64 * 1024));
  const done = tmpFile("fix127-done.txt", "REMOVED-DONE-TOKEN-DDD4\nplain");
  const kept = tmpFile("fix127-kept.txt", "KEPT-TOKEN-KKK5\nthis file is sent");
  const out = { id, steps: [] };

  // Hold uploads so the removal timing is under our control.
  const holds = new Map(); // filename -> {resolve}
  let lateMode = false;
  await ctx.route(`**/conversations/${id}/files`, async (route) => {
    const req = route.request();
    if (req.method() !== "POST") return route.continue();
    const body = req.postDataBuffer() || Buffer.alloc(0);
    const m = /filename="([^"]+)"/.exec(body.toString("latin1", 0, 2000));
    const name = m ? m[1] : "?";
    if (name === "fix127-inflight.txt") {
      // never reaches the server until released; the page aborts first
      await new Promise((r) => holds.set(name, r));
      return route.abort("aborted");
    }
    if (name === "fix127-late.txt" && lateMode) {
      // the body reaches the server (row created), the response is delayed
      // past the chip's removal — the page never learns the id.
      const res = await route.fetch();
      await wait(2500);
      return route.fulfill({ response: res }).catch(() => {});
    }
    return route.continue();
  });

  // (1) removed while queued: three files at once -> MAX_CONCURRENT 3, so add a 4th? Simpler: the
  // in-flight hold occupies one worker; the "queued" file is removed before its turn.
  lateMode = true;
  await page.setInputFiles('input[type="file"]', [inflight, big, late, done]);
  await wait(300);
  // remove chips: big (queued behind? the pool is 3, so big may be uploading — remove it anyway,
  // any timing counts as "removed before send"), inflight (held), late (body sent, no response yet).
  const removeButtons = async () => page.locator('button[aria-label="Remove file"], button[aria-label="นำไฟล์ออก"]');
  let rb = await removeButtons();
  out.steps.push({ chipsRemoveButtons: await rb.count() });
  // Remove three of four chips (leave the last one, fix127-done.txt, to finish uploading).
  for (let i = 0; i < 3; i++) { rb = await removeButtons(); if (await rb.count() > 1) await rb.first().click(); await wait(150); }
  await wait(3500); // let the held/late responses settle (late: server row created, DELETE by client id + retry)
  for (const [, r] of holds) r();
  // (4) the remaining chip finished uploading: remove it too
  rb = await removeButtons();
  if (await rb.count()) await rb.first().click();
  await wait(4000);
  out.afterRemovals = { server: await serverFiles(tok, id), storage: storageNames(id), db: dbFiles(id) };
  // Now attach the file that IS sent, and ask for the tokens.
  await page.setInputFiles('input[type="file"]', [kept]);
  await wait(2500);
  await send(page, "[FIX128] ไฟล์ที่แนบมามีข้อความ TOKEN อะไรบ้าง ตอบเป็นรายการ token ทั้งหมดที่เห็นในทุกไฟล์ ถ้าไม่เห็นไฟล์ไหนก็ไม่ต้องเดา");
  out.turnSeconds = await waitEnd(page, 180000);
  await wait(1000);
  out.reply = (await lastReplyText(page)).slice(0, 400);
  out.modelSawRemoved = /QQQ1|FFF2|LLL3|DDD4/.test(out.reply);
  out.modelSawKept = /KKK5/.test(out.reply);
  out.afterSend = { server: await serverFiles(tok, id), storage: storageNames(id), db: dbFiles(id) };
  out.pass = !out.modelSawRemoved && out.afterSend.storage.filter((n) => !n.includes("fix127-kept")).length === 0 && out.afterSend.server.every((f) => f.name.includes("fix127-kept"));
  await page.close();
  return out;
}

// ── a3 ─────────────────────────────────────────────────────────────────────
async function a3(tok, ctx) {
  const id = await newChat(tok);
  const page = await open(ctx, id);
  const text = "[FIX128] ข้อความนี้ต้องไม่หาย " + Date.now();
  await page.fill(COMPOSER, text);
  await api(tok, `/conversations/${id}`, { method: "DELETE" }); // "another tab"
  await page.keyboard.press("Enter");
  await wait(2500);
  const out = { id, url: page.url() };
  out.banner = await page.locator('[data-testid="error-banner"]').textContent().catch(() => null);
  out.button = await page.locator('[data-testid="send-in-new-chat"]').count();
  out.composer = await page.inputValue(COMPOSER).catch(() => null);
  out.textKept = out.composer === text;
  if (out.button) {
    await page.click('[data-testid="send-in-new-chat"]');
    await wait(4000);
    out.urlAfterButton = page.url();
    const m = /\/chat\/([0-9a-f-]{36})/.exec(out.urlAfterButton);
    if (m) { noteChat(m[1]); const rows = (await api(tok, `/conversations/${m[1]}/messages?limit=5`)).body.messages || []; out.newChatFirstUser = rows.find((r) => r.role === "user")?.content?.slice(0, 60); out.sentInNewChat = out.newChatFirstUser === text; await waitEnd(page, 120000); }
  }
  out.pass = out.textKept && !!out.button && /ถูกลบ/.test(out.banner || "") && !!out.sentInNewChat;
  await page.close();
  return out;
}

// ── a4 ─────────────────────────────────────────────────────────────────────
async function a4(tok, ctx) {
  const id = await newChat(tok);
  // a message far over any model's window -> message_too_long (FIX105)
  const huge = "[FIX128] " + "ยาว ".repeat(Number(process.env.HUGE_WORDS || 60000));
  const r = await fetch(process.env.API + `/conversations/${id}/messages`, { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify({ content: huge, stream: true }) });
  const body = await r.text();
  const codes = [...body.matchAll(/"code":"([a-z_]+)"/g)].map((m) => m[1]);
  const page = await open(ctx, id);
  await wait(1000);
  const card = await page.evaluate(() => { const rows = [...document.querySelectorAll("[data-msg-index]")]; const r = rows[rows.length - 1]; if (!r) return null; return { alerts: r.querySelectorAll('[role="alert"]').length, errorLabel: r.querySelector('[data-testid="error-label"]')?.textContent ?? null, text: (r.textContent || "").slice(0, 200) }; });
  const rows = (await api(tok, `/conversations/${id}/messages?limit=5`)).body.messages || [];
  const out = { id, status: r.status, streamCodes: codes, serverLast: rows.at(-1) && { status: rows.at(-1).status, error_code: rows.at(-1).error_code }, card };
  out.pass = !!card && card.alerts === 1 && card.errorLabel === null;
  await page.close();
  return out;
}

// ── b1 ─────────────────────────────────────────────────────────────────────
async function b1(tok, ctx) {
  const id = await newChat(tok, false); // memory OFF, not temporary
  const page = await open(ctx, id);
  await send(page, "[FIX128] ช่วยอธิบายสั้นๆ ว่าทำไมท้องฟ้าเป็นสีฟ้า");
  const secs = await waitEnd(page, 180000);
  await wait(6000);
  const c = (await api(tok, `/conversations/${id}`)).body;
  const out = { id, turnSeconds: secs, title: c.title, memory_enabled: c.memory_enabled, is_temporary: c.is_temporary };
  out.pass = c.memory_enabled === false && c.is_temporary === false && !!c.title && c.title !== "New conversation" && !c.title.startsWith("[FIX128]");
  await page.close();
  return out;
}

// ── d1 ─────────────────────────────────────────────────────────────────────
async function d1(tok, ctx) {
  const id = await newChat(tok);
  const page = await open(ctx, id);
  await send(page, "[FIX128] เขียนเรียงความยาว 600 คำ เรื่องประวัติศาสตร์ของกรุงศรีอยุธยา");
  await wait(2500);
  const out = { id };
  out.streaming = !!(await page.locator(STOP).count());
  await send(page, "คำถามที่สอง: 2+2 เท่ากับเท่าไร ตอบตัวเลขเดียว");
  await send(page, "คำถามที่สาม: เมืองหลวงของไทยคือ ตอบชื่อเดียว");
  await wait(500);
  out.queued = await page.locator('[data-testid="queued-message"]').count();
  // edit the second: click its text, replace, Enter
  const q = page.locator('[data-testid="queued-message"]').nth(1);
  await q.locator("button").first().click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("คำถามที่สาม (แก้แล้ว): เมืองหลวงของไทยคือ ตอบชื่อเดียว");
  await page.keyboard.press("Enter");
  await wait(300);
  out.editedText = await page.locator('[data-testid="queued-message"]').nth(1).textContent();
  // add a third and delete it
  await send(page, "ข้อความที่จะลบ");
  await wait(300);
  await page.locator('[data-testid="queue-delete"]').nth(2).click();
  await wait(300);
  out.queuedAfterDelete = await page.locator('[data-testid="queued-message"]').count();
  // send now on the first queued: stops the essay, sends 2+2
  const t0 = Date.now();
  await page.locator('[data-testid="queue-send-now"]').first().click();
  await wait(1500);
  out.queuedAfterSendNow = await page.locator('[data-testid="queued-message"]').count();
  out.turn2 = await waitEnd(page, 120000);
  await wait(1500);
  out.queuedAfterTurn2 = await page.locator('[data-testid="queued-message"]').count(); // auto-send should have released the last one
  out.turn3 = await waitEnd(page, 120000);
  await wait(1500);
  const rows = (await api(tok, `/conversations/${id}/messages?limit=20`)).body.messages || [];
  out.serverRows = rows.map((m) => ({ role: m.role, status: m.status, text: (m.content || "").slice(0, 40) }));
  const users = rows.filter((m) => m.role === "user").map((m) => m.content);
  out.pass = out.streaming && out.queued === 2 && /แก้แล้ว/.test(out.editedText || "") && out.queuedAfterDelete === 2 && out.queuedAfterSendNow === 1 && out.queuedAfterTurn2 === 0 && users.length === 3 && users[2].includes("แก้แล้ว");
  await page.close();
  return out;
}

// ── d2 ─────────────────────────────────────────────────────────────────────
async function d2(tok, ctx) {
  // two imported [FIX128] chats, 30 rows each (recent, so both are on the sidebar's first page)
  const mk = async () => {
    const id = await newChat(tok);
    const base = Date.now() - 3600_000; const items = [];
    for (let i = 0; i < 15; i++) { items.push({ client_id: `u${i}`, role: "user", content: `[FIX128] คำถาม ${i}`, status: "completed", created_at: new Date(base + i * 2000).toISOString() }, { client_id: `a${i}`, role: "assistant", content: `คำตอบ ${i} ` + "บรรทัด\n".repeat(6), status: "completed", model_id: MODEL, created_at: new Date(base + i * 2000 + 1000).toISOString() }); }
    const r = await api(tok, `/conversations/${id}/messages/import`, { method: "POST", body: JSON.stringify({ messages: items }) });
    return { id, importStatus: r.status };
  };
  const A = await mk(); const B = await mk();
  const page = await open(ctx, A.id);
  await page.waitForSelector("main [data-msg-index]", { timeout: 60000 });
  await wait(2000);
  const scroller = 'main div.h-full.overflow-y-auto';
  const geo = () => page.evaluate((sel) => { const el = document.querySelector(sel); return { top: Math.round(el.scrollTop), max: Math.round(el.scrollHeight - el.clientHeight) }; }, scroller);
  // in-app navigation (no reload): history.pushState + popstate is honoured by the app router
  // in-app navigation: the sidebar row, selected by id (never by title)
  const go = async (id) => { await page.click(`[data-conversation-id="${id}"]`); await page.waitForFunction((id) => location.pathname.endsWith(id), id, { timeout: 30000 }); await page.waitForSelector("main [data-msg-index]", { timeout: 60000 }); await wait(2500); };
  await page.evaluate(() => { window.__fix127 = "alive"; });
  const startA = await geo();
  const box = await page.locator(scroller).boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -400);
  await wait(1000);
  const before = await geo();
  await go(B.id);
  const inB = await geo();
  await go(A.id);
  const backInA = await geo();
  const out = { A, B, startA, before, inB, backInA, url: page.url(), sameSession: await page.evaluate(() => window.__fix127 === "alive") };
  out.pass = out.sameSession && startA.top >= startA.max - 5 && before.top < startA.top - 100 && Math.abs(backInA.top - before.top) < 60 && inB.top >= inB.max - 5;
  await page.close();
  return out;
}


// ── FIX128 regression extras ───────────────────────────────────────────────
const credit = () => Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-c",
  "select credit_balance from portal_users where id='56726cc2-47d2-4e81-95bd-4aba41d4d677'"], { encoding: "utf8" }).trim());
async function rename(tok, ctx) {
  const id = await newChat(tok);
  const page = await open(ctx, id);
  await send(page, "[FIX128] rename: ตอบคำเดียว สวัสดี");
  const r = await api(tok, `/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ title: "[FIX128] ชื่อที่ตั้งเอง" }) });
  await waitEnd(page, 120000); await wait(6000);
  await send(page, "ต่อ: ตอบคำเดียว ขอบคุณ"); await waitEnd(page, 120000); await wait(4000);
  const t = (await api(tok, `/conversations/${id}`)).body.title;
  await page.close();
  return { id, patch: r.status, title: t, pass: r.status < 300 && t === "[FIX128] ชื่อที่ตั้งเอง" };
}
async function files5(tok, ctx) {
  const id = await newChat(tok);
  const page = await open(ctx, id);
  const fs6 = [1, 2, 3, 4, 5, 6].map((i) => tmpFile(`fix128-f${i}.txt`, `file ${i}`));
  await page.setInputFiles('input[type="file"]', fs6);
  await wait(3000);
  const chips = await page.locator('button[aria-label="Remove file"], button[aria-label="นำไฟล์ออก"]').count();
  const serverRefusal = await fetch(process.env.API + `/conversations/${id}/messages`, { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" },
    body: JSON.stringify({ content: "[FIX128] six", file_ids: ["00000000-0000-4000-8000-000000000001","00000000-0000-4000-8000-000000000002","00000000-0000-4000-8000-000000000003","00000000-0000-4000-8000-000000000004","00000000-0000-4000-8000-000000000005","00000000-0000-4000-8000-000000000006"] }) });
  // clean the five uploads through the chips (A1 path)
  for (let i = 0; i < chips; i++) { const b = page.locator('button[aria-label="Remove file"], button[aria-label="นำไฟล์ออก"]').first(); if (await b.count()) await b.click(); await wait(200); }
  await wait(4000);
  const left = (await api(tok, `/conversations/${id}/files`)).body.files?.length ?? -1;
  await page.close();
  return { id, chipsShown: chips, server6: serverRefusal.status, filesLeftAfterRemove: left, pass: chips === 5 && serverRefusal.status === 422 && left === 0 };
}
async function credits0(tok, ctx) {
  const before = credit();
  const r = await a4(tok, ctx);
  await wait(3000);
  const after = credit();
  return { ...r, creditsBefore: before, creditsAfter: after, pass: !!r.pass && before === after };
}

async function main() {
  const [tag, list] = process.argv.slice(2);
  if (!tag || !list) { console.error("usage: live.js <tag> <case,...>"); process.exit(2); }
  fs.mkdirSync(OUT, { recursive: true });
  const tok = token();
  const { browser, ctx } = await openContext(tok);
  const results = {};
  for (const c of list.split(",")) {
    const fn = { a1, a3, a4, b1, d1, d2, rename, files5, credits0 }[c];
    if (!fn) { results[c] = { error: "unknown case" }; continue; }
    const t0 = Date.now();
    try { results[c] = await fn(tok, ctx); } catch (e) { results[c] = { error: String(e).slice(0, 400) }; }
    results[c].ms = Date.now() - t0;
    console.log(c, results[c].pass ? "PASS" : "FAIL", JSON.stringify(results[c]).slice(0, 700));
  }
  fs.writeFileSync(path.join(OUT, `${tag}-live.json`), JSON.stringify({ model: MODEL, at: new Date().toISOString(), results, created }, null, 2));
  await browser.close();
}
main().catch((e) => { console.error(e); process.exit(1); });
