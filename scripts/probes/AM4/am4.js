#!/usr/bin/env node
// AM4 probes — through the real HTTP API (chat-edge control plane +
// agent-runner + sandbox), on the TEST stack only. Reuses the AM3 helpers and
// people (run `am3.js seed` and `am3.js setup` first).
//
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js install   # one click ("ใช้ค่าแนะนำทั้งหมด"): defaults + the sample self-test, nothing asked
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js rename    # the owner's test: GL → aaa.xlsx, invoice → bbb.pdf, identical results
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js rounds    # a finished round is kept; the chip asks, never guesses; เริ่มรอบใหม่; a second thread
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js why       # "why is this figure …" answered from the round's own record; else "I don't know"
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js models    # the picker: auto first, external only when the organisation allows it
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js access    # an admin sees metadata; content opens with a stated reason, logged, owner told
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js storage   # retention and storage used, per install
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js budget    # AM3's budget probe on the AM4 round flow
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js thai      # errors a person can meet are Thai (what happened, what next, data safe)
//   SMOKE_TARGET=test node scripts/probes/AM4/am4.js cleanup   # remove the installs this probe made (by id)
//
// Results go to results/e2e/ (gitignored).
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { execFileSync } = require("child_process");
const A = require("../AM3/am3.js");

const { L, PEOPLE, FIXTURES, call, j, send, waitRun, finishWithDefaults, download, terminal, parked, log, check } = A;
const RES = path.join(__dirname, "results", "e2e");
fs.mkdirSync(RES, { recursive: true });
const PKG = path.join(L.ROOT, "backend", "agent_market", "packages");
const EDGE = () => `${L.API_BASE.edge}/api/v1/agent-market`;
const saveJSON = (name, obj) => fs.writeFileSync(path.join(RES, name), JSON.stringify(obj, null, 1));
const state = () => { try { return JSON.parse(fs.readFileSync(path.join(RES, "state.json"), "utf8")); } catch { return {}; } };
const remember = (k, v) => { const s = state(); s[k] = v; saveJSON("state.json", s); };
const need = (k) => { const v = state()[k]; if (!v) throw new Error(`state ${k} missing — run the step that makes it first`); return v; };
const WHO = "member";

async function uploadAs(who, installId, file, name) {
  const fd = new FormData();
  fd.append("file", new Blob([fs.readFileSync(file)]), name);
  const r = await fetch(`${EDGE()}/installs/${installId}/files`, { method: "POST", headers: { ...L.bearerSession(L.mintPortalToken(PEOPLE[who])).headers }, body: fd });
  const t = await r.json();
  if (r.status >= 400) throw new Error(`upload ${name}: ${r.status} ${JSON.stringify(t)}`);
  return t.file;
}
const thread = (who, inst, th) => j(who, `/installs/${inst}/thread${th ? `?thread=${th}` : ""}`);
async function waitItem(who, inst, pred, timeoutS = 180, th) {
  const t0 = Date.now();
  for (;;) {
    const t = await thread(who, inst, th);
    const hit = t.items.filter(pred).pop();
    if (hit) return hit;
    if ((Date.now() - t0) / 1000 > timeoutS) throw new Error("timeout waiting for a thread item");
    await L.wait(1500);
  }
}
// one click: the agent's own defaults and the sample self-test
async function quickInstall(who, slug) {
  const r = await send(who, "POST", "/installs", { slug, idempotency_key: `am4-${slug}-${crypto.randomUUID()}`, self_test: true });
  const installs = state().installs || [];
  remember("installs", [...installs, r.install.id]);
  const st = await waitRun(who, r.self_test_run_id, (d) => terminal(d) || parked(d));
  return { install: r.install, selfTest: st };
}
async function round(who, inst, files, text) {
  const ids = [];
  for (const [file, name] of files) ids.push((await uploadAs(who, inst, file, name)).id);
  const m = await send(who, "POST", `/installs/${inst}/messages`, { text, file_ids: ids, client_id: crypto.randomUUID() });
  return m;
}
async function computed(who, runId) {
  const r = await call(who, `/runs/${runId}/computed`);
  if (r.status !== 200) throw new Error(`computed ${runId}: ${r.status}`);
  return r.json;
}
function relabel(v, names) {
  if (typeof v === "string") { for (const [given, orig] of Object.entries(names)) v = v.split(given).join(orig); return v; }
  if (Array.isArray(v)) return v.map((x) => relabel(x, names));
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [relabel(k, names), relabel(x, names)]));
  return v;
}
// the cells of every sheet of an .xlsx (python openpyxl on the host)
function cells(file) {
  const py = "import sys,json;from openpyxl import load_workbook;wb=load_workbook(sys.argv[1]);" +
    "print(json.dumps({ws.title:[[None if c.value is None else str(c.value) for c in r] for r in ws.iter_rows()] for ws in wb.worksheets},ensure_ascii=False))";
  return JSON.parse(execFileSync(process.env.PYTHON || "python", ["-c", py, file], { encoding: "utf8", maxBuffer: 64 << 20, env: { ...process.env, PYTHONIOENCODING: "utf-8" } }));
}
// where two values differ (paths), for the report
function diff(a, b, at = "$", out = []) {
  if (out.length > 40) return out;
  if (JSON.stringify(a) === JSON.stringify(b)) return out;
  if (a && b && typeof a === "object" && typeof b === "object") {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) diff(a[k], b[k], `${at}.${k}`, out);
    return out;
  }
  out.push(`${at}: ${JSON.stringify(a)?.slice(0, 120)} ≠ ${JSON.stringify(b)?.slice(0, 120)}`);
  return out;
}
async function outcome(who, d, names, tag) {
  const comp = relabel(await computed(who, d.run.id), names);
  const xlsx = d.artifacts.find((a) => a.format === "xlsx");
  let wb = null;
  if (xlsx) {
    const f = path.join(RES, "rename", `${tag}_${xlsx.name}`);
    const got = await download(who, `${EDGE()}/artifacts/${xlsx.id}/download`, f);
    if (got.status === 200) wb = relabel(cells(f), names);
  }
  const questions = (d.clarifications || []).flatMap((c) => c.questions.map((q) => [c.kind, q.key, relabel(q.question || q.label || "", names)]));
  return { status: d.run.status, period: d.run.period, questions, computed: comp, workbook: wb,
    artifacts: d.artifacts.map((a) => [a.format, a.name]) };
}
// volatile fields no file name can influence (run ids, times)
function stable(o) {
  const drop = new Set(["run_id", "generated_at", "created_at", "computed_at", "install_id", "id", "rendered_at"]);
  const walk = (v) => Array.isArray(v) ? v.map(walk) : v && typeof v === "object"
    ? Object.fromEntries(Object.entries(v).filter(([k]) => !drop.has(k)).map(([k, x]) => [k, walk(x)])) : v;
  return walk(o);
}

const errorsSeen = [];
function sawError(r, what) {
  const e = r.json && r.json.error;
  if (e) errorsSeen.push({ what, status: r.status, code: e.code, message: e.message });
  return r;
}

const cmds = {
  async install() {
    const out = {};
    for (const slug of ["sran.financial-report", "sran.invoice-processing", "sran.expense-audit"]) {
      const t0 = Date.now();
      const { install, selfTest } = await quickInstall(WHO, slug);
      const s = { install: install.id, status: selfTest.run.status, cards: (selfTest.clarifications || []).length,
        files: selfTest.artifacts.map((a) => a.name), seconds: Math.round((Date.now() - t0) / 1000) };
      out[slug] = s;
      check(s.status === "succeeded" && s.cards === 0, `${slug}: one click → self-test ${s.status}, ${s.cards} question(s), ${s.files.length} file(s), ${s.seconds} s`);
      remember(`quick_${slug}`, install.id);
    }
    saveJSON("install.json", out);
  },

  async rename() {
    const out = {};
    // financial report: two fresh installs, the same three files; in the second the GL is aaa.xlsx
    const fin = (n) => path.join(PKG, "financial_report", "sample-data", n);
    const gl = fin("GL_2569-09.xlsx"), prior = fin("GL_2569-08.xlsx"), budget = fin("งบประมาณ_2569.xlsx");
    const text = "[AM4] ทำรายงานประจำเดือน";
    const runs = {};
    for (const [tag, glName] of [["named", "GL_2569-09.xlsx"], ["renamed", "aaa.xlsx"]]) {
      const { install } = await quickInstall(WHO, "sran.financial-report");
      remember(`fin_${tag}`, install.id);
      const m = await round(WHO, install.id, [[gl, glName], [prior, "GL_2569-08.xlsx"], [budget, "งบประมาณ_2569.xlsx"]], text);
      check(m.route === "new_run" && !!m.run_id, `financial ${tag}: the first round starts at once (${m.route})`);
      const d = await finishWithDefaults(WHO, m.run_id);
      remember(`fin_${tag}_run`, m.run_id);
      runs[tag] = await outcome(WHO, d, tag === "renamed" ? { "aaa.xlsx": "GL_2569-09.xlsx" } : {}, `fin_${tag}`);
    }
    const fd = diff(stable(runs.renamed), stable(runs.named));
    out.financial = { named: runs.named.status, renamed: runs.renamed.status, period: [runs.named.period, runs.renamed.period], differences: fd };
    check(runs.named.status === "succeeded" && fd.length === 0, `GL renamed aaa.xlsx: ${fd.length} difference(s) in period, questions, computed table and workbook`);

    // invoices: the tax invoice as bbb.pdf
    const inv = (n) => path.join(PKG, "invoice_processing", "sample-data", n);
    const pdf = inv("ใบกำกับภาษี_IV6909-0015.pdf"), wht = inv("ใบแจ้งหนี้ค่าบริการ_หักณที่จ่าย.pdf"), xml = inv("eTax_ใบเสร็จ.xml");
    const iruns = {};
    for (const [tag, name] of [["named", "ใบกำกับภาษี_IV6909-0015.pdf"], ["renamed", "bbb.pdf"]]) {
      const { install } = await quickInstall(WHO, "sran.invoice-processing");
      remember(`inv_${tag}`, install.id);
      const m = await round(WHO, install.id, [[pdf, name], [wht, "ใบแจ้งหนี้ค่าบริการ_หักณที่จ่าย.pdf"], [xml, "eTax_ใบเสร็จ.xml"]], "[AM4] ทำทะเบียนซื้อ");
      const d = await finishWithDefaults(WHO, m.run_id);
      remember(`inv_${tag}_run`, m.run_id);
      iruns[tag] = await outcome(WHO, d, tag === "renamed" ? { "bbb.pdf": "ใบกำกับภาษี_IV6909-0015.pdf" } : {}, `inv_${tag}`);
    }
    const idf = diff(stable(iruns.renamed), stable(iruns.named));
    out.invoice = { named: iruns.named.status, renamed: iruns.renamed.status, differences: idf };
    check(iruns.named.status === "succeeded" && idf.length === 0, `invoice renamed bbb.pdf: ${idf.length} difference(s) in questions, computed table and workbook`);
    saveJSON("rename.json", out);
    saveJSON("rename_full.json", { financial: runs, invoice: iruns });
  },

  async rounds() {
    const out = {};
    // a fresh install whose only round has finished (the renamed-file one)
    const inst = need(process.env.AM4_ROUNDS || "fin_renamed");
    const first = need(`${process.env.AM4_ROUNDS || "fin_renamed"}_run`);
    const roundsBefore = (await j(WHO, `/installs/${inst}/runs`)).runs.filter((r) => r.kind === "run").length;
    // 1. an instruction after a finished round: a chip, never a guess
    const m1 = await send(WHO, "POST", `/installs/${inst}/messages`, { text: "[AM4] ทำรายงานเดือนตุลาคม", client_id: crypto.randomUUID() });
    const chip1 = (await thread(WHO, inst)).items.filter((i) => i.kind === "intent").pop();
    out.text_after_finished = { route: m1.route, run_id: m1.run_id, chip: chip1 && chip1.payload };
    check(m1.route === "intent" && !m1.run_id && chip1 && chip1.payload.status === "open",
      `an instruction after a finished round asks (route ${m1.route}, last round ${chip1 && chip1.payload.last_round.period_label})`);
    // 2. "ถามเกี่ยวกับรอบที่แล้ว": no new round
    const a1 = await send(WHO, "POST", `/intents/${chip1.id}`, { choice: "about_last" });
    const runs1 = (await j(WHO, `/installs/${inst}/runs`)).runs.filter((r) => r.kind === "run");
    out.about_last = { payload: a1.item && a1.item.payload, runs: runs1.length };
    check(runs1.length === roundsBefore, `"ถามเกี่ยวกับรอบที่แล้ว" starts nothing (${runs1.length} round(s) before and after)`);
    // 3. files after a finished round: a chip with the files; "เริ่มรอบใหม่" starts a round with those files only
    const fin = (n) => path.join(PKG, "financial_report", "sample-data", n);
    const m2 = await round(WHO, inst, [[fin("GL_2569-09.xlsx"), "GL_2569-09.xlsx"]], "[AM4] รอบใหม่");
    const chip2 = (await thread(WHO, inst)).items.filter((i) => i.kind === "intent").pop();
    check(m2.route === "intent" && chip2.id !== chip1.id && (chip2.payload.files || []).length === 1,
      `files after a finished round ask too (route ${m2.route}, ${(chip2.payload.files || []).length} file waiting)`);
    const a2 = await send(WHO, "POST", `/intents/${chip2.id}`, { choice: "new_round" });
    const again = await send(WHO, "POST", `/intents/${chip2.id}`, { choice: "new_round" });
    const newRun = a2.run_id;
    check(!!newRun && again.changed === false, `"เริ่มรอบใหม่" on the chip starts one round (a second click changes nothing)`);
    const d2 = await finishWithDefaults(WHO, newRun);
    const d1 = await j(WHO, `/runs/${first}`);
    out.new_round = { status: d2.run.status, message: d2.run.message, files: d2.files.map((f) => f.name), parent: d2.run.parent_run_id,
      first_files: d1.files.map((f) => f.name) };
    check(d2.run.status === "succeeded", `the new round: ${d2.run.status}`);
    check(d2.files.length === 1 && !/ประจำเดือน/.test(d2.run.message || ""),
      `the new round has only its own file and message (${d2.files.map((f) => f.name).join(", ")} · “${d2.run.message}”)`);
    // 4. the finished round is never cleared
    const th = await thread(WHO, inst);
    const firstItems = th.items.filter((i) => i.run_id === first);
    out.first_round_items = firstItems.map((i) => i.kind);
    check(firstItems.some((i) => i.kind === "outputs") && d1.artifacts.length > 0,
      `the first round is still in the thread with its files (${firstItems.length} items, ${d1.artifacts.length} files)`);
    // 5. the divider's "เริ่มรอบใหม่": after it, files start a round without a chip
    const rs = await send(WHO, "POST", `/installs/${inst}/rounds`, {});
    check(!!rs.item && rs.item.payload.round_start === true, `“เริ่มรอบใหม่” posts: ${rs.item && rs.item.payload.text}`);
    const m3 = await round(WHO, inst, [[fin("GL_2569-09.xlsx"), "GL_2569-09.xlsx"]], "");
    check(m3.route === "new_run" && !!m3.run_id, `after เริ่มรอบใหม่ files start the round at once (${m3.route})`);
    const d3 = await finishWithDefaults(WHO, m3.run_id);
    out.after_button = { status: d3.run.status };
    // 6. what carries over: settings and remembered answers only
    const defs = await j(WHO, `/installs/${inst}/defaults`);
    out.remembered = defs;
    const q1 = (d1.clarifications || []).length, q3 = (d3.clarifications || []).filter((c) => c.status !== "cancelled").length;
    out.questions = { first: q1, third: q3 };
    log(`questions: first round ${q1}, third round ${q3}; remembered: ${JSON.stringify(defs).slice(0, 200)}`);
    // 7. "แชทใหม่กับ agent นี้": a second thread, greeted, listed under the agent
    const nt = await send(WHO, "POST", `/installs/${inst}/threads`, {});
    const tid = nt.thread && nt.thread.id;
    const list = await j(WHO, `/installs/${inst}/threads`);
    const t2 = await thread(WHO, inst, tid);
    out.threads = { count: list.threads.length, second: t2.items.map((i) => i.kind) };
    check(list.threads.length >= 2 && t2.items.every((i) => !i.run_id), `a new chat with this agent: ${list.threads.length} threads, the new one has ${t2.items.length} item(s) and no rounds`);
    // its first round starts at once (nothing finished in it), and stays in it
    const f4 = await uploadAs(WHO, inst, fin("GL_2569-09.xlsx"), "GL_2569-09.xlsx");
    const m4 = await send(WHO, "POST", `/installs/${inst}/messages`, { text: "", file_ids: [f4.id], thread_id: tid, client_id: crypto.randomUUID() });
    check(m4.route === "new_run", `the new chat's first round starts at once (${m4.route})`);
    const d4 = await finishWithDefaults(WHO, m4.run_id);
    const inMain = (await thread(WHO, inst)).items.some((i) => i.run_id === m4.run_id);
    const inNew = (await thread(WHO, inst, tid)).items.some((i) => i.run_id === m4.run_id);
    out.second_thread_round = { status: d4.run.status, thread_id: d4.run.thread_id, in_main: inMain, in_new: inNew,
      questions: (d4.clarifications || []).length };
    check(d4.run.status === "succeeded" && inNew && !inMain, `its round (${d4.run.status}) is in the new chat only`);
    remember("rounds_thread", tid);
    saveJSON("rounds.json", out);
  },

  async why() {
    const out = {};
    const inst = need("fin_named");
    const runId = need("fin_named_run");
    const comp = await computed(WHO, runId);
    const cites = comp.citations || [];
    out.citations = cites.length;
    check(cites.length > 0, `the round's record has ${cites.length} cited figures`);
    const c = cites.find((x) => x.value && (x.sources || []).length > 0) || cites[0];
    const asks = [
      { q: `[AM4] ทำไม${c.label}ถึงเป็น ${c.value}`, expect: "record" },
      { q: "[AM4] ทำไมลูกค้ารายใหญ่ถึงจ่ายเงินช้า", expect: "dont_know" },
    ];
    // the latest round in the main thread is a later round (rounds step); ask on the first thread's latest
    for (const a of asks) {
      const before = (await thread(WHO, inst)).items.length;
      const m = await send(WHO, "POST", `/installs/${inst}/messages`, { text: a.q, client_id: crypto.randomUUID() });
      if (m.route === "intent") {
        const chip = (await thread(WHO, inst)).items.filter((i) => i.kind === "intent").pop();
        await send(WHO, "POST", `/intents/${chip.id}`, { choice: "about_last" });
      }
      const reply = await waitItem(WHO, inst, (i) => i.kind === "agent_text" && i.rev > 0 && i.payload.text && i.created_at > m.item.created_at, 240);
      a.route = m.route;
      a.answer = reply.payload.text;
      a.count_before = before;
      if (a.expect === "record") {
        const ok = a.answer.includes(c.label) && /ที่มาของตัวเลข|มาจาก/.test(a.answer) && !/เพราะ|เนื่องจาก|สาเหตุ/.test(a.answer.replace(/เพราะอะไร/g, ""));
        check(ok, `“${a.q}” answered from the record: ${a.answer.slice(0, 160).replace(/\n/g, " ⏎ ")}`);
      } else {
        check(/ไม่ทราบ/.test(a.answer) && /จะไม่เดา/.test(a.answer), `“${a.q}” → I don't know: ${a.answer.slice(0, 120)}`);
      }
    }
    out.asks = asks;
    out.figure = c;
    saveJSON("why.json", out);
  },

  // AM9 (F): rewritten against today's menu (AM6: the chat's model list, {id, allowed} rows and one
  // blocked reason) and today's admin door — the AM4 picker ("options", curated agent_models) and the
  // chat app's /admin/models are long gone, so the old phase could only throw. It now walks AM9's
  // per-agent egress: the organisation's switch is the ceiling, an accounting agent is off under it
  // until an admin opens that agent, and both levels are in the menu.
  async models() {
    const out = {};
    const fin = need("fin_named"), inv = need("inv_named");
    const org0 = (await j("admin", "/admin/org")).org;
    out.org_before = { documents_may_leave: org0.documents_may_leave };
    const outside = (p) => (p.models || []).filter((m) => !m.allowed);
    const menu = async (inst) => j(WHO, `/installs/${inst}/models`);
    try {
      await send("admin", "PUT", "/admin/org", { documents_may_leave: false });
      for (const [tag, inst] of [["financial", fin], ["invoice", inv]]) {
        const p = await menu(inst);
        out[tag] = p;
        check(p.egress && p.egress.org === false && p.egress.effective === false, `${tag}: the menu says the organisation does not allow it`);
        const ext = outside(p)[0];
        if (ext) {
          const r = sawError(await call(WHO, `/installs/${inst}/model`, { method: "PUT", body: JSON.stringify({ choice: ext.id }) }), "outside model while not allowed");
          check(r.status === 409 && r.json?.error?.code === "MODEL_NOT_ALLOWED", `${tag}: choosing ${ext.id} anyway is refused`);
        }
      }
      // the organisation allows it: an accounting agent still keeps its documents in, and says why
      await send("admin", "PUT", "/admin/org", { documents_may_leave: true });
      const p1 = await menu(fin);
      out.org_on = p1.egress;
      check(p1.egress.org === true && p1.egress.agent === false && p1.egress.accounting === true && !p1.egress.effective,
        `financial: organisation on, the agent's own default off (“${p1.blocked_reason}”)`);
      // an admin opens this agent: now its outside models may be chosen
      await send("admin", "PUT", "/admin/egress/sran.financial-report", { documents_may_leave: true });
      const p2 = await menu(fin);
      out.agent_on = p2.egress;
      check(p2.egress.effective === true && outside(p2).length === 0, "financial: opened by an admin, every model may be chosen");
      const egress = await j("admin", "/admin/egress");
      out.matrix = egress.agents.map((a) => ({ slug: a.slug, agent: a.agent, default: a.agent_default, effective: a.effective }));
      // a person who is not an admin is refused at the admin door
      const r = await call(WHO, "/admin/egress");
      check(r.status === 403, `a person who is not an admin cannot read the egress settings (${r.status})`);
    } finally {
      await send("admin", "PUT", "/admin/egress/sran.financial-report", { documents_may_leave: false });
      await send("admin", "PUT", "/admin/org", { documents_may_leave: out.org_before.documents_may_leave });
    }
    // the outputs card says which model saw the documents
    const th = await thread(WHO, inv);
    const outs = th.items.filter((i) => i.kind === "outputs").pop();
    out.invoice_model_line = outs && outs.payload.model;
    check(!!(outs && outs.payload.model), `the invoice files card carries the model line: ${JSON.stringify(outs && outs.payload.model)}`);
    saveJSON("models.json", out);
  },

  async access() {
    const out = {};
    const runId = need("inv_named_run");
    const inst = need("inv_named");
    const list = await j("admin", "/admin/runs");
    const row = list.runs.find((r) => r.id === runId || r.run_id === runId);
    out.row = row;
    check(!!row, `an admin sees the member's run in the list (metadata: ${row && Object.keys(row).join(", ")})`);
    const shut = sawError(await call("admin", `/admin/runs/${runId}`), "admin content without a reason");
    check(shut.status === 403 && shut.json?.error?.code === "ACCESS_REASON_NEEDED", `its content is closed by default: ${shut.status} ${shut.json?.error?.message}`);
    const shutFile = await call("admin", `/admin/runs/${runId}/computed`);
    check(shutFile.status === 403, `the computed table too: ${shutFile.status}`);
    const short = sawError(await call("admin", `/admin/runs/${runId}/open`, { method: "POST", body: JSON.stringify({ reason: "ดู" }) }), "admin open with a short reason");
    check(short.status === 400 && short.json?.error?.code === "ACCESS_REASON_REQUIRED", `a reason is required: ${short.json?.error?.message}`);
    const before = (await thread(WHO, inst)).items.length;
    const open = await send("admin", "POST", `/admin/runs/${runId}/open`, { reason: "[AM4] ผู้ใช้แจ้งว่ายอดรวมไม่ตรงกับเอกสาร ขอตรวจที่มา" });
    check(open.opened === true, `opened with a stated reason, until ${open.expires_at}`);
    const content = await call("admin", `/admin/runs/${runId}`);
    const comp = await call("admin", `/admin/runs/${runId}/computed`);
    check(content.status === 200 && comp.status === 200, `then content (${content.status}) and the computed table (${comp.status}) open`);
    const notice = (await thread(WHO, inst)).items.filter((i) => i.kind === "notice" && i.payload.access).pop();
    out.notice = notice && notice.payload.text;
    check(!!notice && /เหตุผล/.test(notice.payload.text) && /ขอตรวจที่มา/.test(notice.payload.text), `the owner is told, with the reason: ${out.notice && out.notice.slice(0, 160)}`);
    const log1 = content.json && (content.json.access_log || content.json.access);
    out.access_log = log1;
    log(`access log on the run: ${JSON.stringify(log1).slice(0, 200)}`);
    // the owner's page lists every opening too
    const mine = await j(WHO, `/runs/${runId}`);
    out.owner_sees = mine.access_log || mine.access;
    check(Array.isArray(out.owner_sees) && out.owner_sees.length >= 1, `the owner's run page lists ${out.owner_sees && out.owner_sees.length} opening(s)`);
    out.thread_grew = (await thread(WHO, inst)).items.length - before;
    saveJSON("access.json", out);
  },

  async storage() {
    const out = {};
    for (const k of ["fin_named", "inv_named"]) {
      const s = await j(WHO, `/installs/${need(k)}/storage`);
      out[k] = s;
      check(s.retention_years === 5 && /5 ปี/.test(s.retention) && /ไม่ใช่ที่เก็บเอกสารของบริษัท/.test(s.not_archive),
        `${k}: kept ${s.retention_years} years, stored ${JSON.stringify(s.storage).slice(0, 120)}`);
    }
    const ds = await j(WHO, "/catalog/sran.invoice-processing/datasheet");
    out.datasheet = ds.retention;
    const first = (ds.retention || [])[0] || {};
    check(first.keep === 5 && first.unit === "years" && /รอบที่สำเร็จ/.test(first.what || ""),
      `the data sheet states the period: ${first.keep} ${first.unit} — ${first.what}`);
    saveJSON("storage.json", out);
  },

  async budget() {
    const inst = need("inv_named");
    const out = {};
    await send("admin", "PUT", `/admin/members/${PEOPLE.member.email}`, { monthly_credits: 1 });
    try {
      await send(WHO, "POST", `/installs/${inst}/rounds`, {});
      const m = await round(WHO, inst, [[path.join(FIXTURES, "e2", "docs", "d02_classic_hyphen_taxid.pdf"), "d02_classic_hyphen_taxid.pdf"]], "[AM4] รอบใหม่ งบหมด");
      check(m.route === "new_run", `after เริ่มรอบใหม่ the files start a round (${m.route})`);
      let d = await waitRun(WHO, m.run_id, (x) => parked(x) || terminal(x));
      out.parked = { status: d.run.status, reason: d.run.status_reason };
      check(d.run.status === "awaiting_input" && d.run.status_reason === "budget_user", `the run waits for budget: ${d.run.status}/${d.run.status_reason}`);
      const th = await thread(WHO, inst);
      const notice = th.items.filter((i) => i.kind === "notice" && i.payload.action === "resume_budget").pop();
      out.notice = notice && notice.payload.text;
      check(!!notice && /ผู้ดูแลทดสอบ/.test(notice.payload.text), `the notice names who to ask: ${out.notice}`);
      const again = sawError(await call(WHO, `/runs/${m.run_id}/resume`, { method: "POST" }), "resume while out of budget");
      check(again.status === 409, `"ลองต่อ" while it is still out: ${again.json?.error?.message}`);
      const raised = await send("admin", "PUT", `/admin/members/${PEOPLE.member.email}`, { monthly_credits: null });
      check(raised.resumed >= 1, `raising the budget resumes it (${raised.resumed})`);
      d = await finishWithDefaults(WHO, m.run_id);
      out.after = { status: d.run.status };
      check(d.run.status === "succeeded", `after the budget was raised: ${d.run.status}`);
    } finally {
      await send("admin", "PUT", `/admin/members/${PEOPLE.member.email}`, { monthly_credits: null });
    }
    saveJSON("budget.json", out);
  },

  async thai() {
    const inst = need("fin_named");
    const probes = [
      ["bad id", () => call(WHO, "/runs/not-a-uuid")],
      ["unknown run", () => call(WHO, `/runs/${crypto.randomUUID()}`)],
      ["bad json", () => call(WHO, `/installs/${inst}/messages`, { method: "POST", body: "{" })],
      ["unknown chip choice", () => call(WHO, `/intents/${crypto.randomUUID()}`, { method: "POST", body: JSON.stringify({ choice: "maybe" }) })],
      ["unknown chip", () => call(WHO, `/intents/${crypto.randomUUID()}`, { method: "POST", body: JSON.stringify({ choice: "new_round" }) })],
      ["unknown thread", () => call(WHO, `/installs/${inst}/thread?thread=${crypto.randomUUID()}`)],
      ["not an admin", () => call(WHO, "/admin/runs")],
      ["not computed", () => call(WHO, `/runs/${need("fin_named_run")}/computed`.replace(need("fin_named_run"), crypto.randomUUID()))],
    ];
    for (const [what, fn] of probes) {
      const r = sawError(await fn(), what);
      log(`  ${what}: ${r && r.status} ${r && r.json?.error?.code}`);
    }
    // an upload of a kind the agent does not read
    const tmp = path.join(RES, "note.exe");
    fs.writeFileSync(tmp, "MZ not a document");
    const fd = new FormData();
    fd.append("file", new Blob([fs.readFileSync(tmp)]), "note.exe");
    const r = await fetch(`${EDGE()}/installs/${inst}/files`, { method: "POST", headers: { ...L.bearerSession(L.mintPortalToken(PEOPLE[WHO])).headers }, body: fd });
    sawError({ status: r.status, json: await r.json().catch(() => null) }, "an .exe upload");
    const bad = [];
    // allowed Latin: the code in parentheses, proper nouns, file extensions, emails
    // (the code is quoted since the messages were reworded: “BAD_ID”)
    const allowed = /\(รหัส “?[A-Z_0-9]+”?\)|Agent Market|SRAN|Sran|PDF|Excel|Word|PowerPoint|\.[a-z]{2,4}\b|[\w.+-]+@[\w.-]+|agent/g;
    for (const e of errorsSeen) {
      const left = (e.message || "").replace(allowed, "");
      const latin = left.match(/[A-Za-z]{3,}/g);
      if (latin || !/[฀-๿]/.test(e.message || "")) bad.push({ ...e, latin });
      log(`${e.status} ${e.code}: ${e.message}`);
    }
    check(bad.length === 0, `${errorsSeen.length} errors a person can meet, all Thai (${bad.length} with English: ${JSON.stringify(bad).slice(0, 300)})`);
    saveJSON("thai.json", { errors: errorsSeen, bad });
  },

  async cleanup() {
    const s = state();
    for (const id of s.installs || []) {
      const r = await call(WHO, `/installs/${id}`, { method: "DELETE" });
      log(`removed install ${id}: ${r.status}`);
    }
    const mi = s.member_invoice;
    remember("installs", []);
    log(`done${mi ? "" : ""}`);
  },
};

const cmd = process.argv[2];
if (!cmds[cmd]) {
  console.log(`usage: am4.js ${Object.keys(cmds).join(" | ")}`);
  process.exit(2);
}
cmds[cmd]().catch((e) => {
  console.error(e);
  process.exit(1);
});
