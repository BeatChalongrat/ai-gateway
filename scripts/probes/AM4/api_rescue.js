#!/usr/bin/env node
// AM4 §7 — an API call that names qwen gets the web's rescue, and far sooner
// than the 91 s of 2026-09-28 06:52–07:02Z. On the TEST stack only.
//
//   SMOKE_TARGET=test node scripts/probes/AM4/api_rescue.js
//
// The outage's shape: the self-hosted box accepts the connection and never
// answers. A container on the test network does exactly that; the test
// gateway's qwen key is pointed at it for the probe and pointed back after
// (the value read first, restored in `finally`), the test backend restarted
// each time so no cached endpoint survives. A throwaway API key for the
// seeded test owner is made through the test backend's own endpoint, kept in
// memory only (never printed or written), and revoked at the end.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { execFileSync } = require("child_process");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") {
  console.error("api_rescue.js runs on the test stack only");
  process.exit(2);
}
const OUT = path.join(__dirname, "results", "api_rescue");
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const results = [];
const check = (ok, what) => { log(`${ok ? "PASS" : "FAIL"} ${what}`); if (!ok) process.exitCode = 1; results.push({ ok, what }); return ok; };
const QWEN = "qwen3.8-27b-fp8";
const KEY_ID = "bb2b65da-8360-4ebf-90d1-a0664a325075"; // the test gateway's qwen key (label 9beat)
const HOLE = "sran-test-blackhole";
const BACKEND = "http://127.0.0.1:18081";
const GW = "http://127.0.0.1:18080";
const sh = (cmd, args, opts = {}) => execFileSync(cmd, args, { encoding: "utf8", ...opts }).trim();
const psql = (sql) => sh("docker", ["exec", "-i", "sran-test-postgres-1", "sh", "-c", 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'], { input: sql });
const MODEL_ROW = `(SELECT id FROM model_configs WHERE provider = 'sranai' AND model = '${"qwen3.8-27b-fp8"}')`;

async function restartBackend() {
  sh("docker", ["restart", "sran-test-backend-1"]);
  for (let i = 0; i < 90; i++) {
    const s = sh("docker", ["inspect", "-f", "{{.State.Health.Status}}", "sran-test-backend-1"]);
    if (s === "healthy") return;
    await L.wait(2000);
  }
  throw new Error("test backend not healthy after restart");
}

async function portal(method, p, body) {
  const tok = L.mintPortalToken(L.TS_USER);
  const r = await L.http(BACKEND + p, { method, timeoutMs: 30000, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined });
  if (r.status >= 400) throw new Error(`${method} ${p} → ${r.status} ${r.text.slice(0, 200)}`);
  return r.json;
}

async function ask(key, extra = {}) {
  const t0 = Date.now();
  const r = await L.http(`${GW}/v1/chat/completions`, { method: "POST", timeoutMs: 180000,
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", ...extra },
    body: JSON.stringify({ model: QWEN, max_tokens: 4000,
      // a fresh message each call: the gateway's response cache keys on the messages
      messages: [{ role: "user", content: `[AM4] ตอบคำเดียวว่า สวัสดี (${crypto.randomUUID().slice(0, 8)})` }] }) });
  const s = (Date.now() - t0) / 1000;
  const j = r.json || {};
  const out = { status: r.status, seconds: Math.round(s * 10) / 10, model: j.model, cached: j.cached, fallback_info: j.fallback_info,
    error: j.error && { code: j.error.code || j.error.type, message: String(j.error.message || "").slice(0, 200) },
    headers: Object.fromEntries([...(r.headers ? r.headers.entries() : [])].filter(([k]) => /^x-gateway|^x-sran/i.test(k))),
    answer: j.choices && j.choices[0] && String(j.choices[0].message && j.choices[0].message.content || "").slice(0, 120) };
  return out;
}

async function main() {
  // the key's endpoint, the endpoint it was tested at, and the box qwen is served by — all three read first
  const original = psql(`select coalesce(base_url, '') || '|' || coalesce(last_tested_base_url, '') from provider_keys where id = '${KEY_ID}';`);
  const served = psql(`select served_by_keys::text from model_configs where id = ${MODEL_ROW};`);
  const [origBase, origTested] = original.split("|");
  check(!!origBase && !!origTested && served.includes(KEY_ID), `the qwen key's endpoint and qwen's served-by record read first`);
  fs.writeFileSync(path.join(OUT, "restore.sql"), `-- to restore by hand if the probe died
update provider_keys set base_url = '${origBase}', last_tested_base_url = '${origTested}' where id = '${KEY_ID}';
` +
    `update model_configs set served_by_keys = '${served.replace(/'/g, "''")}'::jsonb where id = ${MODEL_ROW};
`);
  // a throwaway key
  const projects = (await portal("GET", "/portal/projects")).projects;
  const project = projects[0];
  const made = await portal("POST", `/portal/projects/${project.project_id || project.id}/keys`, { name: "[AM4] api rescue probe" });
  const key = made.api_key;
  const pid = project.project_id || project.id;
  const out = {};
  try {
    out.healthy = await ask(key);
    log(`healthy qwen: ${out.healthy.status} ${out.healthy.model} in ${out.healthy.seconds} s`);
    // the outage: accepts, never answers
    try { sh("docker", ["rm", "-f", HOLE], { stdio: "ignore" }); } catch { /* none */ }
    sh("docker", ["run", "-d", "--name", HOLE, "--network", "sran-test_default", "--entrypoint", "python", "ai-gateway-mock-provider:latest", "-c",
      "import socket\ns=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',9999));s.listen(200);k=[]\nwhile True:\n c,_=s.accept();k.append(c)"]);
    const hole = `http://${HOLE}:9999/v1`;
    psql(`begin;
      update provider_keys set base_url = '${hole}', last_tested_base_url = '${hole}' where id = '${KEY_ID}';
      update model_configs set served_by_keys = jsonb_set(served_by_keys, '{${KEY_ID},base_url}', to_jsonb('${hole}'::text)) where id = ${MODEL_ROW};
      commit;`);
    await restartBackend();
    out.rescued = await ask(key);
    const r = out.rescued;
    check(r.status === 200 && r.model && r.model !== QWEN && r.fallback_info,
      `qwen hung: answered by ${r.model} in ${r.seconds} s (status ${r.status}), fallback_info ${JSON.stringify(r.fallback_info || null).slice(0, 200)}`);
    check(r.seconds <= 60, `far shorter than the outage's 91 s: ${r.seconds} s`);
    out.opt_out = await ask(key, { "X-Sran-Fallback": "off" });
    const o = out.opt_out;
    check(o.status >= 400 && (!o.model || o.model === QWEN), `X-Sran-Fallback: off → qwen's own error, no other model: ${o.status} ${JSON.stringify(o.error)} in ${o.seconds} s`);
    out.again = await ask(key);
    log(`a second call while qwen is still down: ${out.again.status} ${out.again.model} in ${out.again.seconds} s`);
  } finally {
    psql(`begin;
      update provider_keys set base_url = '${origBase}', last_tested_base_url = '${origTested}' where id = '${KEY_ID}';
      update model_configs set served_by_keys = '${served.replace(/'/g, "''")}'::jsonb where id = ${MODEL_ROW};
      commit;`);
    const back = psql(`select coalesce(base_url, '') || '|' || coalesce(last_tested_base_url, '') from provider_keys where id = '${KEY_ID}';`);
    const backServed = psql(`select served_by_keys::text from model_configs where id = ${MODEL_ROW};`);
    check(back === original && backServed === served, "the qwen key's endpoint and qwen's served-by record are restored exactly");
    try { sh("docker", ["rm", "-f", HOLE]); } catch { /* gone */ }
    await restartBackend();
    await portal("DELETE", `/portal/projects/${pid}/keys/${made.key_id}`).catch((e) => log(`revoke: ${e.message}`));
    log("throwaway key revoked");
  }
  fs.writeFileSync(path.join(OUT, "api_rescue.json"), JSON.stringify({ out, results }, null, 1));
}
main().catch((e) => {
  console.error(String(e && e.message || e).replace(/sk-[\w-]+/g, "sk-…"));
  process.exit(1);
});
