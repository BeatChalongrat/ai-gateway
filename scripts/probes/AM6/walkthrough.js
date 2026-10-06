#!/usr/bin/env node
// AM6 — the journey on both clients, clicking the real UI:
//
//   journey: the chat → Agent Market → ตรวจใบกำกับภาษีกับใบสั่งซื้อ (its scope on the card) →
//     "ใช้ค่าแนะนำทั้งหมด" → the workspace (the cost line before a round, the suggestions above the
//     composer, the model menu = the chat's) → the sample month's three files → the one question
//     "บริษัทออกใบรับของหรือไม่" with its guess → the files card (three counts, the depth line, the
//     two-way warning, "no invoice is approved") → the working paper checked (depth on every line
//     and on its face) → the receipt offers → "ร่างอีเมล" (no recipient, no send without a mail
//     connector) → the install's settings (declared settings, reminders) → a reminder that falls
//     due in two minutes → its notification → the round that waits for files → the files → done.
//   bank: a statement as a PDF (ties) and a month that does not tie ("เสร็จแล้ว · ยังไม่ลงตัว").
//
//   SMOKE_TARGET=live node scripts/probes/AM6/walkthrough.js open            # desktop: install e61, relaunch with CDP 9333
//   SMOKE_TARGET=live node scripts/probes/AM6/walkthrough.js journey desktop # in the owner's desktop app
//   SMOKE_TARGET=live node scripts/probes/AM6/walkthrough.js journey web     # the same in a browser of its own
//   SMOKE_TARGET=live node scripts/probes/AM6/walkthrough.js bank desktop|web
//   SMOKE_TARGET=live node scripts/probes/AM6/walkthrough.js financial desktop|web  # AM8: the report on the live model and a customer's form
//   SMOKE_TARGET=live node scripts/probes/AM6/walkthrough.js cleanup [web]   # the walkthrough's reminders, installs (by id) and its rounds' evidence
//
// On live, cleanup's admin door is https through Caddy: export NODE_EXTRA_CA_CERTS (the machine's
// local CA, infra/caddy/certs/rootCA.pem, as scripts/smoke.sh does) or it ends with "fetch failed".
// A person who already has the agent gets another copy through the install wizard's "ติดตั้งอีกชุด";
// their own install is never opened.
//   SMOKE_TARGET=live node scripts/probes/AM6/walkthrough.js close           # relaunch the app without the debugging port
//
// Safety (CARD-RULES §4): the app is only closed when its log shows no use in the last 10
// minutes; `close` relaunches it without the debugging port. The web client runs in a headless
// browser with its own profile and a short-lived session for the same account — the owner's own
// browser is never touched. Every message is tagged [AM6]; no chat is created and none of the
// owner's chats is opened; the installs and reminders made here are removed by id. The file
// dialog of the owner's app cannot be driven: its click is counted and the files are handed to the
// input the dialog fills.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { spawn, spawnSync, execFileSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

const PORT = 9333;
// AM7: the desktop build of the checkout (was pinned to e61)
const VERSION = require(path.join(L.ROOT, "apps", "desktop", "package.json")).version;
const EXE = path.join(process.env.LOCALAPPDATA || "", "Programs", "sran-desktop", "SRAN Desktop.exe");
const LOGF = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "logs", "shell.log");
const PROCS = ["SRAN Desktop.exe", "SranDesktop.ToolsSidecar.exe", "SranDesktop.BrowserHost.exe"];
const SETUP = path.join(L.ROOT, "apps", "desktop", "release", `SRAN Desktop Setup ${VERSION}.exe`);
// AM7: the card that walks it tags what it makes (default [AM6])
const TAG = process.env.AM6_TAG || "[AM6]";
// AM10: a later card's walk keeps its own results (AM6_OUT), beside its own tag
const OUT = process.env.AM6_OUT || path.join(__dirname, "results", "walk", L.TARGET);
const STATE = path.join(OUT, "state.json");
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
const WEB = () => process.env.AM6_WEB || (L.TARGET === "live" ? "https://localhost:3002" : "http://localhost:13002");
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
// AM6_WINDOW=1536x889@1.25 — the web client in a window like the desktop app's on a display at
// 125 %: 889.6 px tall, which the page is told is 890 (the credits badge over the model menu)
const WINDOW = /^(\d+)x(\d+)@([\d.]+)$/.exec(process.env.AM6_WINDOW || "");
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

// ── AM6 ──────────────────────────────────────────────────────────────────
const PO_DIR = path.join(L.ROOT, "backend", "agent_market", "packages", "po_matching");
const PO_SLUG = "sran.invoice-po-matching";
const BANK_FIX = path.join(L.ROOT, "backend", "agent_market", "packages", "bank_reconciliation", "evals", "fixtures");
const BANK_SLUG = "sran.bank-reconciliation";
const MIME = { ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".pdf": "application/pdf", ".csv": "text/csv" };
// the agent's own sample month, named the way a person names files (the engine never reads the names)
const SAMPLE = [
  { src: path.join(PO_DIR, "sample-data", "ใบสั่งซื้อ_2569-09.xlsx"), name: "PO เดือน ก.ย.69.xlsx" },
  { src: path.join(PO_DIR, "sample-data", "ใบรับของ_2569-09.xlsx"), name: "ใบรับของ ก.ย.69.xlsx" },
  { src: path.join(PO_DIR, "sample-data", "ใบกำกับภาษี_2569-09.xlsx"), name: "ทะเบียนใบกำกับภาษีซื้อ ก.ย.69.xlsx" },
];
// the documents are made out to a company that does not exist; the install says it is that company
const SAMPLE_BUYER = "บริษัท สยามตัวอย่างการค้า จำกัด";
const EXPECT_PO = { passed: 3, three_way: 5, two_way: 1, value_passed: "106,304.50" };
const BANK_PDF = [
  { src: path.join(BANK_FIX, "statement_sep.pdf"), name: "Statement กสิกร ก.ย.69.pdf" },
  { src: path.join(BANK_FIX, "ledger_sep.xlsx"), name: "สมุดบัญชีเงินฝาก ก.ย.69.xlsx" },
];
const BANK_OPEN = [
  { src: path.join(BANK_FIX, "kbank_sep_page_missing.xlsx"), name: "Statement กสิกร ก.ย.69 (ขาดหน้า).xlsx" },
  { src: path.join(BANK_FIX, "ledger_sep.xlsx"), name: "สมุดบัญชีเงินฝาก ก.ย.69.xlsx" },
];
const TECH = /\b[A-Z][A-Za-z]*(?:Error|Exception)\b|NoneType|Traceback|not iterable|object is not|engine step failed|sandbox:|dial tcp|SQLSTATE|\b[a-z]+(?:_[a-z0-9]+)*_failed\b|not_tied|gate_not_passed|completed_open/;
const TERMINAL = ["succeeded", "failed", "cancelled"];
const buffersOf = (files) => files.map((f) => ({ name: f.name, mimeType: MIME[path.extname(f.src)] || "application/octet-stream", buffer: fs.readFileSync(f.src) }));
// Thai time, for a reminder that falls due in a few minutes
function thaiSoon(minutes) {
  const t = new Date(Date.now() + 7 * 3600_000 + minutes * 60_000);
  const hh = String(t.getUTCHours()).padStart(2, "0");
  const mm = String(t.getUTCMinutes()).padStart(2, "0");
  return { weekday: ((t.getUTCDay() + 6) % 7) + 1, time: `${hh}:${mm}` };
}

async function session(client, name) {
  const email = process.env.AM6_EMAIL || (L.TARGET === "live" ? "" : L.TS_USER.email);
  const s = client === "desktop" ? await connectDesktop() : await openWeb(email || process.env.OWNER_EMAIL);
  const tag = `${client}/${name}`;
  const dir = path.join(OUT, client, name);
  fs.mkdirSync(dir, { recursive: true });
  const t0 = Date.now();
  const x = {
    s, page: s.page, tag, dir, clicks: 0, checks: [], marks: [],
    check(ok, what, detail = "") { x.checks.push({ ok: !!ok, what, detail: String(detail).slice(0, 400) }); log(`${tag} ${ok ? "PASS" : "FAIL"} ${what}${detail ? ` — ${String(detail).slice(0, 180)}` : ""}`); return !!ok; },
    mark(what) { const sec = Number(((Date.now() - t0) / 1000).toFixed(1)); x.marks.push({ what, s: sec, clicks: x.clicks }); log(`${tag} +${sec}s clicks=${x.clicks} ${what}`); },
    shot(n, opts = {}) { return capture(s.page, path.join(dir, `${n}.png`), opts, (m) => log(`${tag}: ${m}`)); },
    async click(loc, what) { await loc.first().click({ timeout: 30000 }); x.clicks += 1; if (what) log(`${tag} click ${x.clicks}: ${what}`); },
    async words(loc) { return (await loc.first().innerText({ timeout: 5000 }).catch(() => "")).replace(/\s+/g, " ").trim(); },
  };
  return x;
}

async function installFromMarket(x, cardText) {
  const { page } = x;
  await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  await page.locator('a[href="/chat/agents"]').first().waitFor({ state: "visible", timeout: 90000 });
  await L.wait(1200);
  x.webBuild = await page.evaluate(async () => (await (await fetch("/api/version")).json().catch(() => ({}))).build).catch(() => null);
  log(`${x.tag}: web build ${x.webBuild}`);
  await x.click(page.locator('a[href="/chat/agents"]'), "Agent Market");
  await page.waitForSelector('[data-testid="am-market-grid"]', { timeout: 60000 });
  await L.wait(1200);
  const card = page.locator('[data-testid="am-agent-card"]').filter({ hasText: cardText });
  x.check(await card.count(), `“${cardText}” is an agent of the market`);
  await x.shot("01-market");
  await x.click(card.locator('[data-testid="am-install"]'), `อ่านก่อนติดตั้ง (${cardText})`);
  await page.waitForSelector('[data-testid="am-detail-install"]', { timeout: 60000 });
  await L.wait(1000);
  // AM10: a card says what the agent does, what to feed it and what comes back; whom it is for is on its page
  const scope = await x.words(page.locator('[data-testid="am-scope"]'));
  await x.shot("02-agent-page");
  await x.click(page.locator('[data-testid="am-detail-install"]'), "ใช้ค่าแนะนำทั้งหมด");
  await page.waitForURL(/\/chat\/agents\/i\//, { timeout: 60000 });
  const installId = page.url().split("/chat/agents/i/")[1].split(/[?#/]/)[0];
  state.installs = [...new Set([...(state.installs || []), installId])];
  save();
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
  await L.wait(1500);
  x.mark("workspace (installed)");
  return { installId, scope };
}

// the files go in through the composer (or the card that asks for them); every card is answered
// with its recommendation, else its default, the way a person who reads it and agrees would
async function sendFiles(x, installId, files, message, n) {
  const { page } = x;
  const pick = page.locator('[data-testid="am-empty-pick"]');
  if (await pick.count()) {
    const [chooser] = await Promise.all([page.waitForEvent("filechooser", { timeout: 30000 }), x.click(pick, "เลือกไฟล์")]);
    await chooser.setFiles(buffersOf(files));
  } else {
    const start = page.locator('[data-testid="am-round-start"]');
    if (await start.count()) await x.click(start.last(), "เริ่มรอบใหม่");
    await L.wait(1000);
    x.clicks += 1;
    log(`${x.tag} click ${x.clicks}: แนบไฟล์`);
    await page.locator('[data-testid="am-composer-file"]').setInputFiles(buffersOf(files));
  }
  x.clicks += 2;
  await page.waitForSelector('[data-testid="composer-chip-row"]', { timeout: 60000 }).catch(() => null);
  await L.wait(2000);
  const estimate = await x.words(page.locator('[data-testid="am-estimate"]'));
  await x.shot(`${n}a-files-staged`);
  await page.locator('[data-testid="am-composer-input"]').fill(message);
  const before = ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).map((r) => r.id);
  await x.click(page.locator('[data-testid="am-composer-send"]'), "ส่ง");
  await L.wait(1500);
  const chip = page.locator('[data-testid="am-intent-new"]');
  if (await chip.count()) await x.click(chip.last(), "เริ่มรอบใหม่ (chip)");
  const run = await waitFor(async () => ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).find((r) => r.kind === "run" && !before.includes(r.id)), 90000, 1000);
  if (!run) throw new Error("no round started");
  state.runs = [...new Set([...(state.runs || []), run.id])];
  save();
  return { runId: run.id, estimate };
}
async function finish(x, runId, n) {
  const { page } = x;
  const seen = new Set();
  const asked = [];
  for (;;) {
    const d = await waitFor(async () => {
      const r = (await api(page, `/runs/${runId}`)).body;
      if (!r || !r.run) return null;
      if (TERMINAL.includes(r.run.status)) return r;
      const open = (r.clarifications || []).filter((c) => c.status === "open" && !seen.has(c.id) && c.kind !== "request_files");
      return r.run.status === "awaiting_input" && open.length ? r : null;
    }, 900000, 1500);
    if (!d) throw new Error("the round did not finish in 15 minutes");
    if (TERMINAL.includes(d.run.status)) return { run: d.run, detail: d, asked };
    const c = d.clarifications.filter((y) => y.status === "open" && !seen.has(y.id) && y.kind !== "request_files").pop();
    seen.add(c.id);
    const cardEl = page.locator(`[data-card-id="${c.id}"]`).first();
    await cardEl.waitFor({ state: "visible", timeout: 90000 });
    await cardEl.scrollIntoViewIfNeeded();
    await L.wait(800);
    await x.shot(`${n}b-question-${asked.length + 1}`);
    const items = cardEl.locator('[data-testid="question-item"]');
    for (let k = 0; k < (await items.count()); k++) {
      const q = (c.questions || [])[k] || {};
      asked.push(q);
      const want = (q.recommended_label || q.default_label || "").trim();
      const opts = items.nth(k).locator('[data-testid="question-option"]');
      let o = opts.first();
      for (let m = 0; m < (await opts.count()); m++) if (want && (await opts.nth(m).innerText()).includes(want)) { o = opts.nth(m); break; }
      log(`${x.tag} card q: ${(q.question || "").slice(0, 90)} → ${(await o.innerText()).replace(/\s+/g, " ").slice(0, 60)}`);
      await x.click(o);
    }
    await x.click(cardEl.locator('[data-testid="question-submit"]'), "ส่งคำตอบ");
    await L.wait(2500);
  }
}
async function download(x, detail, fmt, name) {
  const art = (detail.artifacts || []).filter((a) => a.format === fmt).sort((a, b) => b.version - a.version)[0];
  if (!art) return null;
  const file = path.join(x.dir, name);
  fs.writeFileSync(file, await fetchFile(x.page, `${EDGE()}/api/v1/agent-market/artifacts/${art.id}/download`));
  x.clicks += 1;
  return file;
}
function checkMatchPaper(file) {
  const sheets = readWorkbook(file);
  const face = Object.fromEntries((sheets["สรุป"] || []).filter((r) => r && r[0]).map((r) => [r[0], r[1]]));
  const faceText = (sheets["สรุป"] || []).map((r) => (r || []).filter(Boolean).join(" ")).join("\n");
  const lines = sheets["ผลตรวจรายบรรทัด"] || [];
  const head = lines.findIndex((r) => r && r[0] === "ลำดับ");
  const col = head >= 0 ? lines[head].indexOf("วิธีตรวจ") : -1;
  const why = head >= 0 ? lines[head].indexOf("เหตุที่ตรวจแบบนี้") : -1;
  const body = head >= 0 ? lines.slice(head + 1).filter((r) => r && /^\d+$/.test(String(r[0]))) : [];
  const depths = body.map((r) => String(r[col] || ""));
  return {
    sheets: Object.keys(sheets),
    depth_on_face: face["วิธีตรวจของรอบนี้"] || null,
    approval_on_face: face["การอนุมัติจ่าย"] || null,
    two_way_warning_on_face: /ตรวจ 2 ทาง.*ไม่ได้ตรวจกับใบรับของ/.test(faceText),
    lines: body.length,
    three_way: depths.filter((d) => d.startsWith("ตรวจ 3 ทาง")).length,
    two_way: depths.filter((d) => d.startsWith("ตรวจ 2 ทาง")).length,
    every_line_says_why: why >= 0 && body.every((r) => String(r[why] || "").length > 3),
    two_way_line: body.filter((r) => String(r[col] || "").startsWith("ตรวจ 2 ทาง")).map((r) => r.filter(Boolean).join(" | "))[0] || null,
  };
}
async function threadText(x) {
  return x.words(x.page.locator('[data-testid="am-workspace"]'));
}

// ── the invoice ↔ PO journey ─────────────────────────────────────────────
async function journey(client) {
  const x = await session(client, "po");
  const { page } = x;
  const t1 = Date.now();
  const { installId, scope } = await installFromMarket(x, "ใบสั่งซื้อ");
  x.check(/ใช้ได้ทุกบริษัท|ทั่วไป|generic/i.test(scope) || scope.length > 0, "the agent's page says where the agent can be used (its scope)", scope);
  state[`po_${client}`] = installId;
  save();
  const put = await api(page, `/installs/${installId}/defaults/company`, { method: "PUT", body: JSON.stringify({ value: SAMPLE_BUYER }) });
  log(`${x.tag}: the install is the sample's buyer (${put.status}) — the sample documents are made out to it`);
  await page.reload({ waitUntil: "load" });
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
  await L.wait(1500);
  await x.shot("03-workspace-empty");
  // D2. the suggestions above the composer, the cost before a round
  // the one-click install runs the sample self-test first: while it runs a round is underway and the chips wait
  await page.locator('[data-testid="am-composer-suggestions"]').waitFor({ state: "visible", timeout: 180000 }).catch(() => null);
  await L.wait(800);
  const sugg = await page.locator('[data-testid="am-composer-suggestions"]').locator("button, a").evaluateAll((els) => els.map((e) => e.innerText.trim()));
  x.check(sugg.length >= 2 && sugg.length <= 3, "two or three suggestions above the composer before a round", sugg.join(" | "));
  const est0 = await x.words(page.locator('[data-testid="am-estimate"]'));
  x.check(est0.length > 0, "what a round costs is said before it starts", est0);
  // D. the model menu is the chat's
  const control = page.locator('[data-testid="am-model-control"]');
  let menu = { rows: [], hidden: "", notice: "" };
  if (await control.count()) {
    await control.first().click({ timeout: 15000 });
    await page.waitForSelector('[data-testid="am-model-menu"]', { timeout: 15000 });
    await L.wait(800);
    await x.shot("04-model-menu");
    const m = page.locator('[data-testid="am-model-menu"]');
    menu = {
      rows: await m.locator('[data-testid="model-row-name"]').evaluateAll((els) => els.map((e) => e.innerText.replace(/\s+/g, " ").trim())),
      hidden: await x.words(m.locator('[data-testid="model-group-hidden"]')),
      notice: await x.words(m.locator('[data-testid="model-picker-notice"]')),
      text: await x.words(m),
    };
    fs.writeFileSync(path.join(x.dir, "model_menu.json"), JSON.stringify(menu, null, 1));
    x.check(menu.rows.filter((r) => /อัตโนมัติ/.test(r)).length <= 1, "อัตโนมัติ is one row at most", menu.rows.join(" | "));
    x.check(!/≈|วินาที|เร็วและประหยัด|แม่นที่สุด|บาท\/รอบ/.test(menu.text), "no tiers, cost, time or labels in the menu", menu.text.slice(0, 200));
    await page.keyboard.press("Escape");
    await L.wait(400);
  } else {
    x.check(true, "the agent offers no model menu");
  }
  // A. the sample month
  const r1 = await sendFiles(x, installId, SAMPLE, TAG + " ตรวจใบแจ้งหนี้กับใบสั่งซื้อ เดือนกันยายน 2569", 5);
  x.check(r1.estimate.length > 0, "with files staged, the cost is said before sending", r1.estimate);
  const gone = !(await page.locator('[data-testid="am-composer-suggestions"]:visible').count());
  x.check(gone, "the suggestions are gone once a round is underway");
  const done1 = await finish(x, r1.runId, 5);
  x.mark(`round 1: ${done1.run.status} ${done1.detail.run.outcome || ""}`);
  const practice = done1.asked.find((q) => q.code === "ap_gr_practice" || /ใบรับของ/.test(q.question || ""));
  x.check(!!practice, "the one question: does the company issue goods receipts", practice && practice.question);
  x.check(practice && practice.recommended_label, "asked with a guess from the batch pre-filled", practice && `${practice.recommended_label}`);
  x.check(done1.asked.length === 1, "nothing else is asked about the sample month", done1.asked.map((q) => q.code).join(", "));
  x.check(done1.run.status === "succeeded", "the round succeeds", `${done1.run.status} ${done1.run.status_reason || ""}`);
  const outs = page.locator('[data-testid="am-outputs"]:visible').last();
  await outs.waitFor({ state: "visible", timeout: 120000 });
  await outs.scrollIntoViewIfNeeded();
  await L.wait(1500);
  await x.shot("06-files-card");
  const counts = await x.words(outs.locator('[data-testid="am-po-counts"]'));
  const depth = await x.words(outs.locator('[data-testid="am-po-depth"]'));
  const twoWay = await x.words(outs.locator('[data-testid="am-po-two-way"]'));
  const noApproval = await x.words(outs.locator('[data-testid="am-po-no-approval"]'));
  x.check(counts.includes(String(EXPECT_PO.passed)) && counts.includes(EXPECT_PO.value_passed), "three invoices pass, with their value", counts);
  x.check(/3 ทาง\D*5/.test(depth) && /2 ทาง\D*1/.test(depth), "the depth line: five lines three-way, one two-way", depth);
  x.check(twoWay.length > 0, "the two-way warning is under the depth line", twoWay);
  x.check(noApproval.length > 0, "the card says the agent approved nothing", noApproval);
  const paper = await download(x, done1.detail, "xlsx", "po_paper.xlsx");
  const pc = paper ? checkMatchPaper(paper) : {};
  fs.writeFileSync(path.join(x.dir, "paper.json"), JSON.stringify(pc, null, 1));
  x.check(pc.three_way === EXPECT_PO.three_way && pc.two_way === EXPECT_PO.two_way && pc.lines === 6, "every line of the working paper says three-way or two-way", JSON.stringify({ lines: pc.lines, three: pc.three_way, two: pc.two_way }));
  x.check(pc.every_line_says_why, "and why");
  x.check(pc.depth_on_face && pc.two_way_warning_on_face, "the face says the depth and what a two-way line does not prove", pc.depth_on_face);
  x.check(pc.approval_on_face && /ไม่ได้อนุมัติ/.test(pc.approval_on_face), "the face says nothing was approved for payment", pc.approval_on_face);
  const total = Number(((Date.now() - t1) / 1000).toFixed(1));
  const clicksToPaper = x.clicks;
  x.mark("working paper in hand");
  // A. the receipt offers (optional; the sample month has every receipt, so nothing is confirmed)
  const rec = outs.locator('[data-testid="am-receipts"]');
  if (await rec.count()) {
    await x.click(rec.locator("button").first(), "ยืนยันการรับของ (ไม่บังคับ)");
    await L.wait(2000);
    await x.shot("07-receipts");
    x.check(true, "the receipt offers open", `${await rec.locator('[data-testid="am-receipt-row"]').count()} line(s) offered`);
  } else {
    x.check(true, "no receipt to offer: every line of the sample month has its receipt");
  }
  // C. the e-mail draft
  await x.click(outs.getByRole("button", { name: "ร่างอีเมล" }), "ร่างอีเมล");
  const card = page.locator('[data-testid="am-email-draft"]').last();
  await card.waitFor({ state: "visible", timeout: 60000 });
  await card.scrollIntoViewIfNeeded();
  await L.wait(1200);
  await x.shot("08-email-draft");
  const subject = await x.words(card.locator('[data-testid="am-email-subject"]'));
  const body = await x.words(card.locator('[data-testid="am-email-body"]'));
  const cannot = await x.words(card.locator('[data-testid="am-email-cannot-send"]'));
  const toValues = await card.locator("input").evaluateAll((els) => els.map((e) => e.value));
  x.check(subject.length > 0 && body.length > 0, "a subject and a short Thai summary with the figures", `${subject} · ${body.slice(0, 160)}`);
  x.check(!/@|https?:|www\./i.test(body), "no address and no link in the draft", body.slice(0, 120));
  x.check(toValues.every((v) => !v), "no recipient is filled in", JSON.stringify(toValues));
  x.check(cannot.length > 0 || (await card.getByRole("button", { name: /ส่ง/ }).count()) > 0, "sending says what it needs (no mail connector: copy it instead)", cannot);
  await x.click(card.getByRole("button", { name: /คัดลอก/ }), "คัดลอก");
  // B. the install's settings: declared settings, reminders
  await page.goto(`${WEB()}/chat/agents/i/${installId}/settings`, { waitUntil: "load" });
  x.clicks += 1;
  await page.waitForSelector('[data-testid="am-reminders"]', { timeout: 60000 });
  await L.wait(1500);
  const declared = await page.locator('[data-testid="am-declared-row"]').count();
  x.check(declared >= 10, "the agent's own settings, each named in words", `${declared} row(s)`);
  await x.shot("09-settings");
  const note = await x.words(page.locator('[data-testid="am-reminders-note"]'));
  x.check(/ไม่มีอะไรทำงานเอง/.test(note), "it says nothing runs without a person", note);
  const soon = thaiSoon(3);
  const form = page.locator('[data-testid="am-reminder-form"]');
  await form.locator("input").first().fill(TAG + " ตรวจใบแจ้งหนี้");
  await form.locator("select").first().selectOption("week");
  await form.locator("select").nth(1).selectOption(String(soon.weekday));
  await form.locator('input[type="time"]').fill(soon.time);
  x.clicks += 4;
  await x.click(form.getByRole("button", { name: "เพิ่มการเตือน" }), `เพิ่มการเตือน (ทุกสัปดาห์ ${soon.time})`);
  const row = page.locator('[data-testid="am-reminder"]').last();
  await row.waitFor({ state: "visible", timeout: 30000 });
  await L.wait(800);
  await x.shot("10-reminder");
  const reminders = (await api(page, `/installs/${installId}/reminders`)).body?.reminders || [];
  const rem = reminders[reminders.length - 1];
  if (rem) state.reminders = [...new Set([...(state.reminders || []), rem.id])];
  save();
  x.check(!!rem && rem.status === "active", "the reminder is kept, with its rule in words", rem && `${rem.rule_text} · ${rem.next_due_text}`);
  // the notification: the server opens a round that waits for files; the page shows it
  x.mark("waiting for the reminder to fall due");
  const note1 = await waitFor(async () => {
    const n = page.locator('[data-testid="am-notification"]');
    return (await n.count()) ? n.first() : null;
  }, 6 * 60_000, 3000);
  x.check(!!note1, "the reminder's notification reaches the page");
  if (!note1) throw new Error("no notification in six minutes");
  await L.wait(1000);
  await x.shot("11-notification");
  const ntext = await x.words(note1);
  x.check(/ถึงเวลา/.test(ntext) && /ไฟล์/.test(ntext), "it says what is due and which files it needs", ntext);
  const now = ((await api(page, `/installs/${installId}/reminders`)).body?.reminders || []).find((r) => rem && r.id === rem.id);
  const waitingRun = now && now.waiting_run_id;
  if (client === "desktop") {
    // the app's own poller shows the same notification as a Windows notification (AM9: a notification stays
    // listed for every surface until somebody acts on it, so the toast is read from the app's own log)
    const shown = await waitFor(async () => logSince(new Date(Date.now() - 8 * 60_000).toISOString())
      .some((l) => /AGENT NOTIFY: \d+ notification\(s\) shown/.test(l)), 150_000, 5000);
    x.check(!!shown, "the desktop app raised it as a Windows notification (its log says so)");
  }
  if (waitingRun) state.runs = [...new Set([...(state.runs || []), waitingRun])];
  save();
  const wr = waitingRun ? (await api(page, `/runs/${waitingRun}`)).body : null;
  x.check(wr && wr.run.status === "awaiting_input" && (wr.clarifications || []).some((c) => c.kind === "request_files" && c.status === "open"),
    "the round waits for files; nothing ran", wr && `${wr.run.status} · steps ${(wr.steps || []).length}`);
  await x.click(note1.getByRole("button", { name: "เปิด" }), "เปิด");
  await page.waitForURL(/\/chat\/agents\/i\//, { timeout: 60000 });
  const req = page.locator('[data-testid="am-request-files"]').last();
  await req.waitFor({ state: "visible", timeout: 60000 });
  await req.scrollIntoViewIfNeeded();
  await L.wait(1500);
  await x.shot("12-waiting-round");
  x.clicks += 3;
  await req.locator('input[type="file"]').first().setInputFiles(buffersOf(SAMPLE));
  const done2 = waitingRun ? await finish(x, waitingRun, 13) : null;
  await L.wait(1500);
  x.check(done2 && done2.run.status === "succeeded" && done2.asked.length === 0, "the files go on the round the reminder opened; it runs and asks nothing again",
    done2 && `${done2.run.status} · ${done2.asked.length} question(s)`);
  await L.wait(2000);
  await x.shot("13-reminder-round-done");
  // nothing technical in the thread
  const tt = await threadText(x);
  x.check(!TECH.test(tt), "nothing technical anywhere in the workspace");
  const res = { client, web_build: x.webBuild, install: installId, reminder: rem, runs: state.runs, seconds_from_market_to_paper: total, clicks_to_paper: clicksToPaper,
    clicks: x.clicks, marks: x.marks, checks: x.checks, paper: pc, model_menu: menu, suggestions: sugg, estimate: est0, email: { subject, body, cannot }, notification: ntext };
  fs.writeFileSync(path.join(x.dir, "journey.json"), JSON.stringify(res, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail · ${total} s and ${clicksToPaper} clicks from the market to the working paper`);
  await x.s.close();
}

// ── E and F with the bank reconciliation ─────────────────────────────────
async function bank(client) {
  const x = await session(client, "bank");
  const { page } = x;
  const { installId } = await installFromMarket(x, "กระทบยอด");
  state[`bank_${client}`] = installId;
  save();
  await page.waitForSelector('[data-testid="am-empty-pick"]', { timeout: 60000 });
  // E. a statement as a PDF
  const r1 = await sendFiles(x, installId, BANK_PDF, TAG + " กระทบยอดเงินฝากธนาคาร ก.ย. 2569 (Statement เป็น PDF)", 3);
  const d1 = await finish(x, r1.runId, 3);
  x.mark(`round 1: ${d1.run.status}`);
  x.check(d1.run.status === "succeeded" && d1.asked.length === 0, "a statement as a PDF: it ties and asks nothing", `${d1.run.status} · ${d1.asked.length} question(s)`);
  const o1 = page.locator('[data-testid="am-outputs"]:visible').last();
  await o1.waitFor({ state: "visible", timeout: 120000 });
  await o1.scrollIntoViewIfNeeded();
  await L.wait(1200);
  await x.shot("04-pdf-ties");
  x.check(/1,538,519\.65/.test(await x.words(o1.locator('[data-testid="am-outputs-kpis"]'))), "the adjusted balances from the PDF, to the satang");
  // F. a month that does not tie: finished, open — not an error
  const r2 = await sendFiles(x, installId, BANK_OPEN, TAG + " กระทบยอดเงินฝากธนาคาร ก.ย. 2569 (รายการเดินบัญชีขาดหน้า)", 5);
  const d2 = await finish(x, r2.runId, 5);
  x.mark(`round 2: ${d2.run.status} ${d2.detail.run.outcome || ""}`);
  x.check(d2.detail.run.outcome === "completed_open", "a month that does not tie is finished and open, not failed", `${d2.run.status} · ${d2.detail.run.outcome}`);
  const open = page.locator('[data-testid="am-round-open"]:visible').last();
  await open.waitFor({ state: "visible", timeout: 120000 });
  await open.scrollIntoViewIfNeeded();
  await L.wait(1500);
  await x.shot("06-finished-open");
  const otext = await x.words(open);
  x.check(/เสร็จแล้ว · ยังไม่ลงตัว/.test(otext), "it reads “เสร็จแล้ว · ยังไม่ลงตัว”", otext.slice(0, 160));
  x.check(/21,435\.00/.test(otext), "and says how far off it is", otext.slice(0, 200));
  x.check(!(await page.locator(`[data-testid="am-failed-round"]:visible`).count()), "no failed-round card for it");
  x.check(!(await open.getByRole("button", { name: /ลองใหม่/ }).count()), "and no “try the same files again”");
  const tt = await threadText(x);
  x.check(!TECH.test(tt), "nothing technical anywhere in the workspace");
  const res = { client, install: installId, runs: [r1.runId, r2.runId], checks: x.checks, marks: x.marks, finished_open: otext };
  fs.writeFileSync(path.join(x.dir, "bank.json"), JSON.stringify(res, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  await x.s.close();
}

// a reminder's round that still waits for its files: open it from its link and send them on its card
async function waiting(client) {
  const x = await session(client, "waiting");
  const { page } = x;
  const installId = state[`po_${client}`];
  await page.goto(`${WEB()}/chat/agents/i/${installId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
  const rems = (await api(page, `/installs/${installId}/reminders`)).body?.reminders || [];
  const rem = rems.find((r) => r.waiting_run_id);
  if (!rem) throw new Error("no reminder of this install has a waiting round");
  const runId = rem.waiting_run_id;
  state.runs = [...new Set([...(state.runs || []), runId])];
  save();
  const before = (await api(page, `/runs/${runId}`)).body;
  x.check(before.run.status === "awaiting_input" && !(before.steps || []).length, "the reminder's round still waits; nothing has run", `${before.run.status} · steps ${(before.steps || []).length}`);
  // the link as a notification carries it: the first thread is named "main"
  await page.goto(`${WEB()}/chat/agents/i/${installId}?thread=${before.run.thread_id || "main"}&run=${runId}`, { waitUntil: "load" });
  const req = page.locator('[data-testid="am-request-files"]').last();
  await req.waitFor({ state: "visible", timeout: 60000 });
  await req.scrollIntoViewIfNeeded();
  await L.wait(1500);
  await x.shot("12-waiting-round");
  x.clicks += 3;
  await req.locator('input[type="file"]').first().setInputFiles(buffersOf(SAMPLE));
  const done = await finish(x, runId, 13);
  x.check(done.run.status === "succeeded" && done.asked.length === 0, "the files go on the round the reminder opened; it runs and asks nothing again", `${done.run.status} · ${done.asked.length} question(s)`);
  await L.wait(2000);
  const outs = page.locator('[data-testid="am-outputs"]:visible').last();
  await outs.scrollIntoViewIfNeeded().catch(() => null);
  await L.wait(1000);
  await x.shot("13-reminder-round-done");
  const credits = (await api(page, `/runs/${runId}`)).body?.run?.credits_spent;
  x.check(Number(credits) > 0, "charged once it delivered", String(credits));
  const tt = await threadText(x);
  x.check(!TECH.test(tt), "nothing technical anywhere in the workspace");
  fs.writeFileSync(path.join(x.dir, "waiting.json"), JSON.stringify({ client, install: installId, run: runId, checks: x.checks }, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  await x.s.close();
}

// ── AM8: the financial report on the live model, and a customer's form the model maps ──────────
const FIN = ["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"].map((n) => ({
  name: n, src: path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report", "sample-data", n) }));
const FORM = path.join(L.ROOT, "scripts", "probes", "AM8", "fixtures", "customer_form.docx");

/** The report's sentences read back out of the .docx: each figure, the citation after it, and the
 *  appendix's line naming the model that wrote the words. */
function readReport(file) {
  const py = [
    "import json, sys, re",
    "from docx import Document",
    "d = Document(sys.argv[1])",
    "pairs, text = [], []",
    "for p in d.paragraphs:",
    "    runs = [(r.text, bool(r.font.superscript)) for r in p.runs]",
    "    for i, (t, sup) in enumerate(runs):",
    "        if sup and i:",
    "            m = re.search(r'([-+(]?[0-9][0-9,.]*%?(?: ล้าน)?)\\s*$', runs[i - 1][0])",
    "            pairs.append([m.group(1) if m else runs[i - 1][0][-12:], t.strip()])",
    "    text.append(p.text)",
    "about = [t for t in text if 'โมเดลที่อ่านตัวเลข' in t or 'เขียนจากกฎ' in t]",
    "print(json.dumps({'pairs': pairs, 'about': about, 'braces': [t for t in text if '{' in t][:3]}, ensure_ascii=False))",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON || "python", ["-c", py, file], { encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } }));
}

// A person who already has the agent installs another copy the way the product offers it: the install
// wizard's "ติดตั้งอีกชุด". The copy is this walk's (recorded for cleanup); the person's own install is
// never opened, changed or removed.
async function installAnother(x, slug, cardText) {
  const { page } = x;
  const before = new Set(((await api(page, "/installs")).body?.installs || []).map((i) => i.id));
  await x.click(page.locator('a[href="/chat/agents"]'), "Agent Market");
  await page.waitForSelector('[data-testid="am-market-grid"]', { timeout: 60000 });
  await L.wait(1200);
  const card = page.locator('[data-testid="am-agent-card"]').filter({ hasText: cardText });
  x.check(await card.count(), `“${cardText}” is an agent of the market (installed already)`);
  await x.shot("01-market");
  await page.goto(`${WEB()}/chat/agents/install/${slug}`, { waitUntil: "load" });
  // AM11: the button reads in the screen's language
  const again = page.getByRole("button", { name: /^(ติดตั้งอีกชุด|Install another copy)$/ });
  await again.waitFor({ state: "visible", timeout: 60000 });
  await L.wait(800);
  await x.shot("02-already-installed");
  await x.click(again, "ติดตั้งอีกชุด");
  let id = null;
  for (let i = 0; i < 60 && !id; i++) {
    await L.wait(1000);
    const rows = (await api(page, "/installs")).body?.installs || [];
    id = rows.find((r) => r.slug === slug && !before.has(r.id))?.id || null;
  }
  if (!id) throw new Error("the second install was not created");
  state.installs = [...new Set([...(state.installs || []), id])];
  save();
  x.mark("another copy installed");
  return id;
}

async function financial(client) {
  const x = await session(client, "financial");
  const { page } = x;
  const slug = "sran.financial-report";
  await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  await page.locator('a[href="/chat/agents"]').first().waitFor({ state: "visible", timeout: 90000 });
  await L.wait(1200);
  const had = ((await api(page, "/installs")).body?.installs || []).some((i) => i.slug === slug);
  const installId = had ? await installAnother(x, slug, "รายงานผลประกอบการ")
    : (await installFromMarket(x, "รายงานผลประกอบการ")).installId;
  state[`fin_${client}`] = installId;
  save();
  // B. the customer's form: uploaded on the install's settings page, the model proposes, the person saves
  await page.goto(`${WEB()}/chat/agents/i/${installId}/settings`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-template"]', { timeout: 60000 });
  await L.wait(1500);
  x.clicks += 2;
  await page.locator('[data-testid="am-template-file"]').setInputFiles([{ name: "แบบฟอร์มลูกค้า.docx",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: fs.readFileSync(FORM) }]);
  const mapper = page.locator('[data-testid="am-template-mapper"]');
  await mapper.waitFor({ state: "visible", timeout: 200000 });
  await L.wait(1500);
  await x.shot("01-form-mapper");
  const by = await x.words(page.locator('[data-testid="am-template-proposed-by"]'));
  x.check(/โมเดล .+ เสนอการจับคู่นี้/.test(by), "the mapping screen says a model proposed the mapping", by);
  const selects = await page.locator('[data-testid="am-template-mapper"] select').evaluateAll((els) =>
    els.map((e) => ({ aria: e.getAttribute("aria-label"), value: e.value })));
  const chosen = selects.filter((s) => s.value);
  x.check(chosen.length >= 4, "the model's proposal fills most slots of a form named in its own words", JSON.stringify(selects));
  state.fin_mapping = selects;
  save();
  await x.click(page.locator('[data-testid="am-template-save"]'), "บันทึกการจับคู่");
  await page.waitForSelector('[data-testid="am-template-saved"]', { timeout: 60000 });
  x.mark("form saved");
  // A + C. a round on the live model
  await page.goto(`${WEB()}/chat/agents/i/${installId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
  await L.wait(2000);
  const r1 = await sendFiles(x, installId, FIN, TAG + " รายงานผลประกอบการเดือนกันยายน 2569", 2);
  const d1 = await finish(x, r1.runId, 2);
  x.mark(`round: ${d1.run.status}`);
  x.check(d1.run.status === "succeeded", "the round succeeds", `${d1.run.status} · ${d1.asked.length} question(s)`);
  const o1 = page.locator('[data-testid="am-outputs"]:visible').last();
  await o1.waitFor({ state: "visible", timeout: 120000 });
  await o1.scrollIntoViewIfNeeded();
  if (!(await page.locator('[data-testid="am-outputs-details-body"]:visible').count())) {
    await x.click(o1.locator('[data-testid="am-outputs-details"]'), "รายละเอียดการตรวจ");
  }
  await L.wait(1200);
  await x.shot("03-files-card");
  const otext = await x.words(o1);
  x.check(/เปิดไฟล์ตรวจซ้ำ: ตัวเลข [\d,]+ ค่าตรงกับที่คำนวณ|เปิดไฟล์ตรวจซ้ำ/.test(otext), "the quality line says what was asserted, not a count of checks", otext.match(/เปิดไฟล์ตรวจซ้ำ[^·]*/)?.[0] || otext.slice(0, 160));
  const tpl = o1.locator('[data-testid="am-artifact"][data-format="template"]');
  x.check(await tpl.count(), "the customer's form is delivered on the card");
  const note = await x.words(tpl.locator('[data-testid="am-artifact-not-passed"]'));
  x.check(/ยังกรอกไม่ครบ \d+ ช่อง/.test(note) && /PreparedBy/.test(note), "a form with a slot the agent has no value for says so on its row, naming it", note);
  const detail = (await api(page, `/runs/${r1.runId}`)).body;
  const model = detail?.run?.model_used || "";
  x.check(!!model, "a model wrote the words (not the rules' fallback)", model);
  const docx = await download(x, detail, "docx", "report.docx");
  if (docx) {
    const rep = readReport(docx);
    state.fin_report = rep;
    save();
    x.check(rep.pairs.length >= 2, "figures in the sentences carry citations", JSON.stringify(rep.pairs.slice(0, 6)));
    x.check(!rep.braces.length, "no figure key left unprinted", JSON.stringify(rep.braces));
    x.check(rep.about.some((t) => t.includes(model.split("/").pop())), "the report names the model that wrote its words", JSON.stringify(rep.about));
  }
  // F. the formats menu offers what this agent makes
  await x.click(o1.locator('[data-testid="am-more-files"]'), "ขอไฟล์เพิ่ม");
  await L.wait(800);
  const menu = await x.words(page.locator('[role="menu"]').last());
  await x.shot("04-more-files");
  await page.keyboard.press("Escape");
  x.check(/PDF/.test(menu), "the report's extra-formats menu offers its PDF", menu);
  const tt = await threadText(x);
  x.check(!TECH.test(tt), "nothing technical anywhere in the workspace");
  const res = { client, install: installId, run: r1.runId, model, checks: x.checks, marks: x.marks };
  fs.writeFileSync(path.join(x.dir, "financial.json"), JSON.stringify(res, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  await x.s.close();
}

// ── AM9: the Mermaid canvas, a reminder on every surface, both levels of egress ─────────────────

/** A test chat of the card's tag with memory off, made the way the smoke makes one. */
async function testChat(page, title) {
  return page.evaluate(async ({ edge, title }) => {
    const h = { "Content-Type": "application/json" };
    const c = await (await fetch(`${edge}/api/v1/conversations`, { method: "POST", credentials: "include", headers: h, body: JSON.stringify({ model_id: "auto", title }) })).json();
    const p = await (await fetch(`${edge}/api/v1/conversations/${c.id}`, { method: "PATCH", credentials: "include", headers: h,
      body: JSON.stringify({ title, memory_enabled: false }) })).json();
    return { id: c.id, memory: p.memory_enabled };
  }, { edge: EDGE(), title });
}

// H. a real diagram from the model, opened in Canvas: it fits, zooms, pans and comes back to fit
async function canvas(client) {
  const x = await session(client, "canvas");
  const { page } = x;
  await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 90000 });
  const conv = await testChat(page, `${TAG} canvas`);
  state.chats = [...new Set([...(state.chats || []), conv.id])];
  save();
  x.check(conv.id && conv.memory === false, "a test chat of the card, memory off", conv.id);
  await page.goto(`${WEB()}/chat/${conv.id}`, { waitUntil: "load" });
  const box = page.locator('textarea[data-shortcut="composer"]');
  await box.waitFor({ state: "visible", timeout: 90000 });
  await L.wait(1500);
  await box.fill(`${TAG} ตอบเป็นโค้ดบล็อก mermaid อย่างเดียว ไม่ต้องอธิบาย: flowchart LR ของขั้นตอนปิดบัญชีสิ้นเดือน 24 ขั้น ` +
    "ตั้งชื่อแต่ละขั้นเป็นภาษาไทยสั้น ๆ เรียงต่อกันเป็นเส้นเดียว");
  await box.press("Enter");
  const open = page.getByRole("button", { name: /เปิดใน Canvas|Open in Canvas/ }).last();
  await open.waitFor({ state: "visible", timeout: 240000 });
  x.mark("the reply has a Mermaid block");
  await L.wait(2500);
  await open.click();
  const el = page.locator('iframe[data-testid="canvas-frame"]').last();
  await el.waitFor({ state: "visible", timeout: 60000 });
  const frame = await (await el.elementHandle()).contentFrame();
  await frame.locator("#view svg").waitFor({ state: "visible", timeout: 60000 });
  const zoom = async () => Number(await frame.locator("#stage").getAttribute("data-zoom"));
  await waitFor(async () => (await zoom()) > 0, 30000, 300);
  await L.wait(800);
  const fit = await zoom();
  const fb = await el.boundingBox();
  const sb = await frame.locator("#view svg").boundingBox();
  x.check(fit > 0 && sb && fb && sb.x >= fb.x - 1 && sb.x + sb.width <= fb.x + fb.width + 1,
    "on open the whole diagram fits the pane, its level shown", `${fit}% · ${await x.words(frame.locator("#zoom-level"))}`);
  await x.shot("01-canvas-fit");
  await frame.locator("#zoom-in").click();
  x.check((await zoom()) > fit, "the + button zooms in", `${await zoom()}%`);
  await page.mouse.move(fb.x + fb.width / 2, fb.y + fb.height / 2);
  await page.mouse.wheel(0, -500);
  await L.wait(500);
  const wheeled = await zoom();
  x.check(wheeled > fit, "the wheel zooms (around the pointer)", `${wheeled}%`);
  const before = await frame.locator("#view").evaluate((v) => v.style.transform);
  await page.mouse.down();
  await page.mouse.move(fb.x + fb.width / 2 - 180, fb.y + fb.height / 2 - 40, { steps: 8 });
  await page.mouse.up();
  const after = await frame.locator("#view").evaluate((v) => v.style.transform);
  x.check(after !== before, "a drag pans", after);
  await x.shot("02-canvas-zoomed");
  await frame.locator("#stage").focus();
  await page.keyboard.press("0");
  x.check((await zoom()) === fit, "0 returns to the whole diagram", `${await zoom()}%`);
  await page.keyboard.press("+");
  x.check((await zoom()) > fit, "+ zooms in while the canvas has focus", `${await zoom()}%`);
  await page.keyboard.press("-");
  await page.keyboard.press("-");
  x.check((await zoom()) < fit + 1, "- zooms out", `${await zoom()}%`);
  await frame.locator("#zoom-fit").click();
  x.check((await zoom()) === fit, "the fit button returns to fit", `${await zoom()}%`);
  const del = await page.evaluate(async ({ edge, id }) => (await fetch(`${edge}/api/v1/conversations/${id}`, { method: "DELETE", credentials: "include" })).status,
    { edge: EDGE(), id: conv.id });
  x.check(del === 204 || del === 200, "the test chat is deleted", String(del));
  if (del === 204 || del === 200) {
    state.chats = (state.chats || []).filter((c) => c !== conv.id);
    save();
  }
  fs.writeFileSync(path.join(x.dir, "canvas.json"), JSON.stringify({ client, checks: x.checks, marks: x.marks }, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  await x.s.close();
}

// G. one reminder, the desktop app (its page and its Windows notification) and a browser of the same
// person open at once: every surface shows it; closing it in one ends it in the other
async function surfaces() {
  const d = await session("desktop", "surfaces");
  const w = await session("web", "surfaces");
  const since = new Date().toISOString();
  for (const x of [d, w]) {
    await x.page.goto(`${WEB()}/chat`, { waitUntil: "load" });
    await x.page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 90000 });
  }
  const installs = (await api(w.page, "/installs")).body?.installs || [];
  const inst = installs.filter((i) => (state.installs || []).includes(i.id)).pop();
  if (!inst) throw new Error("no install of this walk to set a reminder on (run journey first)");
  const soon = thaiSoon(2);
  const made = await api(w.page, `/installs/${inst.id}/reminders`, { method: "POST",
    body: JSON.stringify({ label: TAG + " สองหน้าจอ", rule: { every: "week", weekday: soon.weekday, time: soon.time } }) });
  const rem = made.body?.reminder;
  if (rem) state.reminders = [...new Set([...(state.reminders || []), rem.id])];
  save();
  w.check(!!rem, "a reminder that falls due in two minutes", rem && `${rem.rule_text}`);
  const seen = {};
  const banner = (x) => x.page.locator('[data-testid="am-notification"]').filter({ hasText: "สองหน้าจอ" });
  await waitFor(async () => {
    for (const [k, x] of [["desktop", d], ["web", w]]) {
      if (!seen[k] && await banner(x).count()) seen[k] = new Date().toISOString();
      if (!seen[k]) await x.page.evaluate(() => window.dispatchEvent(new Event("focus"))).catch(() => {});
    }
    return seen.desktop && seen.web;
  }, 6 * 60_000, 5000);
  d.check(!!seen.desktop, "the desktop app's page shows it", seen.desktop || "never");
  w.check(!!seen.web, "the browser shows it too — the desktop app's page did not take it", seen.web || "never");
  await d.shot("01-desktop-banner");
  await w.shot("01-web-banner");
  const toast = await waitFor(async () => logSince(since).some((l) => /AGENT NOTIFY: \d+ notification\(s\) shown/.test(l)), 120_000, 5000);
  d.check(!!toast, "and the desktop app raised it as a Windows notification");
  const cur = ((await api(w.page, `/installs/${inst.id}/reminders`)).body?.reminders || []).find((r) => rem && r.id === rem.id);
  if (cur && cur.waiting_run_id) state.runs = [...new Set([...(state.runs || []), cur.waiting_run_id])];
  save();
  // closed in the browser: gone from the desktop app's page at its next read
  await w.click(banner(w).getByRole("button", { name: "ปิด" }), "ปิด");
  const gone = await waitFor(async () => {
    await d.page.evaluate(() => window.dispatchEvent(new Event("focus"))).catch(() => {});
    return (await banner(d).count()) === 0;
  }, 90_000, 5000);
  d.check(!!gone, "closed in the browser, it is gone from the desktop app's page");
  for (const x of [d, w]) {
    fs.writeFileSync(path.join(x.dir, "surfaces.json"), JSON.stringify({ checks: x.checks, seen }, null, 1));
    const bad = x.checks.filter((c) => !c.ok);
    log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  }
  await w.s.close();
  await d.s.close();
}

// D. where a person picks the agent's model, both levels of egress are shown
async function egress(client) {
  const x = await session(client, "egress");
  const { page } = x;
  await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 90000 });
  const installs = (await api(page, "/installs")).body?.installs || [];
  const inst = installs.filter((i) => (state.installs || []).includes(i.id)).pop();
  if (!inst) throw new Error("no install of this walk (run journey first)");
  await page.goto(`${WEB()}/chat/agents/i/${inst.id}/settings`, { waitUntil: "load" });
  const line = page.locator('[data-testid="am-model-egress"]');
  await line.first().waitFor({ state: "visible", timeout: 60000 });
  await L.wait(800);
  const text = await x.words(line);
  x.check(/องค์กร:/.test(text) && /agent นี้:/.test(text), "the settings page shows the organisation's and the agent's own answer", text);
  const m = (await api(page, `/installs/${inst.id}/models`)).body;
  x.check(m && m.egress && typeof m.egress.org === "boolean" && m.egress.accounting === true && m.egress.agent_default === true,
    "an accounting agent nobody has set: its own answer is its default", JSON.stringify(m && m.egress));
  x.check(m && m.egress && m.egress.effective === false, "so its documents stay in, whatever the organisation allows", JSON.stringify(m && m.egress));
  await x.shot("01-settings-egress");
  fs.writeFileSync(path.join(x.dir, "egress.json"), JSON.stringify({ client, checks: x.checks, egress: m && m.egress }, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  await x.s.close();
}

const phases = {
  async canvas() { await canvas(process.argv[3] || "desktop"); },
  async surfaces() { await surfaces(); },
  async egress() { await egress(process.argv[3] || "desktop"); },
  async financial() { await financial(process.argv[3] || "desktop"); },
  async journey() { await journey(process.argv[3] || "desktop"); },
  async waiting() { await waiting(process.argv[3] || "desktop"); },
  async bank() { await bank(process.argv[3] || "desktop"); },
  async open() {
    const wasRunning = running();
    state.wasRunning = wasRunning;
    if (wasRunning && !(await portOpen(PORT))) {
      const busy = logSince(new Date(Date.now() - 10 * 60_000).toISOString()).filter((l) => /turn|stream|message|tool|composer|send/i.test(l) && !SESSION_LINE.test(l));
      if (busy.length) throw new Error(`the app was used in the last 10 minutes (${busy.length} lines); not closing it`);
      kill();
      await waitFor(async () => !running(), 15000);
    }
    if (process.env.AM6_INSTALL === "0") {
      log("installer not run (AM6_INSTALL=0): the app that is installed is relaunched");
    } else {
      if (!fs.existsSync(SETUP)) throw new Error(`no installer at ${SETUP}`);
      const r = spawnSync(SETUP, ["/S"], { encoding: "utf8", timeout: 300000 });
      log(`installer ${VERSION} /S exit ${r.status}`);
    }
    await L.wait(3000);
    kill();
    await waitFor(async () => !running(), 15000);
    launch([`--remote-debugging-port=${PORT}`]);
    const { page, close } = await connectDesktop();
    await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 90000 });
    const cfg = desktopCfg();
    const me = await page.evaluate(async (gw) => (await (await fetch(`${gw}/portal/me`, { credentials: "include" })).json().catch(() => ({}))), cfg.gatewayUrl);
    const email = me?.email || me?.user?.email || null;
    state.email_masked = email ? email.replace(/^(.).*(@.*)$/, "$1***$2") : null;
    state.desktop_version = (logSince("0000").filter((l) => /SRAN Desktop \S+ pid=/.test(l)).pop() || "").slice(24, 90).trim();
    log(`desktop up: ${state.desktop_version}; signed in: ${state.email_masked}; pageUrl ${cfg.pageUrl}`);
    const avail = await api(page, "/availability");
    log(`availability ${avail.status} available=${avail.body?.available}`);
    save();
    await close();
  },
  async cleanup() {
    const s = L.TARGET === "live" && process.argv[3] !== "web" ? await connectDesktop() : await openWeb(process.env.AM6_EMAIL || L.TS_USER.email);
    if (!s.page.url().startsWith(WEB())) await s.page.goto(`${WEB()}/chat`, { waitUntil: "load" });
    for (const id of state.reminders || []) {
      const r = await api(s.page, `/reminders/${id}`, { method: "DELETE" });
      log(`reminder ${id} removed: ${r.status}`);
    }
    for (const id of state.runs || []) {
      // AM7: the chat app has no admin route any more — the admin door (scripts/probes/AM7/admin_door.js)
      const r = await require("../AM7/admin_door.js").removeEvidence(id, TAG + " รอบทดสอบของการเดินตรวจ AM6 ไม่ใช่งานจริง");
      log(`evidence of round ${id} removed: ${r.status}`);
    }
    for (const id of state.chats || []) {
      const st = await s.page.evaluate(async ({ edge, id }) => (await fetch(`${edge}/api/v1/conversations/${id}`, { method: "DELETE", credentials: "include" })).status,
        { edge: EDGE(), id }).catch(() => 0);
      log(`test chat ${id} deleted: ${st}`);
    }
    state.chats = [];
    for (const id of state.installs || []) {
      const r = await api(s.page, `/installs/${id}`, { method: "DELETE" });
      log(`removed install ${id}: ${r.status}`);
    }
    state.removed = { installs: state.installs, runs: state.runs, reminders: state.reminders };
    state.installs = [];
    state.runs = [];
    state.reminders = [];
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

if (require.main !== module) {
  // AM10: a later card's walk drives the same two clients with these
  module.exports = { L, state, save, log, waitFor, WEB, EDGE, pw, api, fetchFile, capture, session, installFromMarket, installAnother,
    sendFiles, finish, download, testChat, connectDesktop, openWeb, phases, buffersOf, FIN, FORM, MIME, TAG, OUT, TERMINAL };
} else {
  const cmd = process.argv[2];
  if (!phases[cmd]) {
    console.log(`usage: walkthrough.js ${Object.keys(phases).join(" | ")}`);
    process.exit(2);
  }
  phases[cmd]().catch((e) => {
    log(`ERROR ${String((e && e.stack) || e).slice(0, 600)}`);
    process.exit(1);
  });
}
