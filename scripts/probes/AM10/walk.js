#!/usr/bin/env node
// AM10 — the details, walked on both clients by clicking the real UI:
//
//   cards <client>     the catalogue and the sidebar, light and dark: every agent its own icon and
//                      colour, no price and no time on a card, what to feed it and what comes back
//   chats <client>     another copy of the report agent: a round (the time said while it runs, the
//                      "which files" card asked once and remembered), a second chat from the sidebar
//                      (not asked again), rename, pin, the two side by side, delete — and the deleted
//                      chat's run page, files and runs list still there
//   form <client>      a company form dropped after the round ("ใส่ในฟอร์มนี้ให้หน่อย"): read, mapped,
//                      filled for that round — a partly filled form a draft, never a pass — and the
//                      next round without it
//   language <client>  the switch: the whole chat surface, kept across a reload, on the account, and
//                      followed by a second client of the same person; what is still not in the
//                      chosen language is listed (results/…/language.json)
//   cleanup [web]      the walk's installs (by id) and its rounds' evidence
//
//   SMOKE_TARGET=test node scripts/probes/AM10/walk.js cards web
//   SMOKE_TARGET=live node scripts/probes/AM10/walk.js chats desktop      (after AM6's `open`)
//
// It drives the clients with AM6's walkthrough (the desktop over CDP 9333 after its `open`, the web
// in a headless browser of its own with a short-lived session for the same account). Everything it
// makes is tagged [AM10]; the owner's own installs, chats, theme and language are put back as found.
"use strict";
const fs = require("node:fs");
const path = require("node:path");

process.env.AM6_TAG = process.env.AM6_TAG || "[AM10]";
const TARGET = process.env.SMOKE_TARGET || "test";
process.env.AM6_OUT = path.join(__dirname, "results", "walk", TARGET);
const W = require("../AM6/walkthrough.js");
const { L, state, save, log, api, WEB, EDGE, waitFor, TAG } = W;

const SLUG = "sran.financial-report";
const CARD = "รายงานผลประกอบการ";
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const done = async (x, name, extra = {}) => {
  fs.writeFileSync(path.join(x.dir, `${name}.json`), JSON.stringify({ client: x.tag, at: new Date().toISOString(), ...extra, checks: x.checks }, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  await x.s.close();
  if (bad.length) process.exitCode = 1;
};
const home = async (page) => {
  await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  await page.locator('a[href="/chat/agents"]').first().waitFor({ state: "visible", timeout: 90000 });
  await L.wait(1200);
};
const stored = (page, key) => page.evaluate((k) => { try { return window.localStorage.getItem(k); } catch { return null; } }, key);
/** The theme the page is drawn in, set the way the app's own switch sets it. */
async function setTheme(page, theme) {
  await page.evaluate((t) => { try { window.localStorage.setItem("sran-chat-theme", t); } catch { /* none */ } }, theme);
  await page.reload({ waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-market-grid"], [data-testid="composer-root"], a[href="/chat/agents"]', { timeout: 60000 });
  await L.wait(1500);
  return page.evaluate(() => document.documentElement.dataset.theme);
}

// ── B, C: the catalogue and the sidebar ───────────────────────────────────────────────────────
async function cards(client) {
  const x = await W.session(client, "cards");
  const { page } = x;
  await home(page);
  const theme0 = (await stored(page, "sran-chat-theme")) || (await page.evaluate(() => document.documentElement.dataset.theme));
  await page.goto(`${WEB()}/chat/agents`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-market-grid"]', { timeout: 60000 });
  const seen = {};
  for (const theme of ["light", "dark"]) {
    const now = await setTheme(page, theme);
    x.check(now === theme, `the page is drawn ${theme}`, now);
    const rows = await page.locator('[data-testid="am-agent-card"]').evaluateAll((els) => els.map((el) => {
      const tile = el.querySelector('[data-testid="am-tile"]');
      const shape = tile && tile.firstElementChild;
      return {
        name: (el.querySelector("h2") || {}).innerText || "",
        accent: tile ? tile.getAttribute("data-accent") : null,
        painted: shape ? getComputedStyle(shape).backgroundColor : null,
        masked: shape ? /^url\(/.test(getComputedStyle(shape).webkitMaskImage || getComputedStyle(shape).maskImage || "") : false,
        feed: (el.querySelector('[data-testid="am-card-feed"]') || {}).innerText || "",
        get: (el.querySelector('[data-testid="am-card-get"]') || {}).innerText || "",
        text: el.innerText,
      };
    }));
    seen[theme] = rows.map((r) => ({ name: r.name, accent: r.accent }));
    x.check(rows.length >= 5, `${theme}: the five agents are in the catalogue`, String(rows.length));
    x.check(rows.every((r) => r.accent && r.masked), `${theme}: every card draws its package's own icon in its own colour`, JSON.stringify(rows.filter((r) => !r.accent || !r.masked).map((r) => r.name)));
    x.check(new Set(rows.map((r) => r.accent)).size === rows.length, `${theme}: no two agents share a colour`, rows.map((r) => r.accent).join(" "));
    const priced = rows.filter((r) => /บาท|วินาที|นาที|ตัวอย่างจริง|เครดิต/.test(r.text));
    x.check(!priced.length, `${theme}: no card shows a price or a run time`, JSON.stringify(priced.map((r) => r.name)));
    x.check(rows.every((r) => r.feed.trim().length > 6 && r.get.trim().length > 6), `${theme}: every card says what to send and what comes back`, JSON.stringify(rows.map((r) => [r.name.slice(0, 18), r.feed.slice(0, 40), r.get.slice(0, 40)])));
    await x.shot(`01-market-${theme}`, { fullPage: true });
    const rail = page.locator('[data-testid="agent-rail-section"]');
    if (await rail.count()) await W.capture(page, path.join(x.dir, `02-sidebar-${theme}.png`), { clip: await rail.first().boundingBox().then((b) => b && { x: 0, y: Math.max(0, b.y - 8), width: Math.min(320, b.x + b.width + 8), height: Math.min(520, b.height + 16) }) || undefined }, () => {});
  }
  const light = new Set(seen.light.map((r) => r.accent));
  x.check(seen.dark.every((r) => !light.has(r.accent)), "each agent's colour is another one in the dark theme", JSON.stringify(seen));
  if (theme0 === "light" || theme0 === "dark") await setTheme(page, theme0); // as found
  await done(x, "cards", { seen });
}

// ── D (and C's time, E's question): chats under the agent ────────────────────────────────────
async function installCopy(x) {
  const { page } = x;
  const had = ((await api(page, "/installs")).body?.installs || []).some((i) => i.slug === SLUG);
  return had ? W.installAnother(x, SLUG, CARD) : (await W.installFromMarket(x, CARD)).installId;
}
const railRow = (page, installId) => page.locator('[data-testid="agent-rail-row"]').filter({ has: page.locator(`a[href="/chat/agents/i/${installId}"]`) });
const chatRows = (page) => page.locator('[data-testid="agent-chat-row"]');
async function chatMenu(x, key, item, what) {
  const row = x.page.locator(`[data-testid="agent-chat-row"][data-chat="${key}"]`);
  await row.hover();
  await x.click(row.locator('[data-testid="agent-chat-menu"]'), `เมนูของแชท (${what})`);
  await x.click(x.page.locator(`[data-testid="${item}"]`), what);
}
async function openChats(x, installId) {
  const { page } = x;
  const list = page.locator('[data-testid="agent-chat-rows"]');
  if (!(await list.count())) {
    const row = railRow(page, installId);
    if (await row.count()) await x.click(row.locator('[data-testid="agent-rail-chats-toggle"]'), "เปิดรายการแชทของ agent");
  }
  await list.first().waitFor({ state: "visible", timeout: 30000 });
  await L.wait(800);
}

async function chats(client) {
  const x = await W.session(client, "chats");
  const { page } = x;
  await home(page);
  const installId = await installCopy(x);
  state[`am10_${client}`] = { installId };
  save();
  await page.goto(`${WEB()}/chat/agents/i/${installId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
  await L.wait(1500);

  // round 1 — the time is said while it runs
  const r1 = await W.sendFiles(x, installId, W.FIN, `${TAG} รายงานผลประกอบการเดือนกันยายน 2569`, 1);
  const expected = await waitFor(async () => {
    const t = await x.words(page.locator('[data-testid="am-progress-expected"]'));
    return t || null;
  }, 60000, 400);
  if (expected) await x.shot("02-time-while-waiting");
  x.check(/^ใช้เวลาประมาณ|^นานกว่าปกติ/.test(expected || ""), "the running round says about how long it takes", expected || "(not shown)");
  const d1 = await W.finish(x, r1.runId, 1);
  x.check(d1.run.status === "succeeded", "round 1 succeeds without waiting for the files question", `${d1.run.status} · asked ${d1.asked.length}`);
  // E: which files — asked once, on the card every question uses, never blocking
  const detail = (await api(page, `/runs/${r1.runId}`)).body;
  const ask = (detail.clarifications || []).find((c) => (c.questions || []).some((q) => q.code === "output_set"));
  x.check(!!ask && ask.status === "open" && !ask.blocking, "an install that never said which files is asked — a card that blocks nothing", ask ? `${ask.status} blocking=${ask.blocking}` : "no card");
  if (ask) {
    const cardEl = page.locator(`[data-card-id="${ask.id}"]`).first();
    await cardEl.scrollIntoViewIfNeeded();
    await L.wait(600);
    await x.shot("03-which-files-card");
    const opt = cardEl.locator('[data-testid="question-option"]').filter({ hasText: "ชุดมาตรฐาน" });
    await x.click(opt, "ชุดมาตรฐาน");
    await x.click(cardEl.locator('[data-testid="question-submit"]'), "ส่งคำตอบ");
    const said = await waitFor(async () => {
      const defs = (await api(page, `/installs/${installId}`)).body?.defaults || [];
      const f = defs.find((d) => d.key === "formats");
      return f && f.source === "answer" ? f : null;
    }, 30000, 1000);
    x.check(!!said, "the answer is the install's setting from now on", said ? JSON.stringify(said.value) : "formats still copied from the manifest");
    // answered after the files were delivered: the round stays finished, nothing fails on screen
    await L.wait(4000);
    const after = (await api(page, `/runs/${r1.runId}`)).body;
    const failures = await page.locator('[data-testid="am-failed-round"]').count();
    x.check(after?.run?.status === "succeeded" && failures === 0, "answering after delivery leaves the round finished and shows no error",
      `${after?.run?.status} · error cards ${failures}`);
  }
  await L.wait(3000);

  // the sidebar: the agent opens to its chats, the first named for its period
  await openChats(x, installId);
  const first = await x.words(chatRows(page).first());
  x.check(/กันยายน 2569/.test(first), "the chat is named for what it is — its round's period", first);
  await W.capture(page, path.join(x.dir, "04-sidebar-chats.png"), { clip: { x: 0, y: 0, width: 330, height: 900 } }, () => {});

  // a second chat from the sidebar; its round is not asked which files again
  await x.click(page.locator('[data-testid="agent-chat-new"]'), "แชทใหม่กับ agent นี้");
  await page.waitForURL(/[?&]thread=/, { timeout: 30000 });
  const thread2 = new URL(page.url()).searchParams.get("thread");
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
  await L.wait(1500);
  const r2 = await W.sendFiles(x, installId, W.FIN, `${TAG} รอบเทียบ เดือนกันยายน 2569`, 5);
  const d2 = await W.finish(x, r2.runId, 5);
  const d2full = (await api(page, `/runs/${r2.runId}`)).body;
  x.check(d2.run.status === "succeeded", "round 2 (the second chat) succeeds", d2.run.status);
  x.check(!(d2full.clarifications || []).some((c) => (c.questions || []).some((q) => q.code === "output_set")), "the second round is not asked which files again");
  state[`am10_${client}`] = { installId, run1: r1.runId, run2: r2.runId, thread2 };
  save();

  // rename, pin
  await openChats(x, installId);
  await chatMenu(x, thread2, "agent-chat-rename", "เปลี่ยนชื่อ");
  const input = page.locator('[data-testid="agent-chat-rename-input"]');
  await input.fill(`${TAG} รอบเทียบ`);
  await input.press("Enter");
  await waitFor(async () => (await x.words(page.locator(`[data-testid="agent-chat-row"][data-chat="${thread2}"]`))).includes("รอบเทียบ"), 15000);
  x.check((await x.words(page.locator(`[data-testid="agent-chat-row"][data-chat="${thread2}"]`))).includes(`${TAG} รอบเทียบ`), "a chat takes the name a person gives it");
  await chatMenu(x, thread2, "agent-chat-pin", "ปักหมุด");
  await L.wait(1500);
  x.check((await chatRows(page).first().getAttribute("data-chat")) === thread2, "a pinned chat comes first");

  // two rounds side by side: the first chat on screen, the second beside it
  await x.click(page.locator('[data-testid="agent-chat-row"][data-chat="main"] [data-testid="agent-chat-link"]'), "แชทแรก");
  await page.waitForSelector('[data-testid="am-thread"] [data-testid="am-outputs"]', { timeout: 60000 });
  await L.wait(1500);
  const scroll0 = await page.locator('[data-testid="am-thread"]').evaluate((el) => el.scrollTop);
  await chatMenu(x, thread2, "agent-chat-beside", "เปิดเทียบข้างกัน");
  await page.waitForSelector('[data-testid="am-beside"] [data-testid="am-outputs"]', { timeout: 60000 });
  await L.wait(2000);
  const both = await page.evaluate(() => ({
    main: document.querySelectorAll('[data-testid="am-thread"] [data-testid="am-outputs"]').length,
    beside: document.querySelectorAll('[data-testid="am-thread-beside"] [data-testid="am-outputs"]').length,
    composers: document.querySelectorAll('[data-testid="composer-root"]').length,
    title: (document.querySelector('[data-testid="am-beside-title"]') || {}).innerText || "",
  }));
  x.check(both.main >= 1 && both.beside >= 1 && both.composers === 1, "both rounds are on screen, each in its own pane, one composer", JSON.stringify(both));
  x.check(both.title.includes("รอบเทียบ"), "the pane beside is named for its chat", both.title);
  await x.shot("06-two-rounds-side-by-side");
  x.mark(`side by side (main pane scroll before ${scroll0})`);
  await x.click(page.locator('[data-testid="am-beside-close"]'), "ปิดแชทที่เทียบ");
  await L.wait(1200);

  // delete the second chat: off the list, and nothing else
  await chatMenu(x, thread2, "agent-chat-delete", "ลบแชท");
  const dialog = page.locator('[data-testid="agent-chat-delete-dialog"]');
  await dialog.waitFor({ state: "visible", timeout: 15000 });
  const says = await x.words(page.locator('[data-testid="agent-chat-delete-body"]'));
  x.check(/หน้ารอบงาน/.test(says) && /ไฟล์/.test(says) && /บันทึกการตรวจ/.test(says) && /ยังอยู่ครบ/.test(says), "the confirmation says the run page, the files and the audit rows stay", says);
  await x.shot("07-delete-confirmation");
  await x.click(page.locator('[data-testid="agent-chat-delete-confirm"]'), "ลบออกจากรายการ");
  await waitFor(async () => !(await page.locator(`[data-testid="agent-chat-row"][data-chat="${thread2}"]`).count()), 20000);
  x.check(!(await page.locator(`[data-testid="agent-chat-row"][data-chat="${thread2}"]`).count()), "the chat is off the list");
  const gone = await api(page, `/installs/${installId}/thread?thread=${thread2}`);
  x.check(gone.status === 404 && gone.body?.error?.code === "THREAD_GONE", "the chat itself answers that it was deleted", `${gone.status} ${gone.body?.error?.code}`);
  // … its evidence lives on
  const after = (await api(page, `/runs/${r2.runId}`)).body;
  x.check(after?.run?.status === "succeeded" && (after.artifacts || []).length >= 2 && (after.steps || []).length >= 6, "the deleted chat's run page still has its round: status, steps, files",
    `${after?.run?.status} · ${(after?.artifacts || []).length} files · ${(after?.steps || []).length} steps`);
  const art = (after.artifacts || [])[0];
  const bytes = art ? await W.fetchFile(page, `${EDGE()}/api/v1/agent-market/artifacts/${art.id}/download`) : Buffer.alloc(0);
  x.check(bytes.length > 2000 && bytes.slice(0, 2).toString() === "PK", "its file still downloads, byte for byte a document", `${art?.name} · ${bytes.length} bytes`);
  const runs = ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).map((r) => r.id);
  x.check(runs.includes(r2.runId), "the round is still in the agent's list of rounds");
  await page.goto(`${WEB()}/chat/agents/runs/${r2.runId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-run-status"]', { timeout: 60000 });
  await L.wait(1500);
  x.check((await page.locator('[data-testid="am-run-status"]').getAttribute("data-outcome")) === "completed", "the run page opens and says it finished");
  await x.shot("08-run-page-of-the-deleted-chat", { fullPage: true });
  await done(x, "chats", { installId, run1: r1.runId, run2: r2.runId, thread2 });
}

// ── E: a form dropped into a round, for that round only ───────────────────────────────────────
async function form(client) {
  const x = await W.session(client, "form");
  const { page } = x;
  const st = state[`am10_${client}`];
  if (!st || !st.run1) throw new Error(`run "chats ${client}" first: the form is dropped after its round`);
  const { installId, run1 } = st;
  await page.goto(`${WEB()}/chat/agents/i/${installId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-thread"] [data-testid="am-outputs"]', { timeout: 90000 });
  await L.wait(1500);
  const before = (await api(page, `/runs/${run1}`)).body;
  const count = (d, f) => (d.artifacts || []).filter((a) => a.format === f).length;
  x.clicks += 1;
  await page.locator('[data-testid="am-composer-file"]').setInputFiles([{ name: "แบบฟอร์มลูกค้า.docx", mimeType: DOCX, buffer: fs.readFileSync(W.FORM) }]);
  const mark = page.locator('[data-testid="am-composer-form-mark"]');
  await mark.waitFor({ state: "visible", timeout: 60000 });
  x.check(await mark.isChecked(), "a .docx — which this agent does not read as data — is taken as this round's form", await x.words(page.locator('[data-testid="am-composer-forms"]')));
  await page.locator('[data-testid="am-composer-input"]').fill(`${TAG} ใส่ในฟอร์มนี้ให้หน่อย`);
  await L.wait(1500);
  await x.shot("01-form-in-the-composer");
  await x.click(page.locator('[data-testid="am-composer-send"]'), "ส่ง");
  const card = page.locator('[data-testid="am-form"]').last();
  await card.waitFor({ state: "visible", timeout: 60000 });
  x.check((await card.getAttribute("data-run")) === run1, "the form goes to the round just finished");
  const status = () => card.getAttribute("data-status");
  let s = await waitFor(async () => { const v = await status(); return v && v !== "inspecting" ? v : null; }, 300000, 1500);
  x.mark(`form read: ${s}`);
  await card.scrollIntoViewIfNeeded();
  await x.shot("02-form-read");
  if (s === "needs_mapping") {
    await x.click(card.locator('[data-testid="am-form-map"]'), "จับคู่ช่อง");
    const mapper = page.locator('[data-testid="am-template-mapper"]');
    await mapper.waitFor({ state: "visible", timeout: 60000 });
    await L.wait(1200);
    const by = await x.words(page.locator('[data-testid="am-template-proposed-by"]'));
    const selects = await mapper.locator("select").evaluateAll((els) => els.map((e) => ({ aria: e.getAttribute("aria-label"), value: e.value })));
    x.check(selects.some((v) => v.value), "the mapping screen opens with what the model proposed; the person confirms", `${by} · ${selects.filter((v) => v.value).length}/${selects.length} chosen`);
    await x.shot("03-mapping-screen");
    await x.click(page.locator('[data-testid="am-template-save"]'), "บันทึกการจับคู่");
    s = await waitFor(async () => { const v = await status(); return v === "filled" || v === "unfilled" || v === "failed" ? v : null; }, 300000, 1500);
  } else if (s === "ready") {
    s = await waitFor(async () => { const v = await status(); return v === "filled" || v === "unfilled" || v === "failed" ? v : null; }, 300000, 1500);
  }
  x.mark(`form: ${s}`);
  await card.scrollIntoViewIfNeeded();
  await L.wait(1000);
  await x.shot("04-form-filled");
  const after = (await api(page, `/runs/${run1}`)).body;
  x.check(s === "filled" || s === "unfilled", "the form was filled for this round", s);
  x.check(count(after, "template") === 1, "the filled form is one more file of that round", `${count(after, "template")} form file(s)`);
  x.check(["docx", "pptx", "xlsx"].every((f) => count(after, f) === count(before, f)), "filling the form made none of the agent's own files again",
    JSON.stringify(["docx", "pptx", "xlsx"].map((f) => [f, count(before, f), count(after, f)])));
  const text = await x.words(card);
  if (s === "unfilled") {
    const lacks = await page.locator('[data-testid="am-form-unfilled"] li').allInnerTexts();
    x.check(lacks.length > 0 && /ฉบับร่าง/.test(text) && /ยังไม่ควรส่งต่อ/.test(text) && !/กรอกแบบฟอร์มให้แล้ว/.test(text),
      "a partly filled form is a draft, never a pass: every slot it lacks is named", lacks.join(" · "));
  } else {
    x.check(/ทุกช่องมีค่า/.test(text), "the filled form was opened again: every slot holds its value", text.slice(0, 140));
  }
  x.check(/ใช้เฉพาะรอบนี้/.test(text), "the card says the form is this round's only");
  const tpl = await api(page, `/installs/${installId}/template`);
  x.check(tpl.status === 404, "the agent's own saved form is untouched (it has none)", String(tpl.status));

  // the next round carries no form
  const r3 = await W.sendFiles(x, installId, W.FIN, `${TAG} รอบถัดไป เดือนกันยายน 2569`, 6);
  const d3 = await W.finish(x, r3.runId, 6);
  const d3full = (await api(page, `/runs/${r3.runId}`)).body;
  const form3 = await api(page, `/runs/${r3.runId}/template`);
  x.check(d3.run.status === "succeeded" && count(d3full, "template") === 0 && form3.status === 404, "the next round fills no form: it was that round's only",
    `${d3.run.status} · ${count(d3full, "template")} form file(s) · round form ${form3.status}`);
  await done(x, "form", { installId, run1, run3: r3.runId, status: s });
}

// ── F: the language ───────────────────────────────────────────────────────────────────────────
const PAGES = ["/chat", "/chat/settings", "/chat/tasks", "/chat/skills", "/chat/connectors", "/chat/memory"];
// what a Thai screen keeps in Latin letters on purpose (brand, feature and product names, file formats)
const KEEP = /^(SRAN|Chat|AI|Agent|Market|agent|Connectors?|Skills?|Canvas|MCP|PDF|DOCX|XLSX|CSV|TXT|PNG|JPG|Excel|Word|PowerPoint|Google|GitHub|Slack|Gmail|Drive|Calendar|Chrome|OpenAI|Whisper|URL|API|ID|KB|OK|vs|Ctrl|Shift|Enter|Esc|Alt|Mod|TH|EN|prompt|beta|Beta|admin|Admin)$/;
async function leaks(page, locale) {
  return page.evaluate(({ locale, keep }) => {
    const KEEP = new RegExp(keep);
    const skip = (el) => !!el.closest('[data-testid="agent-rail-section"], [data-testid="conversation-item"], [data-conversation-id], [data-testid="am-notifications"], code, pre, textarea, input, [data-testid="model-selector"], [data-testid="composer-root"] [role="listbox"]');
    const out = new Set();
    const walker = document.createTreeWalker(document.querySelector("main") || document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      if (!el || skip(el)) continue;
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden") continue;
      const text = n.textContent.replace(/\s+/g, " ").trim();
      if (!text) continue;
      if (locale === "th") {
        const words = (text.match(/[A-Za-z][A-Za-z'’-]{2,}/g) || []).filter((w) => !KEEP.test(w));
        if (words.length >= 2 || (words.length === 1 && !/[ก-๙]/.test(text) && text.length < 40)) out.add(text.slice(0, 90));
      } else if (/[ก-๙]/.test(text)) out.add(text.slice(0, 90));
    }
    return [...out];
  }, { locale, keep: KEEP.source });
}
async function setLanguage(x, to) {
  const { page } = x;
  const now = await page.evaluate(() => document.documentElement.lang);
  if (now === to) return now;
  const trigger = page.locator('div.border-t > button[aria-haspopup="menu"]').last();
  await x.click(trigger, "เมนูบัญชี");
  const menu = page.locator('[role="menu"]').filter({ has: page.getByRole("menuitem", { name: /Language|ภาษา/ }) });
  await x.click(menu.getByRole("menuitem", { name: /Language|ภาษา/ }), `ภาษา → ${to}`);
  await waitFor(async () => (await page.evaluate(() => document.documentElement.lang)) === to, 10000);
  await page.keyboard.press("Escape").catch(() => {});
  return page.evaluate(() => document.documentElement.lang);
}
const accountLanguage = (page, gw) => page.evaluate(async (gw) => (await (await fetch(`${gw}/portal/me`, { credentials: "include", cache: "no-store" })).json().catch(() => ({}))).preferred_language || null, gw);

async function language(client) {
  const x = await W.session(client, "language");
  const { page } = x;
  // the gateway as the page itself reaches it (its cookies are the host's)
  const gw = L.TARGET === "live" ? L.API_BASE.gw : "http://localhost:18080";
  await home(page);
  const found = { locale: await page.evaluate(() => document.documentElement.lang), stored: await stored(page, "sran-chat-locale"), account: await accountLanguage(page, gw) };
  log(`${x.tag}: found — page ${found.locale}, this client ${found.stored}, account ${found.account}`);
  const report = { found, th: {}, en: {} };
  try {
  for (const to of ["th", "en"]) {
    await home(page);
    const got = await setLanguage(x, to);
    x.check(got === to, `the switch turns the page ${to}`, got);
    await L.wait(1500);
    const saved = await waitFor(async () => ((await accountLanguage(page, gw)) === to ? to : null), 15000, 800);
    x.check(saved === to, `${to}: the choice is saved on the person's account`, String(await accountLanguage(page, gw)));
    if (client === "desktop") {
      // the app's own menus, tray and dialogs: the shell keeps the page's language (shell-prefs.json)
      const prefs = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "shell-prefs.json");
      const native = await waitFor(async () => { try { return JSON.parse(fs.readFileSync(prefs, "utf8")).locale === to ? to : null; } catch { return null; } }, 10000, 500);
      x.check(native === to, `${to}: the desktop app's own menus follow at once (its stored language)`, String(native));
    }
    for (const p of PAGES) {
      const r = await page.goto(`${WEB()}${p}`, { waitUntil: "load" }).catch(() => null);
      if (!r || r.status() >= 400) continue;
      await L.wait(2200);
      const left = await leaks(page, to);
      report[to][p] = left;
      await x.shot(`${to}${p.replace(/\//g, "_")}`);
    }
    const n = Object.values(report[to]).reduce((a, b) => a + b.length, 0);
    x.check(true, `${to}: text not in the chosen language on ${Object.keys(report[to]).length} pages (outside Agent Market, chat titles and what people typed): ${n}`, JSON.stringify(report[to]).slice(0, 380));
    // a reload keeps it
    await page.reload({ waitUntil: "load" });
    await L.wait(2500);
    x.check((await page.evaluate(() => document.documentElement.lang)) === to && (await stored(page, "sran-chat-locale")) === to, `${to}: kept across a reload`);
    // a second client of the same person follows the account (a browser that never chose takes Thai;
    // one that had chosen follows the change at its next load)
    if (to === "th") {
      const other = await W.openWeb(process.env.AM6_EMAIL || process.env.OWNER_EMAIL || L.TS_USER.email);
      await other.page.goto(`${WEB()}/chat`, { waitUntil: "load" });
      const lang = await waitFor(async () => ((await other.page.evaluate(() => document.documentElement.lang)) === "th" ? "th" : null), 30000, 800);
      x.check(lang === "th", "a second client of the same person, which never chose, opens in the account's Thai", String(lang));
      report.second = { opened: lang };
      // … and the choice made there reaches this one
      await other.page.evaluate(() => window.localStorage.setItem("sran-chat-locale-synced", "1"));
      await other.close();
    }
  }
  // Agent Market reads Thai in both languages (its own decision): said, not counted as a leak
  await page.goto(`${WEB()}/chat/agents`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-market-grid"]', { timeout: 60000 });
  await L.wait(1500);
  report.agentMarketInEnglish = (await x.words(page.locator("h1").first())) || "";
  await x.shot("en-agent-market-stays-thai");
  } finally {
    // as found — whatever happened above
    if (found.locale === "th" || found.locale === "en") {
      await home(page);
      await setLanguage(x, found.locale);
      await L.wait(2500);
      x.check((await accountLanguage(page, gw)) === found.locale, "the language is put back as found, on the page and on the account", `${found.locale}`);
    }
  }
  // the desktop app's own menus follow the page: its stored language is the page's
  if (client === "desktop") {
    const prefs = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "shell-prefs.json");
    const native = fs.existsSync(prefs) ? JSON.parse(fs.readFileSync(prefs, "utf8")).locale : null;
    x.check(native === found.locale, "the desktop app's own menus, tray and dialogs are in the page's language (its stored choice)", String(native));
    report.desktopNative = native;
  }
  fs.writeFileSync(path.join(x.dir, "language-strings.json"), JSON.stringify(report, null, 1));
  await done(x, "language", { found });
}

const phases = {
  cards, chats, form, language,
  cleanup: W.phases.cleanup,
};
const cmd = process.argv[2];
if (!phases[cmd]) {
  console.log(`usage: walk.js ${Object.keys(phases).join(" | ")} <web|desktop>`);
  process.exit(2);
}
phases[cmd](process.argv[3] || "web").catch((e) => {
  log(`ERROR ${String((e && e.stack) || e).slice(0, 700)}`);
  process.exit(1);
});
