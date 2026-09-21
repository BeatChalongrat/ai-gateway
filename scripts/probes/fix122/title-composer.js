#!/usr/bin/env node
// FIX122 Part B + C — on a running page, one conversation per case, each
// opened BY ITS LINK while still empty (the case the composer bug needs).
//
//   node scripts/probes/fix122/title-composer.js <tag> [case,case…]
//   cases: qwen deepseek rename fail temporary sendfail   (default: all)
//
// Every case records the composer after Enter (next frame, +1 s, +3 s), what
// a second Enter did, every generate-title call the page made (status, ms,
// body), the conversation's title over time, and — from the gateway tap —
// every model request the server made for it.
// Output: results/<tag>-title-composer.json
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { PAGE, token, api, openContext, chromium, wait } = require("./lib");

const OUT = path.join(__dirname, "results");
const TAP = path.join(OUT, "gateway-tap.jsonl");
const FAIL_FLAG = path.join(OUT, "FAIL_TITLE");
const STOP = '[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="หยุด"]';
const COMPOSER = 'textarea[data-shortcut="composer"]';
const QWEN = "qwen3.8-27b-fp8";
const DEEPSEEK = process.env.DEEPSEEK || "deepseek-v4-flash";
const CDP = PAGE.startsWith("cdp:");

const tapLines = () => (fs.existsSync(TAP) ? fs.readFileSync(TAP, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l)) : []);
const waitEnd = async (page, ms) => { const t0 = Date.now(); await wait(1500); while (Date.now() - t0 < ms) { if (!(await page.locator(STOP).count())) return Math.round((Date.now() - t0) / 1000); await wait(500); } return null; };

async function sendByPage(page, text) {
  const posts = [];
  const onReq = (r) => { if (r.method() === "POST" && /\/conversations\/[^/]+\/messages$/.test(new URL(r.url()).pathname)) posts.push(Date.now()); };
  page.on("request", onReq);
  await page.click(COMPOSER);
  await page.fill(COMPOSER, text);
  await page.keyboard.press("Enter");
  const next = await page.evaluate((sel) => new Promise((r) => requestAnimationFrame(() => r(document.querySelector(sel)?.value ?? null))), COMPOSER);
  await wait(1000);
  const after1s = await page.evaluate((sel) => document.querySelector(sel)?.value ?? null, COMPOSER);
  await wait(2000);
  const after3s = await page.evaluate((sel) => document.querySelector(sel)?.value ?? null, COMPOSER);
  return { composer: { nextFrame: next, after1s, after3s }, posts, off: () => page.off("request", onReq) };
}

async function runCase(ctx, tok, name) {
  const model = name === "deepseek" ? DEEPSEEK : QWEN;
  const created = await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ model_id: model }) });
  const convId = created.body.id;
  const out = { case: name, model, convId, titleAtCreate: created.body.title };
  if (name === "temporary") await api(tok, `/conversations/${convId}`, { method: "PATCH", body: JSON.stringify({ memory_enabled: false }) });
  if (name === "fail") fs.writeFileSync(FAIL_FLAG, "1"); else if (fs.existsSync(FAIL_FLAG)) fs.unlinkSync(FAIL_FLAG);
  const tapBefore = tapLines().length;

  // desktop (cdp): the app has ONE window — reuse its page
  const page = CDP ? ctx.pages()[0] : await ctx.newPage();
  const base = CDP ? new URL(page.url()).origin : PAGE;
  const titleCalls = [];
  page.on("response", async (r) => { if (r.url().includes("/generate-title")) { const t = r.request().timing(); titleCalls.push({ status: r.status(), ms: Math.round(t.responseEnd > 0 ? t.responseEnd : -1), body: await r.text().catch(() => "") }); } });
  if (name === "sendfail") await page.route(/\/conversations\/[^/]+\/messages$/, (route) => (route.request().method() === "POST" ? route.abort("connectionfailed") : route.continue()));
  await page.goto(`${base}/chat/${convId}`, { waitUntil: "domcontentloaded" }); // the link
  await page.waitForSelector(COMPOSER, { timeout: 60000 });
  await wait(1500);

  const first = `[FIX122] ${name}: ตอบสั้น ๆ หนึ่งประโยค ทำไมท้องฟ้าเป็นสีฟ้า`;
  const s = await sendByPage(page, first);
  out.composerAfterSend = s.composer;
  // the second Enter: with the text still in the box this is the repeat question
  await page.click(COMPOSER);
  await page.keyboard.press("Enter");
  await wait(1500);
  out.postsAfterSecondEnter = s.posts.length;
  out.userBubbles = await page.evaluate((t) => [...document.querySelectorAll("[data-msg-index]")].filter((r) => (r.textContent || "").includes(t)).length, first);

  if (name === "sendfail") {
    await wait(1500);
    out.composerAfterFailure = await page.evaluate((sel) => document.querySelector(sel)?.value ?? null, COMPOSER);
    out.draftInStorage = await page.evaluate((id) => { try { return JSON.parse(localStorage.getItem("sran-webchat-composer-drafts") || "{}")[id]?.t ?? null; } catch { return null; } }, convId);
    out.outboxInStorage = await page.evaluate(() => localStorage.getItem("sran-webchat-send-outbox"));
    s.off();
    if (CDP) await page.unroute(/\/conversations\/[^/]+\/messages$/); else await page.close();
    return out;
  }

  if (name === "rename") {
    // the person renames while the first turn is still being answered
    const r = await api(tok, `/conversations/${convId}`, { method: "PATCH", body: JSON.stringify({ title: "[FIX122] ชื่อที่ตั้งเอง" }) });
    out.renameStatus = r.status;
  }
  out.firstTurnSeconds = await waitEnd(page, 360000);
  s.off();

  // the title, watched for 45 s after the turn
  out.titles = [];
  for (let i = 0; i < 9; i++) { const c = await api(tok, `/conversations/${convId}`); out.titles.push(c.body.title); if (i && titleCalls.length && name !== "rename") break; await wait(5000); }

  if (name === "rename") {
    out.laterTurns = [];
    for (let i = 1; i <= 5; i++) {
      const t = await sendByPage(page, `[FIX122] เทิร์น ${i + 1}: ตอบเป็นตัวเลขอย่างเดียว ${i} + ${i} เท่ากับเท่าไร`);
      const secs = await waitEnd(page, 360000);
      t.off();
      await wait(3000);
      const c = await api(tok, `/conversations/${convId}`);
      out.laterTurns.push({ turn: i + 1, seconds: secs, title: c.body.title, sidebarHasTitle: await page.evaluate(() => document.body.innerText.includes("[FIX122] ชื่อที่ตั้งเอง")) });
    }
  }
  const msgs = await api(tok, `/conversations/${convId}/messages?limit=50`);
  out.firstAssistant = (msgs.body.messages || []).filter((m) => m.role === "assistant").map((m) => ({ status: m.status, model_id: m.model_id, fallback_from: m.fallback_from ?? null, chars: (m.content || "").length }))[0] ?? null;
  out.finalTitle = (await api(tok, `/conversations/${convId}`)).body.title;
  out.sidebarShowsFinalTitle = await page.evaluate((t) => document.body.innerText.includes(t), out.finalTitle);
  out.generateTitleCalls = titleCalls;
  out.gatewayRequests = tapLines().slice(tapBefore).map((l) => ({ model: l.model, served: l.served ?? null, isTitle: l.isTitle, stream: l.stream, tools: l.tools, max_tokens: l.max_tokens, reasoning: l.reasoning?.mode ?? null, bodyBytes: l.bodyBytes, status: l.status, ms: l.ms, usage: l.usage ?? null, content: l.isTitle ? l.content ?? null : undefined, injected: l.injected ?? false }));
  if (fs.existsSync(FAIL_FLAG)) fs.unlinkSync(FAIL_FLAG);
  page.removeAllListeners("response");
  if (!CDP) await page.close();
  return out;
}

(async () => {
  const [tag, list] = process.argv.slice(2);
  const cases = (list || "qwen,deepseek,rename,fail,temporary,sendfail").split(",");
  fs.mkdirSync(OUT, { recursive: true });
  const tok = token();
  let browser, ctx;
  if (CDP) { browser = await chromium.connectOverCDP(`http://127.0.0.1:${PAGE.slice(4)}`); ctx = browser.contexts()[0]; }
  else ({ browser, ctx } = await openContext(tok));
  const results = [];
  for (const c of cases) {
    try { const r = await runCase(ctx, tok, c); results.push(r); console.log(JSON.stringify(r)); }
    catch (e) { results.push({ case: c, error: String(e) }); console.log(JSON.stringify({ case: c, error: String(e) })); }
    fs.writeFileSync(path.join(OUT, `${tag}-title-composer.json`), JSON.stringify({ tag, page: PAGE, at: new Date().toISOString(), results }, null, 2));
  }
  await browser.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
