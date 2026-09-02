#!/usr/bin/env node
// FIX44 — before/after shots of the states the silent no-op classes affect,
// both themes, on the real :3002 bundle; plus a computed-style probe that
// mounts every surveyed class on a scratch element and reads what the
// browser actually resolves (the only honest way to say "this class does
// nothing").
//
//   node scripts/fix44/shots.js <tag>          # e.g. before | after | l1
//   node scripts/fix44/shots.js probe <tag>    # computed values → docs/fix44/probe-<tag>.json
//
// States: landing+toast (Toast bg-bg-surface/95) · account menu (dividers)
// · tasks (section divider) · memory (danger tints) · drag-over sheet
// (bg-bg-surface/85) · focused input (focus:ring-border-strong/40) ·
// conversation delete-confirm (bg-status-danger/85) · stop button while a
// real turn streams (bg-status-danger/85).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const REPO = path.resolve(__dirname, "..", "..");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const OUT = path.join(REPO, "docs", "fix44");
const WEB = process.env.FIX44_WEB || "http://localhost:3002", API = "http://localhost:4000/api/v1";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677", TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const argv = process.argv.slice(2);
const MODE = argv[0] === "probe" ? "probe" : "shots";
const tag = (MODE === "probe" ? argv[1] : argv[0]) || "shot";

function token() {
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c", `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "chalongrat.t@gbtech.co.th", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 3600, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}
const setTheme = async (page, t) => { await page.evaluate((x) => localStorage.setItem("sran-chat-theme", x), t); await page.reload({ waitUntil: "load" }); await wait(1600); };
const shot = async (page, name, theme, clip) => { const p = path.join(OUT, `${name}-${tag}-${theme}.png`); await page.screenshot({ path: p, clip: clip || undefined }); console.log("→", path.relative(REPO, p)); };
const rectOf = (page, sel, pad = 30) => page.evaluate(([s, p]) => { const r = document.querySelector(s)?.getBoundingClientRect(); return r ? { x: Math.max(0, r.x - p), y: Math.max(0, r.y - p), width: r.width + 2 * p, height: r.height + 2 * p } : null; }, [sel, pad]);
const cs = (page, sel) => page.evaluate((s) => { const e = document.querySelector(s); if (!e) return null; const c = getComputedStyle(e); return { bg: c.backgroundColor, color: c.color, border: c.borderTopColor, ring: c.boxShadow.slice(0, 60) }; }, sel);

async function open() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{ name: "portal_access_token", value: token(), domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  return { browser, page };
}

async function probe() {
  const rows = JSON.parse(fs.readFileSync(path.join(process.env.TEMP || "/tmp", "fix44-survey.json"), "utf8"));
  const classes = [...new Set(rows.map((r) => r.cls.replace(/^(?:[\w-]+:)*/, "")))].sort();
  const { browser, page } = await open();
  const out = {};
  for (const theme of ["dark", "light"]) {
    await page.goto(`${WEB}/chat`, { waitUntil: "load" });
    await setTheme(page, theme);
    out[theme] = await page.evaluate((list) => {
      const res = {};
      for (const c of list) {
        const el = document.createElement("div");
        el.className = c + " border";
        document.body.appendChild(el);
        const s = getComputedStyle(el);
        res[c] = { backgroundColor: s.backgroundColor, borderTopColor: s.borderTopColor, color: s.color, boxShadow: s.boxShadow };
        el.remove();
      }
      const ref = document.createElement("div"); ref.className = "border"; document.body.appendChild(ref);
      res.__preflight_border = getComputedStyle(ref).borderTopColor; ref.remove();
      return res;
    }, classes);
  }
  fs.mkdirSync(OUT, { recursive: true });
  const p = path.join(OUT, `probe-${tag}.json`);
  fs.writeFileSync(p, JSON.stringify(out, null, 2));
  console.log("→", path.relative(REPO, p));
  for (const c of classes) console.log(c.padEnd(34), "dark:", out.dark[c].backgroundColor.padEnd(22), out.dark[c].borderTopColor.padEnd(24), "| light:", out.light[c].backgroundColor.padEnd(22), out.light[c].borderTopColor);
  await browser.close();
}

async function shots() {
  fs.mkdirSync(OUT, { recursive: true });
  const tok = token();
  const { browser, page } = await open();
  const log = {};
  for (const theme of ["dark", "light"]) {
    // 1. landing + a real toast (Ctrl+Shift+C with nothing to copy)
    await page.goto(`${WEB}/chat`, { waitUntil: "load" });
    await setTheme(page, theme);
    await page.keyboard.press("Control+Shift+C");
    await wait(500);
    log[`toast-${theme}`] = await cs(page, '[role="status"].toast-appear');
    await shot(page, "toast", theme, await rectOf(page, '[role="status"].toast-appear', 50));
    // 2. account menu
    await page.click('button[aria-haspopup="menu"]');
    await wait(400);
    log[`menu-divider-${theme}`] = await page.evaluate(() => { const d = document.querySelector('[role="menu"] .border-t'); return d ? getComputedStyle(d).borderTopColor : null; });
    await shot(page, "menu", theme, { x: 0, y: 380, width: 340, height: 520 });
    await page.keyboard.press("Escape");
    // 3. tasks page divider (desktop-only section is hidden on web; the divider is above "On this device" — web shows cloud only)
    await page.goto(`${WEB}/chat/projects`, { waitUntil: "load" });
    await wait(1200);
    await shot(page, "projects", theme, { x: 260, y: 0, width: 1180, height: 700 });
    // 4. focused input (Input.tsx focus:ring-border-strong/40) — the search box on /chat/memory
    await page.goto(`${WEB}/chat/memory`, { waitUntil: "load" });
    await wait(1200);
    const inp = await page.$("input[type='search'], input[placeholder]");
    if (inp) { await inp.focus(); await wait(200); log[`input-focus-${theme}`] = await page.evaluate(() => getComputedStyle(document.activeElement).boxShadow); }
    await shot(page, "memory", theme, { x: 260, y: 0, width: 1180, height: 760 });
    // 5. drag-over sheet
    await page.goto(`${WEB}/chat`, { waitUntil: "load" });
    await wait(1000);
    await page.evaluate(() => { const dt = new DataTransfer(); dt.items.add(new File(["x"], "x.txt", { type: "text/plain" })); const ta = document.querySelector("textarea"); const zone = ta.closest("form") || ta.parentElement.parentElement; zone.dispatchEvent(new DragEvent("dragenter", { bubbles: true, dataTransfer: dt })); });
    await wait(300);
    log[`dropzone-${theme}`] = await cs(page, ".border-dashed");
    await shot(page, "dropzone", theme, await rectOf(page, ".border-dashed", 40));
    // 6. delete-confirm on a conversation row
    const conv = await (await fetch(`${API}/conversations`, { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" }, body: JSON.stringify({ title: "FIX44 shot", model_id: "google/gemini-2.5-flash" }) })).json();
    await page.goto(`${WEB}/chat/${conv.id}`, { waitUntil: "load" });
    await wait(1200);
    try {
      // the row is a <div class="group"> holding the title text; its actions button is md:opacity-0 until hover
      const row = page.getByText("FIX44 shot", { exact: true }).first();
      await row.hover({ timeout: 5000 });
      await page.locator('[aria-label="Conversation actions"], [aria-label="การจัดการบทสนทนา"]').first().click({ timeout: 5000, force: true });
      await wait(250);
      await page.getByRole("menuitem", { name: /^(Delete|ลบ)$/ }).first().click({ timeout: 3000 });
      await wait(300);
      const btn = page.getByRole("button", { name: /^(Delete|ลบ)$/ }).first();
      log[`delete-confirm-${theme}`] = await btn.evaluate((b) => { const c = getComputedStyle(b); return { bg: c.backgroundColor, color: c.color, text: b.textContent }; });
      await shot(page, "delete-confirm", theme, { x: 0, y: 150, width: 340, height: 300 });
      await page.keyboard.press("Escape");
    } catch (e) { log[`delete-confirm-${theme}`] = "not reached: " + e.message.split("\n")[0]; }
    // 7. stop button during a real stream
    await page.fill("textarea", "เล่าประวัติศาสตร์ของกาแฟอย่างละเอียด แบ่งเป็น 8 ยุค ยุคละ 3 ย่อหน้า ไม่ต้องถามกลับ");
    await page.keyboard.press("Enter");
    const t0 = Date.now();
    let stop = null;
    while (Date.now() - t0 < 30000) {
      stop = await page.evaluate(() => { const b = [...document.querySelectorAll("button[aria-label]")].find((x) => /^(หยุด|Stop generation)$/.test(x.getAttribute("aria-label"))); if (!b) return null; const c = getComputedStyle(b); return { bg: c.backgroundColor, color: c.color }; });
      if (stop) break;
      await wait(150);
    }
    log[`stop-button-${theme}`] = stop;
    if (stop) await shot(page, "stop-button", theme, await rectOf(page, 'button[aria-label="Stop generation"], button[aria-label="หยุด"]', 60));
    await page.evaluate(() => { const b = [...document.querySelectorAll("button[aria-label]")].find((x) => /^(หยุด|Stop generation)$/.test(x.getAttribute("aria-label"))); b && b.click(); });
    await wait(800);
    await fetch(`${API}/conversations/${conv.id}`, { method: "DELETE", headers: { Authorization: `Bearer ${tok}` } });
  }
  await setTheme(page, "dark");
  await browser.close();
  fs.writeFileSync(path.join(OUT, `states-${tag}.json`), JSON.stringify(log, null, 2));
  console.log(JSON.stringify(log, null, 2));
}

(MODE === "probe" ? probe() : shots()).catch((e) => { console.error(e); process.exit(1); });
