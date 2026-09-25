#!/usr/bin/env node
// AM1 blast radius — Agent Market's database pools exhausted.
//
//   SMOKE_TARGET=test node scripts/probes/AM1/pool.js [holdSeconds=40] [turns=10]
//
// A psql session on the TEST chat database holds an ACCESS EXCLUSIVE lock on
// agent_market.agent_installs, so every Agent Market query waits: the control
// plane's pool (3) and the runner's (4) end up stuck. Meanwhile the probe
// fires a burst of Agent Market reads and measures chat turns and a core
// read (conversation list) against a baseline taken just before. Pass =
// chat unchanged, Agent Market fails fast (lock_timeout) instead of hanging,
// and recovers by itself when the lock goes.
"use strict";
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const L = require("../../smoke/lib.js");

const HOLD = Number(process.argv[2] || 40);
const N = Number(process.argv[3] || 10); // chat turns (and core reads) per phase
const MODEL = process.env.BLAST_MODEL || "ts-mock-model";
const RES = path.join(__dirname, "results");
const session = L.bearerSession(L.mintPortalToken(L.TS_USER));
const EDGE = `${L.API_BASE.edge}/api/v1`;
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const pct = (a, p) => (a.length ? [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.ceil((p / 100) * a.length) - 1)] : null);

async function chat(n, label) {
  const ms = [];
  let bad = 0;
  const conv = await L.newChat(session, MODEL, `[AM1] pool ${label}`);
  for (let i = 0; i < n; i++) {
    const started = Date.now();
    const t = await L.turn(session, conv, `[AM1] pool ${label} ${i}`, { timeoutMs: 120000 });
    if (t.http >= 300 || !t.content) bad++;
    else if (!t.served || t.served === MODEL) ms.push(t.total);
    const gap = 2200 - (Date.now() - started);
    if (gap > 0) await L.wait(gap);
  }
  await L.deleteChat(session, conv);
  L.forget(conv);
  return { n, ok: ms.length, failed: bad, p50: pct(ms, 50), p95: pct(ms, 95), samples: ms };
}

async function coreReads(n) {
  const ms = [];
  let bad = 0;
  for (let i = 0; i < n; i++) {
    const t0 = Date.now();
    const r = await L.tryHttp(`${EDGE}/conversations?limit=5`, { headers: session.headers, timeoutMs: 30000 });
    if (r.status !== 200) bad++;
    ms.push(Date.now() - t0);
    await L.wait(300);
  }
  return { n, failed: bad, p50: pct(ms, 50), p95: pct(ms, 95) };
}

async function amBurst(k) {
  return Promise.all(Array.from({ length: k }, async () => {
    const t0 = Date.now();
    const r = await L.tryHttp(`${EDGE}/agent-market/installs`, { headers: session.headers, timeoutMs: 90000 });
    return { status: r.status, ms: Date.now() - t0 };
  }));
}

async function main() {
  if (L.TARGET !== "test") throw new Error("pool.js runs on the test stack only");
  const out = { at: new Date().toISOString(), hold_s: HOLD };
  // Same shape as the locked phase: chat turns and core reads side by side.
  const [chatBase, coreBase] = await Promise.all([chat(N, "baseline"), coreReads(N)]);
  out.baseline = { chat: chatBase, core: coreBase };
  log(`baseline chat p95 ${out.baseline.chat.p95} ms, core read p95 ${out.baseline.core.p95} ms`);

  const holder = spawn("docker", ["exec", L.T.containers.pgChat, "psql", "-U", "chatedge", "-d", "sran_ai", "-v", "ON_ERROR_STOP=1", "-c",
    `BEGIN; LOCK TABLE agent_market.agent_installs IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(${HOLD}); COMMIT;`], { stdio: "ignore" });
  const released = new Promise((res) => holder.on("exit", (code) => res(code)));
  await L.wait(1500);
  log(`lock held for ${HOLD}s; Agent Market burst + chat`);
  const burstP = amBurst(12);
  await L.wait(1500);
  out.pg_during = L.csql(`select app || ':' || wait || '=' || n from (select coalesce(application_name,'?') app,
    coalesce(wait_event_type,'-') wait, count(*) n from pg_stat_activity where datname='sran_ai' group by 1, 2) s order by 1`).split("\n");
  const [chatDuring, coreDuring] = await Promise.all([chat(N, "locked"), coreReads(N)]);
  out.during = { chat: chatDuring, core: coreDuring };
  const burst = await burstP;
  out.agent_market_burst = { statuses: burst.map((b) => b.status), max_ms: Math.max(...burst.map((b) => b.ms)) };
  log(`during: chat p95 ${chatDuring.p95} ms (failed ${chatDuring.failed}), core p95 ${coreDuring.p95} ms; agent-market burst ${JSON.stringify(out.agent_market_burst)}`);

  out.lock_exit = await released;
  const t0 = Date.now();
  let rec = null;
  for (let i = 0; i < 20; i++) {
    const r = await L.tryHttp(`${EDGE}/agent-market/installs`, { headers: session.headers, timeoutMs: 30000 });
    if (r.status === 200) { rec = Date.now() - t0; break; }
    await L.wait(1000);
  }
  out.recovered_after_ms = rec;
  out.pass = chatDuring.failed === 0 && coreDuring.failed === 0 && rec !== null &&
    out.agent_market_burst.max_ms < HOLD * 1000;
  log(`recovered after ${rec} ms; pass=${out.pass}`);
  fs.writeFileSync(path.join(RES, `pool_${Date.now()}.json`), JSON.stringify(out, null, 1));
}

main().catch((e) => { console.error("FAIL", e.message); process.exit(1); });
