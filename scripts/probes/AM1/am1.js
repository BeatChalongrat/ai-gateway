#!/usr/bin/env node
// AM1 probes — drive Agent Market through the real HTTP API (chat-edge
// control plane + agent-runner), on the test stack by default.
//
//   SMOKE_TARGET=test node scripts/probes/AM1/am1.js key            # the runner's own gateway key (written to results/, never printed)
//   SMOKE_TARGET=test node scripts/probes/AM1/am1.js install        # install + self-test
//   SMOKE_TARGET=test node scripts/probes/AM1/am1.js run <scenario> # clean | ask | text | late | formats | cancel | flood
//
// Uses the smoke suite's helpers (token minting, .env reading — never printed).
// Every chat it creates is an Agent Market thread under the test user; results go to results/.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const L = require("../../smoke/lib.js");

const RES = path.join(__dirname, "results");
fs.mkdirSync(RES, { recursive: true });
const FIX = path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report");
const user = L.TARGET === "live" ? L.SMOKE_USER : L.TS_USER;
let session = null;
function sess() {
  if (!session) session = L.bearerSession(L.mintPortalToken(user));
  return session;
}
const EDGE = () => `${L.API_BASE.edge}/api/v1/agent-market`;
async function am(p, init = {}) {
  const r = await L.http(EDGE() + p, { timeoutMs: 60000, ...init,
    headers: { "Content-Type": "application/json", ...sess().headers, ...(init.headers || {}) } });
  return r;
}
async function j(p, init = {}) {
  const r = await am(p, init);
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 300)}`);
  return r.json;
}
const post = (p, body) => j(p, { method: "POST", body: JSON.stringify(body || {}) });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function upload(installId, rel) {
  const file = path.join(FIX, rel);
  const fd = new FormData();
  fd.append("file", new Blob([fs.readFileSync(file)]), path.basename(file));
  const r = await fetch(`${EDGE()}/installs/${installId}/files`, { method: "POST", headers: { ...sess().headers }, body: fd });
  const t = await r.json();
  if (r.status >= 400) throw new Error(`upload ${rel}: ${r.status} ${JSON.stringify(t)}`);
  return t.file;
}

async function waitRun(runId, pred, timeoutS = 300) {
  const t0 = Date.now();
  let last = "";
  for (;;) {
    const d = await j(`/runs/${runId}`);
    const s = `${d.run.status}${d.run.status_reason ? "/" + d.run.status_reason : ""}`;
    if (s !== last) { log(`run ${runId.slice(0, 8)} ${s}`); last = s; }
    if (pred(d)) return d;
    if ((Date.now() - t0) / 1000 > timeoutS) throw new Error(`timeout waiting run ${runId}: ${s}`);
    await L.wait(700);
  }
}
const terminal = (d) => ["succeeded", "failed", "cancelled"].includes(d.run.status);
const parked = (d) => d.run.status === "awaiting_input";

async function thread(installId, after = 0) {
  return j(`/installs/${installId}/thread?after=${after}`);
}

function saveJSON(name, obj) {
  fs.writeFileSync(path.join(RES, name), JSON.stringify(obj, null, 1));
}

async function downloadArtifacts(d, tag) {
  const dir = path.join(RES, "files", tag);
  fs.mkdirSync(dir, { recursive: true });
  for (const a of d.artifacts) {
    const r = await fetch(`${EDGE()}/artifacts/${a.id}/download`, { headers: { ...sess().headers } });
    const buf = Buffer.from(await r.arrayBuffer());
    fs.writeFileSync(path.join(dir, a.name), buf);
    const sha = crypto.createHash("sha256").update(buf).digest("hex");
    log(`  ${a.name} ${buf.length} B sha=${sha === a.sha256 ? "match" : "MISMATCH"} quality=${a.validation.passed ? "pass" : "FAIL"}`);
  }
  return dir;
}

function summarize(d) {
  d.steps = d.steps || [];
  d.clarifications = d.clarifications || [];
  d.artifacts = d.artifacts || [];
  const steps = d.steps.map((s) => `${s.seq}.${s.name}[${s.status}${s.attempt > 1 ? " x" + s.attempt : ""}${s.duration_ms != null ? " " + s.duration_ms + "ms" : ""}]`);
  return { status: d.run.status, reason: d.run.status_reason, credits: d.run.credits_spent, model: d.run.model_used,
    self_hosted: d.run.model_self_hosted, cards: d.clarifications.map((c) => `${c.kind}:${c.status}`), steps };
}

async function installAgent(selfTest) {
  const idem = `am1-probe-${user.id}`;
  const body = { slug: "sran.financial-report", idempotency_key: idem, self_test: selfTest,
    settings: { company: "บริษัท ทดสอบ AM1 จำกัด", formats: ["docx", "pptx"], currency: "THB" },
    theme: { id: "navy", company: "บริษัท ทดสอบ AM1 จำกัด" } };
  const r = await post("/installs", body);
  log(`install ${r.install.id} created=${r.created}`);
  return r;
}

async function newRound(installId, text, files) {
  const ids = [];
  for (const f of files) ids.push((await upload(installId, f)).id);
  const r = await post(`/installs/${installId}/messages`, { text, file_ids: ids, client_id: crypto.randomUUID() });
  log(`message → ${r.route} run=${r.run_id}`);
  return r;
}

function openCards(d) {
  return d.clarifications.filter((c) => c.status === "open");
}

async function answerAll(card, pick = (q) => q.default_label) {
  const qs = card.questions;
  const answers = {};
  for (const q of qs) answers[q.key] = { labels: [pick(q)] };
  const r = await post(`/cards/${card.id}/answer`, { answers });
  log(`answered card ${card.id.slice(0, 8)} (${qs.map((q) => q.key).join(", ")}) changed=${r.changed}`);
  return r;
}

const scenarios = {
  async clean(inst) {
    const m = await newRound(inst, "ปิดงบเดือนกันยายนให้หน่อยครับ", ["sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx", "sample-data/งบประมาณ_2569.xlsx"]);
    const d = await waitRun(m.run_id, terminal);
    saveJSON("clean_run.json", summarize(d));
    await downloadArtifacts(d, "clean");
    return d;
  },
  async ask(inst) {
    const m = await newRound(inst, "ปิดงบเดือนกันยายน", ["evals/fixtures/five_findings.xlsx"]);
    let d = await waitRun(m.run_id, (x) => parked(x) || terminal(x));
    for (let round = 0; round < 5 && parked(d); round++) {
      const cards = openCards(d);
      log(`round ${round + 1}: ${cards.map((c) => c.kind + "[" + c.questions.map((q) => q.key).join(",") + "]").join(" ")}`);
      for (const c of cards) await answerAll(c);
      // a second submit of the same card must change nothing
      if (round === 0 && cards[0]) {
        const again = await post(`/cards/${cards[0].id}/answer`, { answers: { [cards[0].questions[0].key]: { labels: [cards[0].questions[0].options[1].label] } } });
        log(`resubmit changed=${again.changed} (must be false)`);
      }
      d = await waitRun(m.run_id, (x) => (parked(x) && openCards(x).length > 0 && openCards(x)[0].id !== cards[0]?.id) || terminal(x));
    }
    saveJSON("ask_run.json", summarize(d));
    if (d.run.status === "succeeded") await downloadArtifacts(d, "ask");
    return d;
  },
  async text(inst) {
    const m = await newRound(inst, "ปิดงบเดือนกันยายน", ["evals/fixtures/period_mixed_sep.xlsx"]);
    let d = await waitRun(m.run_id, (x) => parked(x) || terminal(x));
    const r = await post(`/installs/${inst}/messages`, { text: "ตัดออกครับ ใช้เฉพาะกันยายน", client_id: crypto.randomUUID() });
    log(`typed answer → ${r.route}`);
    d = await waitRun(m.run_id, terminal);
    saveJSON("text_run.json", summarize(d));
    return d;
  },
  async late(inst) {
    const m = await newRound(inst, "ปิดงบกันยายน เทียบกับงบประมาณด้วย", ["sample-data/GL_2569-09.xlsx"]);
    let d = await waitRun(m.run_id, (x) => parked(x) || terminal(x));
    log(`parked on: ${openCards(d).map((c) => c.kind).join(",")}`);
    const r = await newRound(inst, "", ["sample-data/งบประมาณ_2569.xlsx"]);
    log(`upload into the same thread → ${r.route}`);
    d = await waitRun(m.run_id, terminal);
    saveJSON("late_run.json", summarize(d));
    return d;
  },
  async formats(inst) {
    const runs = (await j(`/installs/${inst}/runs`)).runs.filter((x) => x.kind === "run" && x.status === "succeeded");
    if (!runs.length) throw new Error("no finished run");
    const parent = runs[0].id;
    const before = (await j(`/runs/${parent}`)).steps.filter((s) => s.name === "compute").length;
    const r = await post(`/installs/${inst}/messages`, { text: "ขอ Excel ด้วยครับ", client_id: crypto.randomUUID() });
    log(`"ขอ Excel ด้วย" → ${r.route}`);
    await L.wait(1500);
    let d;
    for (let i = 0; i < 120; i++) {
      d = await j(`/runs/${parent}`);
      if (d.artifacts.some((a) => a.format === "xlsx")) break;
      await L.wait(1000);
    }
    const after = d.steps.filter((s) => s.name === "compute").length;
    log(`compute steps before=${before} after=${after} (must be equal); formats=${d.artifacts.map((a) => a.name).join(", ")}`);
    await downloadArtifacts(d, "formats");
    saveJSON("formats_run.json", { before, after, artifacts: d.artifacts.map((a) => a.name) });
    return d;
  },
  async cancel(inst) {
    const m = await newRound(inst, "ปิดงบเดือนกันยายน", ["evals/fixtures/period_mixed_sep.xlsx"]);
    await waitRun(m.run_id, (x) => parked(x) || terminal(x));
    const a = await post(`/runs/${m.run_id}/cancel`);
    const b = await post(`/runs/${m.run_id}/cancel`);
    log(`cancel twice: ${a.run.status} / ${b.run.status}`);
    return a;
  },
};

async function main() {
  const [cmd, arg] = process.argv.slice(2);
  if (cmd === "key") {
    const be = `${L.T.plain.backend}/portal/projects`;
    const h = { "Content-Type": "application/json", ...sess().headers };
    let r = await L.http(be, { headers: h });
    let proj = (r.json?.projects || r.json?.items || r.json || []).find?.((p) => p.name === "Agent Market runner");
    if (!proj) {
      r = await L.http(be, { method: "POST", headers: h, body: JSON.stringify({ name: "Agent Market runner", description: "agent-runner's own gateway identity (AM1)" }) });
      if (r.status >= 400) throw new Error(`create project: ${r.status} ${r.text.slice(0, 300)}`);
      proj = r.json;
    }
    const pid = proj.project_id || proj.id;
    r = await L.http(`${be}/${pid}/keys`, { method: "POST", headers: h, body: JSON.stringify({ name: `agent-runner ${new Date().toISOString().slice(0, 10)}` }) });
    if (r.status >= 400) throw new Error(`create key: ${r.status} ${r.text.slice(0, 300)}`);
    fs.writeFileSync(path.join(RES, `runner_key.${L.TARGET}`), r.json.api_key, { mode: 0o600 });
    log(`key ${r.json.key_prefix}… created for project ${pid} (value in results/runner_key.${L.TARGET})`);
    return;
  }
  const insts = (await j("/installs")).installs;
  if (cmd === "install") {
    const r = await installAgent(true);
    if (r.self_test_run_id) {
      const d = await waitRun(r.self_test_run_id, terminal);
      saveJSON("self_test.json", summarize(d));
      await downloadArtifacts(d, "self_test");
    }
    return;
  }
  if (cmd === "run") {
    const inst = insts[0]?.id || (await installAgent(false)).install.id;
    if (["text", "cancel"].includes(arg)) {
      // These scenarios need the question to be asked: clear the remembered answer first.
      const r = await j(`/installs/${inst}/defaults/answers.period_mixed`, { method: "DELETE" });
      log(`cleared remembered answers.period_mixed: ${r.removed}`);
    }
    const d = await scenarios[arg](inst);
    console.log(JSON.stringify(summarize(d), null, 1).slice(0, 3000));
    return;
  }
  console.log("usage: am1.js key | install | run <clean|ask|text|late|formats|cancel>");
}

main().catch((e) => { console.error("FAIL", e.message); process.exit(1); });
