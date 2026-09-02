#!/usr/bin/env node
// FIX47.6 Part D — do the message action buttons actually work, per host?
//
//   node scripts/probes/message-actions/probe.js <tag> [baseUrl|cdp:<port>]
//     tag      e.g. before-web | before-electron | after-web …
//     target   default http://localhost:3002 (headless chromium);
//              cdp:9333 attaches to a desktop host launched with
//              --remote-debugging-port=9333 (Electron or WebView2)
//   MODEL=<id> conversation model (default mock-chat-1 — instant, free)
//
// Measures, from OUTSIDE the app, on a live conversation with one
// completed assistant turn:
//   env        isSecureContext, navigator.clipboard presence, execCommand
//   user-copy / assistant-copy   click → copied-state flip + (where
//              readable) the actual clipboard text
//   user-edit  click → textarea appears → save → content changes
//   thumbs-up / thumbs-down      click → feedback POST status + UI state
//   regenerate click → a new stream starts
//   paste      programmatic paste event into the composer → text lands
//   picker     per-provider row census of the model picker (Part A.5:
//              the host honours show_in_chat)
//
// Output: scripts/probes/message-actions/results/<tag>.json (ignored).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..", "..");
const OUT = path.join(__dirname, "results");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const API = "http://localhost:4000/api/v1";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const MODEL = process.env.MODEL || "mock-chat-1";
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

async function api(tok, p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", ...(init.headers || {}) } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t }; }
}

const STREAMING_SEL = 'button[aria-label*="Stop"], button[aria-label*="หยุด"]';

(async () => {
  const [tag = "before-web", target = "http://localhost:3002"] = process.argv.slice(2);
  const tok = token();
  const isCdp = target.startsWith("cdp:");
  const out = { tag, target, captured_at: new Date().toISOString(), checks: {} };
  let browser, page, ownBrowser = false;
  if (isCdp) {
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${target.slice(4)}`);
    const ctx = browser.contexts()[0];
    page = ctx.pages().find((p) => /\/chat/.test(p.url())) || ctx.pages()[0];
  } else {
    ownBrowser = true;
    browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    // Same grants a real browser gives on click; lets us READ the
    // clipboard back to verify the copy actually landed.
    await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: "http://localhost:3002" });
    await ctx.addCookies([{ name: "portal_access_token", value: tok, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
    page = await ctx.newPage();
  }
  const network = [];
  page.on("response", (r) => {
    if (/feedback|regenerate|messages/.test(r.url())) network.push({ url: r.url().replace(/^https?:\/\/[^/]+/, ""), status: r.status(), method: r.request().method() });
  });
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 200)); });

  const conv = (await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ title: `message-actions ${tag}`, model_id: MODEL }) })).body;
  if (!conv?.id) throw new Error("conversation not created: " + JSON.stringify(conv));
  const base = "http://localhost:3002";
  await page.goto(`${base}/chat/${conv.id}`, { waitUntil: "load" });
  await page.waitForSelector("textarea", { timeout: 60000 });
  await wait(1000);

  // ── env facts (H1) ──────────────────────────────────────────────
  out.checks.env = await page.evaluate(() => {
    let exec = null;
    try {
      const ta = document.createElement("textarea");
      ta.value = "probe-exec";
      document.body.appendChild(ta); ta.select();
      exec = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (e) { exec = "threw: " + e.message; }
    return {
      isSecureContext: window.isSecureContext,
      clipboardDefined: Boolean(navigator.clipboard),
      writeTextDefined: Boolean(navigator.clipboard && navigator.clipboard.writeText),
      execCommandCopyReturned: exec,
      userAgent: navigator.userAgent.slice(0, 120),
    };
  });

  // ── one completed exchange ──────────────────────────────────────
  await page.locator("textarea").first().fill("ตอบสั้น ๆ คำเดียว: เมืองหลวงของไทยคือ?");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelectorAll("[data-msg-index]").length >= 2, null, { timeout: 60000 });
  await page.waitForFunction((s) => !document.querySelector(s), STREAMING_SEL, { timeout: 90000 }).catch(() => {});
  await wait(1500);

  const readClipboard = async () => {
    try { return await page.evaluate(() => navigator.clipboard.readText()); }
    catch (e) { return "unreadable: " + String(e).slice(0, 80); }
  };

  // ── assistant copy ──────────────────────────────────────────────
  await page.evaluate(() => { try { navigator.clipboard.writeText("SENTINEL"); } catch {} });
  const rows = page.locator("[data-msg-index]");
  const asst = rows.last();
  const copyBtn = asst.locator('button[aria-label*="Copy"], button[aria-label*="คัดลอก"]').first();
  const copyVisible = await copyBtn.isVisible().catch(() => false);
  let copiedState = null, clipAfter = null;
  if (copyVisible) {
    await copyBtn.click();
    await wait(400);
    copiedState = await asst.locator('button[aria-label*="Copied"], button[aria-label*="คัดลอกแล้ว"]').count();
    clipAfter = await readClipboard();
  }
  out.checks.assistant_copy = { buttonVisible: copyVisible, copiedStateShown: copiedState > 0, clipboard: clipAfter };

  // ── thumbs up / down ────────────────────────────────────────────
  for (const [name, sel] of [
    ["thumbs_up", 'button[aria-label="Helpful"], button[aria-label="มีประโยชน์"]'],
    ["thumbs_down", 'button[aria-label="Not helpful"], button[aria-label="ไม่มีประโยชน์"]'],
  ]) {
    const b = asst.locator(sel).first();
    const visible = await b.isVisible().catch(() => false);
    let posted = null, pressedClass = null;
    if (visible) {
      network.length = 0;
      await b.click();
      await wait(800);
      posted = network.filter((n) => /feedback/.test(n.url));
      pressedClass = await b.getAttribute("class");
    }
    out.checks[name] = { buttonVisible: visible, feedbackRequests: posted, pressed: pressedClass ? /bg-bg-elevated/.test(pressedClass) : null };
  }

  // ── regenerate ──────────────────────────────────────────────────
  {
    const b = asst.locator('button[aria-label*="Regenerate"], button[aria-label*="สร้างคำตอบใหม่"], button[aria-label*="สร้างใหม่"]').first();
    const visible = await b.isVisible().catch(() => false);
    let streamed = null;
    if (visible) {
      await b.click();
      streamed = await page.waitForSelector(STREAMING_SEL, { timeout: 15000 }).then(() => true).catch(() => false);
      await page.waitForFunction((s) => !document.querySelector(s), STREAMING_SEL, { timeout: 90000 }).catch(() => {});
      await wait(1500);
    }
    out.checks.regenerate = { buttonVisible: visible, streamStarted: streamed };
  }

  // ── user copy + edit ────────────────────────────────────────────
  {
    const user = rows.first();
    await user.hover().catch(() => {});
    await wait(300);
    const uCopy = user.locator('button[aria-label*="Copy"], button[aria-label*="คัดลอก"]').first();
    const uCopyVisible = await uCopy.isVisible().catch(() => false);
    let uCopied = null, uClip = null;
    if (uCopyVisible) {
      await page.evaluate(() => { try { navigator.clipboard.writeText("SENTINEL2"); } catch {} });
      await uCopy.click(); await wait(400);
      uCopied = await user.locator('button[aria-label*="Copied"], button[aria-label*="คัดลอกแล้ว"]').count();
      uClip = await readClipboard();
    }
    out.checks.user_copy = { buttonVisible: uCopyVisible, copiedStateShown: uCopied > 0, clipboard: uClip };

    const uEdit = user.locator('button[aria-label*="Edit"], button[aria-label*="แก้ไข"]').first();
    const uEditVisible = await uEdit.isVisible().catch(() => false);
    let editOpened = null, editSubmitted = null;
    if (uEditVisible) {
      await uEdit.click(); await wait(400);
      const ta = user.locator("textarea");
      editOpened = (await ta.count()) > 0;
      if (editOpened) {
        await ta.fill("ตอบสั้น ๆ คำเดียว: เมืองหลวงของญี่ปุ่นคือ?");
        await user.locator('button:has-text("Save")').first().click();
        editSubmitted = await page.waitForFunction(
          () => Array.from(document.querySelectorAll("[data-msg-index]")).some((r) => /ญี่ปุ่น/.test(r.textContent || "")),
          null, { timeout: 30000 },
        ).then(() => true).catch(() => false);
        await page.waitForFunction((s) => !document.querySelector(s), STREAMING_SEL, { timeout: 90000 }).catch(() => {});
        await wait(1000);
      }
    }
    out.checks.user_edit = { buttonVisible: uEditVisible, editOpened, editSubmitted };
  }

  // ── paste into the composer ─────────────────────────────────────
  {
    const ta = page.locator("textarea").first();
    await ta.click();
    const before = await ta.inputValue();
    const pasted = await page.evaluate(() => {
      const el = document.querySelector("textarea");
      const dt = new DataTransfer();
      dt.setData("text/plain", "PASTE-PROBE-TEXT");
      const ev = new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true });
      el.dispatchEvent(ev);
      // A textarea receives synthetic paste content only through the
      // default action, which synthetic events don't run — so ALSO try
      // execCommand insertText as the browsers' paste path does.
      if (!el.value.includes("PASTE-PROBE-TEXT")) {
        el.focus();
        return { synthetic: false, insertText: document.execCommand("insertText", false, "PASTE-PROBE-TEXT") };
      }
      return { synthetic: true };
    });
    await wait(300);
    const after = await ta.inputValue();
    out.checks.paste = { before, after, landed: after.includes("PASTE-PROBE-TEXT"), detail: pasted };
    await ta.fill("");
  }

  // ── model picker census (Part A.5 — host honours show_in_chat) ──
  {
    await page.locator('button[title*="Select model"], button[title*="เลือกโมเดล"]').first().click().catch(() => {});
    await wait(800);
    // Expand every provider group ("…N more").
    for (let i = 0; i < 10; i++) {
      const more = page.locator('[role="listbox"] button', { hasText: /more|เพิ่มเติม|อีก/ }).first();
      if (!(await more.isVisible().catch(() => false))) break;
      await more.click(); await wait(200);
    }
    out.checks.picker = await page.evaluate(() => {
      const box = document.querySelector('[role="listbox"]');
      if (!box) return { open: false };
      const sections = {};
      // A Section = the div whose header carries the uppercase
      // tracking-wider title span; rows carry data-testid.
      for (const span of box.querySelectorAll("span.tracking-wider")) {
        const sec = span.closest("div.border-t, div") ?.parentElement?.parentElement;
        const title = span.textContent?.trim();
        const rows = sec ? sec.querySelectorAll('[data-testid="model-row-name"]').length : 0;
        if (title) sections[title] = rows;
      }
      return {
        open: true,
        sections,
        totalRows: box.querySelectorAll('[data-testid="model-row-name"]').length,
      };
    });
    await page.keyboard.press("Escape");
  }

  // ── hydrated path: reload, then re-check the assistant action row ──
  // A live-turn message and a server-hydrated one can carry different
  // status fields; the owner's report is about messages he SEES the
  // buttons on, which after a restart are the hydrated kind.
  {
    await page.reload({ waitUntil: "load" });
    await page.waitForSelector("[data-msg-index]", { timeout: 60000 });
    await wait(1500);
    const asst2 = page.locator("[data-msg-index]").last();
    const h = {};
    h.copyVisible = await asst2.locator('button[aria-label*="Copy"], button[aria-label*="คัดลอก"]').first().isVisible().catch(() => false);
    const up = asst2.locator('button[aria-label="Helpful"], button[aria-label="มีประโยชน์"]').first();
    h.thumbsVisible = await up.isVisible().catch(() => false);
    if (h.thumbsVisible) {
      network.length = 0;
      await up.click(); await wait(800);
      h.feedbackRequests = network.filter((n) => /feedback/.test(n.url));
      h.pressed = /bg-bg-elevated/.test((await up.getAttribute("class")) || "");
    }
    h.regenVisible = await asst2.locator('button[aria-label*="Regenerate"], button[aria-label*="สร้างคำตอบใหม่"]').first().isVisible().catch(() => false);
    out.checks.hydrated = h;
  }

  // ── real-key paste (desktop hosts): OS clipboard + real Ctrl+V ────
  // The synthetic-event paste above exercises the app's handler; this
  // exercises the native path the owner actually uses.
  {
    let setOk = true;
    try { execFileSync("powershell", ["-NoProfile", "-Command", "Set-Clipboard -Value 'REAL-PASTE-PROBE'"], { encoding: "utf8" }); }
    catch { setOk = false; }
    const ta = page.locator("textarea").first();
    await ta.click();
    await ta.fill("");
    await page.keyboard.press("Control+V");
    await wait(500);
    const v = await ta.inputValue();
    out.checks.real_paste = { osClipboardSet: setOk, landed: v.includes("REAL-PASTE-PROBE"), value: v.slice(0, 60) };
    await ta.fill("");
  }

  out.network_tail = network.slice(-10);
  out.console_errors = consoleErrors.slice(0, 10);
  fs.mkdirSync(OUT, { recursive: true });
  const f = path.join(OUT, `${tag}.json`);
  fs.writeFileSync(f, JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out.checks, null, 2));
  console.log("wrote", path.relative(REPO, f));
  await api(tok, `/conversations/${conv.id}`, { method: "DELETE" });
  if (ownBrowser) await browser.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
