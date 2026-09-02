#!/usr/bin/env node
// FIX43 — the account menu open, both themes, on web (Chromium) or desktop (CDP).
//   node scripts/probes/ui-shots/menu-shots.js web|desktop <name>
"use strict";
const path = require("node:path");
const fs = require("node:fs");
const REPO = path.resolve(__dirname, "..", "..", "..");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const OUT = path.join(REPO, "docs", process.env.SHOTS_DIR || "shots");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const [kind, name] = process.argv.slice(2);
(async () => {
  let browser, page;
  if (kind === "desktop") {
    browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
    page = browser.contexts().flatMap((c) => c.pages()).find((p) => /localhost:3002/.test(p.url()));
  } else {
    const { execFileSync } = require("node:child_process");
    const crypto = require("node:crypto");
    const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
    const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
    const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677";
    const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c", `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
    const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
    const now = Math.floor(Date.now() / 1000);
    const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "chalongrat.t@gbtech.co.th", type: "portal", token_use: "access", tenant_id: "169323ae-3aa2-4bc9-bb2a-e4467c716e51", tenant_role: "owner", token_version: tv, iat: now, exp: now + 3600, iss: "sran-portal" })}`;
    const token = `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addCookies([{ name: "portal_access_token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
    page = await ctx.newPage();
  }
  fs.mkdirSync(OUT, { recursive: true });
  try {
    for (const theme of ["dark", "light"]) {
      await page.goto("http://localhost:3002/chat", { waitUntil: "load" });
      await page.evaluate((t) => localStorage.setItem("sran-chat-theme", t), theme);
      await page.reload({ waitUntil: "load" });
      await wait(1800);
      await page.click('button[aria-haspopup="menu"]');
      await wait(500);
      const items = await page.evaluate(() => [...document.querySelectorAll('[role="menuitem"]')].map((e) => e.textContent.trim()));
      const p = path.join(OUT, `${name}-${theme}.png`);
      await page.screenshot({ path: p });
      console.log(theme, JSON.stringify(items));
      console.log("→", path.relative(REPO, p));
    }
    await page.evaluate(() => localStorage.setItem("sran-chat-theme", "dark"));
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
