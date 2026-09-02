#!/usr/bin/env node
// FIX43 — before/after screenshots in BOTH themes, plus a computed-style probe.
//
//   node scripts/fix43/shots.js desktop <path> <name> [--full]   # the installed app over CDP (hosted pages)
//   node scripts/fix43/shots.js web <path> <name> [--full]       # real Chromium on :3002, owner's session cookie
//   node scripts/fix43/shots.js switches desktop|web <path>      # role="switch" metrics (knob vs track)
//
// Output: docs/fix43/<name>-dark.png and <name>-light.png. The theme is set
// through the app's own localStorage key and a reload, so the shot shows what
// the user would see, not a CSS override. The desktop app must be running
// with --remote-debugging-port=9333 for `desktop`.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..");
const OUT = path.join(REPO, "docs", "fix43");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const WEB = "http://localhost:3002";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function token() {
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c", `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "chalongrat.t@gbtech.co.th", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 3600, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}

async function openDesktop() {
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
  const page = browser.contexts().flatMap((c) => c.pages()).find((p) => /localhost:3002/.test(p.url()));
  if (!page) throw new Error("no SRAN page over CDP — is the app running with --remote-debugging-port=9333?");
  return { browser, page, close: () => browser.close() };
}

async function openWeb() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{ name: "portal_access_token", value: token(), domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  return { browser, page, close: () => browser.close() };
}

async function setTheme(page, theme) {
  await page.evaluate((t) => localStorage.setItem("sran-chat-theme", t), theme);
  await page.reload({ waitUntil: "load" });
  await wait(1800);
}

async function shots(kind, route, name, full) {
  const s = kind === "desktop" ? await openDesktop() : await openWeb();
  fs.mkdirSync(OUT, { recursive: true });
  try {
    await s.page.goto(WEB + route, { waitUntil: "load" });
    for (const theme of ["dark", "light"]) {
      await setTheme(s.page, theme);
      // the page scrolls inside a flex column, not the document
      await s.page.evaluate(() => document.querySelectorAll(".overflow-y-auto").forEach((el) => el.scrollTo(0, 0)));
      await wait(300);
      const p = path.join(OUT, `${name}-${theme}.png`);
      await s.page.screenshot({ path: p, fullPage: !!full });
      console.log("→", path.relative(REPO, p), s.page.url());
    }
    await setTheme(s.page, "dark");
  } finally {
    await s.close();
  }
}

async function switches(kind, route) {
  const s = kind === "desktop" ? await openDesktop() : await openWeb();
  try {
    await s.page.goto(WEB + route, { waitUntil: "load" });
    await wait(1500);
    const rows = await s.page.evaluate(() => {
      const out = [];
      for (const sw of document.querySelectorAll('[role="switch"]')) {
        const track = sw.tagName === "INPUT" ? sw.nextElementSibling : sw;
        const knob = track.querySelector("span");
        if (!knob) continue;
        const cs = getComputedStyle(knob), ts = getComputedStyle(track);
        const tr = track.getBoundingClientRect(), kr = knob.getBoundingClientRect();
        out.push({
          label: (sw.getAttribute("aria-label") || sw.closest("div")?.innerText || "").replace(/\s+/g, " ").slice(0, 40),
          checked: sw.getAttribute("aria-checked") ?? String(sw.checked),
          knob_position: cs.position, knob_left: cs.left, knob_right: cs.right, knob_transform: cs.transform,
          track_textAlign: ts.textAlign, track_display: ts.display,
          track_w: Math.round(tr.width), knob_offset_in_track: Math.round(kr.left - tr.left), knob_right_edge_vs_track: Math.round(kr.right - tr.right),
        });
      }
      return out;
    });
    console.log(JSON.stringify(rows, null, 2));
    return rows;
  } finally {
    await s.close();
  }
}

(async () => {
  const [cmd, a, b, c] = process.argv.slice(2);
  const full = process.argv.includes("--full");
  if (cmd === "desktop" || cmd === "web") await shots(cmd, a, b, full);
  else if (cmd === "switches") await switches(a, b);
  else throw new Error("usage: shots.js desktop|web <route> <name> | switches desktop|web <route>");
})().catch((e) => { console.error(e); process.exit(1); });
