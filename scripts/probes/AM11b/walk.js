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
// the ledger's own word (a debit and a credit column, a credit note, a credit card) is the work, not the price
const ACCOUNTING = /เดบิต|debit|คอลัมน์เครดิต|ยอดรวมเครดิต|credit column|credit total|บัตรเครดิต|credit card|credit note|ใบลดหนี้/i;

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
  // (the ledger's own word — "เดบิตเครดิตสมดุล" in a finished round's progress — is the work, not a price)
  const priced = (await moneyOn(page)).filter((h) => CREDITS.test(h.match) && !ACCOUNTING.test(h.says));
  x.check((await page.locator('[data-testid="credit-badge"]').count()) === 0 && priced.length === 0, "no credits anywhere in the agent's room — the chat's pill included",
    priced.map((h) => `${h.where}: ${h.says}`).join(" | ") || "no pill · no mention");
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
  // every agent this account has installed: its workspace, settings, dashboard and its latest run's page
  const mine = ((await api(page, "/installs")).body?.installs || []).filter((i) => i.status !== "removed" && i.status !== "catalog");
  for (const i of mine) {
    const short = String(i.slug || "").split(".").pop();
    pages.push([`${short} — workspace`, `/chat/agents/i/${i.id}`], [`${short} — settings`, `/chat/agents/i/${i.id}/settings`], [`${short} — dashboard`, `/chat/agents/i/${i.id}/dashboard`]);
    const last = ((await api(page, `/installs/${i.id}/runs`)).body?.runs || []).find((r) => r.kind === "run");
    if (last) pages.push([`${short} — run page`, `/chat/agents/runs/${last.id}`], [`${short} — run page, source of figures`, `/chat/agents/runs/${last.id}?tab=numbers`], [`${short} — run page, pages opened`, `/chat/agents/runs/${last.id}?tab=sources`]);
  }
  x.mark(`installed agents read: ${mine.map((i) => i.slug).join(", ") || "none"}${installId ? "" : " (no round of the walk's own)"}`);
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
  // (the menu is the chat's own list for this person, narrowed to what the agent can use: a model the server
  // would allow and the chat does not list for them is not a row either)
  x.check(names.length >= 2 && names.length <= listed.length + 1 && new Set(names).size === names.length, "its rows are อัตโนมัติ and models the agent can use — each once, none the server does not list",
    `${names.length} rows, ${listed.length} models the agent can use: ${names.join(" · ")}`);
  await x.shot("01-menu");
  await page.keyboard.press("Escape");
  // AM11B_ROWS="Name one|Name two": pick those rows by the name on them (a long list); else the first AM11B_PICKS rows
  const byName = (process.env.AM11B_ROWS || "").split("|").map((v) => v.trim()).filter(Boolean);
  const pickAt = byName.length || Math.min(Number(process.env.AM11B_PICKS || 5), names.length);
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
    const row = byName.length ? menu.locator('[role="option"]').filter({ hasText: byName[n] }).first() : menu.locator('[role="option"]').nth(n);
    const rowName = ((await row.locator('[data-testid="model-row-name"]').innerText().catch(() => "")) || "").trim();
    await x.click(row, `pick row ${n + 1}: ${rowName}`);
    await L.wait(1500);
    const now = (await api(page, `/installs/${installId}/models`)).body || {};
    const id = now.choice;
    x.check(id === "auto" ? /อัตโนมัติ|Auto/.test(rowName) : listed.includes(id), `${rowName}: the pick is saved, and is a model of the list`, `choice=${id}`);
    const pill = await x.words(page.locator('[data-testid="am-model-control"]').first());
    x.check(id === "auto" ? /อัตโนมัติ|Auto/.test(pill) : pill.toLowerCase().includes(rowName.toLowerCase().slice(0, 12)), `${rowName}: the composer shows what was picked`, pill);
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
    const same = models.length > 0 && models.every((m) => m === want);
    const tried = (((d && d.steps) || []).find((st) => st.name === "narrative") || {}).output;
    const why = tried && Array.isArray(tried.tried) ? tried.tried.map((t) => String(t).slice(0, 110)).join(" ‖ ") : "";
    ran.push({ row: rowName, picked: id, ran: models, same, status: d && d.run.status, why });
    if (same) {
      x.check(true, `${rowName}: the model that ran is the model that was picked`, `picked ${id} · ran ${models.join(", ")}${id === "auto" ? ` (อัตโนมัติ is ${served.auto_model} now)` : ""}`);
    } else {
      // the picked model did not answer (owner policy: rescue with another, and tell the person). It must be
      // said in words on the files card — a pick is never swapped in silence
      await workspace(x, installId);
      const seen = await x.words(page.locator(`[data-testid="am-outputs"][data-run="${runId}"] [data-testid="am-outputs-seen-by"]`).last());
      x.check(/คุณเลือก .+ ไว้ แต่รอบนี้ใช้ .+ แทน/.test(seen), `${rowName}: it did not answer, another model did, and the files card says so in words`, `picked ${id} · ran ${models.join(", ") || "none"} · ${seen.slice(0, 160)} · ${why}`);
      x.mark(`RESCUED ${rowName}: ${why}`);
    }
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
    // (the page as it is, before anything is asked of it: a build from before AM11b has no “รายละเอียด”, and an
    // agent this account cannot see has no page — both are said, with the picture, and the walk goes on)
    await page.locator("h1:visible").first().waitFor({ state: "visible", timeout: 60000 }).catch(() => null);
    await page.locator("h2:visible").first().waitFor({ state: "visible", timeout: 30000 }).catch(() => null);
    await L.wait(1500);
    const more = page.locator('[data-testid="am-detail-more"]');
    if (!(await more.count())) {
      await x.shot(`${short}-01-default`, { fullPage: true });
      x.check(false, `${short}: the page has “รายละเอียด”`, `no such control on this build · sections on screen: ${(await page.locator("h2:visible").allInnerTexts()).join(" · ") || "none"}`);
      continue;
    }
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

// ── asked: an agent that does what it is asked (E) ────────────────────────────────────────────────
// Three requests in plain Thai, each read back out of the finished file (docx_facts.py — the file says
// it, not the thread), and one that cannot be done, refused with its reason. The first is typed with the
// files; the others after the round, about its documents — the files are made again from the same
// figures. The model that reads the words is whichever the install may use (in-house, unless the admin
// opened the agent's switch): no word of the walk is matched by the program.
const { execFileSync } = require("node:child_process");
const facts = (file) => JSON.parse(execFileSync("python", [path.join(__dirname, "docx_facts.py"), file], { encoding: "utf8" }));
const THREAD = '[data-testid="am-thread"]';

async function filesOf(x, runId, label) {
  const d = (await api(x.page, `/runs/${runId}`)).body || {};
  const out = { version: 0, steps: (d.steps || []).map((s) => s.name) };
  for (const fmt of ["docx", "pptx"]) {
    const art = (d.artifacts || []).filter((a) => a.format === fmt).sort((a, b) => b.version - a.version)[0];
    if (!art) continue;
    const file = path.join(x.dir, `${label}_v${art.version}.${fmt}`);
    fs.writeFileSync(file, await W.fetchFile(x.page, `${W.EDGE()}/api/v1/agent-market/artifacts/${art.id}/download`));
    out[fmt] = facts(file);
    out[`${fmt}_version`] = art.version;
    out.version = Math.max(out.version, art.version);
  }
  return out;
}
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

async function asked() {
  const x = await W.session("web", "asked");
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  // an install of its own, made now: an install is pinned to the version it was installed at, and only a
  // version whose manifest says what can be asked takes instructions
  const mine = (await api(page, "/installs")).body?.installs || [];
  const installId = mine.some((i) => i.slug === REPORT && i.status !== "removed") ? await W.installAnother(x, REPORT, CARD[REPORT].th) : (await W.installFromMarket(x, CARD[REPORT].th)).installId;
  state.installs = [...new Set([...(state.installs || []), installId])];
  state.am11b_asked_install = installId;
  save();
  await workspace(x, installId);
  const manifest = ((await api(page, `/installs/${installId}`)).body || {}).manifest || {};
  const can = ((manifest.instructions || {}).can || []).map((d) => d.label);
  x.check(can.length >= 3, "the install's own version says what can be asked of its documents", `v${manifest.version}: ${can.join(" · ") || "nothing"}`);
  const line = await x.words(page.locator('[data-testid="am-greeting-asks"]').first()).catch(() => "");
  x.check(can.length > 0 && can.every((c) => line.includes(c)) && /ข้อไหนทำไม่ได้จะบอกพร้อมเหตุผล/.test(line), "the greeting says the agent takes instructions — the package's own list — and that a refusal comes with its reason", line);
  x.check((await page.locator('[data-testid="am-greeting-form"]').count()) === 1, "… and a form", await x.words(page.locator('[data-testid="am-greeting-form"]').first()).catch(() => ""));
  await x.shot("01-greeting");

  // 1 — typed with the files
  const said1 = `${TAG} รายงานผลประกอบการเดือนกันยายน 2569 ไม่ต้องใส่กราฟ`;
  const { runId } = await W.sendFiles(x, installId, W.FIN, said1, "02");
  const end = await W.finish(x, runId, "02");
  x.check(end.run.status === "succeeded", "request 1 (with the files) — the round delivers", `${end.run.status} ${end.run.status_reason || ""}`);
  await L.wait(2500);
  const f1 = await filesOf(x, runId, "1-no-charts");
  x.check(f1.docx && f1.docx.pictures === 0 && !f1.docx.headings.includes("แนวโน้มและองค์ประกอบ") && f1.docx.headings.includes("รายละเอียดรายบัญชี"),
    "“ไม่ต้องใส่กราฟ” — the report has no chart section and no picture (read from the .docx)", f1.docx ? `${f1.docx.pictures} pictures · ${f1.docx.headings.length} sections: ${f1.docx.headings.join(" | ")}` : "no .docx");
  x.check(f1.pptx && f1.pptx.charts === 0, "… and the slides have no chart (read from the .pptx)", f1.pptx ? `${f1.pptx.slides} slides · ${f1.pptx.charts} charts` : "no .pptx");
  x.check(f1.steps.includes("instructions"), "the reading of the words is a step of the run", f1.steps.join(" → "));
  let thread = await threadText(page);
  x.check(/ทำตามที่ขอแล้ว: ไม่ใส่กราฟ/.test(thread), "the thread says what was done as asked", (thread.match(/ทำตามที่ขอแล้ว[^\n]*/) || [""])[0].slice(0, 200));
  await page.locator(`[data-testid="am-outputs"][data-run="${runId}"]`).last().scrollIntoViewIfNeeded().catch(() => {});
  await x.shot("03-no-charts-done");

  // 2 — after the round: another order
  const waitVersion = async (v) => waitFor(async () => {
    const d = (await api(page, `/runs/${runId}`)).body || {};
    return (d.artifacts || []).some((a) => a.format === "docx" && a.version >= v) ? d : null;
  }, 420000, 2000);
  await sayAfter(x, "เรียงจากรายการที่ต่างมากที่สุด");
  x.check(!!(await waitVersion(2)), "request 2 (after the round) — the files are made again", "version 2");
  await L.wait(2500);
  const f2 = await filesOf(x, runId, "2-sorted");
  const sizes = ((f2.docx || {}).accounts || {}).var_budget || [];
  const mags = sizes.map((v) => Math.abs(v == null ? -1 : v));
  x.check(mags.length > 3 && mags.every((v, i) => i === 0 || mags[i - 1] >= v) && mags[0] > mags[mags.length - 1],
    "“เรียงจากรายการที่ต่างมากที่สุด” — the account table runs from the largest difference to the smallest (read from the .docx)", `${mags.length} rows: ${mags.slice(0, 6).join(" ≥ ")} …`);
  x.check(f2.docx && f2.docx.pictures === 0, "… and what was asked with the files still holds: no charts in version 2", f2.docx ? `${f2.docx.pictures} pictures` : "");
  const computes = ((await api(page, `/runs/${runId}`)).body?.steps || []).filter((s) => s.name === "compute").length;
  x.check(computes === 1, "nothing was computed again", `${computes} compute step`);
  thread = await threadText(page);
  x.check(/ทำตามที่ขอแล้ว: เรียงรายละเอียดรายบัญชีจากรายการที่ต่างมากที่สุด/.test(thread), "the thread says so", (thread.match(/ทำตามที่ขอแล้ว: เรียง[^\n]*/) || [""])[0].slice(0, 220));

  // 3 — short
  await sayAfter(x, "ขอสั้นๆ หน้าเดียว");
  x.check(!!(await waitVersion(3)), "request 3 — the files are made again", "version 3");
  await L.wait(2500);
  const f3 = await filesOf(x, runId, "3-brief");
  const gone = ["แนวโน้มและองค์ประกอบ", "รายการเคลื่อนไหวสำคัญ", "รายละเอียดรายบัญชี"];
  const must = ["สรุปผู้บริหาร", "งบกำไรขาดทุน", "รายการที่ต้องตรวจสอบ", "ข้อสมมติที่ใช้ในรอบนี้"];
  x.check(f3.docx && gone.every((h) => !f3.docx.headings.includes(h)) && must.every((h) => f3.docx.headings.includes(h)) && f3.docx.paragraphs < f1.docx.paragraphs,
    "“ขอสั้นๆ หน้าเดียว” — the short report: the detail sections are gone, what a report must have is there (read from the .docx)",
    f3.docx ? `${f3.docx.headings.length} sections (was ${f1.docx.headings.length}) · ${f3.docx.paragraphs} paragraphs (was ${f1.docx.paragraphs}): ${f3.docx.headings.join(" | ")}` : "no .docx");
  thread = await threadText(page);
  const briefSaid = (thread.match(/ทำตามที่ขอแล้ว: รายงานฉบับย่อ[^\n]*/) || [""])[0];
  x.check(/ยาวกว่าหนึ่งหน้า/.test(briefSaid), "the thread says what “short” is — and plainly that it is longer than one page", briefSaid.slice(0, 260));
  await x.shot("04-after-three");

  // 4 — what cannot be done: refused, with the reason, and nothing is made
  const before = ((await api(page, `/runs/${runId}`)).body?.artifacts || []).length;
  await sayAfter(x, "แยกตามแผนกด้วย");
  const refusal = await waitFor(async () => ((await threadText(page)).match(/[^\n]*ทำตามที่ขอไม่ได้เพราะ[^\n]*/) || [null])[0], 240000, 2000);
  x.check(!!refusal && /แผนก/.test(refusal) && /ยังไม่มีตัวเลขรายแผนก/.test(refusal), "“แยกตามแผนกด้วย” — refused in the card's words, with its reason", refusal || "(nothing was said)");
  await L.wait(4000);
  const after = ((await api(page, `/runs/${runId}`)).body?.artifacts || []).length;
  x.check(after === before, "… and no file was made for it", `${before} → ${after} files`);
  await x.shot("05-refused");

  // the run page: the step that read the words, by name, with the model that read them — and the gate's own line
  await page.goto(`${WEB()}/chat/agents/runs/${runId}`, { waitUntil: "load" });
  await page.getByText("ระบบทำอะไรไปบ้าง").first().waitFor({ state: "visible", timeout: 60000 }).catch(() => null);
  await L.wait(1500);
  const runPage = await page.evaluate(() => document.body.innerText);
  const stepLine = (runPage.match(/อ่านคำขอที่พิมพ์มา[^a-zA-Z]*([A-Za-z0-9.\-]+)?/) || [""])[0].replace(/\s+/g, " ");
  x.check(/อ่านคำขอที่พิมพ์มา/.test(runPage), "the run page names the step that read the words", stepLine);
  x.check(/เอกสารเป็นไปตามที่ขอ \(อ่านจากไฟล์\)/.test(runPage), "… and the gate's check that the file is as asked is on the page with the others", (runPage.match(/เอกสารเป็นไปตามที่ขอ[^\n]*/) || [""])[0].slice(0, 200));
  await x.shot("06-run-page", { fullPage: true });
  const model = ((await api(page, `/runs/${runId}`)).body?.steps || []).find((s) => s.name === "instructions");
  x.mark(`the words were read by: ${(model && (model.model_id || (model.output || {}).model)) || "?"}`);
  state.am11b_asked_run = runId;
  save();
  await done(x, "asked", { installId, runId, manifest_version: manifest.version, files: { f1, f2, f3 }, refusal });
}

// ── asked2: the same on a second agent — the expense audit, a workbook, and an agent no model reads
// the documents of. One request honoured (the claims from the largest amount, read from the .xlsx with
// openpyxl inside the sandbox image), one refused with its reason, and the run still says no model saw
// its data: the model that read the person's words saw only the words.
const EXPENSE = "sran.expense-audit";
const EXPENSE_CARD = { th: "ตรวจการเบิกค่าใช้จ่าย", en: "Expense Audit" };
const EXP_DIR = path.join(L.ROOT, "backend", "agent_market", "fixtures", "expense");
const expenseFiles = () => [
  ...["claims_2569-10.xlsx", "policy_kb.docx"].map((n) => ({ name: n, src: path.join(EXP_DIR, n) })),
  ...fs.readdirSync(path.join(EXP_DIR, "receipts")).map((n) => ({ name: n, src: path.join(EXP_DIR, "receipts", n) })),
];
const workbookFacts = (file) => JSON.parse(execFileSync("docker", ["run", "--rm", "--network", "none", "-v", `${path.dirname(file)}:/in:ro`,
  "-v", `${__dirname}:/p:ro`, "--entrypoint", "python3", process.env.AM_SANDBOX_IMAGE || "ai-gateway-sandbox:latest", "/p/xlsx_facts.py", `/in/${path.basename(file)}`], { encoding: "utf8" }));

async function asked2() {
  const x = await W.session("web", "asked2");
  const { page } = x;
  await home(page);
  await setLanguage(x, "th");
  const mine = (await api(page, "/installs")).body?.installs || [];
  const installId = mine.some((i) => i.slug === EXPENSE && i.status !== "removed") ? await W.installAnother(x, EXPENSE, EXPENSE_CARD.th) : (await W.installFromMarket(x, EXPENSE_CARD.th)).installId;
  state.installs = [...new Set([...(state.installs || []), installId])];
  save();
  await workspace(x, installId);
  const manifest = ((await api(page, `/installs/${installId}`)).body || {}).manifest || {};
  const can = ((manifest.instructions || {}).can || []).map((d) => d.label);
  const line = await x.words(page.locator('[data-testid="am-greeting-asks"]').first()).catch(() => "");
  x.check(can.length > 0 && can.every((c) => line.includes(c)), "the greeting says what can be asked of this agent's workbook", `v${manifest.version}: ${line}`);
  await x.shot("01-greeting");
  const { runId } = await W.sendFiles(x, installId, expenseFiles(), `${TAG} ตรวจการเบิกเดือนตุลาคม เรียงจากยอดสูงสุด`, "02");
  const end = await W.finish(x, runId, "02");
  x.check(end.run.status === "succeeded", "the round delivers", `${end.run.status} ${end.run.status_reason || ""} · ${end.asked.length} question(s) answered`);
  await L.wait(2500);
  const d = (await api(page, `/runs/${runId}`)).body || {};
  const file = await W.download(x, d, "xlsx", "expense-asked_v1.xlsx");
  const facts2 = file ? workbookFacts(file) : {};
  const amounts = (facts2.claims || []).map((c) => (typeof c.amount === "number" ? c.amount : -1));
  x.check(amounts.length > 5 && amounts.every((v, i) => i === 0 || amounts[i - 1] >= v) && amounts[0] > amounts[amounts.length - 1],
    "“เรียงจากยอดสูงสุด” — the claims run from the largest amount down (read from the .xlsx)", `${amounts.length} claims: ${amounts.slice(0, 6).join(" ≥ ")} …`);
  const rows = (facts2.claims || []).map((c) => Number(c.source_row)).filter((n) => !Number.isNaN(n));
  x.check(rows.length === amounts.length && new Set(rows).size === rows.length && rows.some((r, i) => i > 0 && r < rows[i - 1]),
    "… every claim is still there and still names its row in the file that was sent", `rows in the source file: ${rows.slice(0, 8).join(", ")} …`);
  x.check(d.run && d.run.model_used == null, "the run still says no model saw its data — the model that read the words saw only the words", `model_used=${d.run && d.run.model_used}`);
  const step = (d.steps || []).find((s) => s.name === "instructions");
  x.check(!!step, "the reading of the words is a step of the run", step ? `read by ${step.model_id || (step.output || {}).model || "?"}` : (d.steps || []).map((s) => s.name).join(" → "));
  let thread = await threadText(page);
  x.check(/ทำตามที่ขอแล้ว: เรียงรายการเบิกจากยอดสูงสุด/.test(thread), "the thread says what was done as asked", (thread.match(/ทำตามที่ขอแล้ว[^\n]*/) || [""])[0].slice(0, 220));
  await x.shot("03-ordered");
  const before = (d.artifacts || []).length;
  await sayAfter(x, "ขอเฉพาะรายการที่มีปัญหา");
  const refusal = await waitFor(async () => ((await threadText(page)).match(/[^\n]*ทำตามที่ขอไม่ได้เพราะ[^\n]*/) || [null])[0], 240000, 2000);
  x.check(!!refusal && /ทุกรายการเบิกต้องอยู่ในผลตรวจครบ/.test(refusal), "“ขอเฉพาะรายการที่มีปัญหา” — refused, with its reason", refusal || "(nothing was said)");
  await L.wait(4000);
  const after = ((await api(page, `/runs/${runId}`)).body?.artifacts || []).length;
  x.check(after === before, "… and no file was made for it", `${before} → ${after} files`);
  await x.shot("04-refused");
  await done(x, "asked2", { installId, runId, manifest_version: manifest.version, claims: facts2.claims, refusal });
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
  else if (phase === "asked") await asked();
  else if (phase === "asked2") await asked2();
  else if (phase === "cleanup") await cleanup();
  else throw new Error("round | money <th|en> | chats | picker <slug> | page <th|en> | asked | asked2 | cleanup");
})().catch((e) => { console.error(e); process.exit(1); });
