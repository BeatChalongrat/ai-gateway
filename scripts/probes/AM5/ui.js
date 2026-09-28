#!/usr/bin/env node
// AM5 phases A–C on screen, on the test stack: the chat and an agent's
// workspace side by side (composer, a staged file, the person's bubble, a
// sent file, the agent's words), the model menu and "เลือกโมเดลเอง", the
// question card's recommendation, the round's "เอกสารที่ยังไม่ได้รวม" list
// and one click back, a round that fails and is tried again.
//
//   (test stack up with the AM5 chat-edge and runner; web-chat dev on :13002
//    with NEXT_PUBLIC_API_URL=http://localhost:14000/api/v1 and
//    AGENT_MARKET_ENABLED=true; AM3 am3.js seed + setup already run)
//   SMOKE_TARGET=test node scripts/probes/AM5/ui.js tour
//   SMOKE_TARGET=test node scripts/probes/AM5/ui.js cleanup
//
// A headless browser with its own profile and a minted test-user session
// (the owner's browser and live session are never touched). The app runs in
// English, as the owner's does: whatever is Thai on these screens is Thai
// because the workspace says so. Screenshots, each screen's text, what the
// two composers measure, and every technical word a person could read go to
// results/ui/ (gitignored). A failed round is a real one: the test runner's
// sandbox image tag is taken away for one round (AM5_SANDBOX_TAG, a tag of
// its own — the live runner's image is not touched) and put back.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") {
  console.error("ui.js runs on the test stack only (SMOKE_TARGET=test)");
  process.exit(2);
}
const { chromium } = require(require.resolve("playwright", { paths: [path.join(L.ROOT, "apps", "web-chat")] }));
const WEB = process.env.AM5_WEB || "http://localhost:13002";
const EDGE = `${L.API_BASE.edge}/api/v1`;
const AM = `${EDGE}/agent-market`;
const OUT = path.join(__dirname, "results", "ui");
const DOCS = path.join(L.ROOT, "backend", "agent_market", "fixtures", "e2", "docs");
const SANDBOX_TAG = process.env.AM5_SANDBOX_TAG || "ai-gateway-sandbox:am5test";
const SANDBOX_FROM = process.env.AM5_SANDBOX_FROM || "ai-gateway-sandbox:latest";
const CHAT_MODEL = process.env.AM5_CHAT_MODEL || L.FREE_MODEL;
const FILES = [
  { src: "d01_clean_full_tax_invoice.pdf", name: "ใบกำกับภาษี ก.ย.69 ร้านเครื่องเขียน.pdf" },
  { src: "f02_vat_mismatch.pdf", name: "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf" },
];
fs.mkdirSync(OUT, { recursive: true });
const STATE = path.join(OUT, "state.json");
const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, "utf8")) : { installs: [], chats: [] };
const save = () => fs.writeFileSync(STATE, JSON.stringify(state, null, 1));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const session = L.bearerSession(L.mintPortalToken(L.TS_USER, 7200));
const found = { technical: {}, console: [], checks: [] };
const check = (ok, what, detail = "") => {
  found.checks.push({ ok: !!ok, what, detail: String(detail).slice(0, 300) });
  log(`${ok ? "PASS" : "FAIL"} ${what}${detail ? ` — ${String(detail).slice(0, 200)}` : ""}`);
  if (!ok) process.exitCode = 1;
  return ok;
};

async function am(p, init = {}) {
  const r = await L.http(AM + p, { timeoutMs: 60000, ...init, headers: { "Content-Type": "application/json", ...session.headers, ...(init.headers || {}) } });
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 300)}`);
  return r.json;
}
const post = (p, body) => am(p, { method: "POST", body: JSON.stringify(body || {}) });
async function waitFor(fn, ms, step = 1000) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() - t0 > ms) return null;
    await L.wait(step);
  }
}
const docker = (...args) => spawnSync("docker", args, { encoding: "utf8" });

// what no person should read on an agent's screen (the owner's screen showed the first of these)
const TECHNICAL = /\b[A-Z][A-Za-z]*(?:Error|Exception)\b|NoneType|Traceback|not iterable|engine step failed|sandbox:|dial tcp|SQLSTATE|\b[a-z]+(?:_[a-z0-9]+)*_failed\b|fallback_narrative|Unable to find image/;

async function shot(page, name, { full = false, clip = null } = {}) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full, ...(clip ? { clip } : {}) });
  const text = await page.innerText("body").catch(() => "");
  fs.writeFileSync(path.join(OUT, `${name}.txt`), text.slice(0, 30000));
  const bad = text.match(new RegExp(TECHNICAL.source, "g"));
  if (bad) found.technical[name] = [...new Set(bad)];
  log(`shot ${name}${bad ? ` — TECHNICAL: ${[...new Set(bad)].join(" | ")}` : ""}`);
}
const clipOf = async (loc, pad = 12) => {
  const b = await loc.first().boundingBox();
  if (!b) return null;
  return { x: Math.max(0, b.x - pad), y: Math.max(0, b.y - pad), width: b.width + pad * 2, height: b.height + pad * 2 };
};

/** What the composer on this page measures: the same numbers on both pages mean the same box. */
async function measureComposer(page) {
  // at rest on both pages: a composer that has the caret draws a stronger border
  await page.evaluate(() => { const a = document.activeElement; if (a && a.blur) a.blur(); });
  await L.wait(500);
  return page.evaluate(() => {
    const root = document.querySelector('[data-testid="composer-root"]');
    if (!root) return null;
    const ta = root.querySelector("textarea");
    const pill = ta.parentElement.parentElement;
    const send = [...root.querySelectorAll("button")].find((b) => b.querySelector("svg path[d^='M8 14V2']"));
    const attach = root.querySelector('[data-testid="attach-button"]');
    const cs = (el, keys) => Object.fromEntries(keys.map((k) => [k, getComputedStyle(el)[k]]));
    const box = (el) => { const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; };
    return {
      root: { class: root.className, ...cs(root, ["paddingLeft", "paddingTop", "paddingBottom", "backgroundColor"]) },
      column: { ...box(root.firstElementChild), x: Math.round(root.firstElementChild.getBoundingClientRect().left) },
      pill: { class: pill.className, ...box(pill), ...cs(pill, ["borderRadius", "borderTopColor", "backgroundColor", "boxShadow"]) },
      textarea: { class: ta.className, ...cs(ta, ["fontSize", "lineHeight", "fontFamily", "color"]), shortcut: ta.getAttribute("data-shortcut") },
      attach: attach ? { class: attach.className, ...box(attach) } : null,
      send: send ? { class: send.className, ...box(send), label: send.getAttribute("aria-label") } : null,
    };
  });
}

async function measureBubble(page, sel) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const c = getComputedStyle(el);
    return { class: el.className, borderRadius: c.borderRadius, backgroundColor: c.backgroundColor, fontSize: c.fontSize, padding: c.padding, lineHeight: c.lineHeight };
  }, sel);
}

const phases = {
  async tour() {
    // ── what the screens need: a chat with one turn, an installed invoice agent ──
    const chatId = await L.newChat(session, CHAT_MODEL, "[AM5] เทียบหน้าจอ");
    state.chats.push(chatId);
    save();
    const turn = await L.turn(session, chatId, "[AM5] ตอบสั้น ๆ ว่า ภาษีมูลค่าเพิ่มของไทยอัตราเท่าไร");
    log(`chat turn: ${turn.terminal} served=${turn.served} ${turn.content.slice(0, 60).replace(/\s+/g, " ")}`);
    const inst = await post("/installs", { slug: "sran.invoice-processing", idempotency_key: crypto.randomUUID(), self_test: true });
    const installId = inst.install.id;
    state.installs.push(installId);
    save();
    log(`installed ${installId}; waiting for the sample self-test`);
    await waitFor(async () => ((await am(`/installs/${installId}/runs`)).runs || []).some((r) => r.kind === "self_test" && ["succeeded", "failed"].includes(r.status)), 600000, 2000);

    const browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, locale: "th-TH", acceptDownloads: true });
    await ctx.addCookies([{ name: "portal_access_token", value: L.mintPortalToken(L.TS_USER, 7200), domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
    // the owner's app runs in English
    await ctx.addInitScript(() => { try { window.localStorage.setItem("sran-chat-locale", "en"); } catch { /* no storage */ } });
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") found.console.push(m.text().slice(0, 300)); });
    const buffers = FILES.map((f) => ({ name: f.name, mimeType: "application/pdf", buffer: fs.readFileSync(path.join(DOCS, f.src)) }));

    // ── A. the chat ──
    await page.goto(`${WEB}/chat/${chatId}`, { waitUntil: "load", timeout: 120000 });
    await page.waitForSelector('[data-testid="composer-root"]', { timeout: 120000 });
    await page.waitForSelector('[data-testid="assistant-body"]', { timeout: 60000 }).catch(() => null);
    await L.wait(2500);
    await shot(page, "A1_chat_thread");
    const chatComposer = await measureComposer(page);
    const chatBubble = await measureBubble(page, ".bg-bubble-user");
    await page.locator('[data-testid="composer-root"] input[type="file"]').first().setInputFiles(buffers.slice(0, 1));
    await page.waitForSelector('[data-testid="composer-chip-row"]', { timeout: 30000 }).catch(() => null);
    await L.wait(2500);
    await shot(page, "A2_chat_composer_file", { clip: await clipOf(page.locator('[data-testid="composer-root"]'), 0) });
    await page.locator('[data-testid="composer-root"] button').filter({ has: page.locator("svg") }).first().waitFor();
    // the chat's model picker (the one "เลือกโมเดลเอง" must open)
    await page.locator('[data-testid="model-trigger-label"]').first().click({ timeout: 10000 });
    await L.wait(1200);
    await shot(page, "B0_chat_model_picker");
    await page.keyboard.press("Escape");

    // ── A. the workspace ──
    await page.goto(`${WEB}/chat/agents/i/${installId}`, { waitUntil: "load", timeout: 120000 });
    await page.waitForSelector('[data-testid="composer-root"]', { timeout: 120000 });
    await L.wait(3000);
    await shot(page, "A3_workspace_empty");
    const wsComposer = await measureComposer(page);
    fs.writeFileSync(path.join(OUT, "composer_measure.json"), JSON.stringify({ chat: chatComposer, workspace: wsComposer }, null, 1));
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    for (const part of ["root", "pill", "textarea", "attach", "column"]) {
      check(same(chatComposer?.[part], wsComposer?.[part]), `composer ${part}: the workspace's is the chat's`, JSON.stringify(wsComposer?.[part]));
    }
    check(chatComposer?.send?.class === wsComposer?.send?.class && chatComposer?.send?.w === wsComposer?.send?.w, "composer send: the same button", wsComposer?.send?.class);
    check(wsComposer?.send?.label === "ส่ง", "the workspace's composer is Thai in an English app", wsComposer?.send?.label);
    check(!(await page.locator('[data-testid="am-rail"]').count()) && !(await page.locator('[data-testid="am-workspace"] nav').count()),
      "the workspace has no list of agents of its own beside the sidebar");

    // ── B. the model menu ──
    const control = page.locator('[data-testid="am-model-control"]');
    if (await control.count()) {
      await control.click({ timeout: 10000 });
      await page.waitForSelector('[data-testid="am-model-menu"]', { timeout: 10000 });
      await L.wait(800);
      await shot(page, "B1_workspace_model_menu");
      const fits = await page.evaluate(() => {
        const r = document.querySelector('[data-testid="am-model-menu"]').getBoundingClientRect();
        const d = document.documentElement;
        return { left: Math.round(r.left), right: Math.round(r.right), window: window.innerWidth, sideways: d.scrollWidth > d.clientWidth + 1 };
      });
      check(fits.left >= 0 && fits.right <= fits.window && !fits.sideways, "the model menu opens inside the window", JSON.stringify(fits));
      const rows = await page.locator('[data-testid="am-model-option"]').evaluateAll((els) => els.map((e) => ({ model: e.getAttribute("data-model"), text: e.innerText.replace(/\s+/g, " ").trim() })));
      fs.writeFileSync(path.join(OUT, "model_menu.json"), JSON.stringify({ rows, single: await page.locator('[data-testid="am-model-single"]').innerText().catch(() => null) }, null, 1));
      check(new Set(rows.map((r) => r.model)).size === rows.length, "no two rows of the model menu are the same model", rows.map((r) => r.model).join(", "));
      await page.locator('[data-testid="am-model-custom"]').click({ timeout: 10000 });
      await L.wait(1500);
      await shot(page, "B2_workspace_model_custom");
      check(await page.locator('[data-testid="am-model-menu"] input[type="text"]').count(), "“เลือกโมเดลเอง” opens the chat's picker (its search box)");
      const locked = await page.locator('[data-testid="am-model-menu"] [data-testid="model-row-disabled"]').evaluateAll((els) => els.map((e) => e.innerText.replace(/\s+/g, " ").trim().slice(0, 160)));
      fs.writeFileSync(path.join(OUT, "model_custom_locked.json"), JSON.stringify(locked, null, 1));
      await page.keyboard.press("Escape");
      await L.wait(400);
    } else {
      check(false, "the workspace has a model control");
    }

    // ── A. a staged file, then the round ──
    await page.locator('[data-testid="am-composer-file"]').setInputFiles(buffers);
    await page.waitForSelector('[data-testid="composer-chip-row"]', { timeout: 60000 });
    await L.wait(2500);
    await shot(page, "A4_workspace_composer_files", { clip: await clipOf(page.locator('[data-testid="composer-root"]'), 0) });
    await page.locator('[data-testid="am-composer-input"]').fill("[AM5] ทำทะเบียนซื้อเดือนกันยายน");
    const before = ((await am(`/installs/${installId}/runs`)).runs || []).map((r) => r.id);
    await page.locator('[data-testid="am-composer-send"]').click({ timeout: 30000 });
    const run = await waitFor(async () => ((await am(`/installs/${installId}/runs`)).runs || []).find((r) => r.kind === "run" && !before.includes(r.id)), 60000);
    if (!run) throw new Error("no round started");
    state.run = run.id;
    save();
    await L.wait(1500);
    await shot(page, "A5_workspace_working");
    const wsBubble = await measureBubble(page, ".bg-bubble-user");
    fs.writeFileSync(path.join(OUT, "bubble_measure.json"), JSON.stringify({ chat: chatBubble, workspace: wsBubble }, null, 1));
    check(same(chatBubble, wsBubble), "the person's bubble: the workspace's is the chat's", JSON.stringify(wsBubble));
    check(await page.locator('[data-testid="am-progress"] .cat-indicator').count(), "a working round shows the chat's working indicator");

    // ── C. the question card ──
    const parked = await waitFor(async () => { const d = await am(`/runs/${run.id}`); return d.run.status === "awaiting_input" || ["succeeded", "failed"].includes(d.run.status) ? d : null; }, 600000, 1500);
    check(parked?.run?.status === "awaiting_input", "the VAT invoice asks its question", parked?.run?.status);
    const card = page.locator('[data-testid="am-question-card"]').last();
    await card.waitFor({ state: "visible", timeout: 60000 });
    await card.scrollIntoViewIfNeeded();
    await L.wait(1200);
    await shot(page, "C1_question_card");
    const cardText = (await card.innerText()).replace(/\s+/g, " ");
    fs.writeFileSync(path.join(OUT, "question_card.txt"), cardText);
    check(/ค่าแนะนำ: บันทึกตามเอกสาร/.test(cardText), "the card recommends recording the purchase", cardText.slice(0, 200));
    check(!/ค่าแนะนำ: ตัดเอกสารนี้ออก/.test(cardText), "the card does not recommend leaving the purchase out");
    check(/เอกสารที่ยังไม่ได้รวม/.test(cardText), "the card says what skipping does");
    // the person skips it (the day-one trap)
    await card.locator('[data-testid="question-skip"]').click({ timeout: 15000 });
    const done = await waitFor(async () => { const d = await am(`/runs/${run.id}`); return ["succeeded", "failed"].includes(d.run.status) ? d : null; }, 600000, 1500);
    check(done?.run?.status === "succeeded", "the round finishes", done?.run?.status);
    const list = page.locator('[data-testid="am-not-included"]').last();
    await list.waitFor({ state: "visible", timeout: 120000 });
    await list.scrollIntoViewIfNeeded();
    await L.wait(1500);
    await shot(page, "C2_files_card_left_out");
    const listText = (await list.innerText()).replace(/\s+/g, " ");
    check(listText.includes(FILES[1].name) && /เอกสารที่ยังไม่ได้รวม \(1\)/.test(listText), "the invoice that was left out is on the round's list", listText.slice(0, 200));
    check(await page.locator('[data-testid="am-round-left-out"]').count(), "the round's divider says one document is still left out");
    // the register itself carries the sheet
    const d1 = await am(`/runs/${run.id}`);
    const art1 = (d1.artifacts || []).filter((a) => a.format === "xlsx").sort((a, b) => b.version - a.version)[0];
    const r1 = await fetch(`${AM}/artifacts/${art1.id}/download`, { headers: { ...session.headers } });
    fs.writeFileSync(path.join(OUT, art1.name), Buffer.from(await r1.arrayBuffer()));
    // one click back
    await list.locator('[data-testid="am-restore"]').first().click({ timeout: 15000 });
    await L.wait(1500);
    await shot(page, "C3_restore_clicked");
    const v2 = await waitFor(async () => { const d = await am(`/runs/${run.id}`); const a = (d.artifacts || []).filter((x) => x.format === "xlsx"); return d.run.status === "succeeded" && a.some((x) => x.version >= 2) ? d : null; }, 600000, 1500);
    check(!!v2, "the same round computes again and delivers the next version");
    await L.wait(3000);
    const lastFiles = page.locator('[data-testid="am-outputs"]').last();
    await lastFiles.scrollIntoViewIfNeeded();
    await L.wait(800);
    await shot(page, "C4_files_card_v2");
    check(!(await lastFiles.locator('[data-testid="am-not-included"]').count()), "nothing is left out on the newest files card");
    if (v2) {
      const art2 = v2.artifacts.filter((a) => a.format === "xlsx").sort((a, b) => b.version - a.version)[0];
      const r2 = await fetch(`${AM}/artifacts/${art2.id}/download`, { headers: { ...session.headers } });
      fs.writeFileSync(path.join(OUT, art2.name), Buffer.from(await r2.arrayBuffer()));
      state.registers = [art1.name, art2.name];
      save();
    }
    await shot(page, "A6_workspace_thread_full", { full: true });

    // ── C. a round that fails, for real, and is tried again ──
    const has = docker("image", "inspect", SANDBOX_TAG).status === 0;
    if (!has) {
      log(`no ${SANDBOX_TAG}: the failed round is skipped (start the test runner with SANDBOX_IMAGE=${SANDBOX_TAG})`);
      found.checks.push({ ok: null, what: "a failed round (skipped: the test runner does not use a sandbox tag of its own)" });
    } else {
      docker("rmi", SANDBOX_TAG);
      log(`${SANDBOX_TAG} taken away`);
      try {
        await page.locator('[data-testid="am-round-start"]').last().click({ timeout: 15000 }).catch(() => null);
        await L.wait(1500);
        await page.locator('[data-testid="am-composer-file"]').setInputFiles(buffers.slice(0, 1));
        await page.waitForSelector('[data-testid="composer-chip-row"]', { timeout: 60000 });
        await L.wait(2500);
        const seen = ((await am(`/installs/${installId}/runs`)).runs || []).map((r) => r.id);
        await page.locator('[data-testid="am-composer-send"]').click({ timeout: 30000 });
        await L.wait(2000);
        const chip = page.locator('[data-testid="am-intent-new"]');
        if (await chip.count()) await chip.last().click({ timeout: 10000 });
        const bad = await waitFor(async () => ((await am(`/installs/${installId}/runs`)).runs || []).find((r) => r.kind === "run" && !seen.includes(r.id)), 90000);
        if (!bad) throw new Error("the failing round did not start");
        const failed = await waitFor(async () => { const d = await am(`/runs/${bad.id}`); return ["succeeded", "failed"].includes(d.run.status) ? d : null; }, 600000, 1500);
        check(failed?.run?.status === "failed", "a round without its sandbox fails", `${failed?.run?.status} ${failed?.run?.status_reason}`);
        const fcard = page.locator('[data-testid="am-failed-round"]').last();
        await fcard.waitFor({ state: "visible", timeout: 60000 });
        await fcard.scrollIntoViewIfNeeded();
        await L.wait(1200);
        await shot(page, "C5_failed_round");
        const ftext = (await fcard.innerText()).replace(/\s+/g, " ");
        fs.writeFileSync(path.join(OUT, "failed_round.txt"), ftext);
        check(!TECHNICAL.test(ftext) && !/[A-Za-z]{5,}/.test(ftext), "the failed round shows no error text, type name or step id", ftext.slice(0, 240));
        for (const part of ["what", "next", "files", "charged"]) check(await fcard.locator(`[data-testid="am-failed-${part}"]`).count(), `the failed round says: ${part}`);
        const step = (failed.steps || []).find((s) => s.status === "failed");
        check(step && /sandbox|image/i.test(step.error || ""), "the error itself is kept on the run, for the run page", (step?.error || "").slice(0, 120));
        const pcard = page.locator('[data-testid="am-progress"]').last();
        check(!TECHNICAL.test(await pcard.innerText()), "the progress card shows no error text");
        state.failed = bad.id;
        save();
      } finally {
        docker("tag", SANDBOX_FROM, SANDBOX_TAG);
        log(`${SANDBOX_TAG} put back`);
      }
      const fcard = page.locator('[data-testid="am-failed-round"]').last();
      const seen = ((await am(`/installs/${installId}/runs`)).runs || []).map((r) => r.id);
      await fcard.locator('[data-testid="am-failed-retry"]').click({ timeout: 15000 });
      const again = await waitFor(async () => ((await am(`/installs/${installId}/runs`)).runs || []).find((r) => r.kind === "run" && !seen.includes(r.id)), 90000);
      const settled = (id, also = []) => waitFor(async () => { const d = await am(`/runs/${id}`); return ["succeeded", "failed", ...also].includes(d.run.status) ? d : null; }, 600000, 1500);
      let ok = again && (await settled(again.id, ["awaiting_input"]));
      check(!!ok && ok.run.status !== "failed", "“ลองใหม่ด้วยไฟล์เดิม”: the round runs with the files the failed one had", `${ok?.run?.status} ${ok?.run?.status_reason || ""}`);
      if (ok?.run?.status === "awaiting_input") {
        // the file is one the first round already recorded: the agent asks whether it was sent twice
        const q = page.locator('[data-testid="am-question-card"]').last();
        await q.waitFor({ state: "visible", timeout: 60000 });
        await q.scrollIntoViewIfNeeded();
        await L.wait(1000);
        await shot(page, "C6a_retried_round_asks");
        await q.locator('[data-testid="question-option"]').nth(1).click({ timeout: 15000 });
        await L.wait(400);
        await q.locator('[data-testid="question-submit"]').click({ timeout: 15000 });
        ok = await settled(again.id);
      }
      check(ok?.run?.status === "succeeded", "the round that was tried again finishes", `${ok?.run?.status} ${ok?.run?.status_reason || ""}`);
      await L.wait(3000);
      await page.locator('[data-testid="am-outputs"]').last().scrollIntoViewIfNeeded().catch(() => null);
      await shot(page, "C6_retried_round");
      // the run page is where the technical detail lives
      await page.goto(`${WEB}/chat/agents/runs/${state.failed}`, { waitUntil: "load", timeout: 120000 });
      await L.wait(4000);
      await shot(page, "C7_run_page_failed", { full: true });
    }

    // a phone
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, locale: "th-TH" });
    await phone.addCookies([{ name: "portal_access_token", value: L.mintPortalToken(L.TS_USER, 3600), domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
    await phone.addInitScript(() => { try { window.localStorage.setItem("sran-chat-locale", "en"); } catch { /* no storage */ } });
    const pp = await phone.newPage();
    await pp.goto(`${WEB}/chat/agents/i/${installId}`, { waitUntil: "load", timeout: 120000 });
    await pp.waitForSelector('[data-testid="composer-root"]', { timeout: 120000 });
    await L.wait(3500);
    await shot(pp, "A7_workspace_phone");
    const overflow = await pp.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    check(!overflow, "the workspace does not scroll sideways on a 390 px phone");
    await pp.goto(`${WEB}/chat/${chatId}`, { waitUntil: "load", timeout: 120000 });
    await pp.waitForSelector('[data-testid="composer-root"]', { timeout: 120000 });
    await L.wait(3000);
    await shot(pp, "A8_chat_phone");
    await phone.close();
    await browser.close();

    check(Object.keys(found.technical).filter((k) => k !== "C7_run_page_failed").length === 0, "no screen of the workspace shows a technical word",
      JSON.stringify(Object.fromEntries(Object.entries(found.technical).filter(([k]) => k !== "C7_run_page_failed"))));
    fs.writeFileSync(path.join(OUT, "tour.json"), JSON.stringify(found, null, 1));
    const c = found.checks.reduce((a, x) => { a[x.ok === null ? "skip" : x.ok ? "pass" : "fail"]++; return a; }, { pass: 0, fail: 0, skip: 0 });
    log(`tour: ${c.pass} pass, ${c.fail} fail, ${c.skip} skip · console errors ${found.console.length}`);
  },

  async cleanup() {
    for (const id of state.installs || []) {
      const r = await L.http(`${AM}/installs/${id}`, { method: "DELETE", headers: session.headers }).catch((e) => ({ status: String(e) }));
      log(`install ${id} removed: ${r.status}`);
    }
    for (const id of state.chats || []) log(`chat ${id} deleted: ${await L.deleteChat(session, id)}`);
    state.installs = [];
    state.chats = [];
    save();
  },
};

const cmd = process.argv[2] || "tour";
if (!phases[cmd]) {
  console.error(`usage: ui.js ${Object.keys(phases).join("|")}`);
  process.exit(2);
}
phases[cmd]().catch((e) => {
  console.error(e);
  docker("tag", SANDBOX_FROM, SANDBOX_TAG);
  process.exit(1);
});
