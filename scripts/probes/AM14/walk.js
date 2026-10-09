#!/usr/bin/env node
// AM14 — what a package writes, in the language of the screen; walked on the built clients by clicking
// the real UI (CARD-RULES §6b), with the walk's own account and its own installs.
//
//   intro     all six agents, read on an ENGLISH screen and then on a Thai one: the name in the list of my
//             agents and in the sidebar, the page's header, the greeting and its tick list. What each must
//             say is read from the agent's own manifest on disk; the name its row keeps is read from the
//             database and shown beside it. (An agent the account has not got is installed first, from a
//             Thai screen.)
//   run <agent> [en|th] [fresh] [dir]
//             one run of the agent on a screen in that language, from its page to the finished files (its
//             own sample files, or the files of a folder that raises questions), and every piece of Thai
//             the screen showed, sorted: the person's own data, the finished document's own wording, or
//             neither — which on an English screen is the defect (results/…/thai.json). `fresh`: from the
//             install on — a new copy installed from that screen, its greeting read first
//   cleanup   the installs this walk made, by id
//
//   SMOKE_TARGET=test node scripts/probes/AM14/walk.js intro
//   SMOKE_TARGET=test node scripts/probes/AM14/walk.js run bank_reconciliation en
"use strict";
const fs = require("node:fs");
const path = require("node:path");

process.env.AM6_TAG = process.env.AM6_TAG || "[AM14]";
const TARGET = process.env.SMOKE_TARGET || "test";
process.env.AM6_OUT = process.env.AM6_OUT || path.join(__dirname, "results", "walk", TARGET);
const W = require("../AM6/walkthrough.js");
const { L, state, save, log, api, WEB, waitFor } = W;

const PKG = path.join(L.ROOT, "backend", "agent_market", "packages");
const THAI = /[฀-๿]/;
const AGENTS = fs.readdirSync(PKG).filter((d) => fs.existsSync(path.join(PKG, d, "agent.json"))).sort()
  .map((dir) => {
    const m = JSON.parse(fs.readFileSync(path.join(PKG, dir, "agent.json"), "utf8"));
    const en = (s) => (m.en && m.en[s]) || s;
    return {
      dir, slug: m.id, version: m.version,
      th: { name: m.short_name || m.name, card: m.name, greeting: m.greeting.text, role: m.greeting.role || "", needs: (m.inputs || []).map((i) => i.label) },
      en: { name: m.name_en, card: m.name_en, greeting: en(m.greeting.text), role: en(m.greeting.role || ""), needs: (m.inputs || []).map((i) => en(i.label)) },
    };
  });

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
const squash = (s) => String(s || "").replace(/\s+/g, " ").trim();
const thaiIn = (s) => (String(s).match(/[฀-๿][฀-๿\s]*/g) || []).map((t) => t.trim()).filter(Boolean);

/**
 * One install of each agent for this account, on the version the checkout's manifest has: the newest such
 * install it has, or a new one made from this (Thai) screen — a second copy when the account's own are on
 * an older version (a run is made by the version its install is on).
 */
async function installs(x, which = AGENTS) {
  const { page } = x;
  const out = {};
  const versionOf = (id) => L.csql(`select d.version from agent_market.agent_installs i join agent_market.agent_definitions d on d.id = i.definition_id where i.id='${id}'`).trim();
  for (const a of which) {
    const mine = ((await api(page, "/installs")).body?.installs || []).filter((i) => i.slug === a.slug && i.status !== "removed")
      .sort((p, q) => String(q.created_at || "").localeCompare(String(p.created_at || "")));
    let id = (mine.find((i) => versionOf(i.id) === a.version) || {}).id;
    if (!id) {
      id = mine.length ? await W.installAnother(x, a.slug, a.th.card) : (await W.installFromMarket(x, a.th.card)).installId;
      state.installs = [...new Set([...(state.installs || []), id])];
      save();
      log(`${a.dir}: installed ${id} at ${versionOf(id)} (the account had ${mine.length} at an older version)`);
    }
    out[a.slug] = { id, version: versionOf(id), kept: L.csql(`select title from agent_market.agent_installs where id='${id}'`).trim() };
  }
  return out;
}

/** What one screen says of every agent: the list of my agents, the sidebar, and each agent's own page. */
async function read(x, mine, lang) {
  const { page } = x;
  const want = (a) => a[lang];
  // the list of all my agents (the sidebar shows only the first few)
  await page.goto(`${WEB()}/chat/agents/mine`, { waitUntil: "load" });
  await L.wait(2500);
  for (const a of AGENTS) {
    const row = page.locator(`a[href$="/chat/agents/i/${mine[a.slug].id}"]`).first();
    const text = (await row.count()) ? squash(await row.innerText()) : "";
    x.check(text.includes(want(a).name) && (lang === "th" || !THAI.test(text)),
      `${lang} · my agents · ${a.dir}: listed as “${want(a).name}”`, `shown “${text.slice(0, 80)}” · the row keeps “${mine[a.slug].kept}”`);
  }
  await x.shot(`${lang}-01-my-agents`);
  // the sidebar: every row it shows is in the screen's language
  await home(page);
  const rail = await page.locator('[data-testid="agent-rail-row"]').allInnerTexts();
  const names = AGENTS.map((a) => want(a).name);
  const railNames = rail.map((r) => squash(r).replace(/\s*\d+$/, ""));
  x.check(railNames.length > 0 && railNames.every((r) => names.some((n) => r.includes(n))),
    `${lang} · sidebar: every agent it lists is under its ${lang === "en" ? "English" : "Thai"} name`, railNames.join(" | "));
  if (lang === "en") x.check(!rail.some((r) => THAI.test(r)), "en · sidebar: not one Thai letter in the agents' rows", thaiIn(rail.join(" ")).join(" · ") || "none");
  await x.shot(`${lang}-02-sidebar`);
  // each agent's own page
  for (const a of AGENTS) {
    await page.goto(`${WEB()}/chat/agents/i/${mine[a.slug].id}`, { waitUntil: "load" });
    await page.waitForSelector('[data-testid="composer-root"]', { timeout: 90000 });
    const g = page.locator('[data-testid="am-greeting"]').first();
    await g.waitFor({ state: "attached", timeout: 30000 }).catch(() => {});
    const has = await g.count();
    const text = has ? squash(await g.innerText()) : "";
    x.check(has && text.includes(squash(want(a).greeting)), `${lang} · ${a.dir}: the greeting is the manifest's own, in this language`, text.slice(0, 110));
    const ticks = has ? (await g.locator("ul li").allInnerTexts()).map(squash) : [];
    x.check(want(a).needs.every((n) => ticks.includes(squash(n))), `${lang} · ${a.dir}: the tick list is the manifest's own (${want(a).needs.length})`, ticks.join(" | ") || "(none)");
    const header = squash(await page.locator('[data-testid="am-workspace"]').first().evaluate((el) => (el.closest("main") || document.body).querySelector("h1, h2")?.textContent || ""));
    if (lang === "en") {
      x.check(!THAI.test(text), `en · ${a.dir}: not one Thai letter in the greeting block`, thaiIn(text).join(" · ") || "none");
      x.check(header && !THAI.test(header), `en · ${a.dir}: the header is English`, header.slice(0, 70));
    } else {
      x.check(THAI.test(text), `th · ${a.dir}: the greeting is Thai on a Thai screen`, text.slice(0, 60));
    }
    if (a === AGENTS[0] || a.dir === "bank_reconciliation") await x.shot(`${lang}-03-${a.dir}`);
  }
}

async function intro() {
  const x = await W.session("web", "intro");
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  const mine = await installs(x);
  for (const a of AGENTS) log(`${a.dir}: install ${mine[a.slug].id} · its row keeps “${mine[a.slug].kept}”`);
  await home(page);
  await setLanguage(x, "en");
  await read(x, mine, "en");
  await home(page);
  await setLanguage(x, "th");
  await read(x, mine, "th");
  // the rows were read, not rewritten
  const after = AGENTS.map((a) => L.csql(`select title from agent_market.agent_installs where id='${mine[a.slug].id}'`).trim());
  x.check(AGENTS.every((a, i) => after[i] === mine[a.slug].kept), "reading in two languages rewrote no row", after.join(" | "));
  fs.writeFileSync(path.join(x.dir, "intro.json"), JSON.stringify({ at: new Date().toISOString(), target: TARGET, installs: mine, checks: x.checks }, null, 1));
  const bad = x.checks.filter((c) => !c.ok);
  log(`${x.tag}: ${x.checks.length - bad.length} pass, ${bad.length} fail`);
  await x.s.close();
  if (bad.length) process.exitCode = 1;
}

// ── a run, read on an English screen ─────────────────────────────────────────────────────────────────────
//
// What a person who reads only English is shown of one run, from the agent's page to the finished files:
// every piece of Thai on the screen (and in what the server sent that screen) is collected and sorted by
// whose words they are —
//   own       the person's own data: a file's name, a cell of a file they sent, the words they typed
//   document  the finished document's own wording (a sheet's name, a heading, a label inside the file),
//             quoted on the screen — the second pass of this card's subject, not the first
//   other     anything else: the product's or the package's own words, left in Thai. This is the defect.

const MIME_TEXT = new Set([".csv", ".txt", ".json", ".xml"]);
const readPy = (code, ...args) => {
  const { execFileSync } = require("node:child_process");
  return JSON.parse(execFileSync(process.env.PYTHON || "python", ["-c", code, ...args],
    { encoding: "utf8", maxBuffer: 256 << 20, env: { ...process.env, PYTHONIOENCODING: "utf-8" } }));
};
/** Every string a workbook holds: its sheets' names and its cells. */
const workbookWords = (file) => readPy([
  "import sys,json",
  "from openpyxl import load_workbook",
  "wb=load_workbook(sys.argv[1])",
  "out=[ws.title for ws in wb.worksheets]",
  "out+=[str(c) for ws in wb.worksheets for r in ws.iter_rows(values_only=True) for c in r if isinstance(c,str)]",
  "out+=[str(getattr(ws.oddHeader.center,'text','') or '') for ws in wb.worksheets]+[str(getattr(ws.oddFooter.center,'text','') or '') for ws in wb.worksheets]",
  "print(json.dumps(out,ensure_ascii=False))",
].join("\n"), file);
const docxWords = (file) => readPy([
  "import sys,json,zipfile,re",
  "z=zipfile.ZipFile(sys.argv[1])",
  "out=[]",
  "for n in z.namelist():",
  "    if n.endswith('.xml'):",
  "        out+=re.findall(r'>([^<>]*[\\u0e00-\\u0e7f][^<>]*)<', z.read(n).decode('utf-8','replace'))",
  "print(json.dumps(out,ensure_ascii=False))",
].join("\n"), file);
function wordsOfFile(file) {
  const ext = path.extname(file).toLowerCase();
  try {
    if (ext === ".xlsx") return workbookWords(file);
    if (ext === ".docx" || ext === ".pptx") return docxWords(file);
    if (MIME_TEXT.has(ext)) return fs.readFileSync(file, "utf8").split(/[\r\n,\t"<>]+/);
  } catch (e) { log(`could not read ${path.basename(file)}: ${String(e.message).slice(0, 120)}`); }
  return [];
}
const runsOfThai = (s) => (String(s).match(/[฀-๿]+(?:[  ]*[฀-๿]+)*/g) || []);
const thaiOnly = (s) => String(s).replace(/[^฀-๿]/g, "");

/** Sorts each Thai piece of `found` ([{where, text}]) by whose words it is. */
function sortThai(found, own, doc) {
  const ownJ = thaiOnly(own.join("\n")), docJ = thaiOnly(doc.join("\n"));
  const out = { own: [], document: [], other: [] };
  for (const f of found) {
    const left = [], docs = [];
    for (const r of runsOfThai(f.text)) {
      const k = thaiOnly(r);
      if (ownJ.includes(k)) continue;
      if (docJ.includes(k)) { docs.push(r); continue; }
      left.push(r);
    }
    if (left.length) out.other.push({ ...f, thai: left });
    else if (docs.length) out.document.push({ ...f, thai: docs });
    else out.own.push(f);
  }
  return out;
}

/** Every visible text on the page that has a Thai letter, with the nearest test id above it. */
const thaiOnPage = (page, scope) => page.evaluate(([scope]) => {
  const TH = /[฀-๿]/;
  const root = document.querySelector(scope) || document.body;
  const whereOf = (el) => {
    const ids = [];
    for (let p = el; p && p !== document.body && ids.length < 3; p = p.parentElement) {
      const t = p.getAttribute && p.getAttribute("data-testid");
      if (t && !ids.includes(t)) ids.push(t);
    }
    return ids.reverse().join(" > ") || (el.closest("nav,aside,header,main") || {}).tagName || "?";
  };
  const out = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode()); ) {
    const s = (n.nodeValue || "").replace(/\s+/g, " ").trim();
    const el = n.parentElement;
    if (!s || !TH.test(s) || !el || !el.getClientRects().length) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") continue;
    out.push({ where: whereOf(el), text: s.slice(0, 300) });
  }
  for (const el of root.querySelectorAll("[placeholder],[aria-label],[title],[alt]")) {
    for (const at of ["placeholder", "aria-label", "title", "alt"]) {
      const v = el.getAttribute(at);
      if (v && TH.test(v)) out.push({ where: `${whereOf(el)} @${at}`, text: v.slice(0, 300) });
    }
  }
  return out;
}, [scope]);
/** Every string with a Thai letter in a JSON value, with its path. */
function thaiInJson(v, at, out = []) {
  if (typeof v === "string") { if (THAI.test(v)) out.push({ where: at, text: v.slice(0, 300) }); }
  else if (Array.isArray(v)) v.forEach((y, i) => thaiInJson(y, `${at}[${i}]`, out));
  else if (v && typeof v === "object") for (const [k, y] of Object.entries(v)) thaiInJson(y, at ? `${at}.${k}` : k, out);
  return out;
}

const ASK = {
  bank_reconciliation: { en: "Reconcile September", th: "กระทบยอดเดือนกันยายน" },
  expense_audit: { en: "Check this month's expense claims", th: "ตรวจการเบิกค่าใช้จ่ายของเดือนนี้" },
  financial_report: { en: "Prepare the September report", th: "ทำรายงานเดือนกันยายน" },
  invoice_processing: { en: "Record these purchase documents", th: "ลงทะเบียนเอกสารซื้อชุดนี้" },
  po_matching: { en: "Check these invoices against the purchase orders", th: "ตรวจใบกำกับภาษีกับใบสั่งซื้อชุดนี้" },
  // (in English: the request the stand-in web has pages for — AM12's own English round; its search answers to these words)
  procurement: { en: "We need one 48-port PoE network switch, delivered to the head office, 5th floor, Silom Road, Bangkok.", th: "ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน งบไม่เกิน 6,000 บาทต่อตัว" },
};

/**
 * run <agent dir> [en|th] [files dir]: one run of the agent on a screen in that language, from its page to
 * the finished files — the agent's own sample files, or every file of `files dir` (a set that raises
 * questions) — and what Thai the screen showed, sorted.
 */
async function run(which, lang = "en", filesDir = "", fresh = false) {
  const a = AGENTS.find((y) => y.dir === which);
  if (!a) throw new Error(`no agent ${which}: ${AGENTS.map((y) => y.dir).join(" | ")}`);
  const tag = `run-${which}-${lang}${fresh ? "-fresh" : ""}${filesDir ? "-" + path.basename(filesDir) : ""}`;
  const x = await W.session("web", tag);
  const { page } = x;
  await home(page);
  let installId;
  if (fresh) {
    // from the install on: a new copy, installed from this screen, in this screen's language
    await setLanguage(x, lang);
    const had = ((await api(page, "/installs")).body?.installs || []).some((i) => i.slug === a.slug && i.status !== "removed");
    installId = had ? await W.installAnother(x, a.slug, a[lang].card) : (await W.installFromMarket(x, a[lang].card)).installId;
    state.installs = [...new Set([...(state.installs || []), installId])];
    save();
    const version = L.csql(`select d.version from agent_market.agent_installs i join agent_market.agent_definitions d on d.id = i.definition_id where i.id='${installId}'`).trim();
    x.check(version === a.version, `${lang} · ${which}: installed from this screen, on the version of the checkout (${a.version})`, version);
  } else {
    await setLanguage(x, "th"); // (an agent the account has not got is installed from a Thai screen)
    installId = (await installs(x, [a]))[a.slug].id;
    await home(page);
    await setLanguage(x, lang);
  }
  await page.goto(`${WEB()}/chat/agents/i/${installId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 90000 });
  await L.wait(1500);
  await x.shot("01-page");
  if (fresh) {
    // the first thing anyone sees: the greeting and its tick list, in this screen's language
    const g = page.locator('[data-testid="am-greeting"]').first();
    await g.waitFor({ state: "attached", timeout: 30000 }).catch(() => {});
    const text = (await g.count()) ? squash(await g.innerText()) : "";
    x.check(text.includes(squash(a[lang].greeting)), `${lang} · ${which}: the greeting is the manifest's own, in this language`, text.slice(0, 110));
    if (lang === "en") x.check(!THAI.test(text), `en · ${which}: not one Thai letter in the greeting block`, thaiIn(text).join(" · ") || "none");
  }

  const dir = path.join(PKG, a.dir);
  const st = JSON.parse(fs.readFileSync(path.join(dir, "self-test.json"), "utf8"));
  const files = filesDir
    ? fs.readdirSync(filesDir).filter((f) => !f.startsWith(".") && fs.statSync(path.join(filesDir, f)).isFile()).sort().map((f) => ({ src: path.join(filesDir, f), name: f }))
    : (st.files || []).map((rel) => ({ src: path.join(dir, rel), name: path.basename(rel) }));
  const words = `${W.TAG} ${ASK[which][lang]}`;
  let runId;
  if (files.length) ({ runId } = await W.sendFiles(x, installId, files, words, "02"));
  else {
    if (process.env.AM14_MODEL) await api(page, `/installs/${installId}/model`, { method: "PUT", body: JSON.stringify({ choice: process.env.AM14_MODEL }) });
    const runsOf = async () => ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).filter((r) => r.kind === "run");
    const start = page.locator('[data-testid="am-round-start"]');
    if (await start.count()) await x.click(start.last(), "start a new run");
    await page.locator('[data-testid="am-composer-input"]').fill(words);
    const before = (await runsOf()).map((r) => r.id);
    await x.click(page.locator('[data-testid="am-composer-send"]'), "send");
    await L.wait(1500);
    const chip = page.locator('[data-testid="am-intent-new"]');
    if (await chip.count()) await x.click(chip.last(), "start a new run (chip)");
    const made = await waitFor(async () => (await runsOf()).find((r) => !before.includes(r.id)), 60000, 1000);
    if (!made) throw new Error("no run started");
    runId = made.id;
    state.runs = [...new Set([...(state.runs || []), runId])];
    save();
  }
  // every question as the screen showed it, before it is answered (finish() answers and moves on)
  const cards = [];
  const watch = setInterval(async () => {
    try {
      for (const t of await thaiOnPage(page, '[data-testid="am-thread"]')) if (!cards.some((c) => c.where === t.where && c.text === t.text)) cards.push(t);
    } catch { /* the page is between two renders */ }
  }, 1500);
  let end;
  try { end = await W.finish(x, runId, "02"); } finally { clearInterval(watch); }
  x.check(TERMINAL_OK.includes(end.run.status), `${lang} · ${which}: the run finishes`, `${end.run.status} ${end.run.status_reason || ""} · ${end.asked.length} question(s) answered`);
  await L.wait(3000);

  // open what the files card keeps folded, then read the whole screen
  const details = page.locator(`[data-testid="am-outputs"][data-run="${runId}"] [data-testid="am-outputs-details"]`);
  if (await details.count()) { await details.first().click().catch(() => {}); await L.wait(800); }
  const more = page.locator('[data-testid="am-more-files"]');
  if (await more.count()) { await more.first().click().catch(() => {}); await L.wait(500); }
  await x.shot("03-finished");
  const screen = await thaiOnPage(page, "body");
  // the run's own page
  await page.goto(`${WEB()}/chat/agents/runs/${runId}`, { waitUntil: "load" });
  await L.wait(4000);
  for (const b of await page.locator("main details:not([open]) > summary").all()) await b.click().catch(() => {});
  await L.wait(600);
  await x.shot("04-run-page");
  const runPage = (await thaiOnPage(page, "main")).map((t) => ({ ...t, where: `run page · ${t.where}` }));
  // what the server sent this screen (the same requests the page makes, in its language)
  const hdr = { headers: { "Accept-Language": lang } };
  const detail = (await api(page, `/runs/${runId}`, hdr)).body || {};
  const thread = (await api(page, `/installs/${installId}/thread`, hdr)).body || {};
  const sent = [...thaiInJson(detail, "run"), ...thaiInJson((thread.items || []).filter((it) => !it.run_id || it.run_id === runId), "thread")];

  // whose words: the person's own (their files, their words), the finished documents' own, or neither
  const own = [words, ...files.map((f) => f.name), ...files.flatMap((f) => wordsOfFile(f.src))];
  const doc = [];
  for (const art of detail.artifacts || []) {
    const file = path.join(x.dir, `out-${art.format}-${art.name}`.replace(/[^\w.\-฀-๿]+/g, "_"));
    try {
      fs.writeFileSync(file, await W.fetchFile(page, `${W.EDGE()}/api/v1/agent-market/artifacts/${art.id}/download`));
      doc.push(art.name, ...wordsOfFile(file));
    } catch (e) { log(`could not fetch ${art.name}: ${String(e.message).slice(0, 100)}`); }
  }
  // (a chat's title in the sidebar is what its owner typed: the account's own words, of other chats than this run's)
  const chatTitles = [...screen, ...runPage].filter((t) => /^NAV( @|$)/.test(t.where)).map((t) => t.text);
  const onScreen = sortThai([...cards.map((c) => ({ ...c, where: `thread (during) · ${c.where}` })), ...screen, ...runPage]
    .filter((t, i, all) => all.findIndex((u) => u.where === t.where && u.text === t.text) === i), [...own, ...chatTitles], doc);
  const inSent = sortThai(sent, own, doc);
  const report = {
    at: new Date().toISOString(), target: TARGET, agent: which, lang, installId, runId, version: end.detail?.run?.definition_version,
    status: end.run.status, questions: end.asked.length, files: files.map((f) => f.name), artifacts: (detail.artifacts || []).map((y) => y.name),
    screen: { own: onScreen.own.length, document: onScreen.document.length, other: onScreen.other.length },
    sent: { own: inSent.own.length, document: inSent.document.length, other: inSent.other.length },
    screen_other: onScreen.other, screen_document: onScreen.document, sent_other: inSent.other, sent_document: inSent.document,
    checks: x.checks,
  };
  fs.writeFileSync(path.join(x.dir, "thai.json"), JSON.stringify(report, null, 1));
  log(`${x.tag}: Thai on the screen — own data ${report.screen.own}, the document's own wording ${report.screen.document}, OTHER ${report.screen.other}`);
  log(`${x.tag}: Thai in what the server sent — own data ${report.sent.own}, the document's own wording ${report.sent.document}, OTHER ${report.sent.other}`);
  for (const o of onScreen.other.slice(0, 60)) log(`  screen · ${o.where}: ${o.text.slice(0, 150)}`);
  for (const o of inSent.other.slice(0, 60)) log(`  sent · ${o.where}: ${o.text.slice(0, 150)}`);
  if (lang === "en") x.check(onScreen.other.length === 0, `en · ${which}: no Thai on the screen but the person's own data and the document's own wording`, `${onScreen.other.length} left`);
  await x.s.close();
  if (x.checks.some((c) => !c.ok)) process.exitCode = 1;
}
const TERMINAL_OK = ["succeeded", "failed"]; // (a run that ends "not tied" is a finished run: its outcome says so)

async function cleanup() {
  const s = await W.openWeb(process.env.AM6_EMAIL || L.TS_USER.email);
  await s.page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  for (const id of [...new Set(state.installs || [])].filter(Boolean)) {
    const r = await api(s.page, `/installs/${id}`, { method: "DELETE" });
    log(`install ${id} removed: ${r.status}`);
  }
  state.installs = [];
  save();
  await s.close();
}

const phase = process.argv[2];
(async () => {
  if (phase === "intro") await intro();
  else if (phase === "run") {
    const rest = process.argv.slice(5);
    await run(process.argv[3], process.argv[4] || "en", rest.find((y) => y !== "fresh") || "", rest.includes("fresh"));
  }
  else if (phase === "cleanup") await cleanup();
  else throw new Error("intro | run <agent> [en|th] [fresh] [files dir] | cleanup");
})().catch((e) => { console.error(e); process.exit(1); });
