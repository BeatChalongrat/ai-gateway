#!/usr/bin/env node
// AM13 — the other four agents do what they are asked, walked on the built clients by clicking the real UI
// (CARD-RULES §6b), with the walk's own account and its own installs. Each finished workbook is downloaded
// and opened again: the order is read from the file's own cells, not from what the thread says.
//
//   bank        bank reconciliation: "เรียงจากยอดมาก" with the files; then "เรียงตามวันที่" (the new order takes
//               the place of the old, the statement does not move); then "ตัดรายการเล็กออก" (refused, with why)
//   register    invoice processing: "เรียงตามผู้ขาย" with the files; "แยกชีตตามผู้ขาย" (refused, with why and
//               what to ask instead); then "เรียงจากยอดมาก"
//   po          invoice ↔ PO matching: "เรียงจากรายการที่ต่างมากที่สุด" — the three line sheets stay row for row;
//               "เอาเฉพาะบรรทัดที่ไม่ผ่าน" (refused)
//   buy         procurement (the test stack's stand-in web): a request with a budget, "เรียงจากถูกสุด" and
//               "เจ้าไหนดีที่สุด" — the comparison runs from the lowest price, nothing is marked as best, the
//               budget is never answered with a refusal, the verdict is refused with its reason
//   english     the bank agent on an English screen: what can be asked is said in English, and so is what was done
//   cleanup     the walk's installs, by id
//
//   SMOKE_TARGET=test node scripts/probes/AM13/walk.js bank
//   SMOKE_TARGET=live AM6_EMAIL="$QA_EMAIL" NODE_EXTRA_CA_CERTS=… node scripts/probes/AM13/walk.js bank
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

process.env.AM6_TAG = process.env.AM6_TAG || "[AM13]";
const TARGET = process.env.SMOKE_TARGET || "test";
process.env.AM6_OUT = process.env.AM6_OUT || path.join(__dirname, "results", "walk", TARGET);
const W = require("../AM6/walkthrough.js");
const { L, state, save, log, api, WEB, EDGE, waitFor, TAG } = W;

const PKG = path.join(L.ROOT, "backend", "agent_market", "packages");
const AGENT = {
  bank: { slug: "sran.bank-reconciliation", dir: "bank_reconciliation", card: { th: "กระทบยอด", en: "Bank Reconciliation" } },
  register: { slug: "sran.invoice-processing", dir: "invoice_processing", card: { th: "ทะเบียนเอกสารซื้อ", en: "Invoice Processing" } },
  po: { slug: "sran.invoice-po-matching", dir: "po_matching", card: { th: "ใบกำกับภาษีกับ PO", en: "Purchase Order Matching" } },
  buy: { slug: "sran.procurement", dir: "procurement", card: { th: "จัดซื้อ", en: "Procurement" } },
};
const THREAD = '[data-testid="am-thread"]';
const TERMINAL = ["succeeded", "failed", "cancelled"];
const MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];

const done = async (x, name, extra = {}) => {
  fs.writeFileSync(path.join(x.dir, `${name}.json`), JSON.stringify({ client: x.tag, at: new Date().toISOString(), ...extra, checks: x.checks, marks: x.marks }, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  await x.s.close();
  if (bad.length) process.exitCode = 1;
};
const home = async (page) => {
  await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  await page.locator('a[href="/chat/agents"]').first().waitFor({ state: "visible", timeout: 90000 });
  await L.wait(1200);
};
async function setLanguage(x, to) {
  const { page } = x;
  if ((await page.evaluate(() => document.documentElement.lang)) === to) return;
  const trigger = page.locator('div.border-t > button[aria-haspopup="menu"]').last();
  await x.click(trigger, "account menu");
  const menu = page.locator('[role="menu"]').filter({ has: page.getByRole("menuitem", { name: /Language|ภาษา/ }) });
  await x.click(menu.getByRole("menuitem", { name: /Language|ภาษา/ }), `language → ${to}`);
  await waitFor(async () => (await page.evaluate(() => document.documentElement.lang)) === to, 10000);
  await page.keyboard.press("Escape").catch(() => {});
}
async function ownInstall(x, which) {
  const { page } = x;
  const a = AGENT[which];
  const key = `am13_${which}`;
  const mine = (await api(page, "/installs")).body?.installs || [];
  if (state[key] && mine.some((i) => i.id === state[key] && i.status !== "removed")) return state[key];
  const lang = await page.evaluate(() => document.documentElement.lang);
  const card = a.card[lang] || a.card.th;
  const id = mine.some((i) => i.slug === a.slug && i.status !== "removed") ? await W.installAnother(x, a.slug, card) : (await W.installFromMarket(x, card)).installId;
  state[key] = id;
  state.installs = [...new Set([...(state.installs || []), id])];
  save();
  return id;
}
async function workspace(x, installId) {
  await x.page.goto(`${WEB()}/chat/agents/i/${installId}`, { waitUntil: "load" });
  await x.page.waitForSelector('[data-testid="composer-root"]', { timeout: 90000 });
  await L.wait(1500);
}
/** The agent's own sample files, under the names they have in its package. */
const sampleOf = (which) => {
  const dir = path.join(PKG, AGENT[which].dir);
  const st = JSON.parse(fs.readFileSync(path.join(dir, "self-test.json"), "utf8"));
  return st.files.map((rel) => ({ src: path.join(dir, rel), name: path.basename(rel) }));
};
/** Types words about the finished round's documents; answers "about this round" when the chip asks. */
async function sayAfter(x, text) {
  const { page } = x;
  await page.locator('[data-testid="am-composer-input"]').fill(text);
  await x.click(page.locator('[data-testid="am-composer-send"]'), `พิมพ์: ${text}`);
  await L.wait(2000);
  const about = page.locator('[data-testid="am-intent"][data-status="open"] [data-testid="am-intent-about"]');
  if (await about.count()) await x.click(about.last(), "เกี่ยวกับรอบนี้ (chip)");
}
const threadText = (page) => page.evaluate((sel) => (document.querySelector(sel) || document.body).innerText, THREAD);
const refusalsIn = (text) => (text.match(/ทำตามที่ขอไม่ได้เพราะ|cannot be done as asked because/g) || []).length;
const detailOf = async (page, runId) => (await api(page, `/runs/${runId}`)).body || {};
const versionOf = (d, fmt = "xlsx") => Math.max(0, ...(d.artifacts || []).filter((a) => a.format === fmt).map((a) => a.version));

/** Every sheet of a workbook as rows of cells (numbers as numbers), read by the host's openpyxl. */
function sheets(file) {
  const py = [
    "import sys,json",
    "from openpyxl import load_workbook",
    "wb=load_workbook(sys.argv[1],data_only=True)",
    "print(json.dumps({ws.title:[[c if isinstance(c,(int,float)) and not isinstance(c,bool) else (None if c is None else str(c)) for c in r] for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}))",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON || "python", ["-c", py, file], { encoding: "utf8", maxBuffer: 64 << 20 }));
}
/** A sheet's table: the row that holds every one of `heads` is the header; the rows under it that have a first cell. */
function tableOf(sheet, heads) {
  const rows = sheet || [];
  const at = rows.findIndex((r) => heads.every((h) => r.some((c) => typeof c === "string" && c.trim().startsWith(h))));
  if (at < 0) return { cols: {}, rows: [] };
  const cols = {};
  rows[at].forEach((c, i) => { if (typeof c === "string" && c.trim()) cols[c.trim()] = i; });
  const col = (h) => cols[Object.keys(cols).find((k) => k.startsWith(h))];
  return { cols, col, rows: rows.slice(at + 1).filter((r) => r[0] !== null && r[0] !== "") };
}
const runsDown = (v) => v.every((n, i) => i === 0 || v[i - 1] >= n);
const runsUp = (v) => v.every((n, i) => i === 0 || v[i - 1] <= n);
const thaiDate = (s) => {
  const b = String(s || "").trim().split(/\s+/);
  const m = MONTHS.indexOf(b[1]);
  return b.length === 3 && m >= 0 ? (Number(b[2]) - 543) * 10000 + (m + 1) * 100 + Number(b[0]) : null;
};
// a Thai name as a dictionary files it: a leading vowel after its consonant (as the package's own key does)
const thaiKey = (s) => String(s || "").trim().toLowerCase().replace(/([เแโใไ])([ก-ฮ])/g, "$2$1");

/** Waits for a newer version of the round's workbook, downloads it, and returns its sheets. */
async function nextVersion(x, runId, after, name) {
  const d = await waitFor(async () => { const r = await detailOf(x.page, runId); return versionOf(r) > after ? r : null; }, 420000, 2500);
  if (!d) return { d: await detailOf(x.page, runId), book: null };
  await L.wait(2500);
  const file = await W.download(x, d, "xlsx", name);
  return { d, book: file ? sheets(file) : null, file };
}
async function greeting(x, installId) {
  const manifest = ((await api(x.page, `/installs/${installId}`)).body || {}).manifest || {};
  const lang = await x.page.evaluate(() => document.documentElement.lang);
  const can = ((manifest.instructions || {}).can || []).map((d) => (lang === "en" ? (manifest.en || {})[d.label] : d.label) || d.label);
  const line = await x.words(x.page.locator('[data-testid="am-greeting-asks"]').first()).catch(() => "");
  x.check(can.length > 0 && can.every((c) => line.includes(c)), `the greeting says what can be asked of this agent's workbook (${lang})`, `v${manifest.version}: ${line.slice(0, 260)}`);
  return manifest;
}
async function refused(x, runId, text, reason, label) {
  const { page } = x;
  const before = (await detailOf(page, runId)).artifacts || [];
  const was = refusalsIn(await threadText(page));
  await sayAfter(x, text);
  const said = await waitFor(async () => { const t = await threadText(page); return refusalsIn(t) > was ? t : null; }, 240000, 2000);
  const line = said ? (said.match(/[^\n]*(?:ทำตามที่ขอไม่ได้เพราะ|cannot be done as asked because)[^\n]*/g) || []).pop() : "";
  x.check(!!line && reason.test(line), `“${text}” — refused, with its reason${label ? ` (${label})` : ""}`, line || "(nothing was said)");
  await L.wait(4000);
  const after = (await detailOf(page, runId)).artifacts || [];
  x.check(after.length === before.length, "… and no file was made for it", `${before.length} → ${after.length} files`);
}

// ── bank reconciliation ───────────────────────────────────────────────────────────────────────────────
async function bank(lang = "th") {
  const x = await W.session("web", lang === "en" ? "english" : "bank");
  const { page } = x;
  await home(page);
  await setLanguage(x, lang);
  const installId = await ownInstall(x, "bank");
  await workspace(x, installId);
  const manifest = await greeting(x, installId);
  await x.shot("01-greeting");
  const first = lang === "en" ? "Reconcile September, order the items from the largest amount" : "กระทบยอดเดือนกันยายน เรียงจากยอดมาก";
  const { runId } = await W.sendFiles(x, installId, sampleOf("bank"), `${TAG} ${first}`, "02");
  const end = await W.finish(x, runId, "02");
  x.check(end.run.status === "succeeded", "the round delivers", `${end.run.status} ${end.run.status_reason || ""} · ${end.asked.length} question(s) answered`);
  await L.wait(2500);
  const d1 = await detailOf(page, runId);
  const file1 = await W.download(x, d1, "xlsx", `bank-${lang}_v1.xlsx`);
  const b1 = file1 ? sheets(file1) : {};
  const t1 = tableOf(b1["รายการกระทบยอด"], ["ลำดับ", "วันที่", "จำนวนเงิน"]);
  const amounts = t1.rows.map((r) => Math.abs(Number(r[t1.col("จำนวนเงิน")])));
  x.check(amounts.length > 3 && runsDown(amounts) && amounts[0] > amounts[amounts.length - 1],
    `“${first}” — the reconciling items run from the largest amount down (read from the .xlsx)`, `${amounts.length} items: ${amounts.join(" ≥ ")}`);
  let thread = await threadText(page);
  const doneRe = lang === "en" ? /Done as asked: Reconciling items ordered from the largest amount/ : /ทำตามที่ขอแล้ว: เรียงรายการกระทบยอดจากจำนวนเงินมากที่สุด/;
  x.check(doneRe.test(thread), `the thread says what was done as asked (${lang})`, (thread.match(/(?:ทำตามที่ขอแล้ว|Done as asked)[^\n]*/) || [""])[0].slice(0, 240));
  x.check(d1.run && d1.run.model_used == null, "the run still says no model saw its data — the model that read the words saw only the words", `model_used=${d1.run && d1.run.model_used}`);
  const step = (d1.steps || []).find((s) => s.name === "instructions");
  x.check(!!step, "the reading of the words is a step of the run", step ? `read by ${step.model_id || (step.output || {}).model || "?"}` : (d1.steps || []).map((s) => s.name).join(" → "));
  await x.shot("03-by-amount");
  if (lang === "en") return done(x, "english", { installId, runId, manifest_version: manifest.version, amounts });

  // another order, afterwards: it takes the place of the first, and the statement does not move
  const was = refusalsIn(thread);
  await sayAfter(x, "เรียงตามวันที่");
  const { book: b2 } = await nextVersion(x, runId, versionOf(d1), "bank-th_v2.xlsx");
  const t2 = tableOf((b2 || {})["รายการกระทบยอด"], ["ลำดับ", "วันที่", "จำนวนเงิน"]);
  const dates = t2.rows.map((r) => thaiDate(r[t2.col("วันที่")]));
  x.check(dates.length === amounts.length && !dates.includes(null) && runsUp(dates) && dates[0] < dates[dates.length - 1],
    "“เรียงตามวันที่” — version 2 runs from the oldest date (read from the .xlsx)", t2.rows.map((r) => r[t2.col("วันที่")]).join(" · "));
  const a2 = t2.rows.map((r) => Math.abs(Number(r[t2.col("จำนวนเงิน")])));
  x.check([...a2].sort((p, q) => p - q).join() === [...amounts].sort((p, q) => p - q).join() && !runsDown(a2),
    "… the same items with the same amounts, no longer in the order of the amount", a2.join(" · "));
  x.check(!!b2 && JSON.stringify(b2["งบกระทบยอด"]) === JSON.stringify(b1["งบกระทบยอด"]),
    "the statement sheet of version 2 is cell for cell the statement of version 1 — an order moves nothing on it");
  thread = await threadText(page);
  x.check(/ทำตามที่ขอแล้ว: เรียงรายการกระทบยอดตามวันที่/.test(thread), "the thread says the second order was done");
  x.check(refusalsIn(thread) === was, "… and says nothing against the order it replaced", `${was} → ${refusalsIn(thread)} refusals in the thread`);
  await x.shot("04-by-date");
  await refused(x, runId, "ตัดรายการเล็กออก", /รายการกระทบยอดทุกรายการต้องอยู่ครบ/);
  await x.shot("05-refused");
  await done(x, "bank", { installId, runId, manifest_version: manifest.version, amounts, dates });
}

// ── invoice processing ────────────────────────────────────────────────────────────────────────────────
async function register() {
  const x = await W.session("web", "register");
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  const installId = await ownInstall(x, "register");
  await workspace(x, installId);
  const manifest = await greeting(x, installId);
  await x.shot("01-greeting");
  const { runId } = await W.sendFiles(x, installId, sampleOf("register"), `${TAG} ทำทะเบียนเอกสารซื้อเดือนกันยายน เรียงตามผู้ขาย`, "02");
  const end = await W.finish(x, runId, "02");
  x.check(end.run.status === "succeeded", "the round delivers", `${end.run.status} ${end.run.status_reason || ""} · ${end.asked.length} question(s) answered`);
  await L.wait(2500);
  const d1 = await detailOf(page, runId);
  const file1 = await W.download(x, d1, "xlsx", "register_v1.xlsx");
  const b1 = file1 ? sheets(file1) : {};
  const t1 = tableOf(b1["ทะเบียน"], ["ลำดับ", "วันที่", "ผู้ขาย"]);
  const sellers = t1.rows.map((r) => r[t1.col("ผู้ขาย")]);
  x.check(sellers.length >= 3 && runsUp(sellers.map(thaiKey)), "“เรียงตามผู้ขาย” — the register runs by seller (read from the .xlsx)", sellers.join(" · "));
  x.check(Object.keys(b1).filter((n) => /ผู้ขาย/.test(n)).length === 0, "… and is still one register: no sheet per seller", Object.keys(b1).join(" · "));
  let thread = await threadText(page);
  x.check(/ทำตามที่ขอแล้ว: เรียงทะเบียนตามผู้ขาย/.test(thread), "the thread says what was done as asked", (thread.match(/ทำตามที่ขอแล้ว[^\n]*/) || [""])[0].slice(0, 240));
  await x.shot("03-by-seller");
  await refused(x, runId, "แยกชีตตามผู้ขาย", /ยอดรวมย่อย/, "and it says what to ask instead");
  await x.shot("04-refused");
  await sayAfter(x, "เรียงจากยอดมาก");
  const { book: b2 } = await nextVersion(x, runId, versionOf(d1), "register_v2.xlsx");
  const t2 = tableOf((b2 || {})["ทะเบียน"], ["ลำดับ", "วันที่", "ผู้ขาย"]);
  const totals = t2.rows.map((r) => Number(r[t2.col("ยอดรวม (บาท)")]));
  x.check(totals.length === sellers.length && runsDown(totals) && totals[0] > totals[totals.length - 1],
    "“เรียงจากยอดมาก” — version 2 runs from the largest total in baht (read from the .xlsx)", totals.join(" ≥ "));
  x.check(!!b2 && JSON.stringify(b2["สรุป"]) === JSON.stringify(b1["สรุป"]), "the summary sheet of version 2 is cell for cell that of version 1 — the totals did not move");
  thread = await threadText(page);
  x.check(/ทำตามที่ขอแล้ว: เรียงทะเบียนจากยอดรวมมากที่สุด/.test(thread), "the thread says the second order was done");
  await x.shot("05-by-total");
  await done(x, "register", { installId, runId, manifest_version: manifest.version, sellers, totals });
}

// ── invoice ↔ PO matching ─────────────────────────────────────────────────────────────────────────────
async function po() {
  const x = await W.session("web", "po");
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  const installId = await ownInstall(x, "po");
  await workspace(x, installId);
  const manifest = await greeting(x, installId);
  await x.shot("01-greeting");
  const { runId } = await W.sendFiles(x, installId, sampleOf("po"), `${TAG} ตรวจใบแจ้งหนี้กับใบสั่งซื้อเดือนกันยายน เรียงจากรายการที่ต่างมากที่สุด`, "02");
  const end = await W.finish(x, runId, "02");
  x.check(TERMINAL.includes(end.run.status) && end.run.status === "succeeded", "the round delivers", `${end.run.status} ${end.run.status_reason || ""} · ${end.asked.length} question(s) answered`);
  await L.wait(2500);
  const d1 = await detailOf(page, runId);
  const file1 = await W.download(x, d1, "xlsx", "po_v1.xlsx");
  const b1 = file1 ? sheets(file1) : {};
  const t1 = tableOf(b1["ผลตรวจรายบรรทัด"], ["ลำดับ", "ใบแจ้งหนี้", "บรรทัด"]);
  const sizes = t1.rows.map((r) => { const v = r[t1.col("ส่วนต่างราคารวม")]; return typeof v === "number" ? Math.abs(v) : -1; });
  x.check(sizes.length > 3 && runsDown(sizes), "“เรียงจากรายการที่ต่างมากที่สุด” — the lines run from the largest price difference (read from the .xlsx)",
    `${sizes.length} lines: ${sizes.join(" ≥ ")} (the agent's sample month has no price difference: the order is the file's)`);
  const ids = ["ผลตรวจรายบรรทัด", "ผลตรวจรายบรรทัด · หลักฐาน", "ผลตรวจรายบรรทัด · เกณฑ์ที่ใช้"].map((n) => {
    const t = tableOf(b1[n], ["ลำดับ", "ใบแจ้งหนี้", "บรรทัด"]);
    return JSON.stringify(t.rows.map((r) => [r[t.col("ลำดับ")], r[t.col("ใบแจ้งหนี้")], r[t.col("บรรทัด")]]));
  });
  x.check(ids[0] === ids[1] && ids[0] === ids[2] && t1.rows.length > 0, "the line sheet and its two companion sheets are row for row the same lines");
  const thread = await threadText(page);
  x.check(/ทำตามที่ขอแล้ว: เรียงบรรทัดจากส่วนต่างราคามากที่สุด/.test(thread), "the thread says what was done as asked", (thread.match(/ทำตามที่ขอแล้ว[^\n]*/) || [""])[0].slice(0, 240));
  await x.shot("03-ordered");
  await refused(x, runId, "เอาเฉพาะบรรทัดที่ไม่ผ่าน", /ทุกบรรทัดของทุกใบแจ้งหนี้ต้องอยู่ในผลตรวจครบ/);
  await x.shot("04-refused");
  await done(x, "po", { installId, runId, manifest_version: manifest.version, sizes });
}

// ── procurement (the test stack's stand-in web) ───────────────────────────────────────────────────────
async function buy() {
  const x = await W.session("web", "buy");
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  const installId = await ownInstall(x, "buy");
  await workspace(x, installId);
  const manifest = await greeting(x, installId);
  // the model for this install, chosen as a person chooses it in the model menu. CLAUDE.md: a run of several
  // model steps is not judged on the self-hosted reasoning model; nothing else is changed.
  const model = process.env.AM12_MODEL || "deepseek-v4-flash";
  const chosen = await api(page, `/installs/${installId}/model`, { method: "PUT", body: JSON.stringify({ choice: model }) });
  x.check(chosen.status === 200, `the install's model is set to ${model}, in the model menu's own way`, `${chosen.status}`);
  const text = "ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ งบไม่เกิน 6,000 บาทต่อตัว เรียงจากถูกสุด แล้วบอกด้วยว่าเจ้าไหนดีที่สุด";
  const runsOf = async () => ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).filter((r) => r.kind === "run");
  const start = page.locator('[data-testid="am-round-start"]');
  if (await start.count()) await x.click(start.last(), "เริ่มรอบใหม่");
  await page.locator('[data-testid="am-composer-input"]').fill(`${TAG} ${text}`);
  const before = (await runsOf()).map((r) => r.id);
  await x.click(page.locator('[data-testid="am-composer-send"]'), "ส่ง");
  await L.wait(1500);
  const chip = page.locator('[data-testid="am-intent-new"]');
  if (await chip.count()) await x.click(chip.last(), "เริ่มรอบใหม่ (chip)");
  const run = await waitFor(async () => (await runsOf()).find((r) => !before.includes(r.id)), 60000, 1000);
  x.check(!!run, "the request starts a run from the words alone", run ? run.id : "no run");
  if (!run) return done(x, "buy");
  state.runs = [...new Set([...(state.runs || []), run.id])];
  save();
  // every card: answered as it would record by default; the choice card: its first option
  const seen = new Set();
  let end = null;
  for (;;) {
    const d = await waitFor(async () => {
      const r = await detailOf(page, run.id);
      if (!r.run) return null;
      if (TERMINAL.includes(r.run.status)) return r;
      return r.run.status === "awaiting_input" && (r.clarifications || []).some((c) => c.status === "open" && !seen.has(c.id)) ? r : null;
    }, 600000, 1500);
    if (!d) throw new Error("the run neither asked nor finished in 10 minutes");
    if (TERMINAL.includes(d.run.status)) { end = d; break; }
    const c = d.clarifications.filter((y) => y.status === "open" && !seen.has(y.id)).pop();
    seen.add(c.id);
    const el = page.locator(`[data-card-id="${c.id}"]`).first();
    await el.waitFor({ state: "visible", timeout: 90000 });
    await el.scrollIntoViewIfNeeded();
    await L.wait(800);
    if ((c.questions || []).some((q) => q.code === "proc_pick")) {
      const cardText = await x.words(el);
      x.check(!/ดีที่สุด|แนะนำให้ซื้อ|คุ้มที่สุด/.test(cardText), "the card a person chooses from recommends nothing", cardText.slice(0, 160));
      await x.shot("02-choice");
      await x.click(el.locator('[data-testid="question-option"]').first(), "the first option");
    } else {
      for (const [k, q] of (c.questions || []).entries()) {
        const opts = el.locator('[data-testid="question-item"]').nth(k).locator('[data-testid="question-option"]');
        let o = opts.first();
        for (let m = 0; m < (await opts.count()); m++) if (q.default_label && (await opts.nth(m).innerText()).includes(q.default_label)) { o = opts.nth(m); break; }
        await x.click(o, `answer: ${(await o.innerText()).replace(/\s+/g, " ").slice(0, 50)}`);
      }
    }
    await x.click(el.locator('[data-testid="question-submit"]'), "ส่งคำตอบ");
    await L.wait(2500);
  }
  x.check(end.run.status === "succeeded", "the round delivers", `${end.run.status} ${end.run.status_reason || ""}`);
  await L.wait(2500);
  const d = await detailOf(page, run.id);
  const file = await W.download(x, d, "xlsx", "comparison_v1.xlsx");
  const book = file ? sheets(file) : {};
  const name = Object.keys(book).find((n) => /^ตัวเลือก|เปรียบเทียบ/.test(n) && !/·/.test(n)) || Object.keys(book)[1];
  const t = tableOf(book[name], ["ราคาต่อหน่วย"]);
  const heads = Object.keys(t.cols);
  const priceCol = t.col("ราคาต่อหน่วย");
  const totalCol = t.cols[heads.find((h) => /^ยอดรวม|รวมภาษี/.test(h))];
  const face = JSON.stringify(book[Object.keys(book)[0]] || []);
  const byTotal = /ยอดรวมภาษีต่ำสุดก่อน/.test(face);
  const figures = t.rows.map((r) => r[byTotal ? totalCol : priceCol]);
  const numbers = figures.filter((v) => typeof v === "number");
  x.check(t.rows.length >= 2 && runsUp(numbers) && figures.slice(numbers.length).every((v) => typeof v !== "number"),
    `“เรียงจากถูกสุด” — the options run from the lowest ${byTotal ? "total with tax" : "price shown"}, those with no price last (read from the .xlsx, sheet “${name}”)`, figures.join(" ≤ "));
  x.check(/ต่ำสุดก่อน ตามที่ขอ/.test(face) && /ไม่ได้บอกว่าตัวเลือกใดดีกว่า/.test(face) && /ไม่ใช่คำแนะนำ/.test(face),
    "the face of the workbook says the order is by price only, that it does not say which is better, and that the table is not advice");
  const all = JSON.stringify(book);
  x.check(!/ดีที่สุด|คุ้มที่สุด|แนะนำให้ซื้อ|best (?:option|choice|value)/i.test(all), "no cell of the workbook marks an option as best");
  const openCol = t.cols[heads.find((h) => /ยังไม่ทราบ/.test(h))];
  x.check(openCol !== undefined && t.rows.every((r) => typeof r[openCol] === "number"), "every option's row still says how many points are not known about it",
    t.rows.map((r) => r[openCol]).join(" · "));
  const thread = await threadText(page);
  x.check(/ทำตามที่ขอแล้ว: เรียงตัวเลือกจากราคาต่ำสุด/.test(thread), "the thread says what was done as asked", (thread.match(/ทำตามที่ขอแล้ว[^\n]*/) || [""])[0].slice(0, 260));
  const lines = thread.match(/[^\n]*ทำตามที่ขอไม่ได้เพราะ[^\n]*/g) || [];
  x.check(lines.some((l) => /ไม่ตัดสินว่าตัวเลือกใดดีที่สุด/.test(l)), "“เจ้าไหนดีที่สุด” — refused, with its reason", lines.join(" | ").slice(0, 300) || "(nothing was said)");
  x.check(!lines.some((l) => /งบ|6,000|20 ตัว|ที่วางแขน|สีลม/.test(l)), "nothing of the request itself — the budget, the quantity, the place — was answered with a refusal", `${lines.length} refusal(s)`);
  const step = (d.steps || []).find((s) => s.name === "instructions");
  x.mark(`read by ${step ? step.model_id || (step.output || {}).model : "?"}: ${JSON.stringify(step && step.output).slice(0, 300)}`);
  await x.shot("03-delivered");
  await done(x, "buy", { installId, runId: run.id, manifest_version: manifest.version, figures, refusals: lines });
}

async function cleanup() {
  const s = await W.openWeb(process.env.AM6_EMAIL || L.TS_USER.email);
  await s.page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  for (const id of [...new Set(state.installs || [])].filter(Boolean)) {
    const r = await api(s.page, `/installs/${id}`, { method: "DELETE" });
    log(`install ${id} removed: ${r.status}`);
  }
  state.installs = [];
  for (const k of Object.keys(state)) if (k.startsWith("am13_")) delete state[k];
  save();
  await s.close();
}

const phase = process.argv[2];
(async () => {
  if (phase === "bank") await bank("th");
  else if (phase === "english") await bank("en");
  else if (phase === "register") await register();
  else if (phase === "po") await po();
  else if (phase === "buy") await buy();
  else if (phase === "cleanup") await cleanup();
  else throw new Error("bank | register | po | buy | english | cleanup");
})().catch((e) => { console.error(e); process.exit(1); });
void EDGE;
