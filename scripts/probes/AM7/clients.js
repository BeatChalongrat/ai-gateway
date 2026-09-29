#!/usr/bin/env node
// AM7 — the user apps after phase 4, clicked on each client (live or test):
//   * the market page has no "จัดการ Agent Market" button, whoever is signed in (the owner is an
//     Agent Market admin);
//   * the old admin page's address shows no admin page (no tabs, no settings);
//   * the draft of a finished round says the organisation has no mail relay yet and who can set it
//     (live: none is set up) — read from the journey's round when there is one;
//   * a person still reports a problem from their own round (web only), and the report reaches the
//     admin app's queue, where the probe closes it again through the admin door (logged as the script).
//
//   SMOKE_TARGET=live OWNER_EMAIL=… node scripts/probes/AM7/clients.js web|desktop
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const L = require("../../smoke/lib.js");
const { door } = require("./admin_door.js");

const pw = () => require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));
const WEB = L.TARGET === "live" ? "https://localhost:3002" : "http://localhost:13002";
const OUT = path.join(__dirname, "results", "clients", L.TARGET);
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log(`${new Date().toISOString().slice(11, 19)} ${a.join(" ")}`);
const shot = (page, name) => page.screenshot({ path: path.join(OUT, name), fullPage: false, animations: "disabled", caret: "hide", timeout: 30000 }).catch((e) => log(`(screenshot ${name}: ${e.message.slice(0, 80)})`));

async function openWeb(email) {
  const id = L.gsql(`select id from portal_users where email='${email.replace(/'/g, "''")}'`).trim();
  const token = L.mintPortalToken({ id, email }, 3600);
  const browser = await pw().chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, locale: "th-TH", ignoreHTTPSErrors: true });
  await ctx.addCookies([{ name: "portal_access_token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax", secure: L.TARGET === "live" }]);
  return { page: await ctx.newPage(), close: () => browser.close() };
}

const portOpen = (port) => new Promise((resolve) => {
  const s = net.connect({ host: "127.0.0.1", port, timeout: 1500 });
  s.once("connect", () => { s.destroy(); resolve(true); });
  s.once("error", () => resolve(false));
  s.once("timeout", () => { s.destroy(); resolve(false); });
});

async function connectDesktop() {
  if (!(await portOpen(9333))) throw new Error("the desktop app has no debugging port open (run the AM6 walkthrough's `open` first)");
  const browser = await pw().chromium.connectOverCDP("http://127.0.0.1:9333", { timeout: 20000 });
  const ctx = browser.contexts()[0];
  const page = ctx.pages().find((p) => p.url().startsWith(WEB)) || ctx.pages()[0];
  return { page, close: () => browser.close() };
}

async function run(client) {
  const email = process.env.OWNER_EMAIL || "";
  const s = client === "desktop" ? await connectDesktop() : await openWeb(email);
  const page = s.page;
  const res = { client, at: new Date().toISOString() };
  try {
    await page.goto(`${WEB}/chat/agents`, { waitUntil: "load" });
    await page.getByRole("heading", { level: 1 }).first().waitFor({ timeout: 60000 });
    await page.waitForTimeout(2500);
    res.market_admin_link = await page.getByTestId("am-admin-link").count();
    res.market_admin_words = await page.getByText("จัดการ Agent Market").count();
    await shot(page, "01_market.png");
    await page.goto(`${WEB}/chat/agents/admin`, { waitUntil: "load" });
    await page.waitForTimeout(4000);
    res.old_admin_url = {
      admin_tabs: await page.locator('[data-testid^="am-admin-tab-"]').count(),
      egress_control: await page.locator('[data-testid="am-leave-on"], [data-testid="am-leave-off"]').count(),
      says: ((await page.locator("main").textContent().catch(() => "")) || "").replace(/\s+/g, " ").trim().slice(0, 160),
    };
    await shot(page, "02_old_admin_url.png");
    log(`${client}: market admin button ${res.market_admin_link} (words ${res.market_admin_words}); /chat/agents/admin → tabs ${res.old_admin_url.admin_tabs}, egress control ${res.old_admin_url.egress_control}: "${res.old_admin_url.says}"`);

    // the journey's round, when the AM6 walkthrough left one: its draft and a problem report
    const st = path.join(L.ROOT, "scripts", "probes", "AM6", "results", "walk", L.TARGET, "state.json");
    const state = fs.existsSync(st) ? JSON.parse(fs.readFileSync(st, "utf8")) : {};
    const install = (state.installs || []).slice(-1)[0];
    const round = (state.runs || [])[0];
    if (install && round) {
      await page.goto(`${WEB}/chat/agents/i/${install}?thread=main&run=${round}`, { waitUntil: "load" });
      await page.getByTestId("am-thread").waitFor({ timeout: 60000 });
      await page.waitForTimeout(2500);
      for (const f of await page.getByTestId("am-round-folded").all()) await f.locator("button").first().click().catch(() => {});
      const card = page.locator('[data-testid="am-email-draft"]').last();
      if (await card.count()) {
        await card.scrollIntoViewIfNeeded().catch(() => {});
        res.draft_cannot_send = ((await card.getByTestId("am-email-cannot-send").textContent().catch(() => "")) || "").trim();
        await shot(page, "03_draft.png");
        log(`${client}: the draft says: "${res.draft_cannot_send}"`);
      }
      if (client === "web") {
        await page.goto(`${WEB}/chat/agents/runs/${round}`, { waitUntil: "load" });
        await page.getByTestId("am-problem-open").waitFor({ timeout: 60000 });
        await page.getByTestId("am-problem-open").click();
        await page.getByTestId("am-problem-text").fill("[AM7] ทดสอบการแจ้งปัญหาจากรอบของตัวเอง หลังย้ายหน้าผู้ดูแลไปแอปผู้ดูแลระบบ ไม่ใช่ปัญหาจริง");
        await page.getByTestId("am-problem-send").click();
        await page.getByTestId("am-problem-done").waitFor({ timeout: 30000 });
        res.problem_sent = ((await page.getByTestId("am-problem-done").textContent()) || "").trim().slice(0, 160);
        await shot(page, "04_problem.png");
        // it reaches the admin app's queue; the probe closes it there (the admin door, logged as the script)
        const q = await door("GET", "/problems?status=open");
        const mine = (q.json?.problems || []).find((p) => (p.text || "").startsWith("[AM7]"));
        res.problem_in_admin_queue = !!mine;
        if (mine) {
          const c = await door("POST", `/problems/${mine.id}/close`, { note: "[AM7] รายงานทดสอบของการเดินตรวจ ปิดโดยสคริปต์" });
          res.problem_closed = c.status;
        }
        log(`${client}: problem sent — "${res.problem_sent}"; in the admin app's queue: ${res.problem_in_admin_queue}; closed ${res.problem_closed}`);
      }
    }
  } finally {
    fs.writeFileSync(path.join(OUT, `${client}.json`), JSON.stringify(res, null, 1));
    await s.close();
  }
  if (res.market_admin_link || res.old_admin_url.admin_tabs || res.old_admin_url.egress_control) throw new Error("an admin surface is still in the " + client);
}

run(process.argv[2] || "web").catch((e) => {
  console.error("FAILED:", e.message);
  process.exit(1);
});
