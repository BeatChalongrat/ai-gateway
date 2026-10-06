#!/usr/bin/env node
// AM11 — walked on the built clients, by clicking the real UI, with an account and an install of the
// walk's own (CARD-RULES §6b: never the owner's; AM6_EMAIL names the account on live).
//
//   forms <client>     (AM11_LANG=th|en, default th: the screen's language, which is the run's)
//                      a copy of the report agent, one run, then four forms that carry no marker,
//                      each dropped into that run the way a person does it: a Thai Word memo (every
//                      blank has a value → filled), an English Excel cover sheet (six of eight → the
//                      person confirms → a draft naming two), a purchase request (one of twelve →
//                      refused, the rest named, no file) and a letter (nothing to fill → refused).
//                      Every filled file is DOWNLOADED AND KEPT beside its source (results/…/forms/).
//   language <client>  both languages on every page of the chat app and of Agent Market. A run has
//                      one language — the screen's that started it — so each language is read with a
//                      run of its own (the one "forms" made in that language, or a new one), its form
//                      cards and its run page on screen. What is not in the chosen language is
//                      listed, and each such string is put in one of three piles: the app's own (must
//                      be none), the server's or a package's (what the API sent), the person's data.
//                      The account's language is put back as found.
//   consent            the page a connector's consent flow ends on, reached through the backend's own
//                      ticket → start → the stand-in consent page → callback, in each language, for a
//                      flow begun on the web (the way back to where the person was) and for one begun
//                      in the desktop app (the page the system browser shows and the link it hands the
//                      app). All of it in a browser: the desktop app itself is not driven — that the
//                      app follows the link to its Connectors page is its own test
//                      (apps/desktop/test/connector-return.test.ts), and that page exists only in the app.
//   cleanup [web]      the walk's installs (by id), its runs' evidence and its chats.
//
//   SMOKE_TARGET=test node scripts/probes/AM11/walk.js forms web
//   SMOKE_TARGET=live AM6_EMAIL=<the walk's own account> node scripts/probes/AM11/walk.js language web
"use strict";
const fs = require("node:fs");
const path = require("node:path");

process.env.AM6_TAG = process.env.AM6_TAG || "[AM11]";
const TARGET = process.env.SMOKE_TARGET || "test";
process.env.AM6_OUT = process.env.AM6_OUT || path.join(__dirname, "results", "walk", TARGET);
const W = require("../AM6/walkthrough.js");
const R = require("../AM10/reader.js");
const { L, state, save, log, api, WEB, EDGE, waitFor, TAG } = W;

const SLUG = "sran.financial-report";
// an agent is named in the screen's language (its package's name / name_en)
const CARD = { th: "รายงานผลประกอบการ", en: "Financial Report" };
const FORMS = path.join(L.ROOT, "backend", "agent_market", "fixtures", "forms");
const MIME = {
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};
const THAI = /[฀-๿]/;
const done = async (x, name, extra = {}) => {
  fs.writeFileSync(path.join(x.dir, `${name}.json`), JSON.stringify({ client: x.tag, at: new Date().toISOString(), ...extra, checks: x.checks }, null, 1));
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
const gateway = () => (L.TARGET === "live" ? L.API_BASE.gw : "http://localhost:18080");
const accountLanguage = (page) => page.evaluate(async (gw) => (await (await fetch(`${gw}/portal/me`, { credentials: "include", cache: "no-store" })).json().catch(() => ({}))).preferred_language || null, gateway());
async function setLanguage(x, to) {
  const { page } = x;
  const now = await page.evaluate(() => document.documentElement.lang);
  if (now === to) return now;
  const trigger = page.locator('div.border-t > button[aria-haspopup="menu"]').last();
  await x.click(trigger, "account menu");
  const menu = page.locator('[role="menu"]').filter({ has: page.getByRole("menuitem", { name: /Language|ภาษา/ }) });
  await x.click(menu.getByRole("menuitem", { name: /Language|ภาษา/ }), `language → ${to}`);
  await waitFor(async () => (await page.evaluate(() => document.documentElement.lang)) === to, 10000);
  await page.keyboard.press("Escape").catch(() => {});
  return page.evaluate(() => document.documentElement.lang);
}

// ── the walk's own install and its one run, per language ──────────────────────────────────────
// A run is written in the language of the screen that started it, and stays so.
const COMPANY = "บริษัท เดินตรวจ เอเอ็มสิบเอ็ด จำกัด";
const ASK = { th: "รายงานผลประกอบการเดือนกันยายน 2569", en: "the report for September 2026 please" };
async function ownRun(x, client, lang) {
  const key = `am11_${client}_${lang}`;
  if (state[key] && state[key].run1) return state[key];
  const { page } = x;
  await home(page);
  const got = await setLanguage(x, lang);
  x.check(got === lang, `the screen is ${lang} when the run is started`, got);
  await L.wait(1200);
  const mine = (await api(page, "/installs")).body?.installs || [];
  const had = mine.some((i) => i.slug === SLUG && i.status !== "removed");
  const installId = had ? await W.installAnother(x, SLUG, CARD[lang]) : (await W.installFromMarket(x, CARD[lang])).installId;
  const title = ((await api(page, "/installs")).body?.installs || []).find((i) => i.id === installId)?.title || "";
  x.check(lang === "en" ? !THAI.test(title) && /Financial Report/.test(title) : THAI.test(title), `the install is headed by the agent's name in ${lang}`, title);
  // the company's name, which a person gives once in the install's settings (the same request that page sends):
  // without it the agent has no value for a form's "company" field, and says so
  if (process.env.AM11_NO_COMPANY !== "1") {
    const saved = await api(page, `/installs/${installId}/defaults/company`, { method: "PUT", headers: { "Accept-Language": lang }, body: JSON.stringify({ value: COMPANY }) });
    x.check(saved.status === 200, "the company's name is saved on the install", `${saved.status}`);
  }
  // the install's own workspace (a second copy is made on the wizard's page and is not opened by it)
  await page.goto(`${WEB()}/chat/agents/i/${installId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="composer-root"]', { timeout: 90000 });
  await L.wait(1500);
  const r1 = await W.sendFiles(x, installId, W.FIN, `${TAG} ${ASK[lang]}`, 1);
  const d1 = await W.finish(x, r1.runId, 1);
  x.check(d1.run.status === "succeeded", `the walk's own run of the report agent (${lang}) finished`, `${d1.run.status} ${d1.run.status_reason || ""}`);
  const said = ((await api(page, `/runs/${r1.runId}`)).body?.run?.params || {}).lang || "th";
  x.check(said === lang, `the run is in the screen's language: ${lang}`, String(said));
  state[key] = { installId, run1: r1.runId };
  save();
  return state[key];
}

// ── D: forms that carry no marker ──────────────────────────────────────────────────────────────
async function dropForm(x, st, file, words) {
  const { page } = x;
  const before = await page.locator('[data-testid="am-form"]').count();
  x.clicks += 1;
  await page.locator('[data-testid="am-composer-file"]').setInputFiles([{ name: file, mimeType: MIME[path.extname(file)], buffer: fs.readFileSync(path.join(FORMS, file)) }]);
  const mark = page.locator('[data-testid="am-composer-form-mark"]');
  await mark.waitFor({ state: "visible", timeout: 60000 });
  // an .xlsx is a kind this agent also reads as data: the person ticks "this is a form"
  if (!(await mark.isChecked())) await x.click(mark, "ใช้เป็นแบบฟอร์มของรอบนี้");
  await page.locator('[data-testid="am-composer-input"]').fill(`${TAG} ${words}`);
  await L.wait(1200);
  await x.click(page.locator('[data-testid="am-composer-send"]'), "ส่ง");
  // the run's form card: a new one for a first drop, the same one read again for a form dropped before
  const card = page.locator(`[data-testid="am-form"][data-run="${st.run1}"]`).last();
  await waitFor(async () => ((await page.locator('[data-testid="am-form"]').count()) > before || (await card.innerText().catch(() => "")).includes(file) ? true : null), 90000, 800);
  await card.waitFor({ state: "visible", timeout: 60000 });
  x.check((await card.getAttribute("data-run")) === st.run1 && (await card.innerText()).includes(file), `${file}: the form goes to the run just finished`);
  const status = () => card.getAttribute("data-status");
  // it is being read first (a card left by an earlier drop of the same file shows that drop's end until then)
  await waitFor(async () => ((await status()) === "inspecting" ? true : null), 20000, 300);
  const s = await waitFor(async () => { const v = await status(); return v && v !== "inspecting" ? v : null; }, 300000, 1500);
  x.mark(`${file}: read → ${s}`);
  await card.scrollIntoViewIfNeeded();
  await L.wait(800);
  return { card, status, read: s };
}
const settled = (status) => waitFor(async () => { const v = await status(); return ["filled", "unfilled", "failed", "refused"].includes(v) ? v : null; }, 300000, 1500);
async function keep(x, st, file, as) {
  const form = (await api(x.page, `/runs/${st.run1}/template`)).body?.form;
  if (!form || !form.artifact_id) return null;
  const out = path.join(x.dir, as);
  fs.writeFileSync(out, await W.fetchFile(x.page, `${EDGE()}/api/v1/agent-market/artifacts/${form.artifact_id}/download`));
  fs.copyFileSync(path.join(FORMS, file), path.join(x.dir, file));
  x.clicks += 1;
  return out;
}

const WORDS = {
  th: { fill: "ใส่ในฟอร์มนี้ให้หน่อย", figures: "ใส่ตัวเลขลงในฟอร์มนี้ด้วย", request: "กรอกแบบฟอร์มนี้ให้หน่อย" },
  en: { fill: "please put it into this form", figures: "put the figures into this form too", request: "please fill in this form" },
};
async function forms(client) {
  const lang = process.env.AM11_LANG === "en" ? "en" : "th";
  const x = await W.session(client, `forms-${lang}`);
  const { page } = x;
  await home(page);
  const foundLang = await page.evaluate(() => document.documentElement.lang);
  const foundAccount = await accountLanguage(page);
  const st = await ownRun(x, client, lang);
  const say = WORDS[lang];
  // the screen is in the run's language, also when the run was made by an earlier walk
  await home(page);
  await setLanguage(x, lang);
  await L.wait(1200);
  await page.goto(`${WEB()}/chat/agents/i/${st.installId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-thread"] [data-testid="am-outputs"]', { timeout: 90000 });
  await L.wait(1500);
  const count = (d, f) => (d.artifacts || []).filter((a) => a.format === f).length;
  const before = (await api(page, `/runs/${st.run1}`)).body;
  const kept = {};

  // 1 · the Word memo: every blank is something the agent has a value for
  {
    const { card, status, read } = await dropForm(x, st, "monthly_memo.docx", say.fill);
    await x.shot("01-memo-read");
    let s = read;
    if (s === "needs_mapping") {
      // the model left something for the person: the mapping screen, confirmed as proposed
      await x.click(card.locator('[data-testid="am-form-map"]'), "จับคู่ช่อง");
      const mapper = page.locator('[data-testid="am-template-mapper"]');
      await mapper.waitFor({ state: "visible", timeout: 60000 });
      await L.wait(1000);
      const selects = await mapper.locator("select").evaluateAll((els) => els.map((e) => e.value));
      x.mark(`memo: mapping screen, ${selects.filter(Boolean).length}/${selects.length} proposed`);
      await x.shot("01b-memo-mapping");
      await x.click(page.locator('[data-testid="am-template-save"]'), "บันทึกการจับคู่");
    }
    s = await settled(status);
    await card.scrollIntoViewIfNeeded();
    await L.wait(800);
    await x.shot("02-memo-filled");
    const text = await x.words(card);
    x.check(s === "filled", "the memo — a blank beside each label, a row of blanks under a row of headings — is filled", `${s} · ${text.slice(0, 160)}`);
    x.check((await card.locator('[data-testid="am-form-unfilled"]').count()) === 0, "a filled form lists nothing it lacks");
    kept.memo = await keep(x, st, "monthly_memo.docx", "monthly_memo.filled.docx");
    x.check(!!kept.memo, "the filled memo is downloaded and kept", kept.memo || "");
  }

  // 2 · the Excel cover sheet: six of its eight blanks have a value — the person confirms, and gets a draft
  {
    const { card, status, read } = await dropForm(x, st, "board_cover.xlsx", say.figures);
    await x.shot("03-cover-read");
    x.check(read === "needs_mapping", "the cover sheet waits for the person: two of its fields have no value of this agent's", read);
    if (read === "needs_mapping") {
      await x.click(card.locator('[data-testid="am-form-map"]'), "จับคู่ช่อง");
      const mapper = page.locator('[data-testid="am-template-mapper"]');
      await mapper.waitFor({ state: "visible", timeout: 60000 });
      await L.wait(1000);
      const selects = await mapper.locator("select").evaluateAll((els) => els.map((e) => ({ aria: e.getAttribute("aria-label"), value: e.value })));
      const by = await x.words(page.locator('[data-testid="am-template-proposed-by"]'));
      x.check(selects.filter((v) => v.value).length >= 4, "the mapping screen opens with what the model proposed from the form's own labels", `${by} · ${selects.filter((v) => v.value).length}/${selects.length} chosen`);
      await x.shot("04-cover-mapping");
      await x.click(page.locator('[data-testid="am-template-save"]'), "บันทึกการจับคู่");
    }
    const s = await settled(status);
    await card.scrollIntoViewIfNeeded();
    await L.wait(800);
    await x.shot("05-cover-draft");
    const lacks = await card.locator('[data-testid="am-form-unfilled"] li').allInnerTexts();
    const text = await x.words(card);
    x.check(s === "unfilled", "a form filled short is a draft, never a pass", s);
    x.check((lang === "en" ? /Fields that could not be filled/ : /ช่องที่ยังกรอกไม่ได้/).test(text),
      `what it lacks is under its heading: ${lang === "en" ? "Fields that could not be filled" : "ช่องที่ยังกรอกไม่ได้"}`, text.slice(0, 200));
    if (lang === "en") x.check(!THAI.test(lacks.join(" ")), "an English run says why each field has no value in English", lacks.join(" | "));
    x.check(lacks.length >= 1 && lacks.every((l) => / — /.test(l)), "each field it lacks is named with why", lacks.join(" | "));
    x.check(lacks.some((l) => /Headcount/.test(l)) && lacks.some((l) => /Cash at bank/.test(l)), "the two fields the agent has nothing for are the ones named", lacks.join(" | "));
    x.check(!/ทุกช่อง(ที่จับคู่ไว้)?มีค่า|every mapped field holds/.test(text), "a draft does not say every field has a value");
    kept.cover = await keep(x, st, "board_cover.xlsx", "board_cover.draft.xlsx");
    x.check(!!kept.cover, "the draft is downloaded and kept", kept.cover || "");
  }

  // 3 · a purchase request: the agent has a value for one field of twelve — refused before any work
  {
    const filesBefore = count((await api(page, `/runs/${st.run1}`)).body, "template");
    const { card, status, read } = await dropForm(x, st, "purchase_request.docx", say.request);
    const s = read === "refused" ? read : await settled(status).catch(() => read);
    await card.scrollIntoViewIfNeeded();
    await L.wait(800);
    await x.shot("06-request-refused");
    const text = await x.words(card.locator('[data-testid="am-form-text"]'));
    const named = await card.locator('[data-testid="am-form-unfilled"] li').allInnerTexts();
    x.check(s === "refused", "a form the agent can barely fill is refused before any work", `${s} · ${text.slice(0, 200)}`);
    x.check(/12/.test(text) && (lang === "en" ? /less than half/ : /ไม่ถึงครึ่ง/).test(text), "the refusal says how many fields the form expects and that the agent has too few", text.slice(0, 200));
    x.check(named.length >= 9, "the refusal names the fields the agent has nothing for", `${named.length}: ${named.slice(0, 4).join(" | ")} …`);
    x.check((await card.locator('[data-testid="am-form-download"]').count()) === 0, "a refused form offers no file");
    x.check((await card.locator('[data-testid="am-form-map"]').count()) === 1, "it offers the way on: the person may map it themselves");
    const filesAfter = count((await api(page, `/runs/${st.run1}`)).body, "template");
    x.check(filesAfter === filesBefore, "nothing was made for it", `${filesBefore} → ${filesAfter} form files`);
    kept.refusal = { text, named };
    fs.copyFileSync(path.join(FORMS, "purchase_request.docx"), path.join(x.dir, "purchase_request.docx"));
  }

  // 4 · a letter: nothing in it takes a value
  {
    const { card, read } = await dropForm(x, st, "plain_letter.docx", say.fill);
    await x.shot("07-letter-refused");
    const text = await x.words(card.locator('[data-testid="am-form-text"]'));
    x.check(read === "refused" && (lang === "en" ? /No field/ : /ไม่พบช่อง/).test(text), "a file with nothing to fill is refused as that", `${read} · ${text.slice(0, 140)}`);
    x.check((await card.locator('[data-testid="am-form-map"]').count()) === 0, "there is nothing to map, and no button for it");
  }

  const after = (await api(page, `/runs/${st.run1}`)).body;
  x.check(["docx", "pptx", "xlsx"].every((f) => count(after, f) === count(before, f)), "none of it made the agent's own files again",
    JSON.stringify(["docx", "pptx", "xlsx"].map((f) => [f, count(before, f), count(after, f)])));
  const tpl = await api(page, `/installs/${st.installId}/template`);
  x.check(tpl.status === 404, "the agent's own saved form is untouched (it has none)", String(tpl.status));
  // the walk's account is left in the language it was found in
  await home(page);
  await setLanguage(x, foundLang);
  await L.wait(1500);
  if (foundAccount && (await accountLanguage(page)) !== foundAccount) {
    await page.evaluate(async ([gw, l]) => {
      await fetch(`${gw}/portal/me`, { method: "PATCH", credentials: "include", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify({ preferred_language: l }) });
    }, [gateway(), foundAccount]);
  }
  await done(x, "forms", { lang, ...st, kept });
}

// ── A: both languages, every page ──────────────────────────────────────────────────────────────
const PAGES = ["/chat", "/chat/settings", "/chat/tasks", "/chat/skills", "/chat/connectors", "/chat/memory", "/chat/knowledge", "/chat/projects",
  "/chat/archive", "/chat/writing-style"];
// what a Thai screen keeps in Latin letters on purpose: names, formats, standards, version marks (GLOSSARY.md §5)
const KEEP = /^(SRAN|Chat|AI|Agent|Market|agent|Connectors?|Skills?|Canvas|MCP|PDF|DOCX|XLSX|PPTX|CSV|TXT|PNG|JPG|XML|JSON|Excel|Word|PowerPoint|Google|GitHub|Slack|Gmail|Drive|Calendar|Chrome|OpenAI|Whisper|LibreOffice|Sarabun|URL|API|ID|KB|MB|GB|DPI|OK|vs|Ctrl|Shift|Enter|Esc|Alt|Tab|Mod|VAT|WHT|GL|PO|GR|THB|USD|Tax|Python|docx|xlsx|pptx|pdf|csv|xml|xlsm|txt|png|jpg|jpeg|chat|html|conversations|json|ZIP|zip)$/;
// what is on screen without being anybody's words to translate: what people typed, a model's answer, names of models
const TYPED = ['[data-testid="conversation-item"]', "[data-conversation-id]", '[data-testid="assistant-body"]', '[data-testid="model-row-name"]',
  '[data-testid="model-trigger-label"]', "code", "pre", "kbd", '[data-testid="memory-content"]', '[data-testid="memory-origin"] a',
  ':has(> [data-testid="memory-content"]) > p.italic', '[data-testid="am-user-item"]'].join(", ");
const leaks = (page, locale) => R.leaks(page, locale, { notApps: TYPED, keep: KEEP.source, tag: TAG });

/** Every string the API sent for this account's Agent Market (what the server and the packages say),
 *  and, apart, what is the person's own in it: the names of their files and the labels of their forms. */
async function apiWords(page, st) {
  const said = new Set();
  const own = new Set([TAG, COMPANY, ...W.FIN.map((f) => f.name), ...fs.readdirSync(FORMS)]);
  // what a person named: every install of theirs carries the title it was given when it was made
  for (const i of (await api(page, "/installs")).body?.installs || []) if (i && i.title) own.add(String(i.title));
  const take = (v) => {
    if (typeof v === "string") { const t = v.replace(/\s+/g, " ").trim(); if (t.length >= 3) said.add(t); } else if (Array.isArray(v)) v.forEach(take); else if (v && typeof v === "object") Object.values(v).forEach(take);
  };
  for (const p of ["/catalog", `/catalog/${SLUG}`, `/catalog/${SLUG}/datasheet`, "/installs", "/rail", `/installs/${st.installId}`, `/installs/${st.installId}/runs`,
    `/runs/${st.run1}`, `/runs/${st.run1}/template`, `/installs/${st.installId}/models`, `/installs/${st.installId}/estimate`, `/installs/${st.installId}/storage`]) {
    take((await api(page, p)).body);
  }
  const thread = (await api(page, `/installs/${st.installId}/thread`)).body;
  for (const it of (thread && thread.items) || []) {
    const pl = it.payload || {};
    if (it.kind === "form") {
      // a form's file name and the labels in it are the customer's words, shown as written
      for (const w of [pl.name, ...(pl.unfilled || [])]) if (w) own.add(String(w));
      take({ text: pl.text, why: pl.why, unfilled_why: pl.unfilled_why });
    } else if (it.kind === "user") {
      for (const f of pl.files || []) if (f && f.name) own.add(String(f.name));
    } else {
      take(pl);
    }
  }
  // the mapping of the run's form: its slots' labels are the form's own
  const form = (await api(page, `/runs/${st.run1}/template`)).body;
  for (const sl of (form && form.form && form.form.slots) || (form && form.slots) || []) if (sl && sl.label) own.add(String(sl.label));
  return { said: [...said], own: [...own] };
}
const squash = (s) => s.replace(/\s+/g, "").toLowerCase();
/** Whose words a string on screen is: the person's, the server's or a package's, or the app's own. */
function pile(text, words) {
  // a line of a list is read without its bullet ("· ครบ")
  const t = squash(text.replace(/^[\s·—–-]+/, ""));
  if (words.said.some((w) => squash(w) === t)) return words.own.some((w) => squash(w) === t) ? "person" : "server";
  const inside = (list, min) => list.some((w) => { const v = squash(w); return v.length >= min && (v.includes(t) || t.includes(v)); });
  if (inside(words.own, 3)) return "person";
  if (inside(words.said, 4)) return "server";
  return "app";
}

async function language(client) {
  const x = await W.session(client, "language");
  const { page } = x;
  await home(page);
  const found = { locale: await page.evaluate(() => document.documentElement.lang), account: await accountLanguage(page) };
  log(`${x.tag}: found — page ${found.locale}, account ${found.account}`);
  const amPagesOf = (st) => ["/chat/agents", "/chat/agents/mine", `/chat/agents/${SLUG}`, `/chat/agents/${SLUG}/datasheet`, `/chat/agents/install/${SLUG}`,
    `/chat/agents/i/${st.installId}`, `/chat/agents/i/${st.installId}/settings`, `/chat/agents/i/${st.installId}/dashboard`, `/chat/agents/runs/${st.run1}`];
  const runs = {};
  const report = { found, th: {}, en: {}, notOpened: [], piles: {} };
  try {
    for (const to of found.locale === "th" ? ["en", "th"] : ["th", "en"]) {
      // the run this language is read with was started on a screen in this language
      runs[to] = await ownRun(x, client, to);
      await home(page);
      const got = await setLanguage(x, to);
      x.check(got === to, `the switch turns the page ${to}`, got);
      await L.wait(1500);
      for (const p of [...PAGES, ...amPagesOf(runs[to])]) {
        const r = await page.goto(`${WEB()}${p}`, { waitUntil: "load" }).catch(() => null);
        if (!r || r.status() >= 400) {
          report.notOpened.push(`${to} · page ${p}: ${r ? r.status() : "no response"}`);
          continue;
        }
        await L.wait(p.startsWith("/chat/agents") ? 3500 : 2200);
        const name = p.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, "<id>");
        report[to][name] = await leaks(page, to);
        await x.shot(`${to}-${name.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "chat"}`, { fullPage: true });
      }
    }
    // whose words is each string that is not in the screen's language?
    for (const lang of ["en", "th"]) {
      await home(page);
      await setLanguage(x, lang);
      await L.wait(1200);
      const words = await apiWords(page, runs[lang]);
      const piles = { app: [], server: [], person: [] };
      for (const e of R.flat(report[lang])) piles[pile(e.text, words)].push({ text: e.text, on: e.on });
      report.piles[lang] = piles;
      log(`${x.tag}: ${lang} screen, not in ${lang} — the app's own ${piles.app.length} · the server's or a package's ${piles.server.length} · the person's data ${piles.person.length}`);
    }
    x.check(report.piles.en.app.length === 0, "English screen: no Thai string of the app's own (its dictionaries) is left",
      report.piles.en.app.slice(0, 6).map((e) => `${e.text} @ ${e.on[0]}`).join(" | "));
    x.check(report.piles.th.app.length === 0, "Thai screen: no English string of the app's own is left",
      report.piles.th.app.slice(0, 6).map((e) => `${e.text} @ ${e.on[0]}`).join(" | "));
  } finally {
    if (found.locale === "th" || found.locale === "en") {
      await home(page);
      await setLanguage(x, found.locale);
      await L.wait(2000);
      if (found.account && (await accountLanguage(page)) !== found.account) {
        await page.evaluate(async ([gw, lang]) => {
          await fetch(`${gw}/portal/me`, { method: "PATCH", credentials: "include", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify({ preferred_language: lang }) });
        }, [gateway(), found.account]);
        await L.wait(800);
      }
      const back = { page: await page.evaluate(() => document.documentElement.lang), account: await accountLanguage(page) };
      x.check(back.page === found.locale && back.account === found.account, "the walk's own account is left in the language it was found in",
        `page ${back.page} (found ${found.locale}) · account ${back.account} (found ${found.account})`);
    }
  }
  report.flat = { th: R.flat(report.th), en: R.flat(report.en) };
  fs.writeFileSync(path.join(x.dir, "language-strings.json"), JSON.stringify(report, null, 1));
  await done(x, "language", { found, surfaces: { th: Object.keys(report.th).length, en: Object.keys(report.en).length },
    left: { th: report.flat.th.length, en: report.flat.en.length },
    piles: Object.fromEntries(Object.entries(report.piles).map(([l, v]) => [l, { app: v.app.length, server: v.server.length, person: v.person.length }])), notOpened: report.notOpened });
}

// ── E: the page a consent flow ends on ─────────────────────────────────────────────────────────
async function consent() {
  const x = await W.session("web", "consent");
  const { page } = x;
  await home(page);
  const found = await page.evaluate(() => document.documentElement.lang);
  const where = `${WEB()}/chat/agents`;
  await page.goto(where, { waitUntil: "load" });
  await L.wait(1500);
  const gw = gateway();
  const ticket = (query) => page.evaluate(async ([gw, query]) => {
    const r = await fetch(`${gw}/portal/connectors/mail/ticket?${query}`, { method: "POST", credentials: "include" });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [gw, query]);
  const seen = {};
  for (const client of ["web", "desktop"]) {
    for (const lang of ["th", "en"]) {
      for (const answer of ["allow", "deny"]) {
        const key = `${client}-${lang}-${answer}`;
        const asked = client === "desktop" ? `client=desktop&lang=${lang}` : `client=web&lang=${lang}&return_to=${encodeURIComponent(where)}`;
        const t = await ticket(asked);
        if (t.status !== 200 || !t.body || !t.body.start_url) {
          x.check(false, `${key}: a ticket for the consent flow`, `${t.status} ${JSON.stringify(t.body).slice(0, 160)}`);
          continue;
        }
        // the consent page opens in a tab of its own, as window.open (or the system browser) does it
        const tab = await page.context().newPage();
        // the page for the desktop hands over to the app by a sran:// link the browser cannot follow: seen, not followed
        const handed = [];
        tab.on("request", (r) => { if (r.url().startsWith("sran:")) handed.push(r.url()); });
        await tab.goto(t.body.start_url, { waitUntil: "load" }).catch(() => null);
        const onStandIn = await tab.locator(`#${answer}`).count();
        // a deployment with no provider set up (live: no Google client) never reaches a consent page:
        // the flow ends at once on the return page, saying the connector is not set up
        const unset = !onStandIn && (await tab.locator("body[data-status]").count()) === 1;
        if (unset && answer === "allow") { await tab.close(); continue; } // one page per language and client, read once
        x.check(onStandIn === 1 || unset, unset ? `${key}: no provider is set up here — the flow ends on the return page` : `${key}: the flow reaches the consent page`, tab.url().slice(0, 90));
        if (!onStandIn && !unset) { await tab.close(); continue; }
        if (onStandIn) {
          await tab.locator(`#${answer}`).click({ noWaitAfter: true });
          await tab.waitForURL(/\/portal\/connectors\/oauth\/callback/, { timeout: 30000 }).catch(() => null);
          await tab.waitForLoadState("domcontentloaded").catch(() => null);
        }
        await L.wait(1200);
        const body = tab.locator("body");
        const text = (await body.innerText().catch(() => "")).replace(/\s+/g, " ").trim();
        const html = await tab.content().catch(() => "");
        seen[key] = { text, client: await body.getAttribute("data-client").catch(() => null), status: await body.getAttribute("data-status").catch(() => null),
          htmlLang: await tab.evaluate(() => document.documentElement.lang).catch(() => null) };
        await tab.screenshot({ path: path.join(x.dir, `return-${key}.png`) }).catch(() => null);
        x.check(seen[key].client === client, `${key}: the page is the one written for that client`, String(seen[key].client));
        x.check(seen[key].htmlLang === lang, `${key}: the page is in the language of the screen that asked`, `lang=${seen[key].htmlLang}`);
        x.check(lang === "en" ? !THAI.test(text) : THAI.test(text) && !/Connected|Nothing was connected|Close this tab|Back to SRAN/.test(text),
          `${key}: one language, not both`, text.slice(0, 160));
        x.check(seen[key].status === (answer === "allow" ? "ok" : "error"), `${key}: it says how the flow ended`, `${seen[key].status}`);
        x.check(!/standin-(access|refresh)-token|the person said no|access_denied/.test(html), `${key}: no token and no provider text on the page`);
        if (client === "desktop") {
          const link = (await tab.locator('a[href^="sran://"]').first().getAttribute("href").catch(() => null)) || handed[0] || (/sran:\/\/[^"'<\s]+/.exec(html) || [])[0] || null;
          seen[key].link = link ? link.replace(/&amp;/g, "&") : null;
          x.check(!!link && new RegExp(`^sran://oauth/callback\\?connector=mail&(amp;)?status=${answer === "allow" ? "ok" : "error"}`).test(link),
            `${key}: the page hands the app the connector and the outcome`, String(link));
          x.check(/Connectors/.test(text), `${key}: it says the app opens its Connectors page`, text.slice(0, 140));
        } else {
          x.check(!/Desktop|sran:\/\//.test(html), `${key}: a page for the web names no desktop app and carries no app link`);
          const back = tab.locator("a.go");
          const href = (await back.count()) ? await back.getAttribute("href") : null;
          x.check(href === where, `${key}: it offers the way back to where the person was`, String(href));
          if (href) {
            await back.click();
            await tab.waitForLoadState("load").catch(() => null);
            await L.wait(1800);
            x.check(tab.url().startsWith(where) && (await tab.locator('[data-testid="am-market-grid"]').count()) === 1, `${key}: pressing it lands the person back in the app, where they were`, tab.url());
          }
        }
        await tab.close();
      }
    }
  }
  // Where the app lands on that link — its Connectors page, saying what came back — exists only inside the
  // desktop app (the page is the host's): it is not in a browser, and is not looked for here. It is
  // covered by ConnectorReturnNotice.test.tsx and apps/desktop/test/connector-return.test.ts, and by a
  // walk inside the app when one is run.
  await home(page);
  await setLanguage(x, found);
  fs.writeFileSync(path.join(x.dir, "consent.json"), JSON.stringify(seen, null, 1));
  await done(x, "consent", { seen });
}

const phases = { forms, language, consent, cleanup: W.phases.cleanup };
const cmd = process.argv[2];
if (!phases[cmd]) {
  console.log(`usage: walk.js ${Object.keys(phases).join(" | ")} <web|desktop>`);
  process.exit(2);
}
// The desktop app has one signed-in person. A walk inside it is a walk as that person, so it is
// run only when whoever starts it says the account signed in there is the walk's own
// (CARD-RULES 6b: the owner's account and data are not test material).
// (the library's cleanup attaches to the desktop app on live unless told "web": here it is always the web)
if (cmd === "cleanup") process.argv[3] = "web";
const client = process.argv[3] || "web";
if (client === "desktop" && process.env.AM11_DESKTOP_ACCOUNT_IS_THE_WALKS_OWN !== "yes") {
  console.log("refused: the desktop app is signed in as a person. Sign a throwaway account in to it, then set AM11_DESKTOP_ACCOUNT_IS_THE_WALKS_OWN=yes");
  process.exit(2);
}
phases[cmd](client).catch((e) => {
  log(`ERROR ${String((e && e.stack) || e).slice(0, 900)}`);
  process.exit(1);
});
