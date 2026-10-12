#!/usr/bin/env node
// The proof that the smoke can fail: the free model is taken away, and the suite must say so.
//
//   SMOKE_TARGET=test node scripts/smoke/dead_primary.js red      the model's endpoint accepts and never answers;
//                                                                 the suite must come out RED, each turn naming the
//                                                                 model it asked — then the model is put back
//   SMOKE_TARGET=test node scripts/smoke/dead_primary.js green    the model is there; the suite must come out GREEN,
//                                                                 every turn answered by the model it asked
//
// On 9 Oct 2026 the in-house model stopped answering and both live runs of the suite were green. This is
// that day, made on the test stack (primary.js), and run by the gate after every change: the day the
// suite is green with the model down, `red` exits 1 and says NOT PROVEN. Exit 0 = proven.
//
//   SMOKE_SH=<another checkout>/scripts/smoke.sh   the suite to judge (default: this checkout's). The suite
//                                                  as it was before it said who answered is NOT PROVEN.
//   DEAD_PRIMARY_OUT=<folder>                      where the two transcripts go (default: the smoke's results)
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const L = require("./lib");
const primary = require("./primary");

const OUT = process.env.DEAD_PRIMARY_OUT || path.join(L.ROOT, "scripts", "probes", "FIX134", "results", "smoke");
const SMOKE_SH = process.env.SMOKE_SH || path.join(L.ROOT, "scripts", "smoke.sh");
// chat-edge's send limit holds the smoke user for a minute after a run of the suite (its 429 check). A turn
// refused by that limit says nothing about the model, so a run waits out the one before it — whichever
// way that one was started (the newest result of the test target, or this script's own stamp).
const RESULTS = path.join(L.ROOT, "scripts", "probes", "FIX134", "results", "smoke");
const QUIET = path.join(RESULTS, "dead_primary_last_run");
const QUIET_MS = 95_000;
function lastRunMs() {
  let last = 0;
  try { last = Number(fs.readFileSync(QUIET, "utf8")) || 0; } catch { /* no stamp */ }
  try {
    for (const f of fs.readdirSync(RESULTS)) if (/^smoke_test_.*\.json$/.test(f)) last = Math.max(last, fs.statSync(path.join(RESULTS, f)).mtimeMs);
  } catch { /* no results yet */ }
  return last;
}
const LIMITED = /RATE_LIMIT_EXCEEDED|http=429|→ 429/;
const say = (s) => process.stdout.write(`${new Date().toISOString().slice(11, 19)}Z dead-primary ${s}\n`);
const posix = (p) => p.replace(/\\/g, "/");

async function runSuite(step) {
  fs.mkdirSync(OUT, { recursive: true });
  fs.mkdirSync(RESULTS, { recursive: true });
  const left = lastRunMs() + QUIET_MS - Date.now();
  if (left > 0) { say(`waiting ${Math.ceil(left / 1000)} s — the send limit of the run before this one`); await L.wait(left); }
  const json = path.join(OUT, `dead_primary_${step}.json`);
  const txt = path.join(OUT, `dead_primary_${step}.txt`);
  fs.rmSync(json, { force: true });
  const r = spawnSync("bash", [posix(SMOKE_SH)], {
    encoding: "utf8", maxBuffer: 64 << 20, timeout: 540_000,
    env: { ...process.env, SMOKE_TARGET: "test", SMOKE_DESKTOP: "0", SMOKE_OUT: posix(json), SMOKE_ENV_FILE: process.env.SMOKE_ENV_FILE || path.join(L.ROOT, ".env") },
  });
  fs.writeFileSync(QUIET, String(Date.now()));
  fs.writeFileSync(txt, (r.stdout || "") + (r.stderr || ""));
  let result = null;
  try { result = JSON.parse(fs.readFileSync(json, "utf8")); } catch { /* the suite wrote none */ }
  return { code: r.status, result, txt };
}

// The checks that ask a model. A suite that does not say which of its checks made a turn (the one
// before it said who answered) is judged by these names.
const TURN_NAMES = ["webchat.turn", "api.completion", "api.idempotency"];
const turnChecks = (result) => (result?.checks || []).filter((c) => (Array.isArray(c.turns) && c.turns.length) || TURN_NAMES.includes(c.name)).filter((c) => c.status !== "SKIP");
const line = (c) => `  ${c.status} ${c.name}  ${c.detail.slice(0, 200)}`;

async function red() {
  const model = L.FREE_MODEL;
  say(`taking ${model} away on the test stack`);
  let run;
  try {
    await primary.down(model);
    say(`running ${SMOKE_SH}`);
    run = await runSuite("red");
  } finally {
    const back = await primary.up();
    say(`${model} put back (${JSON.stringify(back)})`);
  }
  const checks = turnChecks(run.result);
  const wrong = [];
  if (run.code === 0 || run.result?.ok !== false) wrong.push(`the suite came out GREEN (exit ${run.code}) with ${model} down`);
  if (!checks.length) wrong.push("no check of the suite made a turn");
  for (const c of checks) {
    if (c.status !== "FAIL") { wrong.push(`${c.name} is ${c.status} with ${model} down: ${c.detail.slice(0, 160)}`); continue; }
    // the right words: the model asked, and either who answered in its place or that nobody did —
    // and for that reason, not because the send limit refused the turn before it reached a model
    if (!c.detail.startsWith(`asked ${model}, `)) wrong.push(`${c.name} failed without naming the model it asked: ${c.detail.slice(0, 160)}`);
    else if (LIMITED.test(c.detail)) wrong.push(`${c.name} failed on the send limit, not on the model: ${c.detail.slice(0, 160)}`);
  }
  for (const c of checks) say(line(c).trim());
  if (wrong.length) { for (const w of wrong) say(`NOT PROVEN: ${w}`); say(`transcript: ${run.txt}`); return 1; }
  say(`PROVEN RED: with ${model} down the suite is red (exit ${run.code}) and ${checks.length} turn check(s) name the model asked → ${run.txt}`);
  return 0;
}

async function green() {
  const model = L.FREE_MODEL;
  if (primary.isDown()) throw new Error(`${model} is still down on the test stack — run: SMOKE_TARGET=test node scripts/smoke/primary.js up`);
  say(`running ${SMOKE_SH} with ${model} in place`);
  const run = await runSuite("green");
  const checks = turnChecks(run.result);
  const wrong = [];
  if (run.code !== 0 || run.result?.ok !== true) wrong.push(`the suite came out RED (exit ${run.code}): ${(run.result?.checks || []).filter((c) => c.status === "FAIL").map((c) => c.name).join(", ") || "no result"}`);
  if (!checks.length) wrong.push("no check of the suite made a turn");
  for (const c of checks) {
    if (c.status !== "PASS") wrong.push(`${c.name} is ${c.status}: ${c.detail.slice(0, 160)}`);
    const last = (c.turns || []).slice(-1)[0];
    if (!last || !L.sameModel(last.served, model)) wrong.push(`${c.name} does not say that ${model} answered it`);
  }
  for (const c of checks) say(line(c).trim());
  if (wrong.length) { for (const w of wrong) say(`NOT PROVEN: ${w}`); say(`transcript: ${run.txt}`); return 1; }
  say(`PROVEN GREEN: ${checks.length} turn check(s), each answered by ${model} → ${run.txt}`);
  return 0;
}

if (require.main === module) {
  const step = { red, green }[process.argv[2]];
  if (!step || L.TARGET !== "test") { process.stderr.write("usage: SMOKE_TARGET=test node scripts/smoke/dead_primary.js red|green   (the test stack only)\n"); process.exit(2); }
  step().then((rc) => process.exit(rc)).catch((e) => { say(`FAILED: ${String(e && e.message || e)}`); process.exit(2); });
}
module.exports = { turnChecks };
