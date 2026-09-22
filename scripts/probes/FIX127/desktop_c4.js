#!/usr/bin/env node
// FIX127 C4 — desktop auto-resume after a network drop.
//
// The app must be launched with SRAN_DESKTOP_CONFIG pointing at a config
// whose gatewayUrl is this proxy (127.0.0.1:8089 → :8080) and with
// --remote-debugging-port=9333. The proxy tears every socket mid-answer and
// refuses connections for CUT_S seconds; the host ends the turn as
// `disconnected`; the page must then retry by itself once the link is back.
//
//   node scripts/probes/FIX127/desktop_c4.js <tag> [CUT_S=20]
"use strict";
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const REPO = path.resolve(__dirname, "..", "..", "..");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const OUT = path.join(__dirname, "results");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const COMPOSER = 'textarea[data-shortcut="composer"]';
const STOP = 'button[aria-label*="Stop"], button[aria-label*="หยุด"], [data-testid="stop-button"]';
const sql = (q) => execFileSync("docker", ["exec", "ai-gateway-postgres-webchat", "psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-F", "|", "-c", q], { encoding: "utf8" }).trim();

function startCutProxy(listenPort, targetPort) {
  const sockets = new Set();
  let cut = false;
  const server = net.createServer((c) => {
    if (cut) return c.destroy();
    const u = net.connect(targetPort, "127.0.0.1");
    sockets.add(c); sockets.add(u);
    c.pipe(u); u.pipe(c);
    const drop = () => { c.destroy(); u.destroy(); sockets.delete(c); sockets.delete(u); };
    c.on("error", drop); u.on("error", drop); c.on("close", drop); u.on("close", drop);
  });
  return new Promise((resolve) => server.listen(listenPort, "127.0.0.1", () => resolve({
    cut: () => { cut = true; for (const s of sockets) s.destroy(); sockets.clear(); },
    restore: () => { cut = false; },
    close: () => server.close(),
  })));
}

const row = (page) => page.evaluate(() => {
  const rows = [...document.querySelectorAll("main [data-msg-index]")];
  const r = rows[rows.length - 1];
  const q = (s) => r?.querySelector(s);
  return { rows: rows.length, chars: (r?.textContent || "").length, alert: !!q('[role="alert"]'), reconnecting: !!q('[data-testid="reconnecting-notice"]'), errorLabel: q('[data-testid="error-label"]')?.textContent ?? null, streaming: !!document.querySelector('button[aria-label*="Stop"], button[aria-label*="หยุด"]') };
});

async function main() {
  const tag = process.argv[2] || "c4";
  const CUT_S = Number(process.argv[3] || 20);
  fs.mkdirSync(OUT, { recursive: true });
  const proxy = await startCutProxy(8089, 8080);
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
  const ctx = browser.contexts()[0];
  const page = ctx.pages().find((p) => /localhost:3002/.test(p.url())) || ctx.pages()[0];
  await page.bringToFront();
  await page.goto("http://localhost:3002/chat", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(COMPOSER, { timeout: 60000 }); await wait(1500);
  await page.fill(COMPOSER, "[FIX127] เขียนเรียงความภาษาไทยยาวประมาณ 900 คำ เรื่องประวัติการรถไฟไทย แบ่งเป็น 8 ย่อหน้า ละเอียดทุกยุค");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => /\/chat\/[0-9a-f-]{36}/.test(location.pathname), null, { timeout: 30000 });
  const id = (/\/chat\/([0-9a-f-]{36})/.exec(page.url()) || [])[1];
  fs.appendFileSync(path.join(OUT, `${tag}-chats.txt`), id + "\n");
  // let the answer start
  const t0 = Date.now();
  while (Date.now() - t0 < 60000) { const r = await row(page); if (r.rows >= 2 && r.chars > 40) break; await wait(500); }
  const beforeCut = await row(page);
  proxy.cut();
  const tCut = Date.now();
  const timeline = [];
  const snap = async (label) => timeline.push({ t: Math.round((Date.now() - tCut) / 1000), label, ...(await row(page)) });
  for (const s of [3, 6, 10, 15]) { await wait(Math.max(0, tCut + s * 1000 - Date.now())); await snap(`cut+${s}s`); }
  await wait(Math.max(0, tCut + CUT_S * 1000 - Date.now()));
  proxy.restore();
  await snap("restored");
  // the page polls every 15 s (RESUME_WAIT.pollMs) or wakes on `online`/visibility; wait for the retry
  let retried = false;
  const tR = Date.now();
  while (Date.now() - tR < 120000) { const r = await row(page); if (r.streaming) { retried = true; break; } await wait(500); }
  await snap(retried ? "retry started" : "no retry within 120 s");
  const tE = Date.now();
  while (Date.now() - tE < 240000) { const r = await row(page); if (!r.streaming) break; await wait(1000); }
  const final = await row(page);
  await wait(3000);
  const db = sql(`select role, status, terminal_cause, length(content_masked) from chat.messages_masked where conversation_id='${id}' order by created_at`).split("\n");
  const out = { id, cutSeconds: CUT_S, beforeCut, timeline, retried, final, db };
  out.pass = retried && !final.streaming && !final.alert && final.chars > beforeCut.chars && db.filter((r) => r.startsWith("assistant|completed")).length === 1;
  console.log(JSON.stringify(out, null, 1));
  fs.writeFileSync(path.join(OUT, `${tag}-desktop-c4.json`), JSON.stringify(out, null, 2));
  proxy.close();
  await browser.close();
}
main().catch((e) => { console.error(e); process.exit(1); });
