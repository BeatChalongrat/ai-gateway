#!/usr/bin/env node
// AM1b §3 — desktop.turn: which element keeps the Stop selector true?
//
//   SMOKE_TARGET=live node scripts/probes/AM1b/desktop_stop.js [model]
//
// Same safety as the smoke's desktop area (CARD-RULES §4): never closes the
// app if its log shows use in the last 10 minutes; one "[AM1b]" chat with
// memory off, deleted by id; the app is relaunched without the debugging
// port at the end. Records, every 500 ms after Enter: every element the
// smoke's STOP selector matches (tag, label, test id, visible or not, inside
// the composer or not), the composer send button's label, and the assistant
// row's status. Result: results/desktop_stop_<model>.json.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { spawn, spawnSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

const PORT = 9333;
const EXE = path.join(process.env.LOCALAPPDATA || "", "Programs", "sran-desktop", "SRAN Desktop.exe");
const LOG = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "logs", "shell.log");
const PROCS = ["SRAN Desktop.exe", "SranDesktop.ToolsSidecar.exe", "SranDesktop.BrowserHost.exe"];
const COMPOSER = 'textarea[data-shortcut="composer"]';
const STOP = 'button[aria-label*="Stop"], button[aria-label*="หยุด"], [data-testid="stop-button"]';
const MODEL = process.argv[2] || L.FREE_MODEL;
const OUT = path.join(__dirname, "results", `desktop_stop_${MODEL.replace(/[^a-z0-9.-]/gi, "_")}.json`);

const portOpen = (port) => new Promise((resolve) => {
  const s = net.connect({ host: "127.0.0.1", port, timeout: 1500 });
  s.once("connect", () => { s.destroy(); resolve(true); });
  s.once("error", () => resolve(false));
  s.once("timeout", () => { s.destroy(); resolve(false); });
});
async function waitFor(fn, ms, step = 500) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t0 > ms) return null;
    await L.wait(step);
  }
}
const running = () => /SRAN Desktop\.exe/i.test(spawnSync("tasklist", ["/FI", `IMAGENAME eq ${PROCS[0]}`, "/NH"], { encoding: "utf8" }).stdout || "");
const kill = () => { for (const p of PROCS) spawnSync("taskkill", ["/F", "/T", "/IM", p], { encoding: "utf8" }); };
const launch = (args) => spawn(EXE, args, { detached: true, stdio: "ignore", windowsHide: false }).unref();
function logSince(sinceIso) {
  const out = [];
  for (const f of [`${LOG}.2`, `${LOG}.1`, LOG]) {
    try { out.push(...fs.readFileSync(f, "utf8").split(/\r?\n/).filter((l) => l.slice(0, 24) >= sinceIso)); } catch { /* not rotated */ }
  }
  return out;
}
const SESSION_LINE = /BOOT:|SIGN-IN|SIGNED IN|TICKET|VIEW: signed|SESSION (RESTORED|ROTATED|EXPIRED)|RE-?AUTH/;

async function main() {
  const { chromium } = require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));
  const cfg = JSON.parse(fs.readFileSync(path.join(path.dirname(EXE), "resources", "desktop.config.json"), "utf8"));
  const pageUrl = cfg.pageUrl || "https://localhost:3002/chat";
  const edge = cfg.chatEdgeUrl || "https://localhost:4000";
  const wasRunning = running();
  if (wasRunning && !(await portOpen(PORT))) {
    const busy = logSince(new Date(Date.now() - 10 * 60_000).toISOString()).filter((l) => /turn|stream|message|tool|composer|send/i.test(l) && !SESSION_LINE.test(l));
    if (busy.length) { console.log(`the app was used in the last 10 minutes (${busy.length} log lines); not closing it`); return; }
    kill(); await waitFor(async () => !running(), 15000);
  }
  launch([`--remote-debugging-port=${PORT}`]);
  const out = { model: MODEL, at: new Date().toISOString(), samples: [] };
  let browser, page, chatId;
  try {
    if (!(await waitFor(() => portOpen(PORT), 45000))) throw new Error("CDP port did not open");
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`, { timeout: 20000 });
    const ctx = browser.contexts()[0];
    page = await waitFor(async () => ctx.pages().find((p) => p.url().startsWith(pageUrl.replace(/\/chat.*$/, ""))) || null, 40000);
    if (!page) throw new Error("no signed-in page");
    await page.waitForSelector(COMPOSER, { timeout: 60000 });
    out.version = (logSince("0000").filter((l) => /SRAN Desktop \S+ pid=/.test(l)).pop() || "").slice(24, 80);
    const mk = await page.evaluate(async ([e, model, title]) => {
      const h = { "Content-Type": "application/json" };
      const c = await fetch(`${e}/api/v1/conversations`, { method: "POST", credentials: "include", headers: h, body: JSON.stringify({ model_id: model, title }) });
      const cj = await c.json().catch(() => ({}));
      if (!cj.id) return { status: c.status };
      const p = await fetch(`${e}/api/v1/conversations/${cj.id}`, { method: "PATCH", credentials: "include", headers: h, body: JSON.stringify({ memory_enabled: false, title }) });
      return { id: cj.id, memory: (await p.json().catch(() => ({}))).memory_enabled };
    }, [edge, MODEL, `[AM1b] desktop stop probe ${MODEL}`]);
    if (!mk.id || mk.memory !== false) throw new Error(`chat create: ${JSON.stringify(mk)}`);
    chatId = mk.id;
    await page.goto(`${pageUrl.replace(/\/chat.*$/, "")}/chat/${chatId}`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(COMPOSER, { timeout: 60000 });
    await L.wait(1500);
    out.before = await snapshot(page);
    await page.fill(COMPOSER, "[AM1b] ตอบสั้นๆ เป็นตัวเลขอย่างเดียว: 1+1 เท่ากับเท่าไร");
    const t0 = Date.now();
    await page.keyboard.press("Enter");
    let rowDoneAt = null;
    for (let i = 0; i < 180; i++) {
      await L.wait(500);
      const s = await snapshot(page);
      s.t = Date.now() - t0;
      if (i % 4 === 0 || rowDoneAt === null) {
        s.row = L.csql(`select status from chat.messages_masked where conversation_id='${chatId}' and role='assistant' order by created_at desc limit 1`);
        if (rowDoneAt === null && /completed|failed|cancelled/.test(s.row)) rowDoneAt = s.t;
      }
      out.samples.push(s);
      if (rowDoneAt !== null && s.t - rowDoneAt > 20000) break;
    }
    out.row_done_ms = rowDoneAt;
    out.after = out.samples[out.samples.length - 1];
  } finally {
    if (page && chatId) out.deleted = await page.evaluate(async ([e, id]) => (await fetch(`${e}/api/v1/conversations/${id}`, { method: "DELETE", credentials: "include" })).status, [edge, chatId]).catch(() => 0);
    if (browser) await browser.close().catch(() => {});
    kill();
    out.port_closed = !!(await waitFor(async () => !(await portOpen(PORT)) && !running(), 20000));
    if (wasRunning) launch([]);
    fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  }
  const a = out.after || {};
  console.log(JSON.stringify({ model: MODEL, version: out.version, row_done_ms: out.row_done_ms, deleted: out.deleted, port_closed: out.port_closed,
    stop_matches_after: a.matches, send_label_after: a.send }, null, 1));
}

async function snapshot(page) {
  return page.evaluate((sel) => {
    const els = [...document.querySelectorAll(sel)];
    const vis = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el); return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none"; };
    const composer = document.querySelector('textarea[data-shortcut="composer"]');
    const form = composer ? composer.closest("form") || composer.parentElement?.parentElement?.parentElement : null;
    const buttons = form ? [...form.querySelectorAll("button")] : [];
    const send = buttons.map((b) => b.getAttribute("aria-label")).filter(Boolean);
    return {
      matches: els.map((el) => ({ tag: el.tagName.toLowerCase(), label: el.getAttribute("aria-label"), testid: el.getAttribute("data-testid"),
        visible: vis(el), in_composer: !!(form && form.contains(el)), cls: (el.className || "").toString().slice(0, 80),
        html: el.outerHTML.slice(0, 160) })),
      send,
    };
  }, STOP);
}

main().catch((e) => { console.error("FAIL", e.message); process.exit(1); });
