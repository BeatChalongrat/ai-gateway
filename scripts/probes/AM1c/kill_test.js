#!/usr/bin/env node
// AM1c §2 — kill the agent runner mid-step on the TEST stack and watch the
// orphaned sandbox container go.
//
//   SMOKE_TARGET=test node scripts/probes/AM1c/kill_test.js restart|recreate
//
// restart:  the step's container is frozen (docker pause — a hung step that
//           cannot finish or clean up on its own), the runner is SIGKILLed
//           and comes back in the SAME container (restart policy, or docker
//           start): its startup sweep must remove the orphan at once.
// recreate: the same, but the runner comes back in a NEW container (new
//           hostname, so a new runner id): the startup sweep must leave the
//           orphan alone (another runner's, before its deadline) and the
//           timer sweep must remove it once it is past its deadline.
// Both: the run itself resumes and succeeds. The runner must be started with
// AGENT_SANDBOX_WALL_SECONDS=30, AGENT_RUNNER_SWEEP_SECONDS=10 and
// AGENT_RUNNER_SWEEP_GRACE_SECONDS=10 (kill_test.sh does).
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { execFileSync } = require("child_process");
const L = require("../../smoke/lib.js");

const RUNNER = "sran-test-agent-runner";
const RES = path.join(__dirname, "results");
const NASTY = path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report", "evals", "nasty");
const user = L.TARGET === "live" ? null : L.TS_USER;
if (!user) throw new Error("kill_test.js runs on the test stack only (SMOKE_TARGET=test)");
const session = L.bearerSession(L.mintPortalToken(user));
const AM = `${L.API_BASE.edge}/api/v1/agent-market`;
const out = { scenario: process.argv[2], events: [] };
const log = (m, extra) => { const line = `${new Date().toISOString().slice(11, 23)} ${m}`; console.log(line); out.events.push(extra ? { t: line, ...extra } : line); };
const docker = (...a) => { try { return execFileSync("docker", a, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim(); } catch (e) { return `ERR ${String(e.stderr || e.message).trim()}`; } };
const exists = (name) => docker("ps", "-a", "--filter", `name=^${name}$`, "--format", "{{.Names}}|{{.State}}");

async function j(p, init = {}) {
  const r = await L.http(AM + p, { timeoutMs: 60000, ...init, headers: { "Content-Type": "application/json", ...session.headers, ...(init.headers || {}) } });
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 200)}`);
  return r.json;
}

async function startRun() {
  const inst = (await j("/installs")).installs.find((i) => i.slug === "sran.financial-report");
  const fd = new FormData();
  const name = "GL_Express_2569-09.csv";
  fd.append("file", new Blob([fs.readFileSync(path.join(NASTY, name))]), name);
  const up = await (await fetch(`${AM}/installs/${inst.id}/files`, { method: "POST", headers: session.headers, body: fd })).json();
  const m = await j(`/installs/${inst.id}/messages`, { method: "POST", body: JSON.stringify({
    text: "[AM1c] ปิดงบเดือนกันยายน 2569", file_ids: [up.file.id], client_id: crypto.randomUUID() }) });
  return m.run_id;
}

async function drive(run, maxMs) {
  const t0 = Date.now();
  const answered = new Set();
  for (;;) {
    const d = await j(`/runs/${run}`);
    if (["succeeded", "failed", "cancelled"].includes(d.run.status)) return d;
    for (const c of (d.clarifications || []).filter((x) => x.status === "open" && !answered.has(x.id))) {
      answered.add(c.id);
      const answers = {};
      for (const q of c.questions) answers[q.key] = { labels: [q.default_label] };
      await j(`/cards/${c.id}/answer`, { method: "POST", body: JSON.stringify({ answers }) });
    }
    if (Date.now() - t0 > maxMs) throw new Error(`run ${run} not finished after ${maxMs / 1000} s (${d.run.status})`);
    await L.wait(700);
  }
}

// The first running step container of the run: freeze it, then SIGKILL the runner.
async function killMidStep(run) {
  const t0 = Date.now();
  while (Date.now() - t0 < 120000) {
    const line = docker("ps", "--filter", `label=sran.agent_market.run_id=${run}`, "--filter", "status=running",
      "--format", '{{.Names}}|{{.Label "sran.agent_market.runner"}}|{{.Label "sran.sandbox.deadline_unix"}}');
    if (line && !line.startsWith("ERR")) {
      const [box, runnerId, deadline] = line.split("\n")[0].split("|");
      const paused = docker("pause", box);
      const killed = docker("kill", RUNNER);
      log(`step container ${box} (runner ${runnerId}) frozen: ${paused.startsWith("ERR") ? paused : "paused"}; runner SIGKILLed: ${killed.startsWith("ERR") ? killed : "yes"}`);
      return { box, runnerId, deadline: Number(deadline) };
    }
    await L.wait(100);
  }
  throw new Error("no step container seen within 120 s");
}

async function waitGone(box, maxMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    if (!exists(box)) return (Date.now() - t0) / 1000;
    await L.wait(500);
  }
  return null;
}

function runnerLog(sinceIso) {
  const l = docker("logs", "--since", sinceIso, RUNNER);
  return l.split("\n").filter((x) => /orphaned sandbox|left by a previous runner|agent-runner started/.test(x)).map((x) => x.slice(0, 260));
}

async function main() {
  const scenario = process.argv[2];
  if (!["restart", "recreate"].includes(scenario)) throw new Error("usage: kill_test.js restart|recreate");
  if (scenario === "recreate") docker("update", "--restart=no", RUNNER); // come back only as a new container
  const run = await startRun();
  out.run = run;
  log(`run ${run} started`);
  const k = await killMidStep(run);
  out.orphan = k;
  const sinceIso = new Date(Date.now() - 2000).toISOString();
  log(`orphan right after the kill: ${exists(k.box)}; runner: ${exists(RUNNER)}`);
  if (scenario === "restart") {
    await L.wait(4000);
    let state = exists(RUNNER);
    if (!/running/.test(state)) { log(`runner not restarted by its policy (${state}); docker start`); docker("start", RUNNER); } else log("runner restarted by its restart policy (same container)");
    const gone = await waitGone(k.box, 60000);
    out.gone_after_s = gone;
    log(gone === null ? `ORPHAN STILL THERE after 60 s: ${exists(k.box)}` : `orphan gone ${gone.toFixed(1)} s after the runner came back`);
  } else {
    const env = { ...process.env };
    execFileSync("bash", [path.join(__dirname, "kill_test.sh"), "recreate"], { stdio: "inherit", env });
    const after = exists(k.box);
    out.after_new_runner_start = after;
    log(`new runner container up; orphan now: ${after} (must still exist: another runner's, before its deadline ${new Date(k.deadline * 1000).toISOString().slice(11, 19)})`);
    const maxMs = Math.max(0, k.deadline * 1000 + 10000 - Date.now()) + 40000;
    const gone = await waitGone(k.box, maxMs);
    out.gone_after_s = gone;
    out.gone_vs_deadline_s = gone === null ? null : Math.round(Date.now() / 1000 - k.deadline);
    log(gone === null ? `ORPHAN STILL THERE past deadline+grace: ${exists(k.box)}` : `orphan gone ${out.gone_vs_deadline_s} s after its deadline (grace 10 s, sweep every 10 s)`);
  }
  out.runner_log = runnerLog(sinceIso);
  for (const l of out.runner_log) log(`runner: ${l}`);
  const d = await drive(run, 300000);
  out.status = d.run.status;
  out.steps = (d.steps || []).map((s) => `${s.name}#${s.attempt ?? "?"}[${s.status}]`);
  log(`run ${run}: ${d.run.status} ${d.run.status_reason || ""}; steps ${out.steps.join(" ")}`);
  fs.mkdirSync(RES, { recursive: true });
  fs.writeFileSync(path.join(RES, `kill_test_${scenario}.json`), JSON.stringify(out, null, 1));
  const ok = out.gone_after_s !== null && out.status === "succeeded" && (scenario === "restart" || /\|/.test(out.after_new_runner_start || ""));
  log(ok ? "PASS" : "FAIL");
  process.exit(ok ? 0 : 1);
}

main().catch((e) => { console.error("FAIL", e.message); process.exit(1); });
