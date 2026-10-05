#!/usr/bin/env node
// AM10 on the admin app.
//
//   first-admin   the switch from the admin a deployment was seeded with to its own configured one,
//                 with a working admin at every moment: who the active admins are is read before, after
//                 `python -m app.ops.first_admin ensure`, after the new account signs in, and after it
//                 deactivates the old one on the Accounts page — never zero. The old account can no
//                 longer sign in; the new one's creation and the deactivation are in the audit log.
//   language      every page of the sidebar and every section of the Agent Market page in both languages:
//                 what is not in the language the admin chose (results/admin/<target>/language-strings.json)
//   pages         the Agent Market page in both languages (an agent is named in the page's language,
//                 drawn with its own icon and colour, light and dark), the Accounts page, the sign-in
//                 hint, and the language kept across a reload.
//
//   SMOKE_TARGET=test node scripts/probes/AM10/admin.js first-admin     (test stack: admin-ui dev :13000)
//   SMOKE_TARGET=live node scripts/probes/AM10/admin.js first-admin     (live: https://localhost:3000)
//
// The new account's address and password are the deployment's own (INITIAL_ADMIN_EMAIL /
// INITIAL_ADMIN_PASSWORD of .env on live; the test stack's generated compose and the dev password).
// Neither is ever printed. A sign-in is tried once per account (the limit is 5 per address per 15 min).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("../../smoke/lib.js");
const R = require("./reader.js");

const LIVE = L.TARGET === "live";
const pw = () => require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));
const ADMIN = process.env.AM10_ADMIN || (LIVE ? L.T.front.admin : "http://localhost:13000");
const BACKEND_BROWSER = LIVE ? L.T.front.gw : "http://localhost:18081";
const PG = L.T.containers.pg;
const BACKEND = L.T.containers.backend;
const OUT = path.join(__dirname, "results", "admin", L.TARGET);
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const checks = [];
const check = (ok, what, detail = "") => { checks.push({ ok: !!ok, what, detail: String(detail).slice(0, 400) }); log(`${ok ? "PASS" : "FAIL"} ${what}${detail ? ` — ${String(detail).slice(0, 200)}` : ""}`); return !!ok; };
const finish = (name, extra = {}) => {
  fs.writeFileSync(path.join(OUT, `${name}.json`), JSON.stringify({ at: new Date().toISOString(), target: L.TARGET, ...extra, checks }, null, 1));
  const bad = checks.filter((c) => !c.ok);
  log(`admin ${name}: ${checks.length - bad.length} pass, ${bad.length} fail`);
  if (bad.length) process.exitCode = 1;
};

const sql = (q) => execFileSync("docker", ["exec", "-i", PG, "psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-F", "|", "-c", q], { encoding: "utf8" }).trim();
/** The admins that can sign in and manage accounts right now: e-mail and role, never a hash. */
const activeAdmins = () => sql("select email, role from admin_users where is_active and role in ('admin','super_admin') order by email").split(/\r?\n/).filter(Boolean);
// what the backend container was configured with (the value is used, never printed)
const containerEnv = (key) => execFileSync("docker", ["exec", BACKEND, "printenv", key], { encoding: "utf8" }).trim();
const mask = (email) => email.replace(/^(.).*(@.*)$/, "$1***$2");

/** The TEST stack only. The admin sign-in limit (5 per address in 15 minutes) counts every probe of this
 *  machine as one person; its counter in the test stack's own Redis is cleared before a probe signs in.
 *  On live the limit is left as it is and the probes are spaced. */
function clearTestSignInLimit() {
  if (LIVE) return;
  const cli = 'redis-cli --no-auth-warning ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"}';
  execFileSync("docker", ["exec", "sran-test-redis-1", "sh", "-c", `${cli} --scan --pattern "admin_login_ip:*" | xargs -r ${cli} del`], { encoding: "utf8" });
}

async function signIn(browser, email, password, { expectRefused = false } = {}) {
  clearTestSignInLimit();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  await page.goto(`${ADMIN}/login`, { waitUntil: "load" });
  await page.waitForFunction(() => {
    const f = document.querySelector("form");
    return !!f && Object.keys(f).some((k) => k.startsWith("__reactProps") || k.startsWith("__reactFiber"));
  }, null, { timeout: 90000 });
  await page.locator('input[autocomplete="username"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  const answered = page.waitForResponse((r) => r.url().includes("/auth/session/login"), { timeout: 60000 });
  await page.locator('button[type="submit"]').click();
  const status = (await answered).status();
  if (expectRefused) return { ctx, page, status };
  if (status !== 200) throw new Error(`sign-in refused (${status})`);
  await page.waitForURL((u) => !String(u).includes("/login"), { timeout: 60000 });
  return { ctx, page, status };
}

async function firstAdmin() {
  const timeline = [];
  const at = (step) => { const admins = activeAdmins(); timeline.push({ step, at: new Date().toISOString(), active_admins: admins }); log(`${step}: ${admins.length} active admin(s) — ${admins.join(", ")}`); return admins; };
  const email = containerEnv("INITIAL_ADMIN_EMAIL").toLowerCase();
  const password = containerEnv("INITIAL_ADMIN_PASSWORD") || (LIVE ? "" : "admin1234");
  check(/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) && !/@example\.(com|org|net)$/.test(email), "the backend is configured with the deployment's own admin address", mask(email));
  check(password.length >= 8, "and with its password (never printed)", `${password.length} characters`);
  if (!email || !password) return finish("first_admin", { timeline });

  const before = at("before");
  check(before.length >= 1, "a working admin exists before the change", String(before.length));
  const old = before.map((r) => r.split("|")[0]).filter((e) => e !== email);

  const out = execFileSync("docker", ["exec", BACKEND, "python", "-m", "app.ops.first_admin", "ensure"], { encoding: "utf8" });
  const verdict = /: (created|exists)/.exec(out)?.[1] || "?";
  check(verdict === "created" || verdict === "exists", "`first_admin ensure` made the configured admin (or found it)", verdict);
  const both = at("after ensure");
  check(both.some((r) => r.startsWith(`${email}|`)) && old.every((e) => both.some((r) => r.startsWith(`${e}|`))), "the configured admin exists beside the old one — nothing was taken away", `${both.length} active`);
  if (verdict === "created") {
    const row = sql(`select action || '|' || actor_key_id || '|' || details from admin_audit_log where action = 'admin_account.created' order by "timestamp" desc limit 1`);
    check(row.includes("system:first-admin") && row.includes(email) && !row.includes(password), "its creation is in the audit log, by name, without the password", row.replace(email, mask(email)).slice(0, 160));
  }

  const browser = await pw().chromium.launch();
  try {
    const s = await signIn(browser, email, password);
    at("new admin signed in");
    check(true, "the new admin signs in to the admin app", s.page.url().replace(ADMIN, ""));
    await s.page.goto(`${ADMIN}/accounts`, { waitUntil: "load" });
    await s.page.getByTestId("acct-list").waitFor({ timeout: 60000 });
    await s.page.waitForTimeout(1200);
    check(await s.page.getByTestId(`acct-row-${email}`).count() === 1, "and manages accounts: it is on the Accounts page");
    await s.page.screenshot({ path: path.join(OUT, "01-accounts-before-deactivating-the-old-admin.png"), fullPage: true });
    for (const e of old.filter((o) => /@example\.(com|org|net)$/.test(o))) {
      const btn = s.page.getByTestId(`acct-active-${e}`);
      await btn.waitFor({ timeout: 30000 });
      await btn.click();
      await s.page.getByTestId("acct-msg").waitFor({ timeout: 30000 });
      await s.page.waitForTimeout(1200);
      const left = at(`after deactivating ${e}`);
      check(!left.some((r) => r.startsWith(`${e}|`)) && left.some((r) => r.startsWith(`${email}|`)), `${e} is deactivated on the Accounts page; the configured admin remains`, `${left.length} active`);
      const audit = sql(`select actor_key_id || '|' || details from admin_audit_log where action = 'admin_account.deactivated' order by "timestamp" desc limit 1`);
      check(audit.includes(e) && audit.includes("admin-ui"), "the deactivation is in the audit log: who, which account", audit.slice(0, 160));
    }
    await s.page.screenshot({ path: path.join(OUT, "02-accounts-after.png"), fullPage: true });
    // a fresh request with the session the new admin holds still works
    const me = await s.page.evaluate(async (b) => (await fetch(`${b}/auth/session/me`, { credentials: "include" })).status, BACKEND_BROWSER);
    check(me === 200, "the new admin's session is alive after the change", String(me));
    await s.ctx.close();
  } finally {
    await browser.close();
  }
  check(timeline.every((t) => t.active_admins.length >= 1), "at every moment at least one admin could sign in", timeline.map((t) => `${t.step}: ${t.active_admins.length}`).join(" · "));
  const demo = sql("select count(*) from admin_users where is_active and email ~* '@example\\.(com|org|net)$'");
  check(demo === "0", "no active account carries an example.* address", demo);
  finish("first_admin", { timeline: timeline.map((t) => ({ ...t, active_admins: t.active_admins.map((r) => r.replace(/^([^|@])[^|@]*(@[^|]*)/, "$1***$2")) })) });
}

// What an admin's Thai screen keeps in Latin letters on purpose: the product, protocols, units and
// the names of things an operator types or reads off a provider's console.
const KEEP_ADMIN = /^(SRAN|AI|Gateway|GATEWAY|Agent|Market|Chat|API|APIs|DLP|MCP|KB|URL|URLs|ID|IDs|OK|JSON|CSV|PDF|SSO|JWT|IP|TTL|RPM|TPM|GPT|LLM|NER|PII|SMTP|TLS|SSL|HTTP|HTTPS|OpenAI|Anthropic|Google|Azure|OpenRouter|Presidio|Redis|Postgres|Prometheus|Grafana|Whisper|USD|THB|UTC|admin|Admin|token|tokens|Token|Tokens|prompt|Prompt|regex|Regex|webhook|Webhook|ms|sec|min|GB|MB|KB|vs|PO)$/;
// An admin table's own words — headings, tabs, labels, buttons, column heads, the sentences of a page —
// and not the data in its cells (addresses, ids, model names, what people typed).
const ADMIN_WORDS = "h1, h2, h3, h4, h5, label, legend, summary, button, th, [role='tab'], nav a, aside a, p, option, small";
const ADMIN_DATA = "td, code, pre, kbd, [data-testid$='-row'] span[title], [data-testid='am-tile']";

/** F on the admin app: every page of the sidebar and every section of the Agent Market page, in both
 *  languages — what is not in the language the admin chose (results/admin/<target>/language-strings.json). */
async function language() {
  const browser = await pw().chromium.launch();
  try {
    let email, password;
    if (LIVE) {
      email = containerEnv("INITIAL_ADMIN_EMAIL");
      password = containerEnv("INITIAL_ADMIN_PASSWORD");
    } else {
      const accounts = JSON.parse(fs.readFileSync(path.join(L.ROOT, "scripts", "probes", "AM7", "results", "ts_admins.json"), "utf8"));
      email = "am7-admin@fix128.test";
      password = accounts[email].password;
    }
    const { ctx, page } = await signIn(browser, email, password);
    const found = await page.evaluate(() => window.localStorage.getItem("sran-admin-locale"));
    await page.goto(`${ADMIN}/`, { waitUntil: "load" });
    await page.waitForTimeout(2500);
    const hrefs = await page.evaluate(() => [...new Set([...document.querySelectorAll("aside a[href^='/'], nav a[href^='/']")].map((a) => a.getAttribute("href")))]);
    const sections = ["access", "departments", "spend", "models", "catalog", "problems", "runs", "mail"].map((sec) => `/agent-market?section=${sec}`);
    const pagesToRead = [...hrefs.filter((h) => h !== "/agent-market"), ...sections];
    check(pagesToRead.length >= 20, "the admin app's pages are the sidebar's links and the Agent Market page's sections", `${hrefs.length} links + ${sections.length} sections`);
    const report = { found, pages: pagesToRead, th: {}, en: {}, notOpened: [] };
    for (const lang of ["en", "th"]) {
      await page.evaluate((l) => window.localStorage.setItem("sran-admin-locale", l), lang);
      for (const href of pagesToRead) {
        const r = await page.goto(`${ADMIN}${href}`, { waitUntil: "load" }).catch(() => null);
        if (!r || r.status() >= 400) {
          report.notOpened.push(`${lang} · ${href}: ${r ? r.status() : "no response"}`);
          continue;
        }
        await page.waitForTimeout(1800);
        report[lang][href] = await R.leaks(page, lang, { notApps: ADMIN_DATA, only: ADMIN_WORDS, keep: KEEP_ADMIN.source });
      }
      await page.goto(`${ADMIN}/agent-market?section=catalog`, { waitUntil: "load" });
      await page.waitForTimeout(1500);
      await page.screenshot({ path: path.join(OUT, `08-language-${lang}.png`), fullPage: true });
      check(Object.keys(report[lang]).length >= 20, `${lang}: pages read`, String(Object.keys(report[lang]).length));
    }
    report.flat = { th: R.flat(report.th), en: R.flat(report.en) };
    if (found) await page.evaluate((l) => window.localStorage.setItem("sran-admin-locale", l), found);
    else await page.evaluate(() => window.localStorage.removeItem("sran-admin-locale"));
    fs.writeFileSync(path.join(OUT, "language-strings.json"), JSON.stringify(report, null, 1));
    log(`admin language: not in the chosen language — th ${report.flat.th.length}, en ${report.flat.en.length}; pages ${pagesToRead.length}; not opened ${report.notOpened.length}`);
    await ctx.close();
    finish("language", { left: { th: report.flat.th.length, en: report.flat.en.length }, notOpened: report.notOpened });
  } finally {
    await browser.close();
  }
}

async function pages() {
  const browser = await pw().chromium.launch();
  try {
    let email, password;
    if (LIVE) {
      email = containerEnv("INITIAL_ADMIN_EMAIL");
      password = containerEnv("INITIAL_ADMIN_PASSWORD");
    } else {
      const accounts = JSON.parse(fs.readFileSync(path.join(L.ROOT, "scripts", "probes", "AM7", "results", "ts_admins.json"), "utf8"));
      email = "am7-admin@fix128.test";
      password = accounts[email].password;
    }
    // the sign-in page's hint, before signing in
    const anon = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: true });
    const lp = await anon.newPage();
    await lp.goto(`${ADMIN}/login`, { waitUntil: "load" });
    await lp.waitForSelector('input[autocomplete="username"]', { timeout: 90000 });
    const hint = await lp.locator('input[autocomplete="username"]').getAttribute("placeholder");
    check(!!hint && !/example\.com/i.test(hint), "the sign-in page shows no demo address", hint);
    await lp.screenshot({ path: path.join(OUT, "03-sign-in.png") });
    await anon.close();

    const { ctx, page } = await signIn(browser, email, password);
    const setLang = async (to) => {
      await page.evaluate((l) => window.localStorage.setItem("sran-admin-locale", l), to);
      await page.reload({ waitUntil: "load" });
    };
    const names = {};
    for (const lang of ["th", "en"]) {
      await page.goto(`${ADMIN}/agent-market?section=catalog`, { waitUntil: "load" });
      await setLang(lang);
      await page.getByTestId("am-catalog").waitFor({ timeout: 90000 });
      await page.waitForTimeout(1500);
      const rows = await page.locator('[data-testid="am-catalog-row"]').evaluateAll((els) => els.map((el) => ({
        slug: el.getAttribute("data-slug"),
        name: (el.querySelector("span[style*='font-weight']") || el.querySelector("span")).innerText,
        accent: (el.querySelector('[data-testid="am-tile"]') || { getAttribute: () => null }).getAttribute("data-accent"),
        icon: (el.querySelector('[data-testid="am-tile"]') || { getAttribute: () => null }).getAttribute("data-icon"),
        quiet: (el.querySelector('[data-testid="am-tile"]') || { getAttribute: () => null }).getAttribute("data-quiet") === "true",
        status: (el.querySelector('[data-testid="am-catalog-status"]') || { innerText: "" }).innerText,
        text: el.innerText,
      })));
      names[lang] = rows.map((r) => [r.slug, r.name, r.accent, r.quiet ? "quiet" : "in colour"]);
      // an agent that is not published is drawn quietly, with its own icon all the same
      check(rows.length >= 5 && rows.every((r) => r.icon === "own" && r.accent), `${lang}: every agent of the catalogue is drawn with its package's own icon`,
        JSON.stringify(rows.map((r) => `${r.icon}/${r.accent}/${r.quiet ? "quiet" : "colour"}`)));
      const thai = rows.filter((r) => /[ก-๙]/.test(r.name)).length;
      check(lang === "th" ? thai === rows.length : thai === 0, `${lang}: each agent is named in the page's language`, JSON.stringify(rows.map((r) => r.name)));
      await page.screenshot({ path: path.join(OUT, `04-agent-market-${lang}.png`), fullPage: true });
      // the language is the browser's own: kept across a reload
      await page.reload({ waitUntil: "load" });
      await page.getByTestId("am-catalog").waitFor({ timeout: 90000 });
      check((await page.evaluate(() => window.localStorage.getItem("sran-admin-locale"))) === lang, `${lang}: kept across a reload`);
    }
    for (const theme of ["light", "dark"]) {
      await page.evaluate((t) => window.localStorage.setItem("sran-theme", t), theme);
      await page.reload({ waitUntil: "load" });
      await page.getByTestId("am-catalog").waitFor({ timeout: 90000 });
      await page.waitForTimeout(1200);
      const accents = await page.locator('[data-testid="am-catalog-row"] [data-testid="am-tile"]').evaluateAll((els) => els.map((e) => e.getAttribute("data-accent")));
      check(new Set(accents).size === accents.length && accents.every(Boolean), `${theme}: no two agents share a colour`, accents.join(" "));
      await page.getByTestId("am-catalog").screenshot({ path: path.join(OUT, `05-catalogue-${theme}.png`) });
    }
    await page.goto(`${ADMIN}/accounts`, { waitUntil: "load" });
    await page.getByTestId("acct-list").waitFor({ timeout: 60000 });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(OUT, "06-accounts-en.png"), fullPage: true });
    // spend: where cost lives now
    await page.goto(`${ADMIN}/agent-market?section=spend`, { waitUntil: "load" });
    await page.waitForTimeout(3500);
    await page.screenshot({ path: path.join(OUT, "07-spend-is-the-admins.png"), fullPage: true });
    await ctx.close();
    finish("pages", { names });
  } finally {
    await browser.close();
  }
}

const cmd = process.argv[2];
const run = { "first-admin": firstAdmin, pages, language }[cmd];
if (!run) {
  console.log("usage: admin.js first-admin | pages | language");
  process.exit(2);
}
run().catch((e) => { console.error(String((e && e.stack) || e).slice(0, 700)); process.exit(1); });
