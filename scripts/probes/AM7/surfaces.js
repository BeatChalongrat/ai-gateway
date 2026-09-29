#!/usr/bin/env node
// AM7 — the two admin surfaces, the scoped role, the trail and the relay, clicked in the real UI.
//
//   both       before phase 4: the chat app's admin page (/chat/agents/admin) and the admin app's
//              Agent Market page read the same row — every field of the organisation compared, then a
//              change made in each app is read back in the other (screenshots of both)
//   audit      the egress change in the admin app's /audit-log, searched by its setting's name
//   publisher  an agent_publisher: its menu, its page (catalog + problems), and a request from it to
//              every forbidden endpoint (through the admin app's proxy — the responses are recorded)
//   mail       the relay: not set up (the page and the test button say so), then set to the test
//              stack's sink, the test message sent and read back from the sink's API
//   pages      a screenshot of every section of the admin app's page, as an admin
//   gone       after phase 4: the chat app has no admin page, link or button; its API no admin route
//
//   SMOKE_TARGET=test node scripts/probes/AM7/surfaces.js both|audit|publisher|mail|pages|gone
//
// Test stack only: web-chat dev on :13002, admin-ui dev on :13000 (scripts/probes/AM7/test_stack.sh),
// the accounts of seed_admins.py. Nothing here reaches the live stack.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") throw new Error("surfaces.js runs on the test stack only (SMOKE_TARGET=test)");
const pw = () => require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));
const WEB = process.env.AM7_WEB || "http://localhost:13002";
const ADMIN = process.env.AM7_ADMIN || "http://localhost:13000";
const EDGE = "http://localhost:14000/api/v1";
const SINK = "http://127.0.0.1:18025";
const OUT = path.join(__dirname, "results", "surfaces");
fs.mkdirSync(OUT, { recursive: true });
const ACCOUNTS = JSON.parse(fs.readFileSync(path.join(__dirname, "results", "ts_admins.json"), "utf8"));
const account = (role) => {
  const [email, a] = Object.entries(ACCOUNTS).find(([, v]) => v.role === role) || [];
  if (!email) throw new Error(`no ${role} account: run test_stack.sh seed`);
  return { email, ...a };
};
const log = (...a) => console.log(`${new Date().toISOString().slice(11, 19)} ${a.join(" ")}`);
const out = (name, v) => fs.writeFileSync(path.join(OUT, name), typeof v === "string" ? v : JSON.stringify(v, null, 1));
const shot = (page, name) => page.screenshot({ path: path.join(OUT, name), fullPage: true, animations: "disabled", caret: "hide" });
let browser;

async function chatApp() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const token = L.mintPortalToken(L.TS_USER, 3600);
  await ctx.addCookies([{ name: "portal_access_token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax", secure: false }]);
  return ctx.newPage();
}

async function adminApp(role) {
  const who = account(role);
  // one sign-in per role, kept (the backend allows 5 sign-ins per address per 15 minutes)
  const kept = path.join(OUT, `session_${role}.json`);
  if (fs.existsSync(kept)) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, storageState: kept });
    const page = await ctx.newPage();
    const me = await page.request.get("http://localhost:18081/auth/session/me");
    if (me.ok()) {
      log(`admin app: session of ${who.email} (${role}) reused`);
      return { page, who };
    }
    await ctx.close();
  }
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  await page.goto(`${ADMIN}/login`, { waitUntil: "load" });
  // a dev server hydrates late: a click before React owns the form submits it as a plain GET
  await page.waitForFunction(() => {
    const f = document.querySelector("form");
    return !!f && Object.keys(f).some((k) => k.startsWith("__reactProps") || k.startsWith("__reactFiber"));
  }, null, { timeout: 90000 });
  await page.locator('input[autocomplete="username"]').fill(who.email);
  await page.locator('input[type="password"]').fill(who.password);
  const answered = page.waitForResponse((r) => r.url().includes("/auth/session/login"), { timeout: 60000 });
  await page.locator('button[type="submit"]').click();
  const res = await answered;
  if (res.status() !== 200) throw new Error(`admin app sign-in answered ${res.status()} for ${role}`);
  await page.waitForURL((u) => !String(u).includes("/login"), { timeout: 60000 });
  await ctx.storageState({ path: kept });
  log(`admin app: signed in as ${who.email} (${role})`);
  return { page, who };
}

// The org row each app read, taken from the response its page asked for.
function catchOrg(page, suffix) {
  return page.waitForResponse((r) => r.url().includes(suffix) && r.request().method() === "GET" && r.status() === 200, { timeout: 60000 })
    .then((r) => r.json());
}

async function webModels(page) {
  await page.getByTestId("am-admin-tab-models").click();
  await page.getByTestId("am-retention-years").waitFor({ timeout: 30000 });
  return { leave: await page.getByTestId("am-leave-on").isChecked(), years: await page.getByTestId("am-retention-years").inputValue() };
}

async function adminModels(page) {
  await page.getByTestId("am-tab-models").click();
  await page.getByTestId("am-retention-years").waitFor({ timeout: 30000 });
  return {
    leave: await page.getByTestId("am-leave-on").isChecked(),
    years: await page.getByTestId("am-retention-years").inputValue(),
    warn: await page.getByTestId("am-storage-warn").inputValue(),
  };
}

async function both() {
  const web = await chatApp();
  const webOrg = catchOrg(web, "/agent-market/admin/org");
  await web.goto(`${WEB}/chat/agents/admin`, { waitUntil: "domcontentloaded" });
  const w = await webOrg;
  await web.getByTestId("am-admin-access").waitFor({ timeout: 60000 });
  await shot(web, "both_01_chat_app_access.png");
  const { page: adm } = await adminApp("admin");
  const admOrg = catchOrg(adm, "/api/chat-edge/agent-market/org");
  await adm.goto(`${ADMIN}/agent-market?section=access`, { waitUntil: "domcontentloaded" });
  const a = await admOrg;
  await adm.getByTestId("am-access").waitFor({ timeout: 60000 });
  await shot(adm, "both_02_admin_app_access.png");

  // every field of the organisation, as each app's page received it
  const fields = Object.keys(w.org).sort();
  const rows = fields.map((f) => ({ field: f, chat_app: w.org[f], admin_app: a.org[f], same: JSON.stringify(w.org[f]) === JSON.stringify(a.org[f]) }));
  const people = JSON.stringify(w.members) === JSON.stringify(a.members);
  const depts = JSON.stringify(w.departments) === JSON.stringify(a.departments);
  // and what each page shows in its fields
  const shown = {
    chat_app: { access: await web.locator('input[name="am-access"]:checked').evaluate((e) => e.closest("label").textContent.trim()),
      budget: await web.getByTestId("am-admin-user-budget").inputValue() },
    admin_app: { access: await adm.locator('[data-testid^="am-access-"]:checked').getAttribute("data-testid"),
      budget: await adm.getByTestId("am-org-budget").inputValue(), domains: await adm.getByTestId("am-org-domains").inputValue(),
      name: await adm.getByTestId("am-org-name").inputValue(), contact_email: await adm.getByTestId("am-org-contact-email").inputValue() },
  };
  const before = { web: await webModels(web), admin: await adminModels(adm) };
  await shot(web, "both_03_chat_app_models_before.png");
  await shot(adm, "both_04_admin_app_models_before.png");

  // a change in the admin app, read back in the chat app
  const target = { leave: !before.admin.leave, years: String(Number(before.admin.years) === 7 ? 8 : 7) };
  await adm.getByTestId(target.leave ? "am-leave-on" : "am-leave-off").check();
  await adm.getByTestId("am-retention-years").fill(target.years);
  const saved = adm.waitForResponse((r) => r.url().includes("/api/chat-edge/agent-market/org") && r.request().method() === "PUT", { timeout: 60000 });
  await adm.getByTestId("am-models-save").click();
  const put1 = await saved;
  await adm.waitForTimeout(800);
  await shot(adm, "both_05_admin_app_models_saved.png");
  await web.reload({ waitUntil: "domcontentloaded" });
  await web.getByTestId("am-admin-access").waitFor({ timeout: 60000 });
  const webAfter = await webModels(web);
  await shot(web, "both_06_chat_app_models_after_admin_change.png");

  // a change in the chat app, read back in the admin app
  const back = { leave: before.admin.leave, years: before.admin.years };
  await web.getByTestId(back.leave ? "am-leave-on" : "am-leave-off").check();
  await web.getByTestId("am-retention-years").fill(back.years);
  const saved2 = web.waitForResponse((r) => r.url().includes("/agent-market/admin/org") && r.request().method() === "PUT", { timeout: 60000 });
  await web.getByTestId("am-admin-models-save").click();
  const put2 = await saved2;
  await web.waitForTimeout(800);
  await adm.reload({ waitUntil: "domcontentloaded" });
  const admAfter = await adminModels(adm);
  await shot(adm, "both_07_admin_app_models_after_chat_change.png");

  const result = {
    at: new Date().toISOString(), fields: rows, all_fields_same: rows.every((r) => r.same), members_same: people, departments_same: depts, shown,
    before, admin_app_set: { ...target, status: put1.status() }, chat_app_then_read: webAfter,
    chat_app_set: { ...back, status: put2.status() }, admin_app_then_read: admAfter,
    agree_after_admin_change: webAfter.leave === target.leave && webAfter.years === target.years,
    agree_after_chat_change: admAfter.leave === back.leave && admAfter.years === back.years,
  };
  out("both.json", result);
  log(`fields compared: ${rows.length}, all the same: ${result.all_fields_same}; people ${people}, departments ${depts}`);
  log(`admin app set egress=${target.leave} years=${target.years} (${put1.status()}) → chat app reads egress=${webAfter.leave} years=${webAfter.years}`);
  log(`chat app set egress=${back.leave} years=${back.years} (${put2.status()}) → admin app reads egress=${admAfter.leave} years=${admAfter.years}`);
  if (!result.all_fields_same || !result.agree_after_admin_change || !result.agree_after_chat_change) throw new Error("the two surfaces disagree");
}

async function audit() {
  const { page } = await adminApp("admin");
  await page.goto(`${ADMIN}/audit-log`, { waitUntil: "domcontentloaded" });
  const box = page.locator('input[placeholder^="Search actions"], input[placeholder^="ค้นหา"]').first();
  await box.waitFor({ timeout: 60000 });
  await page.waitForTimeout(2000);
  await box.click();
  await box.pressSequentially("documents may leave", { delay: 20 });
  await page.waitForTimeout(1500);
  await shot(page, "audit_01_search.png");
  // the entry itself, from the endpoint the page reads (the gateway's admin_audit_log)
  const items = await page.evaluate(async (base) => {
    const r = await fetch(`${base}/admin/audit-log?q=documents_may_leave&limit=10`, { credentials: "include" });
    return r.ok ? (await r.json()).items : { status: r.status };
  }, "http://localhost:18081");
  out("audit.json", items);
  const rows = Array.isArray(items) ? items : [];
  log(`audit-log entries for documents_may_leave: ${rows.length}`);
  for (const i of rows.slice(0, 4)) {
    const d = typeof i.details === "string" ? JSON.parse(i.details) : i.details;
    log(`  ${i.timestamp} ${i.actor_key_id} ${i.action} ${JSON.stringify(d.from)} → ${JSON.stringify(d.to)} by ${d.by} via ${d.via}`);
  }
  // expand the newest row on the page
  const row = page.getByText(/Documents May Leave|documents_may_leave/).first();
  if (await row.count()) {
    await row.click().catch(() => {});
    await page.waitForTimeout(800);
    await shot(page, "audit_02_expanded.png");
  }
  if (!rows.length) throw new Error("no audit entry found");
}

async function publisher() {
  const { page, who } = await adminApp("agent_publisher");
  await page.goto(`${ADMIN}/agent-market`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("am-tab-catalog").waitFor({ timeout: 60000 });
  const nav = await page.locator("nav a, aside a").evaluateAll((as) => as.map((a) => a.getAttribute("href")).filter(Boolean));
  const tabs = await page.locator('[data-testid^="am-tab-"]').evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
  await shot(page, "publisher_01_page.png");
  await page.getByTestId("am-tab-problems").click();
  await page.waitForTimeout(1000);
  await shot(page, "publisher_02_problems.png");
  const run = "00000000-0000-4000-8000-000000000001";
  const calls = [
    ["GET", "agent-market/org"], ["PUT", "agent-market/org", { documents_may_leave: true }], ["PUT", "agent-market/org", { access: "everyone" }],
    ["PUT", "agent-market/members/someone@fix128.test", { role: "admin" }], ["PUT", `agent-market/members/${who.email}`, { role: "admin" }],
    ["DELETE", "agent-market/members/someone@fix128.test"], ["PUT", "agent-market/departments/บัญชี", { monthly_credits: null }],
    ["DELETE", "agent-market/departments/บัญชี"], ["GET", "agent-market/spend?days=30"], ["GET", "agent-market/runs"],
    ["POST", `agent-market/runs/${run}/open`, { reason: "อยากดูตัวเลขของเพื่อน" }], ["GET", `agent-market/runs/${run}`],
    ["GET", `agent-market/runs/${run}/computed`], ["GET", `agent-market/runs/${run}/artifacts/${run}/download`],
    ["GET", `agent-market/runs/${run}/files/${run}/download`], ["POST", `agent-market/runs/${run}/remove-evidence`, { reason: "ลบทิ้งเพื่อทดสอบสิทธิ์" }],
    ["PUT", "agent-market/catalog/sran.financial-report", { status: "disabled", reason: "ปิดชั่วคราว" }],
    ["GET", "agent-market/mail"], ["PUT", "agent-market/mail", { host: "evil.example", port: 25, tls_mode: "none", username: "", sender: "x@evil.example" }],
    ["POST", "agent-market/mail/test", { to: who.email }],
    // chat-edge's other admin endpoints, and the gateway's
    ["GET", "conversations"], ["GET", "users"], ["GET", "analytics/usage?period=7d"], ["GET", "retention-settings?tenant_id=" + run],
    // what the role is for
    ["GET", "agent-market/me"], ["GET", "agent-market/catalog"], ["GET", "agent-market/problems?status=all"],
  ];
  const results = await page.evaluate(async (list) => {
    const res = [];
    for (const [method, p, body] of list) {
      const r = await fetch(`/api/chat-edge/${p}`, { method, credentials: "include", headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined });
      const text = await r.text();
      let j = null;
      try { j = JSON.parse(text); } catch { /* not JSON */ }
      res.push({ method, path: p, status: r.status, code: j?.error?.code || "", message: (j?.error?.message || text).slice(0, 160) });
    }
    return res;
  }, calls);
  const gw = await page.evaluate(async () => {
    const r = await fetch("http://localhost:18081/admin/requests?limit=1", { credentials: "include" });
    return { method: "GET", path: "gateway /admin/requests", status: r.status };
  }).catch((e) => ({ path: "gateway /admin/requests", error: String(e) }));
  results.push(gw);
  out("publisher.json", { nav, tabs, results });
  log(`menu: ${nav.join(" ")} · tabs: ${tabs.join(" ")}`);
  for (const r of results) log(`  ${r.method} ${r.path} → ${r.status} ${r.code || ""}`);
  const allowed = new Set(["agent-market/me", "agent-market/catalog", "agent-market/problems?status=all"]);
  const bad = results.filter((r) => (allowed.has(r.path) ? r.status !== 200 : r.status < 400));
  if (bad.length) throw new Error("publisher boundary: " + JSON.stringify(bad));
}

async function mail() {
  const { page, who } = await adminApp("admin");
  // not set up yet: the page says so, the button answers a sentence
  await page.request.put(`${ADMIN}/api/chat-edge/agent-market/mail`, { data: { host: "", port: 587, tls_mode: "starttls", username: "", sender: "" } });
  await page.goto(`${ADMIN}/agent-market?section=mail`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("am-mail").waitFor({ timeout: 60000 });
  const notReady = await page.getByTestId("am-mail-not-ready").textContent().catch(() => "");
  await page.getByTestId("am-mail-test").click();
  const unset = await page.getByTestId("am-mail-test-note").textContent({ timeout: 30000 });
  await shot(page, "mail_01_not_set_up.png");
  // set to the test stack's sink, then the test message
  await page.getByTestId("am-mail-host").fill("mail-sink");
  await page.getByTestId("am-mail-port").fill("1025");
  await page.getByTestId("am-mail-tls").selectOption("none");
  await page.getByTestId("am-mail-username").fill("");
  await page.getByTestId("am-mail-sender").fill("agents@fix128.test");
  const saved = page.waitForResponse((r) => r.url().includes("/agent-market/mail") && r.request().method() === "PUT", { timeout: 60000 });
  await page.getByTestId("am-mail-save").click();
  const put = await saved;
  const before = (await (await fetch(`${SINK}/api/v1/messages`)).json()).total || 0;
  await page.getByTestId("am-mail-test-to").fill(who.email);
  await page.getByTestId("am-mail-test").click();
  const sent = await page.getByTestId("am-mail-test-note").textContent({ timeout: 60000 });
  await page.waitForTimeout(1500);
  await shot(page, "mail_02_set_and_tested.png");
  const list = await (await fetch(`${SINK}/api/v1/messages`)).json();
  const m = (list.messages || [])[0];
  let raw = "";
  if (m) raw = await (await fetch(`${SINK}/api/v1/message/${m.ID}/raw`)).text();
  const result = { not_ready: notReady, test_with_nothing_set: unset, put_status: put.status(), test_note: sent, sink_before: before,
    sink_after: list.total, newest: m ? { subject: m.Subject, to: (m.To || []).map((x) => x.Address), from: m.From?.Address, created: m.Created } : null,
    raw_head: raw.split("\r\n\r\n")[0] };
  out("mail.json", result);
  log(`not set up: "${notReady}"`);
  log(`test with nothing set: "${unset}"`);
  log(`relay saved ${put.status()}; test: "${sent}"; sink ${before} → ${list.total}: ${m ? `${m.Subject} → ${(m.To || []).map((x) => x.Address)}` : "none"}`);
  if (!m || list.total <= before) throw new Error("the test message did not arrive at the sink");
}

async function pages() {
  const { page } = await adminApp("admin");
  for (const s of ["access", "departments", "spend", "models", "catalog", "problems", "runs", "mail"]) {
    await page.goto(`${ADMIN}/agent-market?section=${s}`, { waitUntil: "domcontentloaded" });
    await page.getByTestId(`am-panel-${s}`).waitFor({ timeout: 60000 });
    await page.waitForTimeout(1500);
    await shot(page, `page_${s}.png`);
    log(`section ${s}: shot`);
  }
  for (const [p, name] of [["/models", "pointer_models.png"], ["/policies", "pointer_policies.png"]]) {
    await page.goto(`${ADMIN}${p}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(OUT, name), animations: "disabled", caret: "hide" });
  }
}

async function gone() {
  const web = await chatApp();
  await web.goto(`${WEB}/chat/agents`, { waitUntil: "domcontentloaded" });
  await web.waitForTimeout(4000);
  const link = await web.getByTestId("am-admin-link").count();
  await shot(web, "gone_01_market.png");
  await web.goto(`${WEB}/chat/agents/admin`, { waitUntil: "domcontentloaded" });
  await web.waitForTimeout(4000);
  const adminTabs = await web.locator('[data-testid^="am-admin-tab-"]').count();
  await shot(web, "gone_02_old_admin_url.png");
  // the chat app's API, as the same signed-in person (an Agent Market admin): every former admin route
  const session = L.bearerSession(L.mintPortalToken(L.TS_USER, 600), "minted-test");
  const paths = [["GET", "/agent-market/admin/org"], ["PUT", "/agent-market/admin/org"], ["GET", "/agent-market/admin/catalog"],
    ["PUT", "/agent-market/admin/catalog/sran.financial-report"], ["GET", "/agent-market/admin/problems"], ["GET", "/agent-market/admin/runs"],
    ["GET", "/agent-market/admin/spend"], ["GET", "/agent-market/admin/mail"], ["GET", "/agent-market/availability"]];
  const res = [];
  for (const [m, p] of paths) {
    const r = await L.http(`${EDGE}${p}`, { method: m, headers: { ...session.headers, "Content-Type": "application/json" },
      body: m === "GET" ? undefined : JSON.stringify({ documents_may_leave: true, status: "disabled", reason: "ทดสอบว่าไม่มีแล้ว" }) });
    res.push({ method: m, path: p, status: r.status });
  }
  out("gone.json", { market_admin_link: link, old_admin_url_tabs: adminTabs, chat_api: res });
  log(`market page admin link: ${link}; /chat/agents/admin tabs: ${adminTabs}`);
  for (const r of res) log(`  chat app ${r.method} ${r.path} → ${r.status}`);
}

(async () => {
  browser = await pw().chromium.launch();
  const what = process.argv[2];
  try {
    const f = { both, audit, publisher, mail, pages, gone }[what];
    if (!f) throw new Error("usage: surfaces.js both|audit|publisher|mail|pages|gone");
    await f();
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error("FAILED:", e.message);
  process.exit(1);
});
