#!/usr/bin/env node
// AM4 screens against the real test stack — the "after" shots.
//
//   (web-chat dev on :13002 with NEXT_PUBLIC_API_URL=http://localhost:14000/api/v1
//    and AGENT_MARKET_ENABLED=true; am4.js install/rename/rounds/why/models/access already run)
//   SMOKE_TARGET=test node scripts/probes/AM4/ui.js
//
// A headless browser with its own profile per person (minted test-user
// tokens; the owner's browser and live session are never touched). Full-page
// screenshots, the page's own text, console errors, and every Latin word a
// person can read go to results/ui/.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") {
  console.error("ui.js runs on the test stack only");
  process.exit(2);
}
const A = require("../AM3/am3.js");
const { chromium } = require(require.resolve("playwright", { paths: [path.join(L.ROOT, "apps", "web-chat")] }));
const WEB = process.env.AM4_WEB || "http://localhost:13002";
const OUT = path.join(__dirname, "results", "ui");
fs.mkdirSync(OUT, { recursive: true });
const state = JSON.parse(fs.readFileSync(path.join(__dirname, "results", "e2e", "state.json"), "utf8"));
const TENANT = L.TS_USER.tenant;
const PEOPLE = {
  admin: L.TS_USER,
  member: { id: "a3a30000-0000-4000-8000-000000000021", email: "am3-member@fix128.test", tenant: TENANT, role: "member" },
};
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const errors = {};
const latin = {};
// what may stay English: proper nouns, file names/extensions, model ids, emails
const ALLOWED = /Agent Market|SRAN Chat|SRAN|Sran|Chat|Ctrl|Shift|Enter|Esc|DOC|PPT|XLS|DPI|XML|eTax|e-Tax|PDF|Excel|Word|PowerPoint|LibreOffice|SAP|Express|Xero|GL|VAT|ID|[\w-]+\.(xlsx|docx|pptx|pdf|xml|csv|png|jpg)|[\w.-]+\/[\w.:-]+|qwen[\w.-]*|deepseek[\w.-]*|llama[\w.-]*|gpt[\w.-]*|[\w.+-]+@[\w.-]+|agent|THB|OK/g;

async function tour(browser, who, pages, viewport = { width: 1360, height: 900 }, tag = "") {
  const ctx = await browser.newContext({ viewport, locale: "th-TH", deviceScaleFactor: viewport.width < 500 ? 2 : 1 });
  await ctx.addCookies([{ name: "portal_access_token", value: L.mintPortalToken(PEOPLE[who], 7200), domain: "localhost", path: "/",
    httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") (errors[`${who}${tag}`] ||= []).push(m.text().slice(0, 300)); });
  for (const [name, url, after] of pages) {
    await page.goto(WEB + url, { waitUntil: "load", timeout: 90000 }).catch((e) => log(`goto ${url}: ${e.message}`));
    // the workspace keeps its event stream open: "networkidle" never comes
    await page.waitForTimeout(4000);
    if (after) await after(page).catch((e) => log(`${name}: ${e.message}`));
    const file = `${who}${tag}_${name}`;
    await page.screenshot({ path: path.join(OUT, file + ".png"), fullPage: !name.includes("header") });
    const text = await page.innerText("body").catch(() => "");
    fs.writeFileSync(path.join(OUT, file + ".txt"), text.slice(0, 20000));
    const words = text.replace(ALLOWED, "").match(/[A-Za-z]{3,}/g);
    if (words) latin[file] = [...new Set(words)];
    log(`${file} ✓${words ? ` (Latin: ${[...new Set(words)].slice(0, 12).join(" ")})` : ""}`);
  }
  await ctx.close();
}

async function main() {
  // an open chip for the shot: an instruction after inv_renamed's finished round
  const chipInst = state.inv_renamed;
  const th = await A.j("member", `/installs/${chipInst}/thread`);
  if (!th.items.some((i) => i.kind === "intent" && i.payload.status === "open")) {
    await A.send("member", "POST", `/installs/${chipInst}/messages`, { text: "[AM4] ทำทะเบียนซื้อเดือนตุลาคม", client_id: crypto.randomUUID() });
  }
  const browser = await chromium.launch();
  const empty = state["quick_sran.financial-report"];
  await tour(browser, "member", [
    ["01_market", "/chat/agents"],
    ["02_detail_financial", "/chat/agents/sran.financial-report"],
    ["03_workspace_empty", `/chat/agents/i/${empty}`],
    ["04_workspace_rounds", `/chat/agents/i/${state.fin_renamed}`],
    ["05_workspace_header", `/chat/agents/i/${state.fin_renamed}`],
    ["06_workspace_menu", `/chat/agents/i/${state.fin_renamed}`, async (p) => {
      await p.getByTestId("am-ws-more").click({ timeout: 5000 });
      await p.waitForTimeout(700);
    }],
    ["07_workspace_folded_open", `/chat/agents/i/${state.fin_renamed}`, async (p) => {
      await p.getByTestId("am-round-folded").first().click({ timeout: 5000 });
      await p.waitForTimeout(700);
    }],
    ["08_workspace_chip", `/chat/agents/i/${chipInst}`],
    ["09_model_control", `/chat/agents/i/${state.inv_named}`, async (p) => {
      await p.getByTestId("am-model-control").click({ timeout: 5000 });
      await p.waitForTimeout(700);
    }],
    ["10_access_notice", `/chat/agents/i/${state.inv_named}`],
    ["11_numbers", `/chat/agents/runs/${state.fin_named_run}?tab=numbers`],
    ["12_settings_financial", `/chat/agents/i/${state.fin_named}/settings`],
    ["13_settings_invoice", `/chat/agents/i/${state.inv_named}/settings`],
    ["14_datasheet_invoice", "/chat/agents/sran.invoice-processing/datasheet"],
    ["15_second_thread", `/chat/agents/i/${state.fin_renamed}?thread=${state.rounds_thread}`],
    ["16_mine", "/chat/agents/mine"],
  ]);
  await tour(browser, "member", [
    ["17_market_phone", "/chat/agents"],
    ["18_workspace_phone", `/chat/agents/i/${state.fin_renamed}`],
  ], { width: 390, height: 844 }, "_phone");
  const tabs = ["รอบงานทั้งหมด", "โมเดลและการเก็บข้อมูล", "การเข้าถึง"];
  await tour(browser, "admin", tabs.map((tab, i) => [`0${i + 1}_admin_${i}`, "/chat/agents/admin", async (p) => {
    const b = p.getByRole("tab", { name: tab }).or(p.getByRole("button", { name: tab })).first();
    await b.click({ timeout: 5000 });
    await p.waitForTimeout(1500);
  }]));
  await browser.close();
  fs.writeFileSync(path.join(OUT, "console_errors.json"), JSON.stringify(errors, null, 1));
  fs.writeFileSync(path.join(OUT, "latin_words.json"), JSON.stringify(latin, null, 1));
  log(`console errors: ${Object.entries(errors).map(([k, v]) => `${k} ${v.length}`).join(", ") || "none"}`);
  log(`screens with Latin words left: ${Object.keys(latin).length}`);
}
main().catch((e) => {
  console.error(e);
  process.exit(1);
});
