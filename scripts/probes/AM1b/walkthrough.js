#!/usr/bin/env node
// AM1b §5 — the whole journey on LIVE, in the owner's desktop app (CDP 9333),
// clicking the real UI: install → self-test → the agent's chat → upload the
// nasty fixture → answer the card by clicking → files → "ขอ Excel ด้วย" →
// run 2: close the app mid-run, reopen, upload the missing file, resume →
// run 3: cancel twice. Screens + files + a JSON log in results/walk/.
//
//   SMOKE_TARGET=live node scripts/probes/AM1b/walkthrough.js <phase>
//   phases: open | install | run1 | excel | run2 | run3 | close
//
// Safety (CARD-RULES §4): the app is only closed when its log shows no use in
// the last 10 minutes; no chat is created (the agent's thread is not a chat);
// every run is tagged [AM1b]; the app is relaunched without the debugging
// port by `close`.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const crypto = require("node:crypto");
const { spawn, spawnSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

const PORT = 9333;
const EXE = path.join(process.env.LOCALAPPDATA || "", "Programs", "sran-desktop", "SRAN Desktop.exe");
const LOGF = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "logs", "shell.log");
const PROCS = ["SRAN Desktop.exe", "SranDesktop.ToolsSidecar.exe", "SranDesktop.BrowserHost.exe"];
const OUT = path.join(__dirname, "results", "walk");
const STATE = path.join(OUT, "state.json");
const NASTY = path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report", "evals", "nasty");
const SLUG = "sran.financial-report";
fs.mkdirSync(OUT, { recursive: true });
const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, "utf8")) : { log: [] };
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
  for (const f of [`${LOGF}.2`, `${LOGF}.1`, LOGF]) {
    try { out.push(...fs.readFileSync(f, "utf8").split(/\r?\n/).filter((l) => l.slice(0, 24) >= sinceIso)); } catch { /* not rotated */ }
  }
  return out;
}
const SESSION_LINE = /BOOT:|SIGN-IN|SIGNED IN|TICKET|VIEW: signed|SESSION (RESTORED|ROTATED|EXPIRED)|RE-?AUTH|UPDATE CHECK|PAGE STATE|BRIDGE INBOUND: host\.query/;
const cfg = JSON.parse(fs.readFileSync(path.join(path.dirname(EXE), "resources", "desktop.config.json"), "utf8"));
const WEB = (cfg.pageUrl || "https://localhost:3002/chat").replace(/\/chat.*$/, "");
const EDGE = cfg.chatEdgeUrl || "https://localhost:4000";

async function connect() {
  const { chromium } = require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));
  if (!(await waitFor(() => portOpen(PORT), 45000))) throw new Error("CDP port did not open");
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`, { timeout: 20000 });
  const ctx = browser.contexts()[0];
  const page = await waitFor(async () => ctx.pages().find((p) => p.url().startsWith(WEB)) || null, 40000);
  if (!page) throw new Error("no signed-in page over CDP");
  return { browser, page };
}
async function api(page, p, init = {}) {
  return page.evaluate(async ([url, init]) => {
    const r = await fetch(url, { credentials: "include", ...init, headers: { "Content-Type": "application/json", ...(init.headers || {}) } });
    let body = null;
    try { body = await r.json(); } catch { /* none */ }
    return { status: r.status, body };
  }, [`${EDGE}/api/v1/agent-market${p}`, init]);
}
async function shot(page, name, el) {
  const f = path.join(OUT, name);
  await (el || page).screenshot({ path: f, ...(el ? {} : { fullPage: false }) });
  log("shot", name);
}
async function download(page, art, dir) {
  const b64 = await page.evaluate(async (url) => {
    const r = await fetch(url, { credentials: "include" });
    const buf = new Uint8Array(await r.arrayBuffer());
    let s = "";
    for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return btoa(s);
  }, `${EDGE}/api/v1/agent-market/artifacts/${art.id}/download`);
  const buf = Buffer.from(b64, "base64");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, art.name), buf);
  return { name: art.name, size: buf.length, sha_ok: crypto.createHash("sha256").update(buf).digest("hex") === art.sha256, quality: art.validation?.passed };
}
async function waitRun(page, runId, pred, ms = 600000) {
  let last = "";
  return waitFor(async () => {
    const r = await api(page, `/runs/${runId}`);
    const d = r.body;
    if (!d || !d.run) return null;
    const s = `${d.run.status}${d.run.status_reason ? "/" + d.run.status_reason : ""}`;
    if (s !== last) { log(`run ${runId.slice(0, 8)} ${s}`); last = s; }
    return pred(d) ? d : null;
  }, ms, 1000);
}
async function newestRun(page, after) {
  return waitFor(async () => {
    const r = await api(page, `/installs/${state.install}/runs`);
    const runs = (r.body?.runs || []).filter((x) => x.kind === "run" && !after.includes(x.id));
    return runs[0] || null;
  }, 60000, 1000);
}
async function answerCardByClicking(page, cardEl, tag, questions) {
  await cardEl.scrollIntoViewIfNeeded();
  await L.wait(600);
  await shot(page, `${tag}-card.png`, cardEl);
  const toggle = cardEl.locator('[data-testid="evidence-toggle"]').first();
  if (await toggle.count()) { await toggle.click(); await L.wait(400); await shot(page, `${tag}-card-evidence.png`, cardEl); }
  const items = cardEl.locator('[data-testid="question-item"]');
  const picked = [];
  for (let i = 0; i < await items.count(); i++) {
    const it = items.nth(i);
    // click the option the card recommends (its label, from the API), as a person following the advice would
    const want = (questions[i]?.default_label || "").trim();
    const opts = it.locator('[data-testid="question-option"]');
    let o = opts.first();
    for (let k = 0; k < await opts.count(); k++) {
      if (want && (await opts.nth(k).innerText()).includes(want)) { o = opts.nth(k); break; }
    }
    picked.push((await it.innerText()).split("\n")[0].slice(0, 60) + " → " + (await o.innerText()).replace(/\s+/g, " ").slice(0, 60));
    await o.click();
    await L.wait(250);
  }
  await cardEl.locator('[data-testid="question-submit"]').click();
  log(`${tag}: clicked ${picked.length} answer(s): ${picked.join(" | ")}`);
  return picked;
}

// ── phases ──────────────────────────────────────────────────────────────
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
    // §4.1 the latest build (byte-identical to main; installed anyway)
    const setup = path.join(L.ROOT, "apps", "desktop", "release", "SRAN Desktop Setup 0.1.0-e58.exe");
    if (fs.existsSync(setup) && !(await portOpen(PORT))) {
      const r = spawnSync(setup, ["/S"], { encoding: "utf8", timeout: 240000 });
      log(`installer /S exit ${r.status}`);
      await waitFor(async () => !running(), 15000); // a one-click NSIS may start the app: close it for the CDP launch
      kill();
    }
    if (!(await portOpen(PORT))) launch([`--remote-debugging-port=${PORT}`]);
    const { browser, page } = await connect();
    await page.waitForSelector('textarea[data-shortcut="composer"]', { timeout: 60000 });
    const me = await page.evaluate(async (gw) => (await (await fetch(`${gw}/portal/me`, { credentials: "include" })).json().catch(() => ({}))), cfg.gatewayUrl);
    state.email = me?.email || me?.user?.email || null;
    const ver = (logSince("0000").filter((l) => /SRAN Desktop \S+ pid=/.test(l)).pop() || "").slice(24, 90);
    log(`desktop up: ${ver.trim()}; signed in as ${state.email ? state.email.replace(/^(.).*(@.*)$/, "$1***$2") : "?"}`);
    const avail = await api(page, "/availability");
    log(`availability ${avail.status} ${JSON.stringify(avail.body)}`);
    await page.goto(`${WEB}/chat`, { waitUntil: "load" });
    await L.wait(3000);
    await shot(page, "01-chat-with-rail.png");
    await browser.close();
  },

  async install(opts = {}) {
    const sample = opts.sample || "GL_Express_2569-09.csv";
    const company = opts.company === undefined ? "[AM1b] บริษัท ตัวอย่างเทรดดิ้ง จำกัด" : opts.company;
    const tag = opts.tag || "";
    const { browser, page } = await connect();
    await page.goto(`${WEB}/chat/agents`, { waitUntil: "load" });
    await L.wait(3000);
    await shot(page, "02-market.png");
    await page.goto(`${WEB}/chat/agents/install/${SLUG}`, { waitUntil: "load" });
    const src = page.locator('[data-testid="am-step-source"]');
    await src.waitFor({ timeout: 60000 });
    await src.locator('input[type="file"]').setInputFiles(path.join(NASTY, sample));
    await page.waitForSelector('[data-testid="am-preview-table"]', { timeout: 180000 }).catch(() => null);
    await L.wait(1500);
    await shot(page, "03-install-source.png");
    await page.locator('[data-testid="am-source-next"]').click();
    await page.locator('[data-testid="am-step-mapping"]').waitFor({ timeout: 60000 });
    await L.wait(1000);
    await shot(page, "04-install-mapping.png");
    await page.locator('[data-testid="am-mapping-save"]').click();
    const theme = page.locator('[data-testid="am-step-theme"]');
    await theme.waitFor({ timeout: 60000 });
    if (company) await theme.locator("input").first().fill(company);
    await L.wait(500);
    await shot(page, "05-install-theme.png");
    await page.locator('[data-testid="am-theme-save"]').click();
    await page.locator('[data-testid="am-step-formats"]').waitFor({ timeout: 60000 });
    await shot(page, "06-install-formats.png");
    await page.locator('[data-testid="am-formats-save"]').click();
    await page.locator('[data-testid="am-step-selftest"]').waitFor({ timeout: 60000 });
    const t0 = Date.now();
    await page.locator('[data-testid="am-selftest-start"]').click();
    const done = await waitFor(async () => (await page.locator('[data-testid="am-selftest-thread"] [data-testid="am-outputs"]').count()) > 0, 300000, 1000);
    log(`self-test ${done ? "delivered files" : "did NOT finish"} after ${((Date.now() - t0) / 1000).toFixed(0)} s`);
    await L.wait(1500);
    await shot(page, `07${tag}-selftest.png`);
    const inst = await api(page, "/installs");
    const mine = (inst.body?.installs || []).filter((i) => i.slug === SLUG).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    state[opts.stateKey || "install"] = mine.id;
    log(`install ${mine.id} (self-test run ${mine.self_test_run_id || "?"})`);
    await page.locator('[data-testid="am-selftest-enter"]').click();
    await page.waitForSelector('[data-testid="am-composer-input"]', { timeout: 60000 });
    await L.wait(2000);
    await shot(page, `08${tag}-workspace.png`);
    await browser.close();
  },

  // the owner's own install, left ready after the walkthrough's test data is
  // deleted: the clean sample for the column mapping, no test company name
  async fresh() {
    await phases.install({ sample: "GL_clean_2569-09.xlsx", company: "", tag: "-owner", stateKey: "owner_install" });
  },

  async run1() {
    const { browser, page } = await connect();
    await page.goto(`${WEB}/chat/agents/i/${state.install}`, { waitUntil: "load" });
    await page.waitForSelector('[data-testid="am-composer-input"]', { timeout: 60000 });
    const before = ((await api(page, `/installs/${state.install}/runs`)).body?.runs || []).map((r) => r.id);
    const known = await page.locator("[data-card-id]").evaluateAll((els) => els.map((e) => e.getAttribute("data-card-id")));
    await page.setInputFiles('[data-testid="am-composer-file"]', ["GL_Express_2569-09.csv", "GL_Express_2569-08.xlsx", "งบประมาณ_2569.xlsx"].map((f) => path.join(NASTY, f)));
    await page.waitForSelector('[data-testid="am-staged-chip"]', { timeout: 60000 });
    await page.fill('[data-testid="am-composer-input"]', "[AM1b] ปิดงบเดือนกันยายน 2569 เทียบเดือนก่อนและงบประมาณ");
    const t0 = Date.now();
    await page.click('[data-testid="am-composer-send"]');
    const run = await newestRun(page, before);
    state.run1 = run.id;
    log(`run1 ${run.id} started`);
    const answered = new Set(known);
    let n = 0;
    for (;;) {
      const d = (await api(page, `/runs/${run.id}`)).body;
      if (["succeeded", "failed", "cancelled"].includes(d.run.status)) break;
      const open = (d.clarifications || []).filter((c) => c.status === "open" && !answered.has(c.id));
      for (const c of open) {
        const el = page.locator(`[data-testid="am-question-card"][data-card-id="${c.id}"]`);
        if (!(await el.count())) continue;
        answered.add(c.id);
        n++;
        state.run1_cards = [...(state.run1_cards || []), { kind: c.kind, keys: c.questions.map((q) => q.key) }];
        await answerCardByClicking(page, el, `09-run1-card${n}`, c.questions);
      }
      if (Date.now() - t0 > 900000) throw new Error("run1 timeout");
      await L.wait(1500);
    }
    const d = await waitRun(page, run.id, (x) => ["succeeded", "failed", "cancelled"].includes(x.run.status));
    state.run1_seconds = (Date.now() - t0) / 1000;
    log(`run1 ${d.run.status} in ${state.run1_seconds.toFixed(1)} s, credits ${Math.round(d.run.credits_spent)}, model ${d.run.model_used}`);
    await L.wait(2500);
    const outputs = page.locator('[data-testid="am-outputs"]').last();
    await outputs.scrollIntoViewIfNeeded();
    await shot(page, "10-run1-outputs.png", outputs);
    await shot(page, "10b-run1-thread.png");
    state.run1_files = [];
    for (const a of d.artifacts || []) state.run1_files.push(await download(page, a, path.join(OUT, "files", "run1")));
    log(`run1 files: ${state.run1_files.map((f) => `${f.name} ${f.size}B sha=${f.sha_ok ? "ok" : "BAD"} gate=${f.quality ? "pass" : "FAIL"}`).join(", ")}`);
    state.run1_computed = (await api(page, `/runs/${run.id}/computed`)).body;
    await page.goto(`${WEB}/chat/agents/runs/${run.id}`, { waitUntil: "load" });
    await L.wait(3500);
    await shot(page, "11-run1-provenance.png");
    await browser.close();
  },

  async excel() {
    const { browser, page } = await connect();
    await page.goto(`${WEB}/chat/agents/i/${state.install}`, { waitUntil: "load" });
    await page.waitForSelector('[data-testid="am-composer-input"]', { timeout: 60000 });
    const steps0 = ((await api(page, `/runs/${state.run1}`)).body.steps || []).filter((s) => s.name === "compute").length;
    await page.fill('[data-testid="am-composer-input"]', "[AM1b] ขอ Excel ด้วยครับ");
    await page.click('[data-testid="am-composer-send"]');
    const d = await waitRun(page, state.run1, (x) => (x.artifacts || []).some((a) => a.format === "xlsx"), 300000);
    const steps1 = (d.steps || []).filter((s) => s.name === "compute").length;
    const xl = d.artifacts.filter((a) => a.format === "xlsx").sort((a, b) => b.version - a.version)[0];
    state.excel = await download(page, xl, path.join(OUT, "files", "run1"));
    log(`excel: ${xl.name} v${xl.version}; compute steps before=${steps0} after=${steps1} (must be equal); gate=${xl.validation?.passed ? "pass" : "FAIL"}`);
    await L.wait(2000);
    await shot(page, "12-excel-thread.png");
    await browser.close();
  },

  async run2() {
    // an upload that needs the budget: the run parks on a request for it
    let { browser, page } = await connect();
    await page.goto(`${WEB}/chat/agents/i/${state.install}`, { waitUntil: "load" });
    await page.waitForSelector('[data-testid="am-composer-input"]', { timeout: 60000 });
    const before = ((await api(page, `/installs/${state.install}/runs`)).body?.runs || []).map((r) => r.id);
    await page.setInputFiles('[data-testid="am-composer-file"]', [path.join(NASTY, "GL_clean_2569-09.xlsx")]);
    await page.waitForSelector('[data-testid="am-staged-chip"]', { timeout: 60000 });
    await page.fill('[data-testid="am-composer-input"]', "[AM1b] ปิดงบเดือนกันยายน 2569 เทียบกับงบประมาณด้วย");
    await page.click('[data-testid="am-composer-send"]');
    const run = await newestRun(page, before);
    state.run2 = run.id;
    log(`run2 ${run.id} started`);
    const parked = await waitRun(page, run.id, (x) => x.run.status === "awaiting_input" && (x.clarifications || []).some((c) => c.kind === "request_files" && c.status === "open"), 300000);
    if (!parked) throw new Error("run2 never asked for the missing file");
    await L.wait(1500);
    await shot(page, "13-run2-request-files.png");
    // close the app mid-run
    await browser.close();
    kill();
    await waitFor(async () => !running() && !(await portOpen(PORT)), 20000);
    log("run2: app closed while the run waits for the file");
    await L.wait(5000);
    launch([`--remote-debugging-port=${PORT}`]);
    ({ browser, page } = await connect());
    await page.goto(`${WEB}/chat/agents/i/${state.install}`, { waitUntil: "load" });
    const req = page.locator('[data-testid="am-request-files"]').last();
    await req.waitFor({ timeout: 60000 });
    await L.wait(1500);
    await shot(page, "14-run2-after-reopen.png");
    const still = (await api(page, `/runs/${run.id}`)).body;
    log(`run2 after reopen: ${still.run.status}, request card still open: ${(still.clarifications || []).some((c) => c.kind === "request_files" && c.status === "open")}`);
    await req.locator('input[type="file"]').setInputFiles(path.join(NASTY, "งบประมาณ_2569.xlsx"));
    const t0 = Date.now();
    const d = await waitRun(page, run.id, (x) => ["succeeded", "failed", "cancelled"].includes(x.run.status), 600000);
    const runs = ((await api(page, `/installs/${state.install}/runs`)).body?.runs || []).filter((r) => r.kind === "run" && !before.includes(r.id));
    log(`run2 ${d.run.status} ${((Date.now() - t0) / 1000).toFixed(0)} s after the upload; runs started by this phase: ${runs.length} (must be 1: resumed, not restarted)`);
    state.run2_status = d.run.status;
    state.run2_new_runs = runs.length;
    await L.wait(2500);
    await shot(page, "15-run2-done.png");
    await browser.close();
  },

  async run3() {
    const { browser, page } = await connect();
    await page.goto(`${WEB}/chat/agents/i/${state.install}`, { waitUntil: "load" });
    await page.waitForSelector('[data-testid="am-composer-input"]', { timeout: 60000 });
    const before = ((await api(page, `/installs/${state.install}/runs`)).body?.runs || []).map((r) => r.id);
    await page.setInputFiles('[data-testid="am-composer-file"]', [path.join(NASTY, "GL_Express_2569-09.csv")]);
    await page.waitForSelector('[data-testid="am-staged-chip"]', { timeout: 60000 });
    await page.fill('[data-testid="am-composer-input"]', "[AM1b] ปิดงบเดือนกันยายน 2569 (รอบที่จะยกเลิก)");
    await page.click('[data-testid="am-composer-send"]');
    const run = await newestRun(page, before);
    state.run3 = run.id;
    const cancel = page.locator('[data-testid="am-progress-cancel"]').last();
    await cancel.waitFor({ timeout: 60000 });
    await cancel.click();
    await L.wait(300);
    const second = await cancel.isVisible().catch(() => false) ? await cancel.click().then(() => "clicked twice").catch((e) => `second click: ${e.message.slice(0, 60)}`) : "button gone after the first click";
    const a = await api(page, `/runs/${run.id}/cancel`, { method: "POST", body: "{}" }); // and a third time through the API
    const d = await waitRun(page, run.id, (x) => ["cancelled", "succeeded", "failed"].includes(x.run.status), 120000);
    log(`run3 ${run.id.slice(0, 8)}: ${second}; API cancel → ${a.status} ${a.body?.run?.status}; final ${d.run.status}`);
    state.run3_status = d.run.status;
    await L.wait(1500);
    await shot(page, "16-run3-cancelled.png");
    await browser.close();
  },

  async close() {
    try { const { browser } = await connect(); await browser.close(); } catch { /* not attached */ }
    kill();
    const gone = await waitFor(async () => !(await portOpen(PORT)) && !running(), 20000);
    launch([]);
    await L.wait(8000);
    log(`app closed (9333 ${gone ? "closed" : "STILL OPEN"}), relaunched without the debugging port; port now ${await portOpen(PORT) ? "OPEN" : "closed"}`);
  },
};

const phase = process.argv[2];
if (!phases[phase]) { console.log(`usage: walkthrough.js ${Object.keys(phases).join("|")}`); process.exit(2); }
phases[phase]().then(() => save(), (e) => { log(`FAIL ${phase}: ${e.message}`); process.exit(1); });
