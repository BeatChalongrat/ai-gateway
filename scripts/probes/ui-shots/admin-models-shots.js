#!/usr/bin/env node
// The admin models page, both themes — the per-row "Chat" switch that
// decides what the chat picker offers (model_configs.show_in_chat).
//
//   ADMIN_USER=admin ADMIN_PASS=… node scripts/probes/ui-shots/admin-models-shots.js <tag>
//
// Output: docs/shots/<tag>-admin-models-<theme>.png (SHOTS_DIR overrides).
// Logs in through the real form on :3000 (HttpOnly cookie + CSRF).
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..", "..", "..");
const OUT = path.join(REPO, "docs", process.env.SHOTS_DIR || "shots");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const ADMIN = "http://localhost:3000";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const [tag = "after"] = process.argv.slice(2);
  const user = process.env.ADMIN_USER || "admin";
  const pass = process.env.ADMIN_PASS;
  if (!pass) throw new Error("ADMIN_PASS is required");
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  try {
    await page.goto(`${ADMIN}/login`, { waitUntil: "load" });
    await page.locator("input").first().fill(user);
    await page.locator('input[type="password"]').fill(pass);
    await page.keyboard.press("Enter");
    await page.waitForURL((u) => !/\/login/.test(u.toString()), { timeout: 30000 });
    for (const theme of ["dark", "light"]) {
      await page.evaluate((t) => localStorage.setItem("sran-theme", t), theme);
      await page.goto(`${ADMIN}/models`, { waitUntil: "load" });
      await page.waitForSelector("table", { timeout: 60000 });
      await wait(1500);
      fs.mkdirSync(OUT, { recursive: true });
      const p = path.join(OUT, `${tag}-admin-models-${theme}.png`);
      await page.screenshot({ path: p });
      console.log("→", path.relative(REPO, p));
    }
    await page.evaluate(() => localStorage.setItem("sran-theme", "dark"));
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
