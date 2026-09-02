#!/usr/bin/env node
// Capability truth in the web UI, both themes — the picker with an image
// attached (non-vision rows disabled with a reason, no-tools rows say so),
// the send gate on a non-vision model, and the error card a dead model
// produces (attempt lines localised, no raw codes).
//
//   node scripts/probes/ui-shots/capability-shots.js <tag> [baseUrl]
//     tag      before | after — prefix of the output files
//     baseUrl  default http://localhost:3002 (a second bundle can run on :3003)
//
// Output: docs/shots/<tag>-<case>-<theme>.png (SHOTS_DIR overrides the folder).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..", "..");
const OUT = path.join(REPO, "docs", process.env.SHOTS_DIR || "shots");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const API = "http://localhost:4000/api/v1";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function token() {
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c", `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "owner@local", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 3600, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}

// a 1×1 PNG so the composer has a real image attached
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");

async function setTheme(page, theme) {
  await page.evaluate((t) => localStorage.setItem("sran-chat-theme", t), theme);
  await page.reload({ waitUntil: "load" });
  await wait(1500);
}

async function api(tok, p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", ...(init.headers || {}) } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t }; }
}

async function shot(page, name, clip) {
  fs.mkdirSync(OUT, { recursive: true });
  const p = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: p, ...(clip ? { clip } : {}) });
  console.log("→", path.relative(REPO, p));
}

async function rectOf(page, selector, pad = 24) {
  const r = await page.evaluate((s) => { const el = document.querySelector(s); if (!el) return null; const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; }, selector);
  if (!r) return undefined;
  return { x: Math.max(0, r.x - pad), y: Math.max(0, r.y - pad), width: r.width + pad * 2, height: r.height + pad * 2 };
}

(async () => {
  const [tag = "after", base = "http://localhost:3002"] = process.argv.slice(2);
  const tok = token();
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{ name: "portal_access_token", value: tok, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const page = await ctx.newPage();
  const made = [];
  try {
    // 1. the picker with an image attached, on a conversation bound to a non-vision model
    const c1 = (await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ title: "capability shots", model_id: "deepseek/deepseek-chat" }) })).body;
    made.push(c1.id);
    await page.goto(`${base}/chat/${c1.id}`, { waitUntil: "load" });
    for (const theme of ["dark", "light"]) {
      await setTheme(page, theme);
      const input = page.locator('input[type="file"]').first();
      await input.setInputFiles({ name: "probe.png", mimeType: "image/png", buffer: PNG });
      await wait(1500);
      await shot(page, `${tag}-visiongate-${theme}`);
      // open the model picker
      const pill = page.locator('button:has-text("DeepSeek"), button:has-text("deepseek")').first();
      await pill.click();
      await wait(800);
      // narrow to the deepseek rows: one cannot read images, one cannot use tools either
      await page.locator('[role="listbox"] input').first().fill("deepseek");
      await wait(500);
      const panel = await rectOf(page, '[role="listbox"]', 12);
      await shot(page, `${tag}-picker-image-attached-${theme}`, panel);
      await page.keyboard.press("Escape");
      // try to send with the image on the non-vision model — the gate must speak before the send
      await page.locator("textarea").first().fill("รูปนี้คืออะไร");
      await page.keyboard.press("Enter");
      await wait(1200);
      await shot(page, `${tag}-send-gate-${theme}`);
      await page.reload({ waitUntil: "load" });
      await wait(800);
    }
    // 2. the error card: a catalogue entry the provider no longer serves
    const c2 = (await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ title: "capability shots — error", model_id: "anthropic/claude-3.5-haiku" }) })).body;
    made.push(c2.id);
    await page.goto(`${base}/chat/${c2.id}`, { waitUntil: "load" });
    for (const theme of ["dark", "light"]) {
      await setTheme(page, theme);
      await page.locator("textarea").first().fill("สวัสดี ตอบสั้น ๆ");
      await page.keyboard.press("Enter");
      try {
        await page.waitForSelector('[role="alert"]', { timeout: 60000 });
        await wait(600);
        await page.locator("details summary").first().click().catch(() => {});
        await wait(300);
        const clip = await rectOf(page, '[role="alert"]', 40);
        await shot(page, `${tag}-error-card-${theme}`, clip);
      } catch (e) {
        console.log("error card not reached:", e.message.split("\n")[0]);
        await shot(page, `${tag}-error-card-${theme}`);
      }
    }
    await setTheme(page, "dark");
  } finally {
    for (const id of made) await api(tok, `/conversations/${id}`, { method: "DELETE" });
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
