#!/usr/bin/env node
// FIX42 C4 — kill the INSTALLED desktop app in the middle of a turn, start it
// again, and look at what the conversation shows.
//
// The app is the packaged e19 build (%LOCALAPPDATA%\Programs\sran-desktop),
// launched with --remote-debugging-port so Playwright can reach the page the
// app hosts (the same web-chat bundle, in hosted/bridge mode). Nothing in the
// app is mocked; the kill is a real taskkill /F of every SRAN Desktop.exe.
//
//   node scripts/fix42-probe/desktop.js kill      # start a turn, kill mid-stream
//   node scripts/fix42-probe/desktop.js resume    # relaunch, open the conversation
//   node scripts/fix42-probe/desktop.js expire    # age the ledger file, relaunch, expect it gone
//
// Results: scripts/fix42-probe/results/C4-*.json (+ screenshots).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execSync, spawn } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..");
const OUT = path.join(__dirname, "results");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));

const EXE = path.join(process.env.LOCALAPPDATA, "Programs", "sran-desktop", "SRAN Desktop.exe");
const USERDATA = path.join(process.env.APPDATA, "SranDesktop-Electron");
const LEDGER_DIR = path.join(USERDATA, "run-ledger");
const SHELL_LOG = path.join(USERDATA, "logs", "shell.log");
const CDP = "http://127.0.0.1:9333";
const STATE = path.join(OUT, "C4-state.json");

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const argv = process.argv.slice(2);
const CASE = argv[0];

function save(name, obj) {
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, name), JSON.stringify(obj, null, 2));
  console.log("→", path.relative(REPO, path.join(OUT, name)));
}

function ledgerFiles() {
  try {
    return fs.readdirSync(LEDGER_DIR).filter((n) => n.endsWith(".jsonl")).map((n) => {
      const p = path.join(LEDGER_DIR, n);
      const st = fs.statSync(p);
      const lines = fs.readFileSync(p, "utf8").split("\n").filter(Boolean);
      const head = JSON.parse(lines[0] || "{}");
      const events = lines.slice(1).map((l) => { try { return JSON.parse(l); } catch (e) { return null; } }).filter(Boolean);
      const last = events[events.length - 1];
      return {
        name: n, bytes: st.size, mtime: st.mtime.toISOString(),
        conversationId: head.conversationId, runId: head.runId, startedAt: head.startedAt,
        frames: events.filter((e) => typeof e.seq === "number").length,
        events: [...new Set(events.map((e) => e.event).filter(Boolean))],
        ended: events.some((e) => e.type === "end"),
        last_seq: last && last.seq, last_event: last && (last.event || last.type),
      };
    });
  } catch (e) {
    return [];
  }
}

function shellLogTail(n = 40) {
  try { return fs.readFileSync(SHELL_LOG, "utf8").split("\n").filter(Boolean).slice(-n); } catch (e) { return []; }
}

function launch() {
  const child = spawn(EXE, ["--remote-debugging-port=9333"], { detached: true, stdio: "ignore" });
  child.unref();
}

function killAll() {
  try { execSync('taskkill /F /IM "SRAN Desktop.exe" /T', { stdio: "ignore" }); } catch (e) {}
  try { execSync('taskkill /F /IM "SranDesktop.ToolsSidecar.exe" /T', { stdio: "ignore" }); } catch (e) {}
}

async function connect(timeoutMs = 60000) {
  const t0 = Date.now();
  for (;;) {
    try {
      const browser = await chromium.connectOverCDP(CDP);
      const pages = browser.contexts().flatMap((c) => c.pages());
      const page = pages.find((p) => /localhost:3002/.test(p.url()));
      if (page) return { browser, page };
      await browser.close();
    } catch (e) { /* not up yet */ }
    if (Date.now() - t0 > timeoutMs) throw new Error("could not reach the app over CDP");
    await wait(1000);
  }
}

async function bubbles(page) {
  return page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll(".message-enter")) {
      const role = el.classList.contains("justify-end") ? "user" : "assistant";
      const txt = el.innerText.replace(/\s+/g, " ").trim();
      out.push({
        role,
        len: txt.length,
        stopped: /หยุดแล้ว|\bstopped\b/.test(txt),
        error: /AI service error|บริการ AI ขัดข้อง|interrupted|ถูกขัดจังหวะ/i.test(txt),
        spinner: !!el.querySelector(".animate-spin, .animate-pulse, [class*='typing'], [class*='cursor-blink']"),
        text: txt.slice(0, 500),
      });
    }
    return out;
  });
}

async function sendButtonLabel(page) {
  return page.evaluate(() => {
    const b = [...document.querySelectorAll("button[aria-label]")].find((x) => /^(ส่ง|Send|หยุด|Stop generation)$/.test(x.getAttribute("aria-label")));
    return b ? b.getAttribute("aria-label") : null;
  });
}

const TYPE_AND_SEND = (text) => `(() => {
  const ta = document.querySelector('textarea');
  if (!ta) return 'no-textarea';
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, ${JSON.stringify(text)});
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  ta.focus();
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
  return 'sent';
})()`;

// ── kill: start a turn in a NEW conversation, kill the app mid-stream ─────
async function kill() {
  const { browser, page } = await connect();
  await page.bringToFront();
  await page.goto("http://localhost:3002/chat", { waitUntil: "load" });
  await page.waitForSelector("textarea", { timeout: 30000 });
  await wait(1500);
  const logBefore = shellLogTail(1);
  const prompt = "เล่าประวัติศาสตร์ของกาแฟอย่างละเอียดตั้งแต่เอธิโอเปียจนถึงร้านกาแฟสมัยใหม่ แบ่งเป็น 12 ยุค แต่ละยุคมีหัวข้อและอย่างน้อย 3 ย่อหน้า ไม่ต้องถามกลับ เริ่มเขียนได้เลย (FIX42 C4)";
  const sent = await page.evaluate(TYPE_AND_SEND(prompt));
  console.log("type+send:", sent);
  const t0 = Date.now();
  let convId = null, asstLen = 0, label = null;
  for (;;) {
    const url = page.url();
    const m = url.match(/\/chat\/([0-9a-f-]{36})/);
    if (m) convId = m[1];
    const b = await bubbles(page);
    const a = b.find((x) => x.role === "assistant");
    asstLen = a ? a.len : 0;
    label = await sendButtonLabel(page);
    if (convId && asstLen > 300 && /หยุด|Stop/.test(label || "")) break;
    if (Date.now() - t0 > 90000) throw new Error(`no streaming answer after 90 s (conv=${convId}, len=${asstLen}, label=${label})`);
    await wait(250);
  }
  const shownAtKill = await bubbles(page);
  const ledgerAtKill = ledgerFiles();
  await page.screenshot({ path: path.join(OUT, "C4-1-before-kill.png") });
  await browser.close().catch(() => {});
  const tKill = new Date().toISOString();
  killAll();
  await wait(2000);
  const procs = execSync('tasklist /FI "IMAGENAME eq SRAN Desktop.exe"', { encoding: "utf8" });
  const stillRunning = /SRAN Desktop\.exe/.test(procs);
  const ledgerAfterKill = ledgerFiles();
  const state = { conversationId: convId, prompt, killed_at: tKill, assistant_chars_at_kill: asstLen, send_button_at_kill: label, shown_at_kill: shownAtKill, ledger_at_kill: ledgerAtKill, ledger_after_kill: ledgerAfterKill, app_still_running_after_kill: stillRunning, shell_log_tail: shellLogTail(12) };
  fs.writeFileSync(STATE, JSON.stringify(state, null, 2));
  save("C4-1-kill-mid-turn.json", state);
  console.log(`conversation=${convId} chars_at_kill=${asstLen} ledger_files=${ledgerAfterKill.length} ${JSON.stringify(ledgerAfterKill.map((f) => ({ frames: f.frames, ended: f.ended, last: f.last_event })))}`);
}

// ── resume: relaunch, open the same conversation, look ────────────────────
async function resume() {
  const state = JSON.parse(fs.readFileSync(STATE, "utf8"));
  const logLenBefore = shellLogTail(100000).length;
  launch();
  const { browser, page } = await connect(90000);
  await wait(4000);
  const interruptedLines = shellLogTail(100000).slice(logLenBefore).filter((l) => /RUN INTERRUPTED|run-ledger|settled|interrupted/i.test(l));
  await page.goto(`http://localhost:3002/chat/${state.conversationId}`, { waitUntil: "load" });
  await wait(6000);
  const shown = await bubbles(page);
  const label = await sendButtonLabel(page);
  await page.screenshot({ path: path.join(OUT, "C4-2-after-relaunch.png"), fullPage: true });
  // the sidebar: is the conversation listed at all?
  const sidebarHas = await page.evaluate((id) => !!document.querySelector(`a[href*="${id}"]`), state.conversationId);
  const ledgerNow = ledgerFiles();
  // and what the SERVER holds for it (what survives a second restart)
  const out = {
    captured_at: new Date().toISOString(),
    conversationId: state.conversationId,
    assistant_chars_at_kill: state.assistant_chars_at_kill,
    shell_log_interrupted_lines: interruptedLines,
    shown_after_relaunch: shown,
    send_button_after_relaunch: label,
    sidebar_lists_conversation: sidebarHas,
    ledger_files_now: ledgerNow,
    verdict: {
      question_shown: shown.some((b) => b.role === "user" && b.len > 20),
      partial_answer_shown: shown.some((b) => b.role === "assistant" && b.len > 50),
      closed_with_real_status: shown.some((b) => b.role === "assistant" && (b.error || b.stopped)),
      spinner_left: shown.some((b) => b.role === "assistant" && b.spinner),
      send_button_back: /ส่ง|Send/.test(label || ""),
    },
  };
  await browser.close().catch(() => {});
  console.log(JSON.stringify(out.verdict, null, 2));
  console.log("interrupted lines:", interruptedLines.length, interruptedLines.slice(0, 3));
  save("C4-2-after-relaunch.json", out);
}

// ── expire: set every ledger file's mtime 11 minutes back, relaunch ───────
async function expire() {
  const before = ledgerFiles();
  const past = new Date(Date.now() - 11 * 60 * 1000);
  for (const f of before) fs.utimesSync(path.join(LEDGER_DIR, f.name), past, past);
  const aged = ledgerFiles();
  killAll();
  await wait(2000);
  launch();
  const { browser } = await connect(90000);
  await wait(6000);
  const after = ledgerFiles();
  await browser.close().catch(() => {});
  const out = { captured_at: new Date().toISOString(), before, aged_to: past.toISOString(), aged, after, verdict: { removed_on_relaunch: after.length === 0 && aged.length > 0 } };
  console.log(JSON.stringify(out.verdict), `before=${before.length} after=${after.length}`);
  save("C4-3-ledger-expiry.json", out);
}

(async () => {
  if (CASE === "kill") await kill();
  else if (CASE === "resume") await resume();
  else if (CASE === "expire") await expire();
  else throw new Error("case must be kill|resume|expire");
})().catch((e) => { console.error(e); process.exit(1); });
