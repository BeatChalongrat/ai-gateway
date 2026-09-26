#!/usr/bin/env node
// AM1b — the nasty fixture through the whole stack (control plane, runner,
// sandbox, model): upload, answer every card with its recommended option (as
// a click would), wait for the files, compare the computed P&L with the
// journal's numbers (expected.json) and download the files.
//
//   SMOKE_TARGET=test node scripts/probes/AM1b/run_nasty.js [scenario]
//     scenario: full (CSV + prior + budget, default) | csv (the CSV alone)
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const L = require("../../smoke/lib.js");

const RES = path.join(__dirname, "results");
const NASTY = path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report", "evals", "nasty");
const EXP = JSON.parse(fs.readFileSync(path.join(NASTY, "expected.json"), "utf8"));
const user = L.TARGET === "live" ? null : L.TS_USER;
if (!user) throw new Error("run_nasty.js drives the test stack; the live walkthrough goes through the desktop app");
const session = L.bearerSession(L.mintPortalToken(user));
const AM = `${L.API_BASE.edge}/api/v1/agent-market`;
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function j(p, init = {}) {
  const r = await L.http(AM + p, { timeoutMs: 60000, ...init, headers: { "Content-Type": "application/json", ...session.headers, ...(init.headers || {}) } });
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 200)}`);
  return r.json;
}
async function upload(inst, name) {
  const fd = new FormData();
  fd.append("file", new Blob([fs.readFileSync(path.join(NASTY, name))]), name);
  const r = await fetch(`${AM}/installs/${inst}/files`, { method: "POST", headers: session.headers, body: fd });
  const t = await r.json();
  if (r.status >= 400) throw new Error(`upload ${name}: ${r.status} ${JSON.stringify(t)}`);
  return t.file.id;
}

async function main() {
  const scenario = process.argv[2] || "full";
  const files = scenario === "csv" ? ["GL_Express_2569-09.csv"] : ["GL_Express_2569-09.csv", "GL_Express_2569-08.xlsx", "งบประมาณ_2569.xlsx"];
  const inst = (await j("/installs")).installs.find((i) => i.slug === "sran.financial-report");
  const ids = [];
  for (const f of files) ids.push(await upload(inst.id, f));
  const t0 = Date.now();
  const m = await j(`/installs/${inst.id}/messages`, { method: "POST", body: JSON.stringify({
    text: "[AM1b] ปิดงบเดือนกันยายน 2569 เทียบเดือนก่อนและงบประมาณ", file_ids: ids, client_id: crypto.randomUUID() }) });
  log(`message → ${m.route} run=${m.run_id}`);
  const answered = new Set();
  const cards = [];
  let d;
  for (;;) {
    d = await j(`/runs/${m.run_id}`);
    if (["succeeded", "failed", "cancelled"].includes(d.run.status)) break;
    for (const c of (d.clarifications || []).filter((x) => x.status === "open" && !answered.has(x.id))) {
      answered.add(c.id);
      const answers = {};
      for (const q of c.questions) answers[q.key] = { labels: [q.default_label] };
      cards.push({ kind: c.kind, moment: c.moment, questions: c.questions.map((q) => ({ key: q.key, header: q.header, picked: q.default_label, question: q.question })) });
      await j(`/cards/${c.id}/answer`, { method: "POST", body: JSON.stringify({ answers }) });
      log(`answered ${c.kind} (${c.questions.map((q) => q.key).join(", ")}) at ${((Date.now() - t0) / 1000).toFixed(1)} s`);
    }
    if ((Date.now() - t0) > 600000) throw new Error("timeout");
    await L.wait(800);
  }
  const secs = (Date.now() - t0) / 1000;
  const comp = await j(`/runs/${m.run_id}/computed`).catch(() => null);
  const lines = {};
  for (const l of (comp?.lines || comp?.computed?.lines || [])) lines[l.key] = l.current;
  const exp = EXP["GL_Express_2569-09.csv"].pl_correct;
  const match = Object.fromEntries(Object.keys(exp).map((k) => [k, lines[k] === exp[k]]));
  const dir = path.join(RES, "files", `nasty_${scenario}`);
  fs.mkdirSync(dir, { recursive: true });
  const arts = [];
  for (const a of d.artifacts || []) {
    const r = await fetch(`${AM}/artifacts/${a.id}/download`, { headers: session.headers });
    const buf = Buffer.from(await r.arrayBuffer());
    fs.writeFileSync(path.join(dir, a.name), buf);
    arts.push({ name: a.name, size: buf.length, sha_ok: crypto.createHash("sha256").update(buf).digest("hex") === a.sha256, quality: a.validation?.passed });
  }
  const out = { scenario, run: m.run_id, status: d.run.status, reason: d.run.status_reason, seconds: secs, credits: d.run.credits_spent,
    model: d.run.model_used, questions: cards.reduce((n, c) => n + c.questions.length, 0), cards, pl: lines, expected: exp, match, artifacts: arts,
    steps: (d.steps || []).map((s) => `${s.seq}.${s.name}[${s.status} ${s.duration_ms}ms]`) };
  fs.writeFileSync(path.join(RES, `run_nasty_${scenario}.json`), JSON.stringify(out, null, 1));
  log(JSON.stringify({ status: out.status, seconds: secs.toFixed(1), credits: out.credits, model: out.model, questions: out.questions,
    match, artifacts: arts.map((a) => `${a.name} ${a.quality ? "pass" : "FAIL"}`) }));
}

main().catch((e) => { console.error("FAIL", e.message); process.exit(1); });
