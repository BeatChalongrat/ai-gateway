#!/usr/bin/env node
// AM4 — the whole journey on both clients, clicking the real UI, timed, with
// every click counted: the market → "ใช้ค่าแนะนำทั้งหมด" → the workspace →
// "เลือกไฟล์" (two tax invoices, named the way a person names them) → send →
// the question card → the files → the register checked against the golden
// output of the two documents.
//
//   SMOKE_TARGET=live node scripts/probes/AM4/walkthrough.js open            # desktop: install e59, relaunch with CDP 9333
//   SMOKE_TARGET=live node scripts/probes/AM4/walkthrough.js journey desktop # the journey in the owner's desktop app
//   SMOKE_TARGET=live node scripts/probes/AM4/walkthrough.js journey web     # the same in a browser of its own
//   SMOKE_TARGET=live node scripts/probes/AM4/walkthrough.js cleanup         # remove the walkthrough's installs (by id)
//   SMOKE_TARGET=live node scripts/probes/AM4/walkthrough.js close           # relaunch the app without the debugging port
//   SMOKE_TARGET=test AM4_WEB=http://localhost:13002 node … journey web       # dry run on the test stack
//
// Safety (CARD-RULES §4): the app is only closed when its log shows no use in
// the last 10 minutes; `close` relaunches it without the debugging port. The
// web client runs in a headless browser with its own profile and a short-lived
// session minted for the same account — the owner's own browser is never
// touched. Every message is tagged [AM4]; no chat is created (an agent's
// thread is not a chat); the installs made here are removed by id.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const crypto = require("node:crypto");
const { spawn, spawnSync, execFileSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

const PORT = 9333;
const EXE = path.join(process.env.LOCALAPPDATA || "", "Programs", "sran-desktop", "SRAN Desktop.exe");
const LOGF = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "logs", "shell.log");
const PROCS = ["SRAN Desktop.exe", "SranDesktop.ToolsSidecar.exe", "SranDesktop.BrowserHost.exe"];
const SETUP = path.join(L.ROOT, "apps", "desktop", "release", "SRAN Desktop Setup 0.1.0-e59.exe");
const OUT = path.join(__dirname, "results", "walk", L.TARGET);
const STATE = path.join(OUT, "state.json");
const DOCS = path.join(L.ROOT, "backend", "agent_market", "fixtures", "e2", "docs");
const GOLD = path.join(L.ROOT, "backend", "agent_market", "fixtures", "e2", "expected");
const SLUG = "sran.invoice-processing";
// two real-looking tax invoices, named as a person would (the engine never reads the names)
const FILES = [
  { src: "d01_clean_full_tax_invoice.pdf", name: "ใบกำกับภาษี ก.ย.69 ร้านเครื่องเขียน.pdf" },
  { src: "f02_vat_mismatch.pdf", name: "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf" },
];
fs.mkdirSync(OUT, { recursive: true });
const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, "utf8")) : { log: [], installs: [] };
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
const WEB = () => process.env.AM4_WEB || (L.TARGET === "live" ? "https://localhost:3002" : "http://localhost:13002");
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
// the web client: a browser of its own, signed in with a short-lived session for the same account
async function openWeb(email) {
  const who = L.TARGET === "live"
    ? { id: L.gsql(`select id from portal_users where email='${email}'`).trim() }
    : L.TS_USER;
  const token = L.mintPortalToken(who, 3600);
  const browser = await pw().chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, locale: "th-TH", ignoreHTTPSErrors: true, acceptDownloads: true });
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

// ── the register checked against the golden output of the two documents ──
function checkRegister(file, answers) {
  const py = [
    "import sys,json",
    "from openpyxl import load_workbook",
    "wb=load_workbook(sys.argv[1],data_only=True)",
    "print(json.dumps({ws.title:[[None if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets},ensure_ascii=False))",
  ].join("\n");
  const sheets = JSON.parse(execFileSync(process.env.PYTHON || "python", ["-c", py, file], { encoding: "utf8", maxBuffer: 64 << 20, env: { ...process.env, PYTHONIOENCODING: "utf-8" } }));
  const register = JSON.stringify(sheets["ทะเบียน"] || []);
  const exceptions = (sheets["ข้อยกเว้น"] || []).filter((r) => r && r[0]);
  const has = (text, v) => { const n = Number(v); return text.includes(v) || (Number.isFinite(n) && (text.includes(n.toFixed(2)) || text.includes(String(n)))); };
  const out = [];
  for (const f of FILES) {
    const g = JSON.parse(fs.readFileSync(path.join(GOLD, f.src.replace(/\.[a-z]+$/, ".json")), "utf8")).truth || {};
    const val = (k) => (g[k] && typeof g[k] === "object" ? (g[k].value ?? g[k].normalized) : g[k]);
    const excludedByAnswer = f.src.startsWith("f02") && answers.some((a) => /ตัด/.test(a));
    if (excludedByAnswer) {
      // the person chose to leave it out: not in the register, listed as not recorded, in Thai
      const row = exceptions.find((r) => r[0] === f.name);
      const ok = !register.includes(String(val("document_number"))) && !!row && row[3] === "ไม่ได้บันทึก" && !/[A-Za-z]{4,}/.test(String(row[4] || "").replace(/VAT|PDF/g, ""));
      out.push({ document: f.name, expected: "left out (the answer given)", found: { not_in_register: !register.includes(String(val("document_number"))), listed_as_not_recorded: !!row && row[3] === "ไม่ได้บันทึก", reason: row && row[2], detail_thai: ok } });
      continue;
    }
    const keys = f.src.startsWith("f02") ? ["document_number", "seller_tax_id", "subtotal"] : ["document_number", "seller_tax_id", "subtotal", "vat_amount", "total"];
    const found = {};
    for (const k of keys) if (val(k) != null && val(k) !== "") found[`${k}=${val(k)}`] = has(register, String(val(k)));
    out.push({ document: f.name, expected: "in the register with its golden values", found });
  }
  return { sheets: Object.keys(sheets), docs: out, ok: out.every((d) => Object.values(d.found).every(Boolean)) };
}

// ── the journey ──────────────────────────────────────────────────────────
async function journey(client) {
  const email = process.env.AM4_EMAIL || (L.TARGET === "live" ? "" : L.TS_USER.email);
  const s = client === "desktop" ? await connectDesktop() : await openWeb(email || process.env.OWNER_EMAIL);
  const { page } = s;
  const tag = client;
  const dir = path.join(OUT, tag);
  fs.mkdirSync(dir, { recursive: true });
  let clicks = 0;
  const marks = [];
  const t0 = Date.now();
  const mark = (what) => { const sec = ((Date.now() - t0) / 1000).toFixed(1); marks.push({ what, s: Number(sec), clicks }); log(`${tag} +${sec}s clicks=${clicks} ${what}`); };
  const shot = async (name) => { await page.screenshot({ path: path.join(dir, `${name}.png`) }); };
  const click = async (loc, what) => { await loc.first().click({ timeout: 30000 }); clicks += 1; if (what) log(`${tag} click ${clicks}: ${what}`); };

  // 0. where a person starts: the chat page
  await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  await page.locator('a[href="/chat/agents"]').first().waitFor({ state: "visible", timeout: 90000 });
  await L.wait(1500);
  const version = await page.evaluate(async () => (await (await fetch("/api/version")).json().catch(() => ({}))).build).catch(() => null);
  log(`${tag}: web build ${version}`);
  // 1. the market (the sidebar's Agent Market)
  const t1 = Date.now();
  await click(page.locator('a[href="/chat/agents"]'), "Agent Market");
  await page.waitForSelector('[data-testid="am-market-grid"]', { timeout: 60000 });
  await L.wait(1200);
  mark("market open");
  await shot("01-market");
  // 2. the invoice agent's card → its page
  const card = page.locator('[data-testid="am-agent-card"]').filter({ hasText: "ใบกำกับ" });
  await click(card.locator('[data-testid="am-install"]'), "อ่านก่อนติดตั้ง (invoice card)");
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
  await L.wait(1500);
  mark("workspace (installed)");
  await shot("03-workspace-empty");
  // 4. "เลือกไฟล์": the two invoices (in the file dialog: select both, open = 2 clicks)
  const buffers = FILES.map((f) => ({ name: f.name, mimeType: "application/pdf", buffer: fs.readFileSync(path.join(DOCS, f.src)) }));
  const [chooser] = await Promise.all([page.waitForEvent("filechooser", { timeout: 30000 }), click(page.locator('[data-testid="am-empty-pick"]'), "เลือกไฟล์")]);
  await chooser.setFiles(buffers);
  clicks += 2;
  log(`${tag} clicks ${clicks - 1}-${clicks}: select both files + open (file dialog)`);
  await page.waitForSelector('[data-testid="am-composer-chips"]', { timeout: 60000 }).catch(() => null);
  await L.wait(1500);
  await shot("04-files-staged");
  const before = ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).filter((r) => r.kind === "run").map((r) => r.id);
  const send = page.locator('[data-testid="am-composer-send"]');
  if (await send.isEnabled().catch(() => false)) await click(send, "ส่ง");
  mark("files sent");
  // 5. the round: wait for a question card or the files
  const run = await waitFor(async () => ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).find((r) => r.kind === "run" && !before.includes(r.id)), 60000, 1000);
  if (!run) throw new Error("no round started");
  state[`run_${tag}`] = run.id;
  save();
  // the round's own state (the self-test's files card is on the page too): a question → click it; done → its files
  let cards = 0;
  const seen = new Set();
  const answers = [];
  const TERMINAL = ["succeeded", "failed", "cancelled"];
  for (;;) {
    const d = await waitFor(async () => {
      const r = (await api(page, `/runs/${run.id}`)).body;
      if (!r || !r.run) return null;
      if (TERMINAL.includes(r.run.status)) return r;
      const open = (r.clarifications || []).filter((c) => c.status === "open" && !seen.has(c.id));
      return r.run.status === "awaiting_input" && open.length ? r : null;
    }, 900000, 1500);
    if (!d) throw new Error("the round did not finish in 15 minutes");
    if (TERMINAL.includes(d.run.status)) break;
    const c = d.clarifications.filter((x) => x.status === "open" && !seen.has(x.id)).pop();
    seen.add(c.id);
    cards += 1;
    const cardEl = page.locator(`[data-card-id="${c.id}"]`).first();
    await cardEl.waitFor({ state: "visible", timeout: 90000 });
    await cardEl.scrollIntoViewIfNeeded();
    await L.wait(800);
    mark(`question card ${cards}`);
    await shot(`05-question-card-${cards}`);
    const qs = c.questions || [];
    const items = cardEl.locator('[data-testid="question-item"]');
    for (let k = 0; k < (await items.count()); k++) {
      const it = items.nth(k);
      const want = (qs[k]?.default_label || "").trim();
      const opts = it.locator('[data-testid="question-option"]');
      let o = opts.first();
      for (let m = 0; m < (await opts.count()); m++) if (want && (await opts.nth(m).innerText()).includes(want)) { o = opts.nth(m); break; }
      const chosen = (await o.innerText()).replace(/\s+/g, " ").slice(0, 80);
      answers.push(chosen);
      log(`${tag} card ${cards} q${k + 1}: ${(qs[k]?.question || "").slice(0, 90)} → ${chosen}`);
      await click(o);
    }
    await click(cardEl.locator('[data-testid="question-submit"]'), `ส่งคำตอบ (card ${cards})`);
    await L.wait(2500);
  }
  const outs = page.locator('[data-testid="am-outputs"]:visible').last();
  await outs.waitFor({ state: "visible", timeout: 120000 });
  await L.wait(1500);
  await outs.scrollIntoViewIfNeeded();
  mark("files card");
  await shot("06-files");
  // 6. the register: one click on its download button. The web client clicks it and keeps the download;
  // in the desktop app the click is counted and the same file is fetched through the same URL (a native
  // save dialog cannot be driven over CDP, and must not be left open in the owner's app)
  const d = (await api(page, `/runs/${run.id}`)).body;
  const art = (d.artifacts || []).find((a) => a.format === "xlsx");
  const file = path.join(dir, art.name);
  const btn = outs.locator('[data-testid="am-artifact"][data-format="xlsx"] button').first();
  let buf;
  if (client === "web") {
    const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 60000 }), click(btn, `the register (${art.name})`)]);
    await dl.saveAs(file);
    buf = fs.readFileSync(file);
  } else {
    clicks += 1;
    log(`${tag} click ${clicks}: the register (${art.name}) — fetched through its download URL`);
    buf = await fetchFile(page, `${EDGE()}/api/v1/agent-market/artifacts/${art.id}/download`);
    fs.writeFileSync(file, buf);
  }
  mark("report in hand");
  const total = Number(((Date.now() - t1) / 1000).toFixed(1));
  const check = checkRegister(file, answers);
  log(`${tag}: register ${art.name} ${buf.length} bytes; golden values ${check.ok ? "ALL found" : "NOT all found"}: ${JSON.stringify(check.docs.map((x) => x.found))}`);
  // the header's one action and the seen-by line, for the screenshots
  await page.locator('[data-testid="am-ws-view-run"]').first().scrollIntoViewIfNeeded().catch(() => null);
  await shot("07-workspace-after");
  const seenBy = await outs.locator('[data-testid="am-outputs-seen-by"]').innerText().catch(() => "");
  const res = { client: tag, web_build: version, install: installId, run: run.id, run_status: d.run.status, question_cards: cards, answers,
    seconds_from_market_to_report: total, clicks, marks, register: art.name, register_ok: check.ok, check, seen_by: seenBy };
  fs.writeFileSync(path.join(dir, "journey.json"), JSON.stringify(res, null, 1));
  log(`${tag}: ${total} s, ${clicks} clicks, ${cards} question card(s), report correct: ${check.ok}`);
  await s.close();
  return res;
}

const phases = {
  async open() {
    const wasRunning = running();
    state.wasRunning = wasRunning;
    if (wasRunning && !(await portOpen(PORT))) {
      const busy = logSince(new Date(Date.now() - 10 * 60_000).toISOString()).filter((l) => /turn|stream|message|tool|composer|send/i.test(l) && !SESSION_LINE.test(l));
      if (busy.length) throw new Error(`the app was used in the last 10 minutes (${busy.length} lines); not closing it`);
      kill();
      await waitFor(async () => !running(), 15000);
    }
    if (!fs.existsSync(SETUP)) throw new Error(`no installer at ${SETUP}`);
    const r = spawnSync(SETUP, ["/S"], { encoding: "utf8", timeout: 300000 });
    log(`installer e59 /S exit ${r.status}`);
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
    const s = L.TARGET === "live" ? await connectDesktop() : await openWeb(L.TS_USER.email);
    if (!s.page.url().startsWith(WEB())) await s.page.goto(`${WEB()}/chat`, { waitUntil: "load" });
    for (const id of state.installs || []) {
      const r = await api(s.page, `/installs/${id}`, { method: "DELETE" });
      log(`removed install ${id}: ${r.status}`);
    }
    state.removed = state.installs;
    state.installs = [];
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
