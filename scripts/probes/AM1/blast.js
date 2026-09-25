#!/usr/bin/env node
// AM1 blast-radius probe (test stack): agent-runner under stress must not
// move chat. Measures chat turns (chat-edge → gateway → mock model) before,
// during a queue flood, while the runner is killed mid-run, and while the
// runner's DB pool is saturated; checks that killed runs resume with no
// duplicate artifact.
//
//   SMOKE_TARGET=test node scripts/probes/AM1/blast.js [flood=12] [turns=20]
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawnSync } = require("child_process");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") throw new Error("blast.js runs on the test stack only");
const RES = path.join(__dirname, "results");
fs.mkdirSync(RES, { recursive: true });
const FIX = path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report");
const RUNNER = process.env.AM_RUNNER_CONTAINER || "sran-test-agent-runner";
const MODEL = process.env.BLAST_MODEL || "ts-mock-model";
const session = L.bearerSession(L.mintPortalToken(L.TS_USER));
const AM = `${L.API_BASE.edge}/api/v1/agent-market`;
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function am(p, init = {}) {
  const r = await L.http(AM + p, { timeoutMs: 60000, ...init, headers: { "Content-Type": "application/json", ...session.headers } });
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} ${r.status} ${r.text.slice(0, 200)}`);
  return r.json;
}

function pct(xs, p) {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
}

async function chatLatency(n, label) {
  // A fresh chat every 5 turns: per-turn work grows with history, and the
  // phases must compare like with like.
  const ms = [];
  const fallbacks = [];
  const statuses = [];
  let bad = 0;
  let conv = null;
  for (let i = 0; i < n; i++) {
    if (i % 5 === 0) {
      if (conv) { await L.deleteChat(session, conv); L.forget(conv); }
      conv = await L.newChat(session, MODEL, `[AM1] blast ${label}`);
    }
    // chat-edge allows 30 messages a minute per user (RATE_LIMIT_MESSAGES):
    // start a turn at most every 2.2 s so the probe never trips it itself.
    const started = Date.now();
    const t = await L.turn(session, conv, `[AM1] ping ${label} ${i}`, { timeoutMs: 120000 });
    if (t.http >= 300 || !t.content) {
      bad++;
      statuses.push(t.http);
    }
    // Only turns the mock model served directly are comparable: a provider
    // fallback (the mock account in cooldown) is provider noise, counted apart.
    if (t.served && t.served !== MODEL) fallbacks.push(t.total);
    else ms.push(t.total);
    const gap = 2200 - (Date.now() - started);
    if (gap > 0) await L.wait(gap);
  }
  if (conv) { await L.deleteChat(session, conv); L.forget(conv); }
  const out = { label, n, direct: ms.length, fallbacks: fallbacks.length, failed: bad, failed_status: statuses,
    p50: pct(ms, 50), p90: pct(ms, 90), p95: pct(ms, 95), max: Math.max(...ms), samples: ms };
  log(`chat ${label}: direct=${ms.length} p50=${out.p50}ms p90=${out.p90}ms p95=${out.p95}ms max=${out.max}ms | fallback turns=${fallbacks.length} failed=${bad} ${statuses.join(",")}`);
  return out;
}

async function upload(installId, rel) {
  const fd = new FormData();
  fd.append("file", new Blob([fs.readFileSync(path.join(FIX, rel))]), path.basename(rel));
  const r = await fetch(`${AM}/installs/${installId}/files`, { method: "POST", headers: session.headers, body: fd });
  return (await r.json()).file;
}

// Every install this probe creates, removed on the way out — also when a
// step throws or the run is interrupted (a killed blast once left 16 behind).
const created = new Set();
async function removeInstalls() {
  for (const id of created) {
    const r = await L.tryHttp(`${AM}/installs/${id}`, { method: "DELETE", headers: session.headers });
    if (r.status === 200) created.delete(id);
    else log(`could not remove install ${id}: ${r.status || r.error}`);
  }
}
process.on("SIGINT", () => removeInstalls().finally(() => process.exit(130)));

async function flood(k) {
  const runs = [];
  for (let i = 0; i < k; i++) {
    const inst = (await am("/installs", { method: "POST", body: JSON.stringify({ slug: "sran.financial-report", idempotency_key: `am1-blast-${Date.now()}-${i}` }) })).install;
    created.add(inst.id);
    const f = await upload(inst.id, "sample-data/GL_2569-09.xlsx");
    const m = await am(`/installs/${inst.id}/messages`, { method: "POST", body: JSON.stringify({ text: "ปิดงบเดือนกันยายน", file_ids: [f.id], client_id: crypto.randomUUID() }) });
    runs.push({ install: inst.id, run: m.run_id });
  }
  log(`flooded ${k} runs`);
  return runs;
}

function pgActivity() {
  const out = L.csql(`select coalesce(application_name,'?') || '=' || count(*) from pg_stat_activity where datname='sran_ai' group by application_name order by application_name`);
  return out.split("\n").filter(Boolean).join(" ");
}

function cpuSnapshot() {
  const r = spawnSync("docker", ["stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}}"], { encoding: "utf8" });
  return (r.stdout || "").split("\n").filter((l) => /agent-runner|sbx-|chat-edge|sran-test-backend|ner/.test(l)).join(" | ");
}

async function statuses(runs) {
  const out = {};
  for (const r of runs) {
    const d = await am(`/runs/${r.run}`);
    out[r.run] = d;
  }
  return out;
}

async function queueDepth() {
  const out = L.csql(`select count(*) filter (where status='queued') || '|' || count(*) filter (where status='running') from agent_market.agent_runs`);
  const [q, r] = out.split("|").map(Number);
  return { queued: q || 0, running: r || 0 };
}

// Interleaved: idle / load / idle / load windows, so drift in the stack
// (providers, other containers) hits both conditions alike.
async function interleaved(rounds, n, perLoad) {
  const result = { at: new Date().toISOString(), model: MODEL, mode: "interleaved", windows: [] };
  const all = [];
  for (let r = 1; r <= rounds; r++) {
    for (;;) { const d = await queueDepth(); if (!d.queued && !d.running) break; await L.wait(3000); }
    const idle = await chatLatency(n, `idle${r}`);
    const runs = await flood(perLoad);
    all.push(...runs);
    for (;;) { const d = await queueDepth(); if (d.running) break; await L.wait(500); }
    const cpu = cpuSnapshot();
    const load = await chatLatency(n, `load${r}`);
    const depth = await queueDepth();
    log(`load${r}: runner busy throughout? queue after window=${JSON.stringify(depth)} cpu=${cpu}`);
    result.windows.push({ idle, load, cpu, depth_after: depth });
  }
  const idleAll = result.windows.flatMap((w) => w.idle.samples);
  const loadAll = result.windows.flatMap((w) => w.load.samples);
  result.summary = {
    idle: { n: idleAll.length, p50: pct(idleAll, 50), p90: pct(idleAll, 90), p95: pct(idleAll, 95) },
    load: { n: loadAll.length, p50: pct(loadAll, 50), p90: pct(loadAll, 90), p95: pct(loadAll, 95) },
    failed: result.windows.reduce((a, w) => a + w.idle.failed + w.load.failed, 0),
    fallback_turns: result.windows.reduce((a, w) => a + w.idle.fallbacks + w.load.fallbacks, 0),
  };
  fs.writeFileSync(path.join(RES, `blast_interleaved_${Date.now()}.json`), JSON.stringify(result, null, 1));
  console.log(JSON.stringify(result.summary, null, 1));
  for (;;) { const d = await queueDepth(); if (!d.queued && !d.running) break; await L.wait(5000); }
  await removeInstalls();
}

async function main() {
  if (process.argv[2] === "interleaved") {
    return interleaved(Number(process.argv[3] || 2), Number(process.argv[4] || 20), Number(process.argv[5] || 6));
  }
  const k = Number(process.argv[2] || 12);
  const n = Number(process.argv[3] || 40);
  const result = { at: new Date().toISOString(), model: MODEL, runner: RUNNER };
  result.pg_idle = pgActivity();
  result.baseline = await chatLatency(n, "baseline");
  const runs = await flood(k);
  await L.wait(3000);
  result.pg_during_flood = [pgActivity()];
  result.cpu_during_flood = cpuSnapshot();
  log(`pg sessions idle: ${result.pg_idle}`);
  log(`pg sessions during flood: ${result.pg_during_flood[0]}`);
  log(`cpu during flood: ${result.cpu_during_flood}`);
  result.during_flood = await chatLatency(n, "flood");
  result.pg_during_flood.push(pgActivity());
  // Kill the runner mid-run (like an OOM kill): no graceful stop.
  const running = Object.values(await statuses(runs)).filter((d) => d.run.status === "running").length;
  log(`killing ${RUNNER} with ${running} runs in flight`);
  spawnSync("docker", ["kill", RUNNER], { encoding: "utf8" });
  result.killed_with_running = running;
  result.while_killed = await chatLatency(Math.ceil(n / 2), "runner-killed");
  spawnSync("docker", ["start", RUNNER], { encoding: "utf8" });
  log("runner restarted; waiting for every flooded run to finish");
  const t0 = Date.now();
  let final;
  for (;;) {
    final = await statuses(runs);
    const done = Object.values(final).filter((d) => ["succeeded", "failed", "cancelled"].includes(d.run.status)).length;
    if (done === runs.length || Date.now() - t0 > 20 * 60 * 1000) break;
    await L.wait(5000);
  }
  result.after = await chatLatency(n, "after");
  const perRun = Object.values(final).map((d) => {
    const byFormat = {};
    for (const a of d.artifacts || []) byFormat[a.format] = (byFormat[a.format] || 0) + 1;
    return { run: d.run.id.slice(0, 8), status: d.run.status, reason: d.run.status_reason,
      attempts: d.run.attempts, replayed: (d.steps || []).filter((s) => s.attempt > 1).map((s) => `${s.name}x${s.attempt}`),
      artifacts: byFormat, duplicate: Object.values(byFormat).some((c) => c > 1) };
  });
  result.runs = perRun;
  result.summary = {
    succeeded: perRun.filter((r) => r.status === "succeeded").length, total: perRun.length,
    resumed_after_kill: perRun.filter((r) => r.attempts > 1).length,
    duplicate_artifacts: perRun.filter((r) => r.duplicate).length,
    p95_baseline: result.baseline.p95, p95_flood: result.during_flood.p95, p95_killed: result.while_killed.p95, p95_after: result.after.p95,
  };
  fs.writeFileSync(path.join(RES, `blast_${Date.now()}.json`), JSON.stringify(result, null, 1));
  console.log(JSON.stringify(result.summary, null, 1));
  await removeInstalls();
}

main().catch(async (e) => { console.error("FAIL", e.message); await removeInstalls(); await L.cleanup(); process.exit(1); });
