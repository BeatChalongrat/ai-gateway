#!/usr/bin/env node
// AM5 — the journey on both clients, clicking the real UI:
//
//   the chat (its composer, measured) → Agent Market → กระทบยอดเงินฝากธนาคาร →
//   "ใช้ค่าแนะนำทั้งหมด" → the workspace (its composer, measured against the
//   chat's; the model menu) → a month that ties → the working paper checked
//   against the scenario's own figures → a month that does not tie → the round
//   says "ยังไม่ลงตัว ต่างกัน … บาท", the draft, no way to sign it.
//
//   SMOKE_TARGET=live node scripts/probes/AM5/walkthrough.js open            # desktop: install e60, relaunch with CDP 9333
//   SMOKE_TARGET=live node scripts/probes/AM5/walkthrough.js journey desktop # in the owner's desktop app
//   SMOKE_TARGET=live node scripts/probes/AM5/walkthrough.js journey web     # the same in a browser of its own
//   SMOKE_TARGET=live node scripts/probes/AM5/walkthrough.js invoice desktop # phases A–C with the invoice agent ("invoice web menu": the model menu only)
//   SMOKE_TARGET=live node scripts/probes/AM5/walkthrough.js legacy          # what the owner's own agents' threads say today, read only
//   SMOKE_TARGET=live node scripts/probes/AM5/walkthrough.js cleanup         # remove the walkthrough's installs (by id) and its rounds' evidence
//   SMOKE_TARGET=live node scripts/probes/AM5/walkthrough.js close           # relaunch the app without the debugging port
//
// Safety (CARD-RULES §4): the app is only closed when its log shows no use in
// the last 10 minutes; `close` relaunches it without the debugging port. The
// web client runs in a headless browser with its own profile and a short-lived
// session minted for the same account — the owner's own browser is never
// touched. Every message is tagged [AM5]; no chat is created and none of the
// owner's chats is opened (an agent's thread is not a chat; the chat page is
// opened empty, to measure its composer); the installs made here are removed
// by id.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { spawn, spawnSync, execFileSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

const PORT = 9333;
const VERSION = "0.1.0-e60";
const EXE = path.join(process.env.LOCALAPPDATA || "", "Programs", "sran-desktop", "SRAN Desktop.exe");
const LOGF = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "logs", "shell.log");
const PROCS = ["SRAN Desktop.exe", "SranDesktop.ToolsSidecar.exe", "SranDesktop.BrowserHost.exe"];
const SETUP = path.join(L.ROOT, "apps", "desktop", "release", `SRAN Desktop Setup ${VERSION}.exe`);
const OUT = path.join(__dirname, "results", "walk", L.TARGET);
const STATE = path.join(OUT, "state.json");
const FIX = path.join(L.ROOT, "backend", "agent_market", "packages", "bank_reconciliation", "evals", "fixtures");
const SLUG = "sran.bank-reconciliation";
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
// one account, named the way a person names the files (the engine never reads the names)
const TIES = [
  { src: "kbank_sep.xlsx", name: "Statement กสิกร ก.ย.69.xlsx" },
  { src: "ledger_sep.xlsx", name: "สมุดบัญชีเงินฝาก ก.ย.69.xlsx" },
];
const DOES_NOT_TIE = [
  { src: "kbank_sep_page_missing.xlsx", name: "Statement กสิกร ก.ย.69 (ขาดหน้า).xlsx" },
  { src: "ledger_sep.xlsx", name: "สมุดบัญชีเงินฝาก ก.ย.69.xlsx" },
];
// the scenario's own figures (tools/make_bank_fixtures.py), not the package's
const EXPECT = { adjusted: "1538519.65", difference_when_rows_are_missing: "21,435.00" };
const TECHNICAL = /Traceback|Error:|Exception|object is not|NoneType|undefined|null\b|_failed|not_tied|gate_not|[a-z]+_[a-z]+_[a-z]+|\bstep\b/;
fs.mkdirSync(OUT, { recursive: true });
const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, "utf8")) : { log: [], installs: [], runs: [] };
const save = () => fs.writeFileSync(STATE, JSON.stringify(state, null, 1));
const log = (...a) => { const line = `${new Date().toISOString().slice(11, 19)} ${a.join(" ")}`; console.log(line); state.log.push(line); save(); };

const portOpen = (port) => new Promise((resolve) => {
  const s = net.connect({ host: "127.0.0.1", port, timeout: 1500 });
  s.once("connect", () => { s.destroy(); resolve(true); });
  s.once("error", () => resolve(false));
  s.once("timeout", () => { s.destroy(); resolve(false); });
});
async function waitFor(fn, ms, step = 500) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => null);
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
  for (const f of [`${LOGF}.2`, `${LOGF}.1`, LOGF]) {
    try { out.push(...fs.readFileSync(f, "utf8").split(/\r?\n/).filter((l) => l.slice(0, 24) >= sinceIso)); } catch { /* not rotated */ }
  }
  return out;
}
const SESSION_LINE = /BOOT:|SIGN-IN|SIGNED IN|TICKET|VIEW: signed|SESSION (RESTORED|ROTATED|EXPIRED)|RE-?AUTH|UPDATE CHECK|PAGE STATE|BRIDGE INBOUND: host\.query/;
const desktopCfg = () => JSON.parse(fs.readFileSync(path.join(path.dirname(EXE), "resources", "desktop.config.json"), "utf8"));
const WEB = () => process.env.AM5_WEB || (L.TARGET === "live" ? "https://localhost:3002" : "http://localhost:13002");
const EDGE = () => (L.TARGET === "live" ? "https://localhost:4000" : "http://localhost:14000");
const pw = () => require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));

async function connectDesktop() {
  if (!(await waitFor(() => portOpen(PORT), 45000))) throw new Error("CDP port did not open");
  const browser = await pw().chromium.connectOverCDP(`http://127.0.0.1:${PORT}`, { timeout: 20000 });
  const ctx = browser.contexts()[0];
  const web = desktopCfg().pageUrl.replace(/\/chat.*$/, "");
  const page = await waitFor(async () => ctx.pages().find((p) => p.url().startsWith(web)) || null, 40000);
  if (!page) throw new Error("no signed-in page over CDP");
  return { browser, page, close: async () => browser.close() };
}
// AM5_WINDOW=1536x889@1.25 — the web client in a window like the desktop app's on a display at
// 125 %: 889.6 px tall, which the page is told is 890 (the credits badge over the model menu)
const WINDOW = /^(\d+)x(\d+)@([\d.]+)$/.exec(process.env.AM5_WINDOW || "");
// the web client: a browser of its own, signed in with a short-lived session for the same account
async function openWeb(email) {
  const who = L.TARGET === "live"
    ? { id: L.gsql(`select id from portal_users where email='${email}'`).trim() }
    : L.TS_USER;
  const token = L.mintPortalToken(who, 3600);
  const browser = await pw().chromium.launch(WINDOW ? { args: [`--force-device-scale-factor=${WINDOW[3]}`, `--window-size=${WINDOW[1]},${WINDOW[2]}`] } : {});
  const ctx = await browser.newContext({ viewport: WINDOW ? null : { width: 1360, height: 900 }, locale: "th-TH", ignoreHTTPSErrors: true, acceptDownloads: true });
  await ctx.addCookies([{ name: "portal_access_token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax", secure: L.TARGET === "live" }]);
  const page = await ctx.newPage();
  return { browser, page, close: async () => browser.close() };
}
async function api(page, p, init = {}) {
  return page.evaluate(async ([url, init]) => {
    const r = await fetch(url, { credentials: "include", ...init, headers: { "Content-Type": "application/json", ...(init.headers || {}) } });
    let body = null;
    try { body = await r.json(); } catch { /* none */ }
    return { status: r.status, body };
  }, [`${EDGE()}/api/v1/agent-market${p}`, init]);
}
async function fetchFile(page, url) {
  const b64 = await page.evaluate(async (url) => {
    const r = await fetch(url, { credentials: "include" });
    const buf = new Uint8Array(await r.arrayBuffer());
    let s = "";
    for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return btoa(s);
  }, url);
  return Buffer.from(b64, "base64");
}

/** A screenshot with the page's animations and caret held still — in the desktop app a plain one
 *  timed out (20 s) on a page with a question card while this one took a second. Tried twice; a
 *  screenshot that cannot be taken is said, and the journey goes on. */
async function capture(page, file, opts, say) {
  for (let i = 0; i < 2; i++) {
    try {
      await page.screenshot({ path: file, timeout: 20000, animations: "disabled", caret: "hide", ...opts });
      return true;
    } catch (e) {
      if (i === 1) say(`no screenshot ${path.basename(file)} (${String(e.message).slice(0, 40)})`);
    }
  }
  return false;
}

/** What the composer on this page measures: the same numbers on both pages mean the same box. */
async function measureComposer(page) {
  await page.evaluate(() => { const a = document.activeElement; if (a && a.blur) a.blur(); });
  await L.wait(500);
  return page.evaluate(() => {
    const root = document.querySelector('[data-testid="composer-root"]');
    if (!root) return null;
    const ta = root.querySelector("textarea");
    const pill = ta.parentElement.parentElement;
    const cs = (el, keys) => Object.fromEntries(keys.map((k) => [k, getComputedStyle(el)[k]]));
    const box = (el) => { const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; };
    const attach = root.querySelector('[data-testid="attach-button"]');
    return {
      root: { class: root.className, ...cs(root, ["paddingLeft", "paddingTop", "paddingBottom", "backgroundColor"]) },
      column: { ...box(root.firstElementChild), x: Math.round(root.firstElementChild.getBoundingClientRect().left) },
      pill: { class: pill.className, ...box(pill), ...cs(pill, ["borderRadius", "borderTopColor", "backgroundColor", "boxShadow"]) },
      textarea: { class: ta.className, ...cs(ta, ["fontSize", "lineHeight", "fontFamily", "color"]) },
      attach: attach ? { class: attach.className, ...box(attach) } : null,
    };
  });
}

// ── the working paper checked against the scenario's own figures ──
function readWorkbook(file) {
  const py = [
    "import sys,json",
    "from openpyxl import load_workbook",
    "wb=load_workbook(sys.argv[1],data_only=True)",
    "print(json.dumps({ws.title:[[None if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets},ensure_ascii=False))",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON || "python", ["-c", py, file], { encoding: "utf8", maxBuffer: 64 << 20, env: { ...process.env, PYTHONIOENCODING: "utf-8" } }));
}
function checkPaper(file, tied) {
  const sheets = readWorkbook(file);
  const face = Object.fromEntries((sheets["งบกระทบยอด"] || []).filter((r) => r && r[0]).map((r) => [r[0], r[1]]));
  const num = (v) => (v == null ? null : Number(v).toFixed(2));
  const out = {
    sheets: Object.keys(sheets).length,
    adjusted_bank: num(face["ยอดหลังปรับปรุงของธนาคาร"]),
    adjusted_book: num(face["ยอดหลังปรับปรุงของบัญชี"]),
    difference: num(face["ผลต่าง"]),
    verdict: face["ผลการกระทบยอด"],
    draft_mark: ((sheets["งบกระทบยอด"] || [])[2] || [])[0] || null,
    preparer: face["ผู้จัดทำ"],
    reviewer: face["ผู้สอบทาน"],
    pairs: (sheets["รายการที่จับคู่"] || []).filter((r) => r && /^\d+$/.test(String(r[0]))).length,
    items: (sheets["รายการกระทบยอด"] || []).filter((r) => r && /^\d+$/.test(String(r[0]))).length,
  };
  out.ok = tied
    ? out.adjusted_bank === EXPECT.adjusted && out.adjusted_book === EXPECT.adjusted && out.difference === "0.00" && out.verdict === "ลงตัว" && !out.draft_mark
    : out.adjusted_bank !== out.adjusted_book && String(out.verdict).startsWith("ยังไม่ลงตัว") && String(out.draft_mark || "").startsWith("ร่าง");
  return out;
}

// ── the journey ──────────────────────────────────────────────────────────
async function journey(client) {
  const email = process.env.AM5_EMAIL || (L.TARGET === "live" ? "" : L.TS_USER.email);
  const s = client === "desktop" ? await connectDesktop() : await openWeb(email || process.env.OWNER_EMAIL);
  const { page } = s;
  const tag = client;
  const dir = path.join(OUT, tag);
  fs.mkdirSync(dir, { recursive: true });
  let clicks = 0;
  const marks = [];
  const checks = [];
  const t0 = Date.now();
  const mark = (what) => { const sec = ((Date.now() - t0) / 1000).toFixed(1); marks.push({ what, s: Number(sec), clicks }); log(`${tag} +${sec}s clicks=${clicks} ${what}`); };
  const check = (ok, what, detail = "") => { checks.push({ ok: !!ok, what, detail: String(detail).slice(0, 300) }); log(`${tag} ${ok ? "PASS" : "FAIL"} ${what}${detail ? ` — ${String(detail).slice(0, 160)}` : ""}`); };
  const shot = (name, opts = {}) => capture(page, path.join(dir, `${name}.png`), opts, (m) => log(`${tag}: ${m}`));
  const click = async (loc, what) => { await loc.first().click({ timeout: 30000 }); clicks += 1; if (what) log(`${tag} click ${clicks}: ${what}`); };
  const words = async (loc) => (await loc.innerText().catch(() => "")).replace(/\s+/g, " ").trim();

  // 0. where a person starts: the chat page, empty (none of the owner's chats is opened)
  await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  await page.locator('a[href="/chat/agents"]').first().waitFor({ state: "visible", timeout: 90000 });
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
  await L.wait(1500);
  const version = await page.evaluate(async () => (await (await fetch("/api/version")).json().catch(() => ({}))).build).catch(() => null);
  log(`${tag}: web build ${version}`);
  const chatComposer = await measureComposer(page);
  await shot("00-chat");
  // 1. the market
  const t1 = Date.now();
  await click(page.locator('a[href="/chat/agents"]'), "Agent Market");
  await page.waitForSelector('[data-testid="am-market-grid"]', { timeout: 60000 });
  await L.wait(1200);
  mark("market open");
  await shot("01-market");
  const card = page.locator('[data-testid="am-agent-card"]').filter({ hasText: "กระทบยอด" });
  check(await card.count(), "the bank reconciliation is an agent of the market, not “coming soon”");
  // 2. its page
  await click(card.locator('[data-testid="am-install"]'), "อ่านก่อนติดตั้ง (bank reconciliation card)");
  await page.waitForSelector('[data-testid="am-detail-install"]', { timeout: 60000 });
  await L.wait(1000);
  mark("agent page");
  await shot("02-agent-page");
  // 3. one click: the recommended settings and the sample self-test
  await click(page.locator('[data-testid="am-detail-install"]'), "ใช้ค่าแนะนำทั้งหมด");
  await page.waitForURL(/\/chat\/agents\/i\//, { timeout: 60000 });
  const installId = page.url().split("/chat/agents/i/")[1].split(/[?#/]/)[0];
  state.installs = [...new Set([...(state.installs || []), installId])];
  state[`install_${tag}`] = installId;
  save();
  await page.waitForSelector('[data-testid="am-empty-pick"]', { timeout: 60000 });
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
  await L.wait(1500);
  mark("workspace (installed)");
  await shot("03-workspace-empty");
  // A. the workspace's composer is the chat's
  const wsComposer = await measureComposer(page);
  fs.writeFileSync(path.join(dir, "composer.json"), JSON.stringify({ chat: chatComposer, workspace: wsComposer }, null, 1));
  // The chat page is opened empty here (none of the owner's chats is opened), and an empty chat
  // puts its composer in its narrower opening column (720 px); a conversation and the workspace use
  // the thread's column (768 px — measured equal, thread against thread, by ui.js on the test
  // stack). So the box itself is compared, and its width is recorded.
  const box = (p) => (p ? { ...p, w: undefined, x: undefined } : p);
  for (const part of ["root", "pill", "textarea", "attach"]) {
    check(JSON.stringify(box(chatComposer?.[part])) === JSON.stringify(box(wsComposer?.[part])), `composer ${part}: the workspace's is the chat's`, JSON.stringify(wsComposer?.[part]));
  }
  log(`${tag}: composer column — empty chat ${JSON.stringify(chatComposer?.column)} · workspace ${JSON.stringify(wsComposer?.column)}`);
  check(!(await page.locator('[data-testid="am-rail"]').count()), "the workspace has no list of agents of its own");
  // B. the model menu
  const control = page.locator('[data-testid="am-model-control"]');
  if (await control.count()) {
    await control.first().click({ timeout: 10000 });
    await page.waitForSelector('[data-testid="am-model-menu"]', { timeout: 10000 });
    await L.wait(800);
    await shot("04-model-menu");
    const rows = await page.locator('[data-testid="am-model-option"]').evaluateAll((els) => els.map((e) => ({ model: e.getAttribute("data-model"), text: e.innerText.replace(/\s+/g, " ").trim() })));
    fs.writeFileSync(path.join(dir, "model_menu.json"), JSON.stringify(rows, null, 1));
    check(new Set(rows.map((r) => r.model)).size === rows.length, "no two rows of the model menu are the same model", rows.map((r) => r.text).join(" | "));
    const fits = await page.evaluate(() => {
      const r = document.querySelector('[data-testid="am-model-menu"]').getBoundingClientRect();
      const d = document.documentElement;
      return { left: Math.round(r.left), right: Math.round(r.right), window: window.innerWidth, sideways: d.scrollWidth > d.clientWidth + 1 };
    });
    check(fits.left >= 0 && fits.right <= fits.window && !fits.sideways, "the model menu opens inside the window", JSON.stringify(fits));
    await page.keyboard.press("Escape");
    await L.wait(400);
  } else {
    // an agent that uses no model has nothing to choose
    check(true, "the bank reconciliation uses no model: the workspace offers no model menu");
  }

  // one round: choose the files, send, answer what is asked with the card's recommendation or default
  const round = async (files, message, n) => {
    const buffers = files.map((f) => ({ name: f.name, mimeType: XLSX, buffer: fs.readFileSync(path.join(FIX, f.src)) }));
    const pick = page.locator('[data-testid="am-empty-pick"]');
    if (await pick.count()) {
      const [chooser] = await Promise.all([page.waitForEvent("filechooser", { timeout: 30000 }), click(pick, "เลือกไฟล์")]);
      await chooser.setFiles(buffers);
    } else {
      const start = page.locator('[data-testid="am-round-start"]');
      if (await start.count()) await click(start.last(), "เริ่มรอบใหม่");
      await L.wait(1000);
      // the attach button opens the system's file dialog, which cannot be driven in the owner's app:
      // the click is counted and the files are handed to the same input the dialog fills
      clicks += 1;
      log(`${tag} click ${clicks}: แนบไฟล์`);
      await page.locator('[data-testid="am-composer-file"]').setInputFiles(buffers);
    }
    clicks += 2;
    log(`${tag} clicks ${clicks - 1}-${clicks}: select both files + open (file dialog)`);
    await page.waitForSelector('[data-testid="composer-chip-row"]', { timeout: 60000 }).catch(() => null);
    await L.wait(2000);
    await shot(`${n}a-files-staged`);
    await page.locator('[data-testid="am-composer-input"]').fill(message);
    const before = ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).filter((r) => r.kind === "run").map((r) => r.id);
    await click(page.locator('[data-testid="am-composer-send"]'), "ส่ง");
    await L.wait(1500);
    const chip = page.locator('[data-testid="am-intent-new"]');
    if (await chip.count()) await click(chip.last(), "เริ่มรอบใหม่ (chip)");
    const run = await waitFor(async () => ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).find((r) => r.kind === "run" && !before.includes(r.id)), 90000, 1000);
    if (!run) throw new Error("no round started");
    state.runs = [...new Set([...(state.runs || []), run.id])];
    save();
    const seen = new Set();
    const TERMINAL = ["succeeded", "failed", "cancelled"];
    let cards = 0;
    for (;;) {
      const d = await waitFor(async () => {
        const r = (await api(page, `/runs/${run.id}`)).body;
        if (!r || !r.run) return null;
        if (TERMINAL.includes(r.run.status)) return r;
        const open = (r.clarifications || []).filter((c) => c.status === "open" && !seen.has(c.id));
        return r.run.status === "awaiting_input" && open.length ? r : null;
      }, 900000, 1500);
      if (!d) throw new Error("the round did not finish in 15 minutes");
      if (TERMINAL.includes(d.run.status)) return { run: d.run, detail: d, cards };
      const c = d.clarifications.filter((x) => x.status === "open" && !seen.has(x.id)).pop();
      seen.add(c.id);
      cards += 1;
      const cardEl = page.locator(`[data-card-id="${c.id}"]`).first();
      await cardEl.waitFor({ state: "visible", timeout: 90000 });
      await cardEl.scrollIntoViewIfNeeded();
      await L.wait(800);
      await shot(`${n}b-question-card-${cards}`);
      const items = cardEl.locator('[data-testid="question-item"]');
      for (let k = 0; k < (await items.count()); k++) {
        const q = (c.questions || [])[k] || {};
        const want = (q.recommended_label || q.default_label || "").trim();
        const opts = items.nth(k).locator('[data-testid="question-option"]');
        let o = opts.first();
        for (let m = 0; m < (await opts.count()); m++) if (want && (await opts.nth(m).innerText()).includes(want)) { o = opts.nth(m); break; }
        log(`${tag} round ${n} card ${cards} q${k + 1}: ${(q.question || "").slice(0, 90)} → ${(await o.innerText()).replace(/\s+/g, " ").slice(0, 60)}`);
        await click(o);
      }
      await click(cardEl.locator('[data-testid="question-submit"]'), `ส่งคำตอบ (card ${cards})`);
      await L.wait(2500);
    }
  };
  const paperOf = async (detail, name) => {
    const art = (detail.artifacts || []).filter((a) => a.format === "xlsx").sort((a, b) => b.version - a.version)[0];
    if (!art) return null;
    const file = path.join(dir, name);
    fs.writeFileSync(file, await fetchFile(page, `${EDGE()}/api/v1/agent-market/artifacts/${art.id}/download`));
    return { art, file };
  };

  // E. a month that ties
  const tied = await round(TIES, "[AM5] กระทบยอดเงินฝากธนาคาร เดือนกันยายน 2569", 5);
  mark(`round 1: ${tied.run.status}`);
  check(tied.run.status === "succeeded", "a month that ties is a success", `${tied.run.status} ${tied.run.status_reason || ""}`);
  check(tied.cards === 0, "the clean month asks nothing", `${tied.cards} card(s)`);
  const outs = page.locator('[data-testid="am-outputs"]:visible').last();
  await outs.waitFor({ state: "visible", timeout: 120000 });
  await outs.scrollIntoViewIfNeeded();
  await L.wait(1500);
  await shot("06-files-tied");
  const tiedText = await words(outs);
  check(/ลงตัว/.test(await words(outs.locator('[data-testid="am-outputs-gate"]'))), "the files card says it ties", tiedText.slice(0, 200));
  check(/1,538,519\.65/.test(await words(outs.locator('[data-testid="am-outputs-kpis"]'))), "the two adjusted balances are on the card, to the satang");
  check(/จับคู่ได้ 10 คู่ · ตามกฎ 10 · คนยืนยัน 0/.test(await words(outs.locator('[data-testid="am-outputs-match"]'))), "who matched what", await words(outs.locator('[data-testid="am-outputs-match"]')));
  const p1 = await paperOf(tied.detail, "paper_tied.xlsx");
  clicks += 1;
  const c1 = p1 ? checkPaper(p1.file, true) : { ok: false };
  check(c1.ok, "the working paper ties at the scenario's own figure", JSON.stringify(c1));
  mark("working paper in hand");
  const total = Number(((Date.now() - t1) / 1000).toFixed(1));
  const clicksToReport = clicks;

  // E. a month that does not tie
  const loose = await round(DOES_NOT_TIE, "[AM5] กระทบยอดเงินฝากธนาคาร เดือนกันยายน 2569 (รายการเดินบัญชีขาดหน้า)", 7);
  mark(`round 2: ${loose.run.status} ${loose.run.status_reason || ""}`);
  check(loose.run.status !== "succeeded", "a month that does not tie is NOT a success", `${loose.run.status} ${loose.run.status_reason || ""}`);
  const failed = page.locator('[data-testid="am-failed-round"]:visible').last();
  await failed.waitFor({ state: "visible", timeout: 120000 });
  await failed.scrollIntoViewIfNeeded();
  await L.wait(1500);
  await shot("08-does-not-tie");
  const what = await words(failed.locator('[data-testid="am-failed-what"]'));
  check(what === `ยังไม่ลงตัว ต่างกัน ${EXPECT.difference_when_rows_are_missing} บาท`, "the round says how far off it is", what);
  for (const part of ["next", "files", "charged"]) check((await words(failed.locator(`[data-testid="am-failed-${part}"]`))).length > 10, `and says: ${part}`);
  check(!(await failed.locator('[data-testid="am-failed-retry"]').count()), "it does not offer the same files again");
  const ftext = await words(failed);
  check(!TECHNICAL.test(ftext), "no error text, type name or step id in it", ftext.slice(0, 200));
  const draft = page.locator('[data-testid="am-outputs"]:visible').last();
  await draft.scrollIntoViewIfNeeded();
  await L.wait(800);
  await shot("09-draft-files");
  check(/ร่าง/.test(await words(draft.locator('[data-testid="am-outputs-draft"]'))), "the files card says the working paper is a draft", (await words(draft)).slice(0, 200));
  check(!(await draft.locator('[data-testid="am-signoff-open"]').count()), "a draft cannot be signed");
  const p2 = await paperOf(loose.detail, "paper_draft.xlsx");
  const c2 = p2 ? checkPaper(p2.file, false) : { ok: false };
  check(c2.ok, "the draft says on its face that it does not tie", JSON.stringify(c2));
  await shot("10-thread", { fullPage: true });
  const thread = await words(page.locator('[data-testid="am-workspace"]'));
  check(!/Traceback|TypeError|KeyError|object is not|NoneType|not_tied|gate_not_passed/.test(thread), "nothing technical anywhere in the workspace");

  const res = { client: tag, web_build: version, install: installId, runs: state.runs, seconds_from_market_to_report: total, clicks_to_report: clicksToReport,
    clicks, marks, checks, tied: c1, draft: c2, failed_round: ftext };
  fs.writeFileSync(path.join(dir, "journey.json"), JSON.stringify(res, null, 1));
  const bad = checks.filter((c) => !c.ok);
  log(`${tag}: ${checks.length - bad.length} pass, ${bad.length} fail · ${total} s and ${clicksToReport} clicks from the market to the working paper`);
  await s.close();
  return res;
}

// ── phases A–C on the live system: the invoice agent ─────────────────────
// the model menu, what the first card recommends, the list of what was left
// out and one click back. (A round that fails is not staged on the live
// system: that would mean breaking its sandbox. ui.js does it on the test stack.)
const E2_DOCS = path.join(L.ROOT, "backend", "agent_market", "fixtures", "e2", "docs");
const INVOICES = [
  { src: "d01_clean_full_tax_invoice.pdf", name: "ใบกำกับภาษี ก.ย.69 ร้านเครื่องเขียน.pdf" },
  { src: "f02_vat_mismatch.pdf", name: "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf" },
];
async function invoiceJourney(client, only) {
  const email = process.env.AM5_EMAIL || (L.TARGET === "live" ? "" : L.TS_USER.email);
  const s = client === "desktop" ? await connectDesktop() : await openWeb(email || process.env.OWNER_EMAIL);
  const { page } = s;
  const tag = `${client}/invoice`;
  const dir = path.join(OUT, client === "web" && WINDOW ? `web-${WINDOW[3] * 100}` : client);
  fs.mkdirSync(dir, { recursive: true });
  const checks = [];
  const check = (ok, what, detail = "") => { checks.push({ ok: !!ok, what, detail: String(detail).slice(0, 300) }); log(`${tag} ${ok ? "PASS" : "FAIL"} ${what}${detail ? ` — ${String(detail).slice(0, 160)}` : ""}`); };
  // a screenshot of a window that is covered or minimised can time out over CDP: the journey goes on
  const shot = (name, opts = {}) => capture(page, path.join(dir, `${name}.png`), opts, (m) => log(`${tag}: ${m}`));
  const words = async (loc) => (await loc.innerText().catch(() => "")).replace(/\s+/g, " ").trim();

  await page.goto(`${WEB()}/chat/agents`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-market-grid"]', { timeout: 60000 });
  const card = page.locator('[data-testid="am-agent-card"]').filter({ hasText: "ใบกำกับ" });
  await card.locator('[data-testid="am-install"]').first().click({ timeout: 30000 });
  await page.waitForSelector('[data-testid="am-detail-install"]', { timeout: 60000 });
  await page.locator('[data-testid="am-detail-install"]').first().click({ timeout: 30000 });
  await page.waitForURL(/\/chat\/agents\/i\//, { timeout: 60000 });
  const installId = page.url().split("/chat/agents/i/")[1].split(/[?#/]/)[0];
  state.installs = [...new Set([...(state.installs || []), installId])];
  save();
  await page.waitForSelector('[data-testid="am-empty-pick"]', { timeout: 60000 });
  await L.wait(1500);
  check(!(await page.locator('[data-testid="am-rail"]').count()), "the workspace has no list of agents of its own");

  // B. the model menu
  await page.locator('[data-testid="am-model-control"]').first().click({ timeout: 15000 });
  await page.waitForSelector('[data-testid="am-model-menu"]', { timeout: 15000 });
  await L.wait(800);
  await shot("20-model-menu");
  const rows = await page.locator('[data-testid="am-model-option"]').evaluateAll((els) => els.map((e) => ({ model: e.getAttribute("data-model"), text: e.innerText.replace(/\s+/g, " ").trim() })));
  fs.writeFileSync(path.join(dir, "model_menu.json"), JSON.stringify({ rows, single: await words(page.locator('[data-testid="am-model-single"]')) }, null, 1));
  check(rows.length > 0 && new Set(rows.map((r) => r.model)).size === rows.length, "no two rows of the model menu are the same model", rows.map((r) => r.text).join(" | "));
  const inHouse = rows.filter((r) => /ในองค์กร/.test(r.text)).length;
  if (inHouse === 1) check((await words(page.locator('[data-testid="am-model-single"]'))) === "ตอนนี้องค์กรมีโมเดลในองค์กรตัวเดียว", "one in-house model is said in one line");
  const fits = await page.evaluate(() => {
    const r = document.querySelector('[data-testid="am-model-menu"]').getBoundingClientRect();
    const d = document.documentElement;
    return { left: Math.round(r.left), right: Math.round(r.right), window: window.innerWidth, sideways: d.scrollWidth > d.clientWidth + 1 };
  });
  check(fits.left >= 0 && fits.right <= fits.window && !fits.sideways, "the model menu opens inside the window", JSON.stringify(fits));
  // what is on top at 121 points of the menu: the menu, or something drawn over it
  const over = await page.evaluate(() => {
    const menu = document.querySelector('[data-testid="am-model-menu"]');
    const r = menu.getBoundingClientRect();
    const vv = window.visualViewport;
    const found = new Set();
    for (let i = 1; i < 12; i++) {
      for (let j = 1; j < 12; j++) {
        const el = document.elementFromPoint(r.left + (r.width * i) / 12, r.top + (r.height * j) / 12);
        if (el && !menu.contains(el)) found.add((el.closest("[data-testid]") || el).getAttribute("data-testid") || el.tagName.toLowerCase());
      }
    }
    return { over: [...found], window: `${window.innerHeight} px, visible ${vv ? vv.height : "?"} px, scale ${window.devicePixelRatio}`,
      composer: getComputedStyle(document.querySelector('[data-testid="composer-root"]')).transform };
  });
  check(!over.over.length, "nothing is drawn over the open model menu", `${over.over.join(", ") || "nothing"} · window ${over.window} · composer transform ${over.composer}`);
  await page.locator('[data-testid="am-model-custom"]').click({ timeout: 10000 });
  await L.wait(1200);
  await shot("21-model-choose-your-own");
  check(await page.locator('[data-testid="am-model-menu"] input[type="text"]').count(), "“เลือกโมเดลเอง” opens the chat's picker (its search box)");
  const locked = await page.locator('[data-testid="am-model-menu"] [data-testid="model-row-disabled"]').count();
  log(`${tag}: ${locked} model(s) outside the organisation, locked with the reason`);
  await page.keyboard.press("Escape");
  await L.wait(400);
  if (only === "menu") {
    fs.writeFileSync(path.join(dir, "model_menu_checks.json"), JSON.stringify({ client, install: installId, checks, model_menu: rows }, null, 1));
    log(`${tag} (the model menu only): ${checks.filter((c) => c.ok).length} pass, ${checks.filter((c) => !c.ok).length} fail`);
    await s.close();
    return;
  }

  // C. the first card a person meets, skipped — the day-one trap
  const buffers = INVOICES.map((f) => ({ name: f.name, mimeType: "application/pdf", buffer: fs.readFileSync(path.join(E2_DOCS, f.src)) }));
  const [chooser] = await Promise.all([page.waitForEvent("filechooser", { timeout: 30000 }), page.locator('[data-testid="am-empty-pick"]').first().click({ timeout: 30000 })]);
  await chooser.setFiles(buffers);
  await page.waitForSelector('[data-testid="composer-chip-row"]', { timeout: 60000 }).catch(() => null);
  await L.wait(2000);
  await page.locator('[data-testid="am-composer-input"]').fill("[AM5] ทำทะเบียนซื้อเดือนกันยายน");
  const before = ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).filter((r) => r.kind === "run").map((r) => r.id);
  await page.locator('[data-testid="am-composer-send"]').click({ timeout: 30000 });
  const run = await waitFor(async () => ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).find((r) => r.kind === "run" && !before.includes(r.id)), 90000, 1000);
  if (!run) throw new Error("no round started");
  state.runs = [...new Set([...(state.runs || []), run.id])];
  save();
  const status = (want, ms) => waitFor(async () => { const d = (await api(page, `/runs/${run.id}`)).body; return d && d.run && want(d) ? d : null; }, ms, 1500);
  const parked = await status((d) => d.run.status === "awaiting_input" || ["succeeded", "failed"].includes(d.run.status), 900000);
  check(parked && parked.run.status === "awaiting_input", "the invoice whose VAT does not add up asks its question", parked && parked.run.status);
  const q = page.locator('[data-testid="am-question-card"]').last();
  await q.waitFor({ state: "visible", timeout: 90000 });
  await q.scrollIntoViewIfNeeded();
  await L.wait(1200);
  await shot("22-question-card");
  const qtext = await words(q);
  check(/ค่าแนะนำ: บันทึกตามเอกสาร/.test(qtext) && !/ค่าแนะนำ: ตัดเอกสารนี้ออก/.test(qtext), "the card recommends recording the purchase, not leaving it out", qtext.slice(0, 200));
  check(/เอกสารที่ยังไม่ได้รวม/.test(qtext), "and says what skipping does");
  await q.locator('[data-testid="question-skip"]').click({ timeout: 15000 });
  const done = await status((d) => ["succeeded", "failed"].includes(d.run.status), 900000);
  check(done && done.run.status === "succeeded", "the round finishes", done && done.run.status);
  const list = page.locator('[data-testid="am-not-included"]').last();
  await list.waitFor({ state: "visible", timeout: 120000 });
  await list.scrollIntoViewIfNeeded();
  await L.wait(1200);
  await shot("23-left-out");
  const ltext = await words(list);
  check(ltext.includes(INVOICES[1].name) && /เอกสารที่ยังไม่ได้รวม \(1\)/.test(ltext), "the invoice that was left out is on the round's list", ltext.slice(0, 200));
  await list.locator('[data-testid="am-restore"]').first().click({ timeout: 15000 });
  const v2 = await status((d) => d.run.status === "succeeded" && (d.artifacts || []).some((a) => a.format === "xlsx" && a.version >= 2), 900000);
  check(!!v2, "one click brings it back: the same round delivers the next version");
  await L.wait(3000);
  const last = page.locator('[data-testid="am-outputs"]:visible').last();
  await last.scrollIntoViewIfNeeded();
  await L.wait(800);
  await shot("24-restored");
  check(!(await last.locator('[data-testid="am-not-included"]').count()), "nothing is left out on the newest files card");
  const thread = await words(page.locator('[data-testid="am-workspace"]'));
  check(!/Traceback|TypeError|KeyError|object is not|NoneType|_failed\b/.test(thread), "nothing technical anywhere in the workspace");
  fs.writeFileSync(path.join(dir, "invoice_journey.json"), JSON.stringify({ client, install: installId, run: run.id, checks, model_menu: rows }, null, 1));
  const bad = checks.filter((c) => !c.ok);
  log(`${tag}: ${checks.length - bad.length} pass, ${bad.length} fail`);
  await s.close();
}

// What the threads of the agents the owner already had say today, read only (GET): the two failed
// rounds on the owner's screen were written with the error's text; they must read without it now.
// Nothing is opened, marked or changed; nothing of a thread is printed — only counts.
async function legacy() {
  const s = await connectDesktop();
  const { page } = s;
  const installs = ((await api(page, "/installs")).body?.installs || []).filter((i) => !(state.installs || []).includes(i.id));
  let items = 0, failedRounds = 0, withFailure = 0;
  const hits = [];
  for (const i of installs) {
    const t = (await api(page, `/installs/${i.id}/thread`)).body?.items || [];
    for (const it of t) {
      const p = it.payload || {};
      const texts = [];
      if (it.kind === "agent_text" || it.kind === "notice") texts.push(p.text || "", ...Object.entries(p.failure || {}).filter(([k, v]) => k !== "reason" && typeof v === "string").map(([, v]) => v));
      if (it.kind === "progress") for (const st of p.steps || []) texts.push(st.label || "", st.detail || "");
      if (it.kind === "agent_text" && p.tone === "error") { failedRounds += 1; if (p.failure) withFailure += 1; }
      items += texts.length ? 1 : 0;
      for (const x of texts) if (/\b[A-Z][A-Za-z]*(?:Error|Exception)\b|NoneType|Traceback|not iterable|object is not|engine step failed|sandbox:|dial tcp|SQLSTATE|\b[a-z]+(?:_[a-z0-9]+)*_failed\b/.test(x)) hits.push(it.kind);
    }
  }
  log(`legacy: ${installs.length} install(s) the owner already had, ${items} item(s) a person reads, ${failedRounds} failed-round message(s) (${withFailure} with the four parts) — technical text in ${hits.length} item(s)`);
  fs.writeFileSync(path.join(OUT, "legacy.json"), JSON.stringify({ installs: installs.length, items, failed_rounds: failedRounds, with_four_parts: withFailure, technical: hits.length }, null, 1));
  await s.close();
}

const phases = {
  async invoice() {
    await invoiceJourney(process.argv[3] || "desktop", process.argv[4]); // "menu": the model menu only, no round
  },
  async legacy() {
    await legacy();
  },
  async open() {
    const wasRunning = running();
    state.wasRunning = wasRunning;
    if (wasRunning && !(await portOpen(PORT))) {
      const busy = logSince(new Date(Date.now() - 10 * 60_000).toISOString()).filter((l) => /turn|stream|message|tool|composer|send/i.test(l) && !SESSION_LINE.test(l));
      if (busy.length) throw new Error(`the app was used in the last 10 minutes (${busy.length} lines); not closing it`);
      kill();
      await waitFor(async () => !running(), 15000);
    }
    if (process.env.AM5_INSTALL === "0") {
      log(`installer not run (AM5_INSTALL=0): the app that is installed is relaunched`);
    } else {
      if (!fs.existsSync(SETUP)) throw new Error(`no installer at ${SETUP}`);
      const r = spawnSync(SETUP, ["/S"], { encoding: "utf8", timeout: 300000 });
      log(`installer ${VERSION} /S exit ${r.status}`);
    }
    await L.wait(3000);
    kill(); // a one-click NSIS may start the app: close it for the CDP launch
    await waitFor(async () => !running(), 15000);
    launch([`--remote-debugging-port=${PORT}`]);
    const { page, close } = await connectDesktop();
    await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 90000 });
    const cfg = desktopCfg();
    const me = await page.evaluate(async (gw) => (await (await fetch(`${gw}/portal/me`, { credentials: "include" })).json().catch(() => ({}))), cfg.gatewayUrl);
    const email = me?.email || me?.user?.email || null;
    state.email_masked = email ? email.replace(/^(.).*(@.*)$/, "$1***$2") : null;
    const ver = (logSince("0000").filter((l) => /SRAN Desktop \S+ pid=/.test(l)).pop() || "").slice(24, 90).trim();
    state.desktop_version = ver;
    log(`desktop up: ${ver}; signed in: ${state.email_masked}; pageUrl ${cfg.pageUrl}`);
    const avail = await api(page, "/availability");
    log(`availability ${avail.status} available=${avail.body?.available}`);
    save();
    await close();
  },
  async journey() {
    const client = process.argv[3] || "desktop";
    await journey(client);
  },
  async cleanup() {
    // "cleanup web": from the browser of its own (the app is not needed, and is not touched)
    const s = L.TARGET === "live" && process.argv[3] !== "web" ? await connectDesktop() : await openWeb(process.env.AM5_EMAIL || L.TS_USER.email);
    if (!s.page.url().startsWith(WEB())) await s.page.goto(`${WEB()}/chat`, { waitUntil: "load" });
    // a test round's files are not kept for five years: its evidence is removed, with the reason
    for (const id of state.runs || []) {
      // AM7: the chat app has no admin route any more — the admin door (scripts/probes/AM7/admin_door.js)
      const r = await require("../AM7/admin_door.js").removeEvidence(id, "[AM5] รอบทดสอบของการเดินตรวจ AM5 ไม่ใช่งานจริง");
      log(`evidence of round ${id} removed: ${r.status}`);
    }
    for (const id of state.installs || []) {
      const r = await api(s.page, `/installs/${id}`, { method: "DELETE" });
      log(`removed install ${id}: ${r.status}`);
    }
    state.removed = { installs: state.installs, runs: state.runs };
    state.installs = [];
    state.runs = [];
    save();
    await s.close();
  },
  async close() {
    const busy = logSince(new Date(Date.now() - 10 * 60_000).toISOString()).filter((l) => /turn|stream|message|tool|composer|send/i.test(l) && !SESSION_LINE.test(l) && !/remote-debugging|CDP|DevTools/i.test(l));
    log(`app log lines of use in the last 10 min (besides this driver): ${busy.length}`);
    kill();
    await waitFor(async () => !running(), 15000);
    launch([]);
    const up = await waitFor(async () => running(), 30000);
    await L.wait(8000);
    const gone = !(await portOpen(PORT));
    const since = new Date(Date.now() - 60_000).toISOString();
    const lines = logSince(since).filter((l) => SESSION_LINE.test(l)).map((l) => l.slice(24, 140));
    const reauth = lines.filter((l) => /RE-?AUTH|EXPIRED/i.test(l)).length;
    log(`relaunched without the debugging port: running ${!!up}, 9333 ${gone ? "closed" : "STILL OPEN"}; session lines: ${lines.slice(-6).join(" | ")}; re-auth lines: ${reauth}`);
  },
};

const cmd = process.argv[2];
if (!phases[cmd]) {
  console.log(`usage: walkthrough.js ${Object.keys(phases).join(" | ")}`);
  process.exit(2);
}
phases[cmd]().catch((e) => {
  log(`ERROR ${String(e && e.stack || e).slice(0, 600)}`);
  process.exit(1);
});
