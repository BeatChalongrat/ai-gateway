#!/usr/bin/env node
// AM16 — the two things a person sees, read on the BUILT clients the way a person reads them (CARD-RULES
// §6b: a test that builds the card itself does not prove the page shows it). Test stack only, the test
// stack's own accounts, a browser of its own.
//
//   card <install id> <name>   the agent's page on the built web client: is the sentence "this run's
//                              documents were sent outside the organisation and were not masked" on the
//                              face of the run's card — in Thai, then with the screen in English, then
//                              put back to Thai — and is it outside everything that folds; screenshots
//   admin <slug>               the built admin app, Agent Market → Models: the agent's two checkboxes as
//                              they stand, then each turned on and off THROUGH THE PAGE, reading what
//                              the row says after every click; then the audit log page: is each change
//                              there. Leaves both switches as it found them.
//
//   SMOKE_TARGET=test node scripts/probes/AM16/walk.js card <install id> invoice-sent-out
//   SMOKE_TARGET=test node scripts/probes/AM16/walk.js admin sran.invoice-processing
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") { console.error("walk.js runs on the test stack only (SMOKE_TARGET=test)"); process.exit(2); }
const OUT = path.join(__dirname, "results", "walk");
fs.mkdirSync(OUT, { recursive: true });
const WEB = process.env.AM16_WEB || "http://localhost:13002";
const ADMIN = process.env.AM16_ADMIN || "http://localhost:13000";
const squash = (s) => String(s || "").replace(/\s+/g, " ").trim();
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const checks = [];
const check = (ok, what, detail = "") => { checks.push({ ok: !!ok, what, detail: String(detail).slice(0, 400) }); log(`${ok ? "PASS" : "FAIL"} ${what}${detail ? ` — ${String(detail).slice(0, 220)}` : ""}`); return !!ok; };
const finish = (name, extra = {}) => {
  fs.writeFileSync(path.join(OUT, `${name}.json`), JSON.stringify({ at: new Date().toISOString(), ...extra, checks }, null, 1));
  const bad = checks.filter((c) => !c.ok);
  log(`${name}: ${checks.length - bad.length} pass, ${bad.length} fail`);
  if (bad.length) process.exitCode = 1;
};
function playwright() {
  for (const d of [path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"), path.join(L.ROOT, "apps", "desktop", "node_modules", "playwright-core")]) {
    try { return require(d); } catch { /* next */ }
  }
  throw new Error("playwright-core not found (apps/web-chat/node_modules)");
}

const TH = "เอกสารของรอบนี้ถูกส่งออกนอกองค์กร และไม่ได้ปิดบังข้อมูล";
const EN = "This run's documents were sent outside the organisation and were not masked";

async function setLanguage(page, to) {
  if ((await page.evaluate(() => document.documentElement.lang)) === to) return;
  await page.locator('div.border-t > button[aria-haspopup="menu"]').last().click();
  const menu = page.locator('[role="menu"]').filter({ has: page.getByRole("menuitem", { name: /Language|ภาษา/ }) });
  await menu.getByRole("menuitem", { name: /Language|ภาษา/ }).click();
  for (let i = 0; i < 40; i++) {
    if ((await page.evaluate(() => document.documentElement.lang)) === to) break;
    await L.wait(250);
  }
  await page.keyboard.press("Escape").catch(() => {});
  await L.wait(800);
}

async function card(install, name) {
  const { chromium } = playwright();
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 1000 }, locale: "th-TH" });
    await ctx.addCookies([{ name: "portal_access_token", value: L.mintPortalToken(L.TS_USER, 1800), domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax", secure: false }]);
    const page = await ctx.newPage();
    await page.goto(`${WEB}/chat/agents/i/${install}`, { waitUntil: "load" });
    await page.locator('[data-testid="am-progress-title"]').last().waitFor({ state: "visible", timeout: 90000 });
    await L.wait(2500);
    const read = () => page.evaluate(() => {
      const text = (el) => (el ? el.innerText.replace(/\s+/g, " ").trim() : "");
      const notes = [...document.querySelectorAll('[data-testid="am-sent-out-unmasked"]')];
      return {
        lang: document.documentElement.lang,
        notes: notes.map(text),
        in_progress_card: notes.some((n) => !!n.closest('[data-testid="am-progress"]')),
        in_files_card: notes.some((n) => !!n.closest('[data-testid="am-outputs"]')),
        // on the face: visible without opening anything, and not inside a region something folds
        visible: notes.map((n) => { const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0; }),
        folded: notes.map((n) => { const d = n.closest("details"); const c = n.closest("[hidden], [aria-hidden='true']"); return !!(d && !d.open) || !!c; }),
        titles: [...document.querySelectorAll('[data-testid="am-progress-title"]')].map(text),
        seen_by: [...document.querySelectorAll('[data-testid="am-outputs-seen-by"]')].map(text),
      };
    });
    const startLang = await page.evaluate(() => document.documentElement.lang);
    await setLanguage(page, "th");
    const th = await read();
    await page.locator('[data-testid="am-sent-out-unmasked"]').first().scrollIntoViewIfNeeded().catch(() => {});
    await page.screenshot({ path: path.join(OUT, `${name}-th.png`), fullPage: false });
    check(th.notes.length > 0 && th.notes.every((n) => n.includes(TH)), "Thai screen: the run's card says its documents were sent outside and not masked", th.notes[0] || "(the sentence is not on the page)");
    check(th.in_progress_card, "… on the run's own card", `title: ${th.titles[th.titles.length - 1] || "?"}`);
    check(th.visible.length > 0 && th.visible.every(Boolean) && th.folded.every((f) => !f), "… on its face: visible, and inside nothing that folds");
    await setLanguage(page, "en");
    const en = await read();
    await page.locator('[data-testid="am-sent-out-unmasked"]').first().scrollIntoViewIfNeeded().catch(() => {});
    await page.screenshot({ path: path.join(OUT, `${name}-en.png`), fullPage: false });
    check(en.lang === "en" && en.notes.length > 0 && en.notes.every((n) => n.includes(EN) && !/[฀-๿]/.test(n)), "English screen: the same sentence in English, no Thai in it", en.notes[0] || "(not on the page)");
    await setLanguage(page, startLang || "th"); // the account's language is put back
    check((await page.evaluate(() => document.documentElement.lang)) === (startLang || "th"), "the screen's language is put back", startLang);
    finish(name, { install, th, en });
  } finally {
    await browser.close();
  }
}

async function admin(slug) {
  const accounts = JSON.parse(fs.readFileSync(path.join(L.ROOT, "scripts", "probes", "AM7", "results", "ts_admins.json"), "utf8"));
  const email = "am7-admin@fix128.test";
  // the test stack's sign-in limit counts every probe of this machine as one person (AM10/admin.js)
  const cli = 'redis-cli --no-auth-warning ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"}';
  try { execFileSync("docker", ["exec", "sran-test-redis-1", "sh", "-c", `${cli} --scan --pattern "admin_login_ip:*" | xargs -r ${cli} del`], { encoding: "utf8" }); } catch { /* the limit stays */ }
  const { chromium } = playwright();
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage();
    await page.goto(`${ADMIN}/login`, { waitUntil: "load" });
    await page.waitForFunction(() => { const f = document.querySelector("form"); return !!f && Object.keys(f).some((k) => k.startsWith("__reactProps") || k.startsWith("__reactFiber")); }, null, { timeout: 90000 });
    await page.locator('input[autocomplete="username"]').fill(email);
    await page.locator('input[type="password"]').fill(accounts[email].password);
    const answered = page.waitForResponse((r) => r.url().includes("/auth/session/login"), { timeout: 60000 });
    await page.locator('button[type="submit"]').click();
    if ((await answered).status() !== 200) throw new Error("the admin sign-in was refused");
    await page.waitForURL((u) => !String(u).includes("/login"), { timeout: 60000 });
    await page.goto(`${ADMIN}/agent-market?section=models`, { waitUntil: "load" });
    const row = page.getByTestId(`am-egress-${slug}`);
    await row.waitFor({ timeout: 60000 });
    await L.wait(1200);
    const leave = page.getByTestId(`am-egress-toggle-${slug}`);
    const masked = page.getByTestId(`am-masked-toggle-${slug}`);
    const now = page.getByTestId(`am-egress-now-${slug}`);
    const state = async () => ({ leave: await leave.isChecked(), masked: await masked.isChecked(), says: squash(await now.innerText()) });
    const said = squash(await page.getByTestId("am-egress-masking").innerText());
    check(/its own switch|สวิตช์ของตัวเอง/.test(said) && /cannot be masked|ปิดบังไม่ได้/.test(said) && /audit log|บันทึกการตรวจสอบ/.test(said) && !/also decides masking|กำหนดการปิดบังข้อมูลด้วย/.test(said),
      "the page says masking is its own switch, that a page cannot be masked, and that each change is logged", said.slice(0, 260));
    // the organisation's ceiling, so that the agent's own switch is what decides
    if (!(await page.getByTestId("am-leave-on").isChecked())) {
      await page.getByTestId("am-leave-on").click();
      await page.getByTestId("am-models-save").click();
      await L.wait(2000);
    }
    const before = await state();
    log(`as found: leave=${before.leave} masked=${before.masked} — "${before.says}"`);
    const click = async (el, what, want) => {
      await el.click();
      for (let i = 0; i < 40; i++) { if ((await el.isChecked()) === want) break; await L.wait(250); }
      await L.wait(700);
      const s = await state();
      log(`${what}: leave=${s.leave} masked=${s.masked} — "${s.says}"`);
      return s;
    };
    // start from both off
    if (before.leave) await click(leave, "(leave off, to start)", false);
    if (before.masked) await click(masked, "(masking off, to start)", false);
    const off = await state();
    check(!off.leave && !off.masked && /Stays inside|อยู่ในองค์กรเท่านั้น/.test(off.says) && /as typed|ตามที่พิมพ์/.test(off.says), "both off: stays inside, sent to the in-house model as typed", off.says);
    const m1 = await click(masked, "masking on", true);
    check(!m1.leave && m1.masked && /masked before a model sees it|ปิดบังข้อมูลส่วนบุคคลก่อนถึงโมเดล/.test(m1.says), "masking on moved masking only: the leave switch did not move", m1.says);
    const l1 = await click(leave, "leave on", true);
    check(l1.leave && l1.masked && /pages read inside only|หน้าเอกสารอ่านในองค์กรเท่านั้น/.test(l1.says), "leave on, masked: text may go out masked, pages are read inside only", l1.says);
    const m0 = await click(masked, "masking off", false);
    check(m0.leave && !m0.masked && /not masked|ไม่ปิดบัง/.test(m0.says) && /outside model|นอกองค์กร/.test(m0.says), "leave on, masking off: the row says in words that it goes out as typed, not masked", m0.says);
    await page.getByTestId("am-egress").screenshot({ path: path.join(OUT, `admin-${slug}-open-unmasked.png`) });
    // put back as found
    if (m0.leave !== before.leave) await click(leave, "(leave put back)", before.leave);
    if ((await masked.isChecked()) !== before.masked) await click(masked, "(masking put back)", before.masked);
    const after = await state();
    check(after.leave === before.leave && after.masked === before.masked, "both switches are left as they were found", `leave=${after.leave} masked=${after.masked}`);
    // the audit log page: each change is there
    await page.goto(`${ADMIN}/audit-log`, { waitUntil: "load" });
    await L.wait(2500);
    const body = squash(await page.locator("main").innerText().catch(() => page.locator("body").innerText()));
    // (the page writes an action's name in words: "Agent Market.Agent.Masked")
    const maskedRows = (body.match(/agent[ _]market\.agent\.masked/gi) || []).length;
    const leaveRows = (body.match(/agent[ _]market\.agent\.documents[ _]may[ _]leave/gi) || []).length;
    check(maskedRows >= 2 && leaveRows >= 1, "the audit log page shows the masking changes and the leave change", `masking entries on the page: ${maskedRows}, leave entries: ${leaveRows}`);
    await page.screenshot({ path: path.join(OUT, `admin-audit-log.png`), fullPage: false });
    finish(`admin-${slug}`, { slug, before, after });
  } finally {
    await browser.close();
  }
}

// question <install> <name> <model id> [th|en]: the question card of a run whose page that model read,
// as a person reads it on the built web client, on a screen in the language the run was started in (a run
// keeps its language). It must name the model that read the page and never an in-house one.
async function question(install, name, model, runLang) {
  const { chromium } = playwright();
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 1000 }, locale: "th-TH" });
    await ctx.addCookies([{ name: "portal_access_token", value: L.mintPortalToken(L.TS_USER, 1800), domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax", secure: false }]);
    const page = await ctx.newPage();
    await page.goto(`${WEB}/chat/agents/i/${install}`, { waitUntil: "load" });
    await page.locator('[data-testid="am-question-card"]').last().waitFor({ state: "visible", timeout: 90000 });
    await L.wait(2500);
    const startLang = await page.evaluate(() => document.documentElement.lang);
    if (runLang === "en") { await setLanguage(page, "en"); await L.wait(1500); }
    const got = await page.evaluate(() => ({
      lang: document.documentElement.lang,
      cards: [...document.querySelectorAll('[data-testid="am-question-card"]')].map((el) => el.innerText.replace(/\s+/g, " ").trim()),
    }));
    const text = got.cards.join(" ¦ ");
    const named = `“${model}”`;
    await page.locator('[data-testid="am-question-card"]').last().scrollIntoViewIfNeeded().catch(() => {});
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false });
    if (runLang === "en") {
      check(got.lang === "en" && text.includes(`the model outside the organisation ${named} read`), "English screen, a run started in English: the question names the model outside the organisation that read the page", text.slice(0, 300));
      check(!/in-house|inside the organisation/.test(text), "… and nowhere says an in-house model read it");
    } else {
      check(got.cards.length > 0 && text.includes(`โมเดลนอกองค์กร ${named} อ่านจากภาพได้`), "Thai screen: the question names the model outside the organisation that read the page", text.slice(0, 300));
      check(!text.includes("โมเดลในองค์กร"), "… and nowhere says an in-house model read it");
    }
    // (a sentence the control plane could not read reaches a person as a lone "…")
    check(!/(^|\s)…(\s|$)/.test(text), "… and no sentence was withheld", (text.match(/.{0,40}(^|\s)…(\s|$)/) || [""])[0]);
    await setLanguage(page, startLang || "th");
    check((await page.evaluate(() => document.documentElement.lang)) === (startLang || "th"), "the screen's language is put back", startLang);
    finish(name, { install, model, run_language: runLang || "th", screen: got });
  } finally {
    await browser.close();
  }
}

const [cmd, a, b, c, d] = process.argv.slice(2);
(async () => {
  if (cmd === "card" && a) return card(a, b || "card");
  if (cmd === "question" && a && c) return question(a, b || "question", c, d === "en" ? "en" : "th");
  if (cmd === "admin" && a) return admin(a);
  console.error("usage: SMOKE_TARGET=test node scripts/probes/AM16/walk.js card <install id> <name> | question <install id> <name> <model id> [th|en] | admin <slug>");
  process.exit(2);
})().catch((e) => { console.error(`walk: ${String((e && e.message) || e)}`); process.exit(1); });
