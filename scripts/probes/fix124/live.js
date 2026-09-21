#!/usr/bin/env node
// FIX124 — live checks on a running page (web, or the desktop over CDP).
//
//   node scripts/probes/fix124/live.js <tag> <case>[,<case>…]
//   PAGE (default http://localhost:3002)  API (default http://localhost:4000/api/v1)
//   EDGE (default ai-gateway-chat-edge-api)  MODEL (default deepseek-v4-flash)
//   PAGE=cdp:9333 drives the installed desktop app.
//
// cases
//   dead        first turn on DEAD_MODEL (default qwen3.8-27b-fp8): another
//               model must answer in the same turn — no red card.
//   title       a new chat gets a model title (memory ON — a temporary chat is
//               never titled — and 1 turn, far from the 20-message extractor).
//   titledead   chat pinned to DEAD_MODEL: the turn is rescued, and the title
//               comes from a model that works.
//   rename      rename during the first turn, 2 more turns: the name stays.
//   drop5 / drop30 / drop60   the network goes away for N s in the middle of
//               an answer: the full answer must show, never "failed".
//   toolcap     a task that needs more tool rounds than the cap: no silent cut.
//   retry       (desktop) a turn, then Regenerate: one user row on the server.
// Test chats: title prefix [FIX124] in the first message, memory OFF except
// the title cases. Output: results/<tag>-live.json (gitignored).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
process.env.PAGE = process.env.PAGE || "http://localhost:3002";
process.env.API = process.env.API || "http://localhost:4000/api/v1";
process.env.EDGE = process.env.EDGE || "ai-gateway-chat-edge-api";
const { PAGE, token, api, openContext, chromium, wait } = require("../fix122/lib");

const OUT = path.join(__dirname, "results");
const CDP = PAGE.startsWith("cdp:");
const MODEL = process.env.MODEL || "deepseek-v4-flash";
const DEAD = process.env.DEAD_MODEL || "qwen3.8-27b-fp8";
const STOP = '[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="หยุด"]';
const COMPOSER = 'textarea[data-shortcut="composer"]';

const waitEnd = async (page, ms) => { const t0 = Date.now(); await wait(1500); while (Date.now() - t0 < ms) { if (!(await page.locator(STOP).count())) return Math.round((Date.now() - t0) / 1000); await wait(500); } return null; };
const lastReply = (page) => page.evaluate(() => {
  const rows = [...document.querySelectorAll("[data-msg-index]")];
  const r = rows[rows.length - 1];
  if (!r) return null;
  const q = (s) => r.querySelector(s);
  return {
    chars: (r.textContent || "").length, text: (r.textContent || "").slice(0, 160),
    errorLabel: q('[data-testid="error-label"]')?.textContent ?? null,
    errorCard: !!q('[data-testid="error-notice"], [role="alert"]'),
    reconnecting: !!q('[data-testid="reconnecting-notice"]'), awaitingReload: !!q('[data-testid="awaiting-reload-notice"]'),
    stopNotice: q('[data-testid="stop-reason-notice"]')?.textContent ?? null, continueButton: !!q('[data-testid="continue-button"]'),
    fallbackNotice: q('[data-testid="fallback-notice"]')?.textContent ?? null,
  };
});
const serverRows = async (tok, id) => ((await api(tok, `/conversations/${id}/messages?limit=50`)).body.messages || []).map((m) => ({ role: m.role, status: m.status, model_id: m.model_id, fallback_from: m.fallback_from ?? null, stop_reason: m.stop_reason ?? null, error_code: m.error_code ?? null, chars: (m.content || "").length, attempts: (m.error_attempts || []).length }));

async function newChat(tok, model, memory) {
  const c = await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ model_id: model }) });
  if (!memory) await api(tok, `/conversations/${c.body.id}`, { method: "PATCH", body: JSON.stringify({ memory_enabled: false }) });
  return c.body.id;
}
async function open(ctx, id) {
  const page = CDP ? ctx.pages()[0] : await ctx.newPage();
  const base = CDP ? new URL(page.url()).origin : PAGE;
  await page.goto(`${base}/chat/${id}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(COMPOSER, { timeout: 60000 });
  await wait(1500);
  return page;
}
async function send(page, text) { await page.click(COMPOSER); await page.fill(COMPOSER, text); await page.keyboard.press("Enter"); }

const CASES = {
  async dead(ctx, tok) {
    const id = await newChat(tok, DEAD, false);
    const page = await open(ctx, id);
    const frames = [];
    const watch = setInterval(async () => { try { const r = await lastReply(page); if (r) frames.push({ label: r.errorLabel, card: r.errorCard }); } catch { /* navigating */ } }, 1000);
    await send(page, "[FIX124] dead: ตอบสั้น ๆ หนึ่งประโยค ทำไมท้องฟ้าเป็นสีฟ้า");
    const secs = await waitEnd(page, 240000);
    clearInterval(watch);
    await wait(1500);
    return { id, secs, reply: await lastReply(page), redCardEverShown: frames.some((f) => f.label || f.card), server: await serverRows(tok, id) };
  },
  async title(ctx, tok) {
    const id = await newChat(tok, MODEL, true);
    const page = await open(ctx, id);
    const titles = [];
    await send(page, "[FIX124] title: ตอบสั้น ๆ หนึ่งประโยค ทำไมใบไม้เป็นสีเขียว");
    await wait(800);
    titles.push({ at: "sent", title: (await api(tok, `/conversations/${id}`)).body.title, sidebarNewConversation: await page.evaluate(() => document.body.innerText.includes("New conversation")) });
    const secs = await waitEnd(page, 240000);
    for (let i = 0; i < 6; i++) { await wait(4000); const t = (await api(tok, `/conversations/${id}`)).body.title; titles.push({ at: `+${(i + 1) * 4}s`, title: t }); if (!t.startsWith("[FIX124]") && t !== "New conversation") break; }
    return { id, secs, titles, server: await serverRows(tok, id) };
  },
  async titledead(ctx, tok) {
    const id = await newChat(tok, DEAD, true);
    const page = await open(ctx, id);
    await send(page, "[FIX124] titledead: ตอบสั้น ๆ หนึ่งประโยค ทำไมน้ำทะเลเค็ม");
    const secs = await waitEnd(page, 240000);
    const titles = [];
    for (let i = 0; i < 8; i++) { await wait(4000); const t = (await api(tok, `/conversations/${id}`)).body.title; titles.push(t); if (!t.startsWith("[FIX124]") && t !== "New conversation") break; }
    return { id, secs, titles, reply: await lastReply(page), server: await serverRows(tok, id) };
  },
  async rename(ctx, tok) {
    const id = await newChat(tok, MODEL, true);
    const page = await open(ctx, id);
    await send(page, "[FIX124] rename: ตอบสั้น ๆ หนึ่งประโยค ทำไมฝนตก");
    await api(tok, `/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ title: "[FIX124] ชื่อที่ตั้งเอง" }) });
    await waitEnd(page, 240000);
    const titles = [];
    for (let i = 2; i <= 3; i++) { await wait(3000); await send(page, `[FIX124] เทิร์น ${i}: ตอบเป็นตัวเลขอย่างเดียว ${i} + ${i}`); await waitEnd(page, 240000); await wait(6000); titles.push((await api(tok, `/conversations/${id}`)).body.title); }
    return { id, titles };
  },
  async toolcap(ctx, tok) {
    const id = await newChat(tok, MODEL, false);
    const page = await open(ctx, id);
    await send(page, "[FIX124] toolcap: ใช้ run_python ทีละขั้น ขั้นละหนึ่งการเรียก รวม 8 ขั้น: ขั้นที่ k ให้คำนวณ k ยกกำลัง 3 แล้ว print ออกมา ห้ามรวมหลายขั้นในการเรียกเดียว เมื่อครบแล้วสรุปผลทั้ง 8 ค่าเป็นตาราง");
    const secs = await waitEnd(page, 420000);
    await wait(1500);
    const reply = await lastReply(page);
    const tools = await page.evaluate(() => document.querySelectorAll('[data-testid="code-run-card"], [data-testid^="tool-activity"]').length);
    return { id, secs, reply, toolCards: tools, server: await serverRows(tok, id) };
  },
  async retry(ctx, tok) {
    const id = await newChat(tok, MODEL, false);
    const page = await open(ctx, id);
    await send(page, "[FIX124] retry: ตอบสั้น ๆ หนึ่งประโยค ทำไมเหล็กเป็นสนิม");
    await waitEnd(page, 240000);
    await wait(2500);
    const before = await serverRows(tok, id);
    await page.locator('[data-msg-index]').last().hover();
    await page.locator('button[aria-label*="Regenerate"], button[aria-label*="สร้างคำตอบใหม่"], button[aria-label*="ตอบใหม่"]').last().click();
    await waitEnd(page, 240000);
    await wait(3000);
    const after = await serverRows(tok, id);
    return { id, before, after, userRowsBefore: before.filter((r) => r.role === "user").length, userRowsAfter: after.filter((r) => r.role === "user").length };
  },
};
for (const n of [5, 30, 60]) {
  CASES[`drop${n}`] = async (ctx, tok) => {
    const id = await newChat(tok, MODEL, false);
    const page = await open(ctx, id);
    await send(page, "[FIX124] drop: เขียนเรียงความภาษาไทยประมาณ 500 คำ เรื่องประวัติการรถไฟไทย แบ่งเป็น 5 ย่อหน้า");
    // wait until text is really streaming
    for (let i = 0; i < 120; i++) { const r = await lastReply(page); if (r && r.chars > 250) break; await wait(500); }
    const atDrop = await lastReply(page);
    const seen = [];
    await ctx.setOffline(true);
    const t0 = Date.now();
    while (Date.now() - t0 < n * 1000) { await wait(1000); const r = await lastReply(page); seen.push({ t: Math.round((Date.now() - t0) / 1000), label: r?.errorLabel ?? null, reconnecting: r?.reconnecting ?? false, awaiting: r?.awaitingReload ?? false, chars: r?.chars ?? 0 }); }
    await ctx.setOffline(false);
    const back = Date.now();
    let settled = null;
    for (let i = 0; i < 240; i++) { await wait(1000); const r = await lastReply(page); const streaming = await page.locator(STOP).count(); seen.push({ t: `back+${Math.round((Date.now() - back) / 1000)}`, label: r?.errorLabel ?? null, reconnecting: r?.reconnecting ?? false, awaiting: r?.awaitingReload ?? false, chars: r?.chars ?? 0 }); if (!streaming && !r?.reconnecting) { settled = Math.round((Date.now() - back) / 1000); break; } }
    await wait(1500);
    const reply = await lastReply(page);
    const server = await serverRows(tok, id);
    const serverChars = server.filter((r) => r.role === "assistant").at(-1)?.chars ?? 0;
    return { id, offlineSeconds: n, charsAtDrop: atDrop?.chars ?? 0, settledSecondsAfterBack: settled, failedEverShown: seen.some((s) => s.label), reconnectingShown: seen.some((s) => s.reconnecting), reply, serverChars, server, timeline: seen.filter((_, i) => i % 5 === 0) };
  };
}

(async () => {
  const [tag, list] = process.argv.slice(2);
  if (!tag || !list) { console.error("usage: live.js <tag> <case,case…>"); process.exit(2); }
  fs.mkdirSync(OUT, { recursive: true });
  const tok = token();
  let browser, ctx;
  if (CDP) { browser = await chromium.connectOverCDP(`http://127.0.0.1:${PAGE.slice(4)}`); ctx = browser.contexts()[0]; }
  else ({ browser, ctx } = await openContext(tok));
  const results = {};
  for (const name of list.split(",")) {
    try { results[name] = await CASES[name](ctx, tok); } catch (e) { results[name] = { error: String(e).slice(0, 400) }; }
    console.log(name, JSON.stringify(results[name]));
    fs.writeFileSync(path.join(OUT, `${tag}-live.json`), JSON.stringify({ tag, page: PAGE, at: new Date().toISOString(), results }, null, 2));
  }
  if (!CDP) await browser.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
