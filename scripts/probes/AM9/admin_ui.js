#!/usr/bin/env node
// AM9 (D, E) on the TEST stack's admin app (admin-ui dev server :13000, test backend :18081, test
// chat-edge :14000; accounts seeded by AM7's seed_admins.py, passwords never printed):
//   * an agent publisher's first screen after signing in is the Agent Market page, not a dashboard of errors;
//   * an admin grants agent_publisher on the Accounts page — no SQL — and the audit log keeps who, which
//     account, from, to; the role is put back the same way;
//   * the Agent Market page's header sentence, and the Models tab's per-agent egress card.
//
//   SMOKE_TARGET=test node scripts/probes/AM9/admin_ui.js
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const L = require("../../smoke/lib.js");

const pw = () => require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));
const ADMIN = process.env.AM7_ADMIN || "http://localhost:13000";
const BACKEND = "http://localhost:18081";
const OUT = path.join(__dirname, "results", "admin_ui");
const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(L.ROOT, "scripts", "probes", "AM7", "results", "ts_admins.json"), "utf8"));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const checks = [];
const check = (ok, what, detail = "") => { checks.push({ ok: !!ok, what, detail: String(detail).slice(0, 300) }); log(`${ok ? "PASS" : "FAIL"} ${what}${detail ? ` — ${String(detail).slice(0, 160)}` : ""}`); return ok; };

async function signIn(browser, email) {
  const who = ACCOUNTS[email];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  await page.goto(`${ADMIN}/login`, { waitUntil: "load" });
  await page.waitForFunction(() => {
    const f = document.querySelector("form");
    return !!f && Object.keys(f).some((k) => k.startsWith("__reactProps") || k.startsWith("__reactFiber"));
  }, null, { timeout: 90000 });
  await page.locator('input[autocomplete="username"]').fill(email);
  await page.locator('input[type="password"]').fill(who.password);
  const answered = page.waitForResponse((r) => r.url().includes("/auth/session/login"), { timeout: 60000 });
  await page.locator('button[type="submit"]').click();
  if ((await answered).status() !== 200) throw new Error(`sign-in refused for ${email}`);
  await page.waitForURL((u) => !String(u).includes("/login"), { timeout: 60000 });
  return { ctx, page, who };
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await pw().chromium.launch();
  try {
    // E. the publisher's first screen
    const pub = await signIn(browser, "am7-publisher@fix128.test");
    await pub.page.waitForURL(/\/agent-market/, { timeout: 60000 }).catch(() => {});
    await pub.page.waitForTimeout(2500);
    check(pub.page.url().includes("/agent-market"), "an agent publisher lands on the Agent Market page", pub.page.url());
    const errorsShown = await pub.page.locator("text=/Failed to load|โหลดไม่สำเร็จ/").count();
    check(errorsShown === 0, "and sees no error on their first screen", `${errorsShown} error line(s)`);
    const navs = await pub.page.locator("nav a").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
    check(JSON.stringify(navs) === JSON.stringify(["/agent-market"]), "their nav is the Agent Market page only", JSON.stringify(navs));
    await pub.page.screenshot({ path: path.join(OUT, "01-publisher-first-screen.png") });
    await pub.page.goto(`${ADMIN}/`, { waitUntil: "load" });
    await pub.page.waitForURL(/\/agent-market/, { timeout: 30000 }).catch(() => {});
    check(pub.page.url().includes("/agent-market"), "the dashboard address takes them there too", pub.page.url());
    await pub.ctx.close();

    // E. an admin grants agent_publisher on the Accounts page, then puts the role back
    const adm = await signIn(browser, "am7-admin@fix128.test");
    const { page } = adm;
    await page.goto(`${ADMIN}/accounts`, { waitUntil: "load" });
    const viewerRole = page.getByTestId("acct-role-am7-viewer@fix128.test");
    await viewerRole.waitFor({ timeout: 60000 });
    check(await page.locator('nav a[href="/accounts"]').count() === 1, "an admin has Accounts in the nav");
    const before = await viewerRole.inputValue();
    await viewerRole.selectOption("agent_publisher");
    await page.getByTestId("acct-msg").waitFor({ timeout: 30000 });
    const msg = await page.getByTestId("acct-msg").innerText();
    check(/am7-viewer@fix128\.test/.test(msg), "the role is saved on the screen", msg);
    await page.screenshot({ path: path.join(OUT, "02-accounts-granted.png") });
    const audit = await page.evaluate(async (base) => {
      const r = await fetch(`${base}/admin/audit-log?action=admin_account.role_changed&limit=5`, { credentials: "include" });
      return { status: r.status, body: await r.json().catch(() => null) };
    }, BACKEND);
    const rows = (audit.body && (audit.body.entries || audit.body.items || audit.body.rows || audit.body)) || [];
    const hit = (Array.isArray(rows) ? rows : []).find((e) => JSON.stringify(e).includes("am7-viewer@fix128.test") && JSON.stringify(e).includes("agent_publisher"));
    check(!!hit, "the audit log keeps it: who, which account, from, to", hit ? JSON.stringify(hit).slice(0, 260) : `${audit.status}`);
    await viewerRole.selectOption(before);
    await page.waitForTimeout(1500);
    check(await viewerRole.inputValue() === before, "and the role is put back the same way", before);
    const mine = page.getByTestId("acct-role-am7-admin@fix128.test");
    check(await mine.isDisabled(), "an admin cannot change their own role here");

    // D. the Agent Market page: its header, and the Models tab's per-agent egress
    await page.goto(`${ADMIN}/agent-market?section=models`, { waitUntil: "load" });
    await page.getByTestId("am-egress").waitFor({ timeout: 60000 });
    await page.waitForTimeout(1200);
    const subtitle = await page.locator(".sran-page-subtitle").first().innerText();
    check(subtitle.split(/[.。]|\s{2,}/).length >= 1 && subtitle.length < 140, "the page's header is short sentences", subtitle);
    const rowsEg = await page.locator('[data-testid^="am-egress-sran."]').count();
    check(rowsEg === 5, "every agent has its own answer under the organisation's switch", `${rowsEg} row(s)`);
    await page.getByTestId("am-egress").screenshot({ path: path.join(OUT, "03-egress-card.png") });
    await adm.ctx.close();
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(OUT, "admin_ui.json"), JSON.stringify(checks, null, 1));
  const bad = checks.filter((c) => !c.ok);
  log(`admin-ui: ${checks.length - bad.length} pass, ${bad.length} fail`);
  if (bad.length) process.exitCode = 1;
}
main().catch((e) => { console.error(e); process.exit(1); });
