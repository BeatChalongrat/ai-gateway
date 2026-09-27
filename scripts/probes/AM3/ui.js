#!/usr/bin/env node
// AM3 screens against the real test stack — what each person sees.
//
//   (web-chat dev on :13002 with NEXT_PUBLIC_API_URL=http://localhost:14000/api/v1
//    and AGENT_MARKET_ENABLED=true; am3.js seed/setup/stranger/signoff already run)
//   SMOKE_TARGET=test node scripts/probes/AM3/ui.js
//
// A headless browser with its own profile per person (minted test-user
// tokens; the owner's browser and live session are never touched). Full-page
// screenshots, the page's own text, and console errors go to results/ui/.
"use strict";
const fs = require("fs");
const path = require("path");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") {
  console.error("ui.js runs on the test stack only");
  process.exit(2);
}
const { chromium } = require(require.resolve("playwright", { paths: [path.join(L.ROOT, "apps", "web-chat")] }));
const WEB = process.env.AM3_WEB || "http://localhost:13002";
const OUT = path.join(__dirname, "results", "ui");
fs.mkdirSync(OUT, { recursive: true });
const state = JSON.parse(fs.readFileSync(path.join(__dirname, "results", "e2e", "state.json"), "utf8"));
const TENANT = L.TS_USER.tenant;
const PEOPLE = {
  admin: L.TS_USER,
  member: { id: "a3a30000-0000-4000-8000-000000000021", email: "am3-member@fix128.test", tenant: TENANT, role: "member" },
  manager: { id: "a3a30000-0000-4000-8000-000000000022", email: "am3-manager@fix128.test", tenant: TENANT, role: "member" },
};
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const errors = {};

async function tour(browser, who, pages, viewport = { width: 1360, height: 900 }, tag = "") {
  const ctx = await browser.newContext({ viewport, locale: "th-TH", deviceScaleFactor: viewport.width < 500 ? 2 : 1 });
  await ctx.addCookies([{ name: "portal_access_token", value: L.mintPortalToken(PEOPLE[who], 7200), domain: "localhost", path: "/",
    httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") (errors[`${who}${tag}`] ||= []).push(m.text().slice(0, 300)); });
  for (const [name, url, after] of pages) {
    await page.goto(WEB + url, { waitUntil: "networkidle", timeout: 60000 }).catch((e) => log(`goto ${url}: ${e.message}`));
    await page.waitForTimeout(1500);
    if (after) await after(page).catch((e) => log(`${name}: ${e.message}`));
    const file = `${who}${tag}_${name}`;
    await page.screenshot({ path: path.join(OUT, file + ".png"), fullPage: true });
    fs.writeFileSync(path.join(OUT, file + ".txt"), (await page.innerText("body").catch(() => "")).slice(0, 20000));
    log(`${file} ✓`);
  }
  await ctx.close();
}

async function main() {
  const browser = await chromium.launch();
  const inst = state.member_invoice;
  const run = state.member_run;
  await tour(browser, "member", [
    ["01_chat_rail", "/chat"],
    ["02_rail_search", "/chat", async (p) => {
      await p.keyboard.press("Control+k");
      await p.waitForTimeout(500);
      await p.keyboard.type("ใบกำกับ");
      await p.waitForTimeout(1500);
    }],
    ["03_market", "/chat/agents"],
    ["04_detail_invoice", "/chat/agents/sran.invoice-processing"],
    ["05_detail_expense", "/chat/agents/sran.expense-audit"],
    ["06_detail_financial", "/chat/agents/sran.financial-report"],
    ["07_datasheet_invoice", "/chat/agents/sran.invoice-processing/datasheet"],
    ["08_mine", "/chat/agents/mine"],
    ["09_workspace", `/chat/agents/i/${inst}`],
    ["10_run", `/chat/agents/runs/${run}`],
    ["11_install_expense_wizard", "/chat/agents/install/sran.expense-audit"],
  ]);
  await tour(browser, "member", [
    ["12_detail_invoice_phone", "/chat/agents/sran.invoice-processing"],
    ["13_chat_phone", "/chat"],
  ], { width: 390, height: 844 }, "_phone");
  if (state.share) {
    await tour(browser, "manager", [
      ["01_shared", `/chat/agents/shared/${state.share}`],
      ["02_chat_no_access", "/chat"],
      ["03_market_no_access", "/chat/agents"],
    ]);
  }
  const tabs = ["การเข้าถึง", "แผนกและงบ", "การใช้จ่าย", "แคตตาล็อก", "ปัญหาที่แจ้ง"];
  await tour(browser, "admin", tabs.map((tab, i) => [`0${i + 1}_admin_${i}`, "/chat/agents/admin", async (p) => {
    const b = p.getByRole("tab", { name: tab }).or(p.getByRole("button", { name: tab })).first();
    await b.click({ timeout: 5000 });
    await p.waitForTimeout(1500);
  }]));
  await browser.close();
  fs.writeFileSync(path.join(OUT, "console_errors.json"), JSON.stringify(errors, null, 1));
  log(`console errors: ${Object.entries(errors).map(([k, v]) => `${k} ${v.length}`).join(", ") || "none"}`);
}
main().catch((e) => {
  console.error(e);
  process.exit(1);
});
