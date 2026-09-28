#!/usr/bin/env node
// AM3 — one continuous browser session for the stranger walkthrough (a proxy
// of the colleague trial, never a replacement for it). A headless Chromium
// signed in as a test-stack person, driven through a local HTTP port so the
// person's tab, its sessionStorage and uploads survive between actions like a
// real session does. Test stack only: the page may only open
// http://localhost:13002 (web-chat dev pointed at test services).
//
//   SMOKE_TARGET=test node scripts/probes/AM3/stranger_driver.js   (listens on 127.0.0.1:13999)
//   curl -s localhost:13999/do -d '{"action":"goto","url":"/chat"}'
//   actions: goto{url} · click{text|role+name|selector, nth} · fill{label|placeholder|selector, value}
//            press{key} · upload{files[], selector} · shot{name} · text · wait{ms} · scroll{y} · select{label|selector, option}
"use strict";
const http = require("http");
const fs = require("fs");
const path = require("path");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") {
  console.error("test stack only (SMOKE_TARGET=test)");
  process.exit(2);
}
const { chromium } = require(require.resolve("playwright", { paths: [path.join(L.ROOT, "apps", "web-chat")] }));
const WEB = "http://localhost:13002";
const OUT = path.join(__dirname, "results", "stranger", "shots");
fs.mkdirSync(OUT, { recursive: true });
const PERSON = { id: process.env.STRANGER_ID || "a3a30000-0000-4000-8000-000000000031",
  email: process.env.STRANGER_EMAIL || "am3-stranger@fix128.test", tenant: L.TS_USER.tenant, role: "member" };
const LOG = path.join(__dirname, "results", "stranger", "actions.jsonl");

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, locale: "th-TH", acceptDownloads: true });
  await ctx.addCookies([{ name: "portal_access_token", value: L.mintPortalToken(PERSON, 6 * 3600), domain: "localhost", path: "/",
    httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  const downloads = path.join(__dirname, "results", "stranger", "downloads");
  fs.mkdirSync(downloads, { recursive: true });
  page.on("download", async (d) => {
    const f = path.join(downloads, d.suggestedFilename());
    await d.saveAs(f).catch(() => {});
    fs.appendFileSync(LOG, JSON.stringify({ at: new Date().toISOString(), download: f }) + "\n");
  });
  let n = 0;
  const loc = (a) => {
    if (a.selector) return page.locator(a.selector).nth(a.nth || 0);
    if (a.role) return page.getByRole(a.role, a.name ? { name: a.name } : {}).nth(a.nth || 0);
    if (a.label) return page.getByLabel(a.label).nth(a.nth || 0);
    if (a.placeholder) return page.getByPlaceholder(a.placeholder).nth(a.nth || 0);
    return page.getByText(a.text, { exact: !!a.exact }).nth(a.nth || 0);
  };
  const act = async (a) => {
    switch (a.action) {
      case "goto": {
        const u = new URL(a.url, WEB);
        if (u.origin !== WEB) throw new Error("only " + WEB);
        await page.goto(u.href, { waitUntil: "domcontentloaded", timeout: 60000 });
        await page.waitForTimeout(2500);
        return {};
      }
      case "click": await loc(a).click({ timeout: 8000 }); await page.waitForTimeout(a.ms || 1500); return {};
      case "fill": await loc(a).fill(String(a.value), { timeout: 8000 }); return {};
      case "select": await loc(a).selectOption(a.option, { timeout: 8000 }); return {};
      case "press": await page.keyboard.press(a.key); await page.waitForTimeout(800); return {};
      case "type": await page.keyboard.type(String(a.value)); return {};
      case "upload": {
        const files = (a.files || []).map((f) => path.resolve(f));
        await page.locator(a.selector || "input[type=file]").nth(a.nth || 0).setInputFiles(files, { timeout: 8000 });
        await page.waitForTimeout(a.ms || 2000);
        return { files };
      }
      case "wait": await page.waitForTimeout(Math.min(a.ms || 1000, 120000)); return {};
      case "scroll": await page.mouse.wheel(0, a.y || 600); await page.waitForTimeout(500); return {};
      case "shot": {
        const f = path.join(OUT, `${String(++n).padStart(3, "0")}_${(a.name || "shot").replace(/[^\w.-]+/g, "_")}.png`);
        await page.screenshot({ path: f, fullPage: !!a.full });
        return { screenshot: f };
      }
      case "text": return { text: (await page.innerText("body")).slice(0, a.max || 12000) };
      default: throw new Error("unknown action " + a.action);
    }
  };
  http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      let a = {};
      let out;
      try {
        a = JSON.parse(body || "{}");
        out = { ok: true, ...(await act(a)), url: page.url() };
      } catch (e) {
        out = { ok: false, error: String(e.message || e).split("\n")[0].slice(0, 400), url: page.url() };
      }
      fs.appendFileSync(LOG, JSON.stringify({ at: new Date().toISOString(), a, ok: out.ok, error: out.error, url: out.url }) + "\n");
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify(out));
    });
  }).listen(13999, "127.0.0.1", () => console.log("stranger driver on 127.0.0.1:13999 as " + PERSON.email));
})();
