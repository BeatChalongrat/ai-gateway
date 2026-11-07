#!/usr/bin/env node
// The free model made unreachable — on the TEST stack only — and put back.
//
//   SMOKE_TARGET=test node scripts/smoke/primary.js down     the model's endpoint accepts and never answers
//   SMOKE_TARGET=test node scripts/smoke/primary.js up       exactly as it was
//   SMOKE_TARGET=test node scripts/smoke/primary.js status
//
// This is the outage of 9 Oct 2026 (and of 28 Sep): the in-house box takes the connection and says
// nothing, so a turn waits out its timeout and another model answers. A container on the test network
// does exactly that; every key the test gateway serves the model with is pointed at it, and the test
// backend restarted so no cached endpoint survives. What the keys said before is written down first
// (primary_down.json beside the smoke's results, gitignored) and `up` puts back those words and no
// others; `down` refuses while that file exists, so a second `down` can never record the hole as the
// original. The circuit the test gateway opened while the model was away is closed again on `up`.
//
// dead_primary.js uses this to prove the smoke goes red when the model is down; anything else that
// must be shown with no in-house model on the test stack can use it the same way.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("./lib");

const HOLE = "sran-test-blackhole";
const HOLE_URL = `http://${HOLE}:9999/v1`;
const NETWORK = "sran-test_default";
const BACKEND = "sran-test-backend-1";
const REDIS = "sran-test-redis-1";
const STATE = process.env.SMOKE_PRIMARY_STATE || path.join(L.ROOT, "scripts", "probes", "FIX134", "results", "smoke", "primary_down.json");
const sh = (cmd, args, opts = {}) => execFileSync(cmd, args, { encoding: "utf8", ...opts }).trim();
const psql = (sql) => sh("docker", ["exec", "-i", L.T.containers.pg, "sh", "-c", 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'], { input: sql });
const lit = (s) => `'${String(s).replace(/'/g, "''")}'`;

function guard() {
  if (L.TARGET !== "test") throw new Error("primary.js changes the TEST stack only (SMOKE_TARGET=test)");
  const missing = L.assertProject(); // throws when a container belongs to another compose project
  if (missing.includes(L.T.containers.pg) || missing.includes(L.T.containers.backend)) throw new Error("the test stack is not running");
}

/** The model's rows and the keys they are served with, as the test gateway holds them now. */
function read(model) {
  const rows = JSON.parse(psql(`select coalesce(json_agg(json_build_object('id', id, 'served', served_by_keys)), '[]') from model_configs where model = ${lit(model)};`) || "[]");
  const ids = [...new Set(rows.flatMap((r) => Object.keys(r.served || {})))];
  const keys = ids.length ? JSON.parse(psql(`select coalesce(json_agg(json_build_object('id', id::text, 'base_url', base_url, 'tested', last_tested_base_url)), '[]') from provider_keys where id::text in (${ids.map(lit).join(",")});`) || "[]") : [];
  return { model, rows, keys };
}

async function restartBackend() {
  sh("docker", ["restart", BACKEND]);
  for (let i = 0; i < 90; i++) {
    if (sh("docker", ["inspect", "-f", "{{.State.Health.Status}}", BACKEND]) === "healthy") return;
    await L.wait(2000);
  }
  throw new Error("the test backend is not healthy after its restart");
}

function isDown() { return fs.existsSync(STATE); }

async function down(model = L.FREE_MODEL) {
  guard();
  if (isDown()) throw new Error(`already down: ${STATE} holds what the keys said before — run "up" first`);
  const was = read(model);
  if (!was.rows.length || !was.keys.length) throw new Error(`the test gateway serves ${model} with no key (rows ${was.rows.length}, keys ${was.keys.length}) — nothing to take away`);
  if (was.keys.some((k) => String(k.base_url || "").includes(HOLE))) throw new Error("a key already points at the hole and no record says what it was — restore the test stack's catalogue (scripts/test-stack.sh seed)");
  fs.mkdirSync(path.dirname(STATE), { recursive: true });
  fs.writeFileSync(STATE, JSON.stringify({ at: new Date().toISOString(), ...was }, null, 1));
  try { sh("docker", ["rm", "-f", HOLE], { stdio: "ignore" }); } catch { /* none */ }
  sh("docker", ["run", "-d", "--name", HOLE, "--network", NETWORK, "--label", "sran.test-stack=true", "--memory", "64m", "--entrypoint", "python", "ai-gateway-mock-provider:latest", "-c",
    "import socket\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',9999));s.listen(200);k=[]\nwhile True:\n c,_=s.accept();k.append(c)"]);
  const sql = ["begin;"];
  for (const k of was.keys) sql.push(`update provider_keys set base_url = ${lit(HOLE_URL)}, last_tested_base_url = ${lit(HOLE_URL)} where id::text = ${lit(k.id)};`);
  for (const r of was.rows) for (const id of Object.keys(r.served || {})) sql.push(`update model_configs set served_by_keys = jsonb_set(served_by_keys, ${lit(`{${id},base_url}`)}, to_jsonb(${lit(HOLE_URL)}::text)) where id = ${Number(r.id)};`);
  sql.push("commit;");
  psql(sql.join("\n"));
  await restartBackend();
  return { model, keys: was.keys.length, rows: was.rows.length };
}

async function up() {
  guard();
  if (!isDown()) return { restored: false };
  const was = JSON.parse(fs.readFileSync(STATE, "utf8"));
  const text = (v) => (v === null || v === undefined ? "null" : lit(v));
  const sql = ["begin;"];
  for (const k of was.keys) sql.push(`update provider_keys set base_url = ${text(k.base_url)}, last_tested_base_url = ${text(k.tested)} where id::text = ${lit(k.id)};`);
  for (const r of was.rows) sql.push(`update model_configs set served_by_keys = ${lit(JSON.stringify(r.served))}::jsonb where id = ${Number(r.id)};`);
  sql.push("commit;");
  psql(sql.join("\n"));
  const now = read(was.model);
  const same = JSON.stringify(now.keys.map((k) => [k.id, k.base_url, k.tested]).sort()) === JSON.stringify(was.keys.map((k) => [k.id, k.base_url, k.tested]).sort())
    && JSON.stringify(now.rows) === JSON.stringify(was.rows);
  if (!same) throw new Error(`the keys of ${was.model} are NOT as they were — ${STATE} is kept; put them back by hand from it`);
  try { sh("docker", ["rm", "-f", HOLE], { stdio: "ignore" }); } catch { /* gone */ }
  // the circuit the gateway opened on the model while it was away (the test stack's own Redis)
  try { sh("docker", ["exec", REDIS, "sh", "-c", 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "provider:*" | xargs -r redis-cli -a "$REDIS_PASSWORD" --no-auth-warning del > /dev/null']); } catch { /* no marker */ }
  await restartBackend();
  fs.rmSync(STATE);
  return { restored: true, model: was.model, keys: was.keys.length, rows: was.rows.length };
}

function status() {
  guard();
  const now = read(L.FREE_MODEL);
  return { model: now.model, down: isDown(), keys: now.keys.length, at_the_hole: now.keys.filter((k) => String(k.base_url || "").includes(HOLE)).length };
}

module.exports = { down, up, status, isDown, HOLE };

if (require.main === module) {
  const cmd = process.argv[2] || "status";
  const run = { down: () => down(), up, status: async () => status() }[cmd];
  if (!run) { process.stderr.write("usage: SMOKE_TARGET=test node scripts/smoke/primary.js down|up|status\n"); process.exit(2); }
  run().then((r) => { process.stdout.write(`primary ${cmd}: ${JSON.stringify(r)}\n`); }).catch((e) => { process.stderr.write(`primary ${cmd}: ${String(e && e.message || e)}\n`); process.exit(1); });
}
