#!/usr/bin/env node
// FIX43.1 — the three spots, both themes, on the real :3002 bundle.
//
//   node scripts/probes/ui-shots/spot-shots.js <tag>      → docs/shots/431-<spot>-<tag>-<theme>.png
//
//   refresh  the "new version" pill: the REAL component + REAL hook; only the
//            /api/version answer is stubbed to a different build id, then a
//            window focus event makes the hook check (that is its trigger).
//   slack    /chat/connectors on the DESKTOP (the route is desktop-only) —
//            needs the app running with --remote-debugging-port=9333.
//   compact  ContextMeter's "summarize and continue": a real conversation is
//            created for the shot; GET .../context-info is stubbed to
//            used_ratio 0.85 so the meter renders (the real trigger is a
//            long conversation, which cannot be produced on demand). The
//            component, popover and button are the real ones. A SUBSTITUTE,
//            and recorded as one.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const REPO = path.resolve(__dirname, "..", "..", "..");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const OUT = path.join(REPO, "docs", process.env.SHOTS_DIR || "shots");
const WEB = "http://localhost:3002", API = "http://localhost:4000/api/v1";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677", TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const tag = process.argv[2] || "shot";

function token() {
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c", `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "chalongrat.t@gbtech.co.th", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 3600, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}
const setTheme = async (page, t) => { await page.evaluate((x) => localStorage.setItem("sran-chat-theme", x), t); await page.reload({ waitUntil: "load" }); await wait(1800); };
const shot = async (page, spot, theme, clip) => { const p = path.join(OUT, `431-${spot}-${tag}-${theme}.png`); await page.screenshot({ path: p, clip }); console.log("→", path.relative(REPO, p)); };
const rectOf = (page, sel, pad = 40) => page.evaluate(([s, p]) => { const r = document.querySelector(s)?.getBoundingClientRect(); return r ? { x: Math.max(0, r.x - p), y: Math.max(0, r.y - p), width: r.width + 2 * p, height: r.height + 2 * p } : null; }, [sel, pad]);

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const tok = token();
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{ name: "portal_access_token", value: tok, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();

  // ── refresh pill ────────────────────────────────────────────────────────
  await page.route("**/api/version", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ build: "fix43-1-other-build" }) }));
  await page.goto(`${WEB}/chat`, { waitUntil: "load" });
  for (const theme of ["dark", "light"]) {
    await setTheme(page, theme);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await wait(800);
    const ok = await page.evaluate(() => !!document.querySelector('[role="status"] button'));
    const btn = await page.evaluate(() => { const b = [...document.querySelectorAll('[role="status"] button')][0]; if (!b) return null; const cs = getComputedStyle(b); return { text: b.textContent, color: cs.color, bg: cs.backgroundColor }; });
    console.log("refresh", theme, ok ? JSON.stringify(btn) : "BANNER NOT SHOWN");
    await shot(page, "refresh", theme, await rectOf(page, '[role="status"]', 60));
  }
  await page.unroute("**/api/version");

  // ── compact button (substitute: context-info stubbed) ───────────────────
  const conv = await (await fetch(`${API}/conversations`, { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" }, body: JSON.stringify({ title: "FIX43.1 shot", model_id: "google/gemini-2.5-flash" }) })).json();
  await page.route(`**/conversations/${conv.id}/context-info`, (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ used_ratio: 0.85, history_tokens: 170000, messages_budget: 200000, threshold_tokens: 140000, messages_total: 40, state_present: false, compacted: false, show_meter: true }) }));
  await page.goto(`${WEB}/chat/${conv.id}`, { waitUntil: "load" });
  for (const theme of ["dark", "light"]) {
    await setTheme(page, theme);
    await page.waitForSelector('[data-testid="context-meter"] button', { timeout: 15000 });
    await page.click('[data-testid="context-meter"] button');
    await wait(400);
    const btn = await page.evaluate(() => { const b = document.querySelector('[data-testid="context-compact-submit"]'); if (!b) return null; const cs = getComputedStyle(b); return { text: b.textContent, color: cs.color, bg: cs.backgroundColor }; });
    console.log("compact", theme, btn ? JSON.stringify(btn) : "BUTTON NOT SHOWN");
    const mr = await rectOf(page, '[data-testid="context-meter"]', 40);
    await shot(page, "compact", theme, mr && { x: Math.max(0, mr.x - 120), y: mr.y, width: mr.width + 240, height: 340 });
  }
  await fetch(`${API}/conversations/${conv.id}`, { method: "DELETE", headers: { Authorization: `Bearer ${tok}` } });
  await setTheme(page, "dark");
  await browser.close();

  // ── slack (desktop) ─────────────────────────────────────────────────────
  try {
    const cdp = await chromium.connectOverCDP("http://127.0.0.1:9333");
    const dp = cdp.contexts().flatMap((c) => c.pages()).find((p) => /localhost:3002/.test(p.url()));
    await dp.goto(`${WEB}/chat/connectors`, { waitUntil: "load" });
    for (const theme of ["dark", "light"]) {
      await setTheme(dp, theme);
      await dp.waitForSelector('[data-testid^="connector-icon"]', { timeout: 15000 });
      await wait(800);
      const info = await dp.evaluate(() => { const o = document.querySelector('[data-testid="connector-icon-owner-slack"]'); const f = document.querySelector('[data-testid="connector-icon-fallback"]'); const g = document.querySelector('[data-testid="connector-icon-github"]'); const img = o?.querySelector("img"); const r = (e) => e && e.getBoundingClientRect(); return { owner: !!o, img: img ? { natural: [img.naturalWidth, img.naturalHeight], rendered: [Math.round(r(img).width), Math.round(r(img).height)] } : null, slackBox: o ? [Math.round(r(o).width), Math.round(r(o).height)] : null, githubBox: g ? [Math.round(r(g).width), Math.round(r(g).height)] : null, fallbackCount: document.querySelectorAll('[data-testid="connector-icon-fallback"]').length }; });
      console.log("slack", theme, JSON.stringify(info));
      await shot(dp, "slack", theme, await rectOf(dp, '[data-testid="connector-icon-owner-slack"], [data-testid="connector-icon-fallback"]', 60));
      const zoom = path.join(OUT, `431-slack-zoom-${tag}-${theme}.png`);
      const r = await dp.evaluate(() => { const e = document.querySelector('[data-testid="connector-icon-owner-slack"], [data-testid="connector-icon-fallback"]'); const b = e.getBoundingClientRect(); return { x: b.x - 4, y: b.y - 4, width: b.width + 8, height: b.height + 8 }; });
      await dp.screenshot({ path: zoom, clip: r });
      console.log("→", path.relative(REPO, zoom));
    }
    await setTheme(dp, "dark");
    await cdp.close();
  } catch (e) {
    console.log("slack: desktop not reachable over CDP —", e.message.split("\n")[0]);
  }
})().catch((e) => { console.error(e); process.exit(1); });
