#!/usr/bin/env node
// AM1 UI walkthrough — the web workspace against the real test stack.
//
//   (web-chat dev on :13002 pointed at the test stack, AGENT_MARKET_ENABLED=true)
//   SMOKE_TARGET=test node scripts/probes/AM1/ui.js
//
// A headless browser with its own profile (the session cookie is a minted
// test-user token, so the owner's browser and live session are untouched).
// Uploads a file with findings through the composer, clicks an answer on the
// question card, waits for the files, opens "ขั้นตอนและที่มาของตัวเลข".
// Screenshots + console errors go to results/ui/.
"use strict";
const fs = require("fs");
const path = require("path");
const L = require("../../smoke/lib.js");

const MAIN = process.env.AM1_MAIN_CHECKOUT || path.resolve(L.ROOT, "..", "ai-gateway");
const { chromium } = require(require.resolve("playwright", { paths: [path.join(L.ROOT, "apps", "web-chat"), path.join(MAIN, "apps", "web-chat")] }));
const WEB = process.env.AM1_WEB || "http://localhost:13002";
const OUT = path.join(__dirname, "results", "ui");
fs.mkdirSync(OUT, { recursive: true });
const FIX = path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report");
const token = L.mintPortalToken(L.TS_USER, 7200);
const H = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
const EDGE = `${L.API_BASE.edge}/api/v1/agent-market`;
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function j(p, init = {}) {
  const r = await L.http(EDGE + p, { timeoutMs: 60000, ...init, headers: { ...H, ...(init.headers || {}) } });
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 200)}`);
  return r.json;
}

async function main() {
  if (L.TARGET !== "test") throw new Error("ui.js runs on the test stack only");
  // The probe's own install (am1.js install) — the oldest one for this agent.
  const inst = (await j("/installs")).installs.filter((i) => i.slug === "sran.financial-report")
    .sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
  if (!inst) throw new Error("no install for the test user (run am1.js install first)");
  // No run left parked from an earlier try (an upload would resume it).
  for (const r of (await j(`/installs/${inst.id}/runs`)).runs.filter((x) => ["queued", "awaiting_input"].includes(x.status))) {
    await j(`/runs/${r.id}/cancel`, { method: "POST", body: "{}" });
    log(`cancelled leftover run ${r.id.slice(0, 8)}`);
  }
  // Start from no remembered answers so the file's findings are asked.
  const defs = (await j(`/installs/${inst.id}/defaults`)).defaults || [];
  for (const d of defs.filter((x) => x.key.startsWith("answers."))) {
    await j(`/installs/${inst.id}/defaults/${encodeURIComponent(d.key)}`, { method: "DELETE" });
    log(`cleared ${d.key}`);
  }

  const browser = await chromium.launch({ args: ["--disable-web-security"] });
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, locale: "th-TH" });
  await ctx.addCookies([{ name: "portal_access_token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => errors.push("pageerror: " + String(e).slice(0, 300)));
  const shot = async (name, target = page, opts = {}) => {
    await target.screenshot({ path: path.join(OUT, name), ...opts });
    log(`shot ${name}`);
  };

  await page.goto(`${WEB}/chat/agents`, { waitUntil: "networkidle", timeout: 120000 });
  await page.waitForTimeout(1500);
  await shot("01-market.png", page, { fullPage: true });

  // The workspace holds its event stream open, so it never reaches "networkidle".
  await page.goto(`${WEB}/chat/agents/i/${inst.id}`, { waitUntil: "load", timeout: 120000 });
  await page.waitForSelector('[data-testid="am-composer-input"]', { timeout: 60000 });
  await page.waitForTimeout(1500);
  await shot("02-workspace.png");

  // Only what this round adds counts: the thread already holds older cards.
  const oldCards = await page.locator('[data-testid="am-question-card"],[data-testid="am-card-answered"],[data-testid="am-card-unused"]').count();
  const openBefore = await page.locator('[data-testid="am-question-card"]').count();
  const idsBefore = await page.locator("[data-card-id]").evaluateAll((els) => els.map((e) => e.getAttribute("data-card-id")));
  const notBefore = idsBefore.map((id) => `:not([data-card-id="${id}"])`).join("");
  const oldOutputs = await page.locator('[data-testid="am-outputs"]').count();
  log(`thread before: ${oldCards} cards (${openBefore} shown as open), ${oldOutputs} output cards`);
  const t0 = Date.now();
  await page.setInputFiles('[data-testid="am-composer-file"]', path.join(FIX, "evals", "fixtures", "five_findings.xlsx"));
  await page.waitForSelector('[data-testid="am-staged-chip"]', { timeout: 30000 });
  await page.fill('[data-testid="am-composer-input"]', "[AM1] ปิดงบเดือนกันยายน");
  await page.click('[data-testid="am-composer-send"]');
  log("sent file + message");

  const card = page.locator(`[data-testid="am-question-card"]${notBefore}`).first();
  await card.waitFor({ timeout: 240000 });
  const tCard = Date.now();
  const cardId = await card.getAttribute("data-card-id");
  await page.waitForTimeout(800);
  await card.scrollIntoViewIfNeeded();
  await shot("03-question-card.png", card);
  await shot("03b-question-card-page.png");
  const toggle = card.locator('[data-testid="evidence-toggle"]').first();
  if (await toggle.count()) {
    await toggle.click();
    await page.waitForTimeout(400);
    await shot("04-question-card-evidence.png", card);
  }

  // Answer like a person: pick the second option on the first question, the
  // recommended one elsewhere; then send.
  const items = card.locator('[data-testid="question-item"]');
  const n = await items.count();
  const picked = [];
  for (let i = 0; i < n; i++) {
    const it = items.nth(i);
    const opts = it.locator('[data-testid="question-option"]');
    const idx = i === 0 && (await opts.count()) > 1 ? 1 : 0;
    const o = opts.nth(idx);
    picked.push((await o.innerText()).replace(/\s+/g, " ").trim());
    await o.click();
    await page.waitForTimeout(200);
  }
  await shot("05-question-card-picked.png", card);
  await card.locator('[data-testid="question-submit"]').click();
  log(`answered ${n} question(s) by click: ${picked.join(" | ")}`);

  // Further cards (a blocking finding beyond four) are answered the same way.
  const outputs = page.locator('[data-testid="am-outputs"]').nth(oldOutputs);
  for (let round = 0; round < 4; round++) {
    const which = await Promise.race([
      outputs.waitFor({ timeout: 300000 }).then(() => "outputs"),
      page.waitForSelector(`[data-testid="am-question-card"]${notBefore}:not([data-card-id="${cardId}"])`, { timeout: 300000 }).then(() => "card"),
    ]);
    if (which === "outputs") break;
    const c2 = page.locator(`[data-testid="am-question-card"]${notBefore}:not([data-card-id="${cardId}"])`).first();
    await shot(`06-card-${round + 2}.png`, c2);
    const it2 = c2.locator('[data-testid="question-item"]');
    for (let i = 0; i < (await it2.count()); i++) await it2.nth(i).locator('[data-testid="question-option"]').first().click();
    await c2.locator('[data-testid="question-submit"]').click();
    log(`answered card ${round + 2}`);
  }
  const tOut = Date.now();
  await page.waitForTimeout(1500);
  await outputs.scrollIntoViewIfNeeded();
  await shot("07-outputs.png", outputs);
  await shot("07b-outputs-page.png");

  const steps = page.getByText("ขั้นตอนและที่มาของตัวเลข").last();
  let runUrl = null;
  if (await steps.count()) {
    await steps.click();
    await page.waitForLoadState("load");
    await page.waitForTimeout(2000);
    runUrl = page.url();
    await shot("08-run-provenance.png", page, { fullPage: true });
  }

  await page.goto(`${WEB}/chat/agents/i/${inst.id}/settings`, { waitUntil: "networkidle", timeout: 120000 });
  await page.waitForTimeout(1500);
  await shot("09-settings.png", page, { fullPage: true });

  // Phone width: the same thread.
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: "th-TH", deviceScaleFactor: 2 });
  await phone.addCookies([{ name: "portal_access_token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const pp = await phone.newPage();
  await pp.goto(`${WEB}/chat/agents/i/${inst.id}`, { waitUntil: "load", timeout: 120000 });
  await pp.waitForSelector('[data-testid="am-composer-input"]', { timeout: 60000 });
  await pp.waitForTimeout(2500);
  await pp.screenshot({ path: path.join(OUT, "10-phone-thread.png") });
  const overflow = await pp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  log(`phone horizontal overflow: ${overflow}px`);

  await browser.close();
  const summary = { install: inst.id, open_cards_before: openBefore, questions_clicked: n, picked, card_after_s: (tCard - t0) / 1000, outputs_after_s: (tOut - t0) / 1000,
    run_url: runUrl, phone_overflow_px: overflow, console_errors: errors };
  fs.writeFileSync(path.join(OUT, "ui_summary.json"), JSON.stringify(summary, null, 1));
  log(JSON.stringify({ ...summary, console_errors: errors.length }));
}

main().catch((e) => { console.error(e); process.exit(1); });
