#!/usr/bin/env node
// AM2 probes — drive the AM2 agents through the real HTTP API (chat-edge
// control plane + agent-runner + doc-converter), on the TEST stack by default.
//
//   SMOKE_TARGET=test node scripts/probes/AM2/am2.js invoice     # install + self-test, then digital / scan / photo rounds; cost per document by path
//   SMOKE_TARGET=test node scripts/probes/AM2/am2.js expense     # install with the policy table, the October month, exceptions decided
//   SMOKE_TARGET=test node scripts/probes/AM2/am2.js financial   # a fresh financial report install (the current version), docx + PDF
//   SMOKE_TARGET=test node scripts/probes/AM2/am2.js legacy      # the install pinned to 1.0.0 runs, then upgrade (self-test) and rollback
//   SMOKE_TARGET=test node scripts/probes/AM2/am2.js dashboard   # every install's dashboard
//   SMOKE_TARGET=test node scripts/probes/AM2/am2.js cleanup     # remove the installs this probe made (by id, from results/)
//
// Uses the smoke suite's helpers (token minting, .env reading — never printed).
// Every chat it creates is an Agent Market thread; results go to results/e2e/.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const L = require("../../smoke/lib.js");

const RES = path.join(__dirname, "results", "e2e");
fs.mkdirSync(RES, { recursive: true });
const FIXTURES = path.join(L.ROOT, "backend", "agent_market", "fixtures");
const FIN = path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report");
const user = L.TARGET === "live" ? L.SMOKE_USER : L.TS_USER;
let session = null;
function sess() {
  if (!session) session = L.bearerSession(L.mintPortalToken(user));
  return session;
}
const EDGE = () => `${L.API_BASE.edge}/api/v1/agent-market`;
async function am(p, init = {}) {
  return L.http(EDGE() + p, { timeoutMs: 60000, ...init,
    headers: { "Content-Type": "application/json", ...sess().headers, ...(init.headers || {}) } });
}
async function j(p, init = {}) {
  const r = await am(p, init);
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 400)}`);
  return r.json;
}
const post = (p, body) => j(p, { method: "POST", body: JSON.stringify(body || {}) });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const saveJSON = (name, obj) => fs.writeFileSync(path.join(RES, name), JSON.stringify(obj, null, 1));
const made = () => { try { return JSON.parse(fs.readFileSync(path.join(RES, "installs.json"), "utf8")); } catch { return {}; } };
const remember = (k, id) => {
  const m = made();
  m[k] = [...new Set([...(Array.isArray(m[k]) ? m[k] : m[k] ? [m[k]] : []), id])];
  saveJSON("installs.json", m);
};

async function upload(installId, file) {
  const fd = new FormData();
  fd.append("file", new Blob([fs.readFileSync(file)]), path.basename(file));
  const r = await fetch(`${EDGE()}/installs/${installId}/files`, { method: "POST", headers: { ...sess().headers }, body: fd });
  const t = await r.json();
  if (r.status >= 400) throw new Error(`upload ${path.basename(file)}: ${r.status} ${JSON.stringify(t)}`);
  return t.file;
}
async function waitRun(runId, pred, timeoutS = 600) {
  const t0 = Date.now();
  let last = "";
  for (;;) {
    const d = await j(`/runs/${runId}`);
    const s = `${d.run.status}${d.run.status_reason ? "/" + d.run.status_reason : ""}`;
    if (s !== last) { log(`run ${runId.slice(0, 8)} ${s}`); last = s; }
    if (pred(d)) return d;
    if ((Date.now() - t0) / 1000 > timeoutS) throw new Error(`timeout waiting run ${runId}: ${s}`);
    await L.wait(1000);
  }
}
const terminal = (d) => ["succeeded", "failed", "cancelled"].includes(d.run.status);
const parked = (d) => d.run.status === "awaiting_input";
const openCards = (d) => (d.clarifications || []).filter((c) => c.status === "open");

async function round(installId, text, files) {
  const ids = [];
  for (const f of files) ids.push((await upload(installId, f)).id);
  const r = await post(`/installs/${installId}/messages`, { text, file_ids: ids, client_id: crypto.randomUUID() });
  log(`message → ${r.route} run=${r.run_id}`);
  if (r.route !== "late_file") return r.run_id;
  // files after a finished round: the agent asks whether they start a new round; this probe wants only the new files
  const d = await j(`/runs/${r.run_id}`);
  const card = openCards(d).find((c) => c.kind === "late_file");
  if (!card) throw new Error("late_file route without a card");
  await post(`/cards/${card.id}/answer`, { answers: { late_file: { labels: ["ทำรอบใหม่ เฉพาะไฟล์นี้"] } } });
  for (let i = 0; i < 30; i++) {
    const runs = (await j(`/installs/${installId}/runs`)).runs.filter((x) => x.kind === "run" && x.id !== r.run_id)
      .sort((x, y) => (x.created_at < y.created_at ? 1 : -1));
    if (runs[0] && runs[0].created_at > d.run.created_at) {
      log(`late file → new round ${runs[0].id}`);
      return runs[0].id;
    }
    await L.wait(500);
  }
  throw new Error("no new round after the late_file answer");
}
// every open card answered with its defaults until the run ends; returns the run and what was asked
async function finishWithDefaults(runId) {
  const asked = [];
  let d = await waitRun(runId, (x) => parked(x) || terminal(x));
  for (let i = 0; i < 8 && parked(d); i++) {
    const cards = openCards(d);
    for (const c of cards) {
      const answers = {};
      for (const q of c.questions) {
        asked.push(q.key);
        answers[q.key] = q.default_label ? { labels: [q.default_label] } : { skipped: true };
      }
      const r = await post(`/cards/${c.id}/answer`, { answers });
      log(`answered ${c.id.slice(0, 8)} (${c.questions.map((q) => q.key).join(", ")}) changed=${r.changed}`);
    }
    const seen = new Set(cards.map((c) => c.id));
    d = await waitRun(runId, (x) => terminal(x) || (parked(x) && openCards(x).some((c) => !seen.has(c.id))));
  }
  return { d, asked };
}
async function artifacts(d, tag) {
  const dir = path.join(RES, "files", tag);
  fs.mkdirSync(dir, { recursive: true });
  const out = [];
  for (const a of d.artifacts || []) {
    const r = await fetch(`${EDGE()}/artifacts/${a.id}/download`, { headers: { ...sess().headers } });
    const buf = Buffer.from(await r.arrayBuffer());
    fs.writeFileSync(path.join(dir, a.name), buf);
    const sha = crypto.createHash("sha256").update(buf).digest("hex");
    const v = a.validation || {};
    out.push({ name: a.name, format: a.format, bytes: buf.length, sha_match: sha === a.sha256, passed: v.passed,
      failed: (v.checks || []).filter((c) => !c.ok).map((c) => c.name || c.label) });
    log(`  ${a.name} ${buf.length} B sha=${sha === a.sha256 ? "match" : "MISMATCH"} gate=${v.passed ? "pass" : "FAIL"}`);
  }
  return out;
}
function steps(d) {
  return (d.steps || []).map((s) => ({ name: s.name, status: s.status, ms: s.duration_ms, credits: s.credits, model: s.model_id || undefined }));
}
// credits and seconds per document: its own parse (and vision) steps plus an even share of the run's shared steps
function perDocument(d, docs) {
  const own = {};
  let shared = 0, sharedMs = 0;
  for (const s of d.steps || []) {
    const m = /^(parse|vision):(.+)$/.exec(s.name);
    if (m && docs.includes(m[2])) {
      own[m[2]] = own[m[2]] || { credits: 0, ms: 0, vision: false };
      own[m[2]].credits += s.credits || 0;
      own[m[2]].ms += s.duration_ms || 0;
      if (m[1] === "vision") { own[m[2]].vision = true; own[m[2]].model = s.model_id; }
    } else {
      shared += s.credits || 0;
      sharedMs += s.duration_ms || 0;
    }
  }
  const out = {};
  for (const n of docs) {
    const o = own[n] || { credits: 0, ms: 0, vision: false };
    out[n] = { ...o, credits: +(o.credits + shared / docs.length).toFixed(2), seconds: +((o.ms + sharedMs / docs.length) / 1000).toFixed(1) };
  }
  return out;
}

async function install(key, body) {
  const r = await post("/installs", { idempotency_key: `am2-probe-${key}-${user.id}-${Date.now()}`, ...body });
  log(`install ${key} ${r.install.id} v${r.install.definition_version} created=${r.created}`);
  remember(key, r.install.id);
  if (r.self_test_run_id) {
    const d = await waitRun(r.self_test_run_id, terminal);
    log(`self-test: ${d.run.status} cards=${(d.clarifications || []).length}`);
    saveJSON(`${key}_self_test.json`, { status: d.run.status, reason: d.run.status_reason, cards: (d.clarifications || []).length,
      artifacts: await artifacts(d, `${key}_self_test`), steps: steps(d) });
  }
  return r.install;
}

const E2 = (n) => path.join(FIXTURES, "e2", "docs", n);
const cmds = {
  async invoice() {
    const inst = await install("invoice", { slug: "sran.invoice-processing", self_test: true,
      settings: { company: "บริษัท สรานเทค โซลูชั่นส์ จำกัด", company_tax_id: "0105563012343", company_branch: "00000", formats: ["xlsx"] },
      theme: { id: "navy", company: "บริษัท สรานเทค โซลูชั่นส์ จำกัด" } });
    const rounds = {
      digital: ["d01_clean_full_tax_invoice.pdf", "d02_classic_hyphen_taxid.pdf", "d05_wht_service_invoice.pdf", "e02_etax_xml_receipt.xml"],
      scan: ["s01_scan300_full_tax_invoice.pdf", "s02_scan200_full_tax_invoice.pdf"],
      photo: ["p02_photo_flat_receipt.jpg"],
    };
    const out = {};
    for (const [tier, docs] of Object.entries(rounds)) {
      log(`— ${tier}: ${docs.join(", ")}`);
      const runId = await round(inst.id, `[AM2] ทำทะเบียนซื้อ (${tier})`, docs.map(E2));
      const { d, asked } = await finishWithDefaults(runId);
      out[tier] = { run: runId, status: d.run.status, reason: d.run.status_reason, credits: d.run.credits_spent,
        model: d.run.model_used, self_hosted: d.run.model_self_hosted, asked, per_document: perDocument(d, docs),
        artifacts: await artifacts(d, `invoice_${tier}`), steps: steps(d) };
      log(`${tier}: ${d.run.status} credits=${d.run.credits_spent} model=${d.run.model_used || "none"} asked=${asked.length}`);
    }
    // the same clean invoice again: the duplicate index catches it
    const dup = await round(inst.id, "[AM2] ทำทะเบียนซื้อ (ซ้ำ)", [E2("d01_clean_full_tax_invoice.pdf")]);
    const dd = await waitRun(dup, (x) => parked(x) || terminal(x));
    out.duplicate = { status: dd.run.status, asked: openCards(dd).flatMap((c) => c.questions.map((q) => q.key)) };
    log(`duplicate asked: ${out.duplicate.asked.join(", ")}`);
    if (parked(dd)) await j(`/runs/${dup}/cancel`, { method: "POST", body: "{}" });
    saveJSON("invoice.json", out);
  },
  async expense() {
    const policy = JSON.parse(fs.readFileSync(path.join(FIXTURES, "expense", "policy.json"), "utf8"));
    const holidays = JSON.parse(fs.readFileSync(path.join(FIXTURES, "expense", "holidays.json"), "utf8"));
    const inst = await install("expense", { slug: "sran.expense-audit", self_test: true,
      settings: { company: "บริษัท สรานเทค โซลูชั่นส์ จำกัด", formats: ["xlsx"], policy, holidays },
      theme: { id: "navy", company: "บริษัท สรานเทค โซลูชั่นส์ จำกัด" } });
    // the server refuses a limit that is not an amount, and says which one
    const wrong = await am(`/installs/${inst.id}/defaults/policy`, { method: "PUT",
      body: JSON.stringify({ value: { ...policy, categories: { ...policy.categories, meals: { ...policy.categories.meals, per_claim: "ประมาณ 2,000" } } } }) });
    log(`policy with "ประมาณ 2,000": ${wrong.status} ${(wrong.json?.error?.message || wrong.text).slice(0, 160)}`);
    const receipts = fs.readdirSync(path.join(FIXTURES, "expense", "receipts")).map((n) => path.join(FIXTURES, "expense", "receipts", n));
    const files = [path.join(FIXTURES, "expense", "claims_2569-10.xlsx"), path.join(FIXTURES, "expense", "policy_kb.docx"), ...receipts];
    const runId = await round(inst.id, "[AM2] ตรวจการเบิกเดือนตุลาคม", files);
    const { d, asked } = await finishWithDefaults(runId);
    const ex = (await j(`/runs/${runId}/exceptions`)).exceptions;
    const open = ex.filter((e) => e.status === "open");
    log(`expense: ${d.run.status} asked=${asked.join(", ")} exceptions open=${open.length} noted=${ex.filter((e) => e.status === "noted").length}`);
    const target = open.find((e) => e.reason_code === "over_claim_limit") || open[0];
    const reject = await am(`/exceptions/${target.id}/decision`, { method: "POST", body: JSON.stringify({ choice: "reject" }) });
    const choice = target.choices[0];
    const ok = await post(`/exceptions/${target.id}/decision`, { choice: choice.value, note: "[AM2] ทดสอบ" });
    const again = await am(`/exceptions/${target.id}/decision`, { method: "POST", body: JSON.stringify({ choice: choice.value }) });
    log(`decide: reject → ${reject.status}, "${choice.label}" → ${ok.exception.status}, again → ${again.status}`);
    saveJSON("expense.json", { status: d.run.status, reason: d.run.status_reason, credits: d.run.credits_spent, model: d.run.model_used,
      asked, policy_refusal: { status: wrong.status, message: wrong.json?.error?.message },
      exceptions: ex.map((e) => ({ code: e.reason_code, severity: e.severity, status: e.status, claim: e.source_ref.claim,
        limit_source: e.source_ref.limit_source, kb_used_as_limit: (e.source_ref.kb_context || []).some((k) => k.used_as_limit) })),
      decision: { reject: reject.status, chosen: ok.exception.source_ref.decision, again: again.status },
      per_document: perDocument(d, files.map((f) => path.basename(f))), artifacts: await artifacts(d, "expense"), steps: steps(d) });
  },
  async financial() {
    const inst = await install("financial", { slug: "sran.financial-report", self_test: false,
      settings: { company: "บริษัท ทดสอบ AM2 จำกัด", formats: ["docx", "pdf"], currency: "THB" },
      theme: { id: "navy", company: "บริษัท ทดสอบ AM2 จำกัด" } });
    const runId = await round(inst.id, "[AM2] ปิดงบเดือนกันยายน", ["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"]
      .map((n) => path.join(FIN, "sample-data", n)));
    const { d, asked } = await finishWithDefaults(runId);
    log(`financial: ${d.run.status} asked=${asked.length}`);
    saveJSON("financial.json", { version: inst.definition_version, status: d.run.status, reason: d.run.status_reason,
      credits: d.run.credits_spent, model: d.run.model_used, asked, artifacts: await artifacts(d, "financial"), steps: steps(d) });
  },
  async legacy() {
    const insts = (await j("/installs")).installs.filter((i) => i.slug === "sran.financial-report" && i.definition_version === "1.0.0");
    if (!insts.length) throw new Error("no install pinned to 1.0.0");
    const inst = insts[0];
    log(`pinned install ${inst.id} v${inst.definition_version}`);
    const runId = await round(inst.id, "[AM2] ปิดงบเดือนกันยายน (รุ่น 1.0.0)", ["GL_2569-09.xlsx", "GL_2569-08.xlsx", "งบประมาณ_2569.xlsx"]
      .map((n) => path.join(FIN, "sample-data", n)));
    const { d, asked } = await finishWithDefaults(runId);
    log(`1.0.0 run: ${d.run.status} ${d.run.status_reason} asked=${asked.length}`);
    const info = await j(`/installs/${inst.id}/upgrade`);
    log(`upgrade: current ${info.current.version} → available ${info.available?.version} (${(info.changes || []).length} changes)`);
    const up = await post(`/installs/${inst.id}/upgrade`, { definition_id: info.available.id });
    const st = await waitRun(up.run.id, terminal);
    const after = (await j("/installs")).installs.find((i) => i.id === inst.id);
    log(`self-test ${st.run.status}; install now v${after.definition_version}`);
    const rb = await post(`/installs/${inst.id}/rollback`);
    log(`rollback → v${rb.install.definition_version}`);
    saveJSON("legacy.json", { pinned_run: { status: d.run.status, reason: d.run.status_reason, asked, artifacts: await artifacts(d, "legacy") },
      upgrade: { from: info.current.version, to: info.available.version, changes: info.changes, self_test: st.run.status,
        switched_to: after.definition_version }, rollback: rb.install.definition_version });
  },
  async dashboard() {
    const out = {};
    for (const i of (await j("/installs")).installs) {
      out[`${i.slug} ${i.id.slice(0, 8)}`] = await j(`/installs/${i.id}/dashboard?days=7`);
    }
    saveJSON("dashboards.json", out);
    for (const [k, v] of Object.entries(out)) log(`${k}: runs=${v.runs.total} first-pass=${v.runs.first_pass}/${v.runs.succeeded} waiting=${v.waiting.runs}+${v.waiting.open_exceptions} spend=${v.spend.credits} per-doc=${v.spend.per_document}`);
  },
  async cleanup() {
    for (const [k, ids] of Object.entries(made())) {
      for (const id of Array.isArray(ids) ? ids : [ids]) {
        const r = await am(`/installs/${id}`, { method: "DELETE" });
        log(`remove ${k} ${id}: ${r.status}`);
      }
    }
  },
};

const cmd = process.argv[2];
if (!cmds[cmd]) {
  console.log("usage: am2.js invoice | expense | financial | legacy | dashboard | cleanup");
  process.exit(2);
}
cmds[cmd]().catch((e) => { console.error("FAIL", e.message); process.exit(1); });
