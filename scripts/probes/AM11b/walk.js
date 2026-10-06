#!/usr/bin/env node
// AM11b — quiet the agent's room, walked on the built clients by clicking the real UI (CARD-RULES §6b),
// with the walk's own account and its own installs.
//
//   round            a round of the report agent from its own sample month: the greeting (three ticks, the
//                    files it gives, that a form can be attached), the footer (one line), the line where a
//                    run starts (the time, or nothing), the files card and the run page (no credits)
//   money <th|en>    every Agent Market page a person sees — the market, each agent's page with "รายละเอียด"
//                    opened, each data sheet, the workspace, the run page, the settings page, the dashboard —
//                    read for เครดิต · บาท · credit · cost · baht · THB · ฿ in the text and in every title,
//                    label, placeholder and alt. Each place is written down with what it says; a hit that
//                    names credits and is not the accounting word fails the walk
//   chats            "แชทใหม่กับ agent นี้" pressed five times; empty chats that were already there; a chat
//                    with a round left untouched; two chats side by side
//   picker <slug>    the model menu: its rows, nothing greyed; each model picked, a round started, and the
//                    model that ran read from the run's own steps
//   page <th|en>     the agent's page: what is on screen by default, and what "รายละเอียด" holds
//   cleanup          the walk's installs, by id
//
//   SMOKE_TARGET=test node scripts/probes/AM11b/walk.js round
"use strict";
const fs = require("node:fs");
const path = require("node:path");

process.env.AM6_TAG = process.env.AM6_TAG || "[AM11b]";
const TARGET = process.env.SMOKE_TARGET || "test";
process.env.AM6_OUT = process.env.AM6_OUT || path.join(__dirname, "results", "walk", TARGET);
const W = require("../AM6/walkthrough.js");
const { L, state, save, log, api, WEB, waitFor, TAG } = W;

const REPORT = "sran.financial-report";
const PROCUREMENT = "sran.procurement";
const CARD = { [REPORT]: { th: "รายงานผลประกอบการ", en: "Financial Report" }, [PROCUREMENT]: { th: "จัดซื้อ", en: "Procurement" } };
const MONEY = /เครดิต|บาท|credit|cost|baht|THB|฿/gi;
const CREDITS = /เครดิต|credit/i;
const ACCOUNTING = /เดบิต|debit|บัตรเครดิต|credit card|credit note|ใบลดหนี้/i;

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
async function ownInstall(x, slug) {
  const { page } = x;
  const key = `am11b_${slug}`;
  const mine = (await api(page, "/installs")).body?.installs || [];
  if (state[key] && mine.some((i) => i.id === state[key] && i.status !== "removed")) return state[key];
  const lang = await page.evaluate(() => document.documentElement.lang);
  const card = (CARD[slug] || {})[lang] || (CARD[slug] || {}).th;
  const id = mine.some((i) => i.slug === slug && i.status !== "removed") ? await W.installAnother(x, slug, card) : (await W.installFromMarket(x, card)).installId;
  state[key] = id;
  state.installs = [...new Set([...(state.installs || []), id])];
  save();
  return id;
}
async function workspace(x, installId, thread) {
  await x.page.goto(`${WEB()}/chat/agents/i/${installId}${thread ? `?thread=${thread}` : ""}`, { waitUntil: "load" });
  await x.page.waitForSelector('[data-testid="composer-root"]', { timeout: 90000 });
  await L.wait(1500);
}

/** Every place on the page that names money: where (text, or which attribute), the match, and the words round it. */
async function moneyOn(page) {
  return page.evaluate((src) => {
    const re = new RegExp(src, "gi");
    const out = [];
    const note = (where, text) => {
      for (const m of String(text || "").matchAll(re)) {
        const at = m.index || 0;
        out.push({ where, match: m[0], says: String(text).slice(Math.max(0, at - 45), at + 45).replace(/\s+/g, " ").trim() });
      }
    };
    note("text", document.body.innerText);
    for (const el of document.querySelectorAll("[title],[aria-label],[placeholder],[alt]")) {
      for (const a of ["title", "aria-label", "placeholder", "alt"]) if (el.hasAttribute(a)) note(a, el.getAttribute(a));
    }
    return out;
  }, MONEY.source);
}

// ── round: the report agent, its own sample month ─────────────────────────────────────────────────
async function round() {
  const x = await W.session("web", "round");
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  const installId = await ownInstall(x, REPORT);
  await workspace(x, installId);
  // the greeting: ticks by name, the files it gives, and that a form can be attached
  const greeting = page.locator('[data-testid="am-greeting"]').first();
  const ticks = await greeting.locator("li").allInnerTexts();
  x.check(ticks.length > 0 && ticks.length <= 3 && ticks.every((t) => !t.includes(" — ") && t.length < 60), "the greeting's ticks are the things to send, by name — three at most, no note on any", ticks.join(" | "));
  x.check((await greeting.locator('[data-testid="am-greeting-form"]').count()) === 1, "it says in one line that a form of the person's own can be attached", await x.words(greeting.locator('[data-testid="am-greeting-form"]')));
  const footer = page.locator('[data-testid="am-ws-footer"]');
  const foot = await x.words(footer);
  x.check((await footer.locator("p").count()) <= 1 && foot === "agent นี้อ่านเฉพาะไฟล์ที่คุณส่งให้", "the footer is one short line", foot);
  // where a run starts: the time, and only the time (or nothing)
  const est = (await x.words(page.locator('[data-testid="am-estimate"]').first()).catch(() => "")) || "";
  x.check(est === "" || /^ราว \d+ (วินาที|นาที)$/.test(est), "the line where a run starts is the time and nothing else (or is not there)", est || "(no line)");
  x.check((await page.locator('header [data-testid="credit-badge"], [data-testid="credit-badge"]').count()) === 0 && !/เครดิต/.test(await page.evaluate(() => document.body.innerText)), "no credits anywhere in the agent's room — the chat's pill included");
  await x.shot("01-empty-workspace");
  const { runId } = await W.sendFiles(x, installId, W.FIN, `${TAG} รายงานผลประกอบการเดือนกันยายน 2569`, "02");
  const end = await W.finish(x, runId, "02");
  x.check(end.run.status === "succeeded", "the round delivers", `${end.run.status} ${end.run.status_reason || ""}`);
  const outputs = page.locator(`[data-testid="am-outputs"][data-run="${runId}"]`).last();
  await outputs.waitFor({ state: "visible", timeout: 120000 }).catch(() => null);
  const details = outputs.locator("summary, button").filter({ hasText: /รายละเอียดการตรวจ/ }).first();
  if (await details.count()) await x.click(details, "รายละเอียดการตรวจ และโมเดล");
  await L.wait(800);
  const card = await x.words(outputs);
  x.check(!CREDITS.test(card), "the files card says nothing of credits, opened to its details", card.slice(0, 160));
  await outputs.scrollIntoViewIfNeeded().catch(() => {});
  await x.shot("03-files-card");
  await page.goto(`${WEB()}/chat/agents/runs/${runId}`, { waitUntil: "load" });
  await page.waitForSelector('[data-testid="am-run-step"]', { timeout: 60000 }).catch(() => null);
  await L.wait(1200);
  const run = await page.evaluate(() => document.body.innerText);
  x.check(!CREDITS.test(run), "the run page says nothing of credits — not for the run, not on a step");
  await x.shot("04-run-page", { fullPage: true });
  state.am11b_run = runId;
  save();
  await done(x, "round", { installId, runId, estimate: est, ticks });
}

// ── money: every page, read for money ─────────────────────────────────────────────────────────────
async function money(lang) {
  const x = await W.session("web", `money-${lang}`);
  const { page } = x;
  await home(page);
  await setLanguage(x, lang);
  const installId = state[`am11b_${REPORT}`];
  const slugs = ((await api(page, "/catalog")).body?.agents || []).map((a) => a.slug).filter(Boolean);
  x.check(slugs.length >= 6, "the market lists the six agents to this account", slugs.join(", "));
  const pages = [["the market", "/chat/agents"], ["my agents", "/chat/agents/mine"]];
  for (const s of slugs) pages.push([`${s} — its page`, `/chat/agents/${s}`, true], [`${s} — data sheet`, `/chat/agents/${s}/datasheet`]);
  if (installId) {
    pages.push(["workspace (report agent, after a round)", `/chat/agents/i/${installId}`], ["settings", `/chat/agents/i/${installId}/settings`],
      ["dashboard", `/chat/agents/i/${installId}/dashboard`]);
    if (state.am11b_run) pages.push(["run page", `/chat/agents/runs/${state.am11b_run}`], ["run page — source of figures", `/chat/agents/runs/${state.am11b_run}?tab=numbers`]);
  }
  const found = [];
  for (const [name, href, openMore] of pages) {
    await page.goto(`${WEB()}${href}`, { waitUntil: "load" });
    await L.wait(2500);
    if (openMore) {
      const more = page.locator('[data-testid="am-detail-more-toggle"]');
      if (await more.count()) await more.click().catch(() => {});
      await L.wait(600);
    }
    for (const d of await page.locator("details:not([open]) > summary").all()) await d.click().catch(() => {});
    await L.wait(400);
    const hits = await moneyOn(page);
    const price = hits.filter((h) => CREDITS.test(h.match) && !ACCOUNTING.test(h.says));
    for (const h of hits) found.push({ page: name, ...h, kind: CREDITS.test(h.match) ? (ACCOUNTING.test(h.says) ? "accounting word" : "CREDITS") : "amount or unit" });
    x.check(price.length === 0, `${name}: nothing names credits`, price.map((h) => `${h.where}: ${h.says}`).join(" ‖ ") || `${hits.length} other mention(s) of money`);
  }
  fs.writeFileSync(path.join(x.dir, "money.json"), JSON.stringify(found, null, 1));
  const by = {};
  for (const f of found) by[f.kind] = (by[f.kind] || 0) + 1;
  x.mark(`pages read: ${pages.length} · mentions: ${JSON.stringify(by)}`);
  await done(x, `money-${lang}`, { pages: pages.length, by });
}

// ── chats: one empty chat at a time ───────────────────────────────────────────────────────────────
async function chats() {
  const x = await W.session("web", "chats");
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  const installId = await ownInstall(x, REPORT);
  await workspace(x, installId);
  const rows = async () => (await api(page, `/installs/${installId}/threads`)).body?.threads || [];
  const before = await rows();
  x.check(before.some((c) => c.rounds > 0), "there is a chat with a round to leave untouched", before.map((c) => `${c.title} · ${c.rounds}`).join(" | "));
  const kept = before.find((c) => c.rounds > 0);
  await x.shot("01-sidebar-before");
  // five presses
  const press = async (n) => {
    const btn = page.locator('[data-testid="agent-chat-rows"]').first().locator("button").first();
    await x.click(btn, `แชทใหม่กับ agent นี้ (${n})`);
    await L.wait(1800);
  };
  for (let i = 1; i <= 5; i++) {
    await press(i);
    if (i < 5) await workspace(x, installId); // back to the chat with the round, the way a person wanders
  }
  const after = await rows();
  const empty = after.filter((c) => c.rounds === 0 && !c.named);
  x.check(after.length === before.length + (before.some((c) => c.rounds === 0 && !c.named) ? 0 : 1) && empty.length === 1, "five presses: one empty chat, not five", after.map((c) => `${c.title} · ${c.rounds}`).join(" | "));
  x.check(empty.length === 1 && /^(แชทใหม่|แชทแรก)$/.test(empty[0].title), "the empty chat is named for what it is, with no date", empty.map((c) => c.title).join(" | "));
  const still = after.find((c) => (c.id || "main") === (kept.id || "main"));
  x.check(!!still && still.rounds === kept.rounds && still.title === kept.title, "the chat with a round is as it was", still ? `${still.title} · ${still.rounds}` : "gone");
  const shown = await page.locator('[data-testid="agent-chat-row"]').count();
  x.check(shown >= after.length, "the sidebar lists the same chats", `${shown} rows on screen, ${after.length} from the server`);
  await x.shot("02-sidebar-after-five-presses");
  // its run page still opens with its files
  if (state.am11b_run) {
    const d = (await api(page, `/runs/${state.am11b_run}`)).body || {};
    x.check(d.run && d.run.status === "succeeded" && (d.artifacts || []).length > 0, "the round's run page still has its steps and its files", `${d.run && d.run.status} · ${(d.artifacts || []).length} files · ${(d.steps || []).length} steps`);
  }
  // two chats side by side
  const other = after.find((c) => (c.id || "main") !== (kept.id || "main"));
  if (other) {
    await page.goto(`${WEB()}/chat/agents/i/${installId}${kept.id ? `?thread=${kept.id}&` : "?"}beside=${other.id || "main"}`, { waitUntil: "load" });
    const pane = page.locator('[data-testid="am-beside"]');
    await pane.waitFor({ state: "visible", timeout: 60000 }).catch(() => null);
    x.check((await pane.count()) === 1, "two chats still open side by side", await x.words(page.locator('[data-testid="am-beside-title"]')));
    await x.shot("03-side-by-side");
  }
  await done(x, "chats", { before: before.map((c) => [c.title, c.rounds]), after: after.map((c) => [c.title, c.rounds]) });
}

// ── picker: what is picked is what runs ───────────────────────────────────────────────────────────
async function picker(slug) {
  const x = await W.session("web", `picker-${slug.split(".").pop()}`);
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  const installId = await ownInstall(x, slug);
  await workspace(x, installId);
  const served = (await api(page, `/installs/${installId}/models`)).body || {};
  const listed = (served.models || []).map((m) => m.id);
  x.check((served.models || []).every((m) => m.allowed !== false), "the server lists no model that cannot be picked", `${listed.length} models · outside_absent=${served.outside_absent}`);
  await x.click(page.locator('[data-testid="am-model-control"]').first(), "model menu");
  const menu = page.locator('[data-testid="am-model-menu"]');
  await menu.waitFor({ state: "visible", timeout: 30000 });
  for (;;) {
    const more = menu.locator("button").filter({ hasText: /^…(อีก \d+ รายการ|\d+ more)$/ }).first();
    if (!(await more.count())) break;
    await more.click();
  }
  const names = await menu.locator('[data-testid="model-row-name"]').allInnerTexts();
  x.check((await menu.locator('[data-testid="model-row-disabled"]').count()) === 0 && (await menu.locator('[data-testid="model-picker-notice"], [data-testid="model-group-hidden"], [data-testid="am-model-egress"]').count()) === 0,
    "the menu is the chat's picker: no greyed row, no reason in a header, no count of hidden models, no egress line", `${names.length} rows`);
  const absent = menu.locator('[data-testid="am-model-outside-absent"]');
  x.check(((await absent.count()) === 1) === !!served.outside_absent, "one line says the outside models are absent — only when they are", (await absent.count()) ? await x.words(absent) : "(no line)");
  x.check(names.length === listed.length + 1, "its rows are อัตโนมัติ and the models the server lists, each once", names.join(" · "));
  await x.shot("01-menu");
  await page.keyboard.press("Escape");
  const pickAt = Math.min(Number(process.env.AM11B_PICKS || 5), names.length);
  const expand = async () => {
    for (;;) {
      const more = menu.locator("button").filter({ hasText: /^…(อีก \d+ รายการ|\d+ more)$/ }).first();
      if (!(await more.count())) break;
      await more.click();
    }
  };
  const ran = [];
  // each row in turn, as a person would: the row is clicked, and what the server then holds is what was picked
  for (let n = 0; n < pickAt; n++) {
    await workspace(x, installId);
    await x.click(page.locator('[data-testid="am-model-control"]').first(), "model menu");
    await menu.waitFor({ state: "visible", timeout: 30000 });
    await expand();
    const row = menu.locator('[role="option"]').nth(n);
    const rowName = ((await row.locator('[data-testid="model-row-name"]').innerText().catch(() => "")) || "").trim();
    await x.click(row, `pick row ${n + 1}: ${rowName}`);
    await L.wait(1500);
    const now = (await api(page, `/installs/${installId}/models`)).body || {};
    const id = now.choice;
    x.check(n === 0 ? id === "auto" : listed.includes(id), `${rowName}: the pick is saved, and is a model of the list`, `choice=${id}`);
    const pill = await x.words(page.locator('[data-testid="am-model-control"]').first());
    x.check(n === 0 ? /อัตโนมัติ|Auto/.test(pill) : pill.toLowerCase().includes(rowName.toLowerCase().slice(0, 12)), `${rowName}: the composer shows what was picked`, pill);
    let runId;
    if (slug === PROCUREMENT) {
      const start = page.locator('[data-testid="am-round-start"]');
      if (await start.count()) await x.click(start.last(), "เริ่มรอบใหม่");
      await L.wait(800);
      await page.locator('[data-testid="am-composer-input"]').fill(`${TAG} ขอซื้อเก้าอี้สำนักงาน 20 ตัว มีที่วางแขน ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ (${n + 1})`);
      const had = ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).map((r) => r.id);
      await x.click(page.locator('[data-testid="am-composer-send"]'), "ส่ง");
      const r = await waitFor(async () => ((await api(page, `/installs/${installId}/runs`)).body?.runs || []).find((y) => y.kind === "run" && !had.includes(y.id)), 60000, 1000);
      runId = r && r.id;
    } else {
      runId = (await W.sendFiles(x, installId, W.FIN, `${TAG} รายงานผลประกอบการเดือนกันยายน 2569 (${n + 1})`, `0${n + 2}`)).runId;
    }
    if (!runId) { x.check(false, `${rowName}: a round starts`); continue; }
    // until the run asks or ends; a question is answered as the card would record by default
    let d;
    if (slug === PROCUREMENT) {
      d = await waitFor(async () => {
        const r = (await api(page, `/runs/${runId}`)).body;
        return r && r.run && (["succeeded", "failed", "cancelled"].includes(r.run.status) || r.run.status === "awaiting_input") ? r : null;
      }, 600000, 2000);
    } else {
      d = (await W.finish(x, runId, `0${n + 2}`)).detail;
    }
    const models = [...new Set(((d && d.steps) || []).filter((s) => s.kind === "ai" && s.model_id).map((s) => s.model_id))];
    const want = id === "auto" ? served.auto_model : id;
    ran.push({ row: rowName, picked: id, ran: models, status: d && d.run.status });
    x.check(models.length > 0 && models.every((m) => m === want), `${rowName}: the model that ran is the model that was picked`, `picked ${id} · ran ${models.join(", ") || "none"}${id === "auto" ? ` (อัตโนมัติ is ${served.auto_model} now)` : ""}`);
    if (slug === PROCUREMENT && d && d.run.status === "awaiting_input") await api(page, `/runs/${runId}/cancel`, { method: "POST" });
  }
  await done(x, `picker-${slug.split(".").pop()}`, { listed, rows: names, picked: ran, listed_count: listed.length, picked_count: pickAt });
}

// ── page: a workspace, not a brochure ─────────────────────────────────────────────────────────────
async function agentPage(lang) {
  const x = await W.session("web", `page-${lang}`);
  const { page } = x;
  await home(page);
  await setLanguage(x, lang);
  for (const slug of [REPORT, PROCUREMENT]) {
    const short = slug.split(".").pop();
    await page.goto(`${WEB()}/chat/agents/${slug}`, { waitUntil: "load" });
    const more = page.locator('[data-testid="am-detail-more"]');
    await more.waitFor({ state: "attached", timeout: 60000 });
    await L.wait(1500);
    x.check((await more.evaluate((d) => d.open)) === false, `${short}: “รายละเอียด” is closed until a person opens it`, await x.words(page.locator('[data-testid="am-detail-more-toggle"]')));
    const headings = await page.locator("h2:visible").allInnerTexts();
    x.check(headings.length === 2, `${short}: on screen by default — what to give it, what comes back — and no other section`, headings.join(" · "));
    for (const inside of ["am-guide-never", "am-time", "am-datasheet-link"]) {
      x.check((await page.locator(`[data-testid="${inside}"]`).count()) === 0 || !(await page.locator(`[data-testid="${inside}"]`).first().isVisible()), `${short}: ${inside} is not the first thing a person reads`);
    }
    await x.shot(`${short}-01-default`, { fullPage: true });
    await x.click(page.locator('[data-testid="am-detail-more-toggle"]'), "รายละเอียด");
    await L.wait(800);
    const opened = await page.locator("h2:visible").allInnerTexts();
    x.check(opened.length >= 5 && (await page.locator('[data-testid="am-datasheet-link"]').isVisible()), `${short}: everything else is still in the product, one click away`, opened.join(" · "));
    await x.shot(`${short}-02-details-open`, { fullPage: true });
  }
  await done(x, `page-${lang}`);
}

async function cleanup() {
  const s = await W.openWeb(process.env.AM6_EMAIL || L.TS_USER.email);
  await s.page.goto(`${WEB()}/chat`, { waitUntil: "load" });
  for (const id of [...new Set(state.installs || [])].filter(Boolean)) {
    const r = await api(s.page, `/installs/${id}`, { method: "DELETE" });
    log(`install ${id} removed: ${r.status}`);
  }
  state.installs = [];
  for (const k of Object.keys(state)) if (k.startsWith("am11b_")) delete state[k];
  save();
  await s.close();
}

const phase = process.argv[2];
(async () => {
  if (phase === "round") await round();
  else if (phase === "money") await money(process.argv[3] === "en" ? "en" : "th");
  else if (phase === "chats") await chats();
  else if (phase === "picker") await picker(process.argv[3] || REPORT);
  else if (phase === "page") await agentPage(process.argv[3] === "en" ? "en" : "th");
  else if (phase === "cleanup") await cleanup();
  else throw new Error("round | money <th|en> | chats | picker <slug> | page <th|en> | cleanup");
})().catch((e) => { console.error(e); process.exit(1); });
