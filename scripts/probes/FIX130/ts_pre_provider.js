#!/usr/bin/env node
// FIX130 E3 on the TEST stack: a turn that fails before it reaches a provider must write a
// request_logs row (marked pre-provider, cost 0, not charged), so the alert rules see it.
// Kills the test mock's key (points it at a closed port), sends N turns pinned to the mock
// model, counts request_logs rows + alert_events in the window, restores the key.
//
//   node scripts/probes/FIX130/ts_pre_provider.js <tag> [turns=5]
"use strict";
const { execFileSync } = require("node:child_process");
const L = require("./ts_lib");

async function restartBackend() {
  execFileSync("docker", ["restart", "sran-test-backend-1"]); // the 5-min catalogue cache
  for (let i = 0; i < 60; i++) { try { if ((await fetch("http://127.0.0.1:18081/ready")).ok) return; } catch {} await L.wait(2000); }
}

(async () => {
  L.assertTestStack();
  const tag = process.argv[2] || "run";
  const n = Number(process.argv[3] || 5);
  const [keyId, keyUrl] = L.tsql("select id, base_url from provider_keys where label='ts-mock'").split("|");
  const bal0 = Number(L.tsql(`select credit_balance from portal_users where id='${L.USERS.A.id}'`));
  L.tsql(`update provider_keys set base_url='http://127.0.0.1:1/v1' where id='${keyId}'`);
  await restartBackend();
  const t0 = L.tsql("select now()");
  const tok = L.token("A");
  const id = await L.newChat(tok, { model: "ts-mock-model", memory: false });
  const outcomes = [];
  try {
    for (let i = 0; i < n; i++) {
      const t = await L.turn(tok, id, `[FIX130] pre-provider probe ${i}`, { timeoutMs: 120000 });
      outcomes.push({ http: t.status, term: t.term?.ev || null, code: t.term?.d?.code || t.term?.d?.error?.code || null });
    }
    await L.wait(8000); // log queue flush
  } finally {
    L.tsql(`update provider_keys set base_url='${keyUrl}' where id='${keyId}'`);
    await restartBackend();
  }
  const rows = L.tsql(`select status, coalesce(error_code,''), coalesce(provider,''), coalesce(policy_action,''), coalesce(estimated_cost_usd::text,''), model from request_logs where created_at >= '${t0}' order by created_at`).split("\n").filter(Boolean);
  const alerts = L.tsql(`select alert_type || ' | ' || delivery_status || ' | ' || left(message, 160) from alert_events where fired_at >= '${t0}' order by fired_at`).split("\n").filter(Boolean);
  const chatRows = L.csql(`select role, status, coalesce(error_code,'') from chat.messages_masked where conversation_id='${id}' order by created_at`).split("\n").filter(Boolean);
  const bal1 = Number(L.tsql(`select credit_balance from portal_users where id='${L.USERS.A.id}'`));
  await L.deleteChat(tok, id);
  const failedTurns = chatRows.filter((r) => r.startsWith("assistant|error")).length;
  const res = { tag, turns: n, outcomes, failedTurns, request_logs: rows, alert_events: alerts, credits: { before: bal0, after: bal1 },
    pass: rows.length >= failedTurns && failedTurns === n && bal1 === bal0 };
  console.log(`failed turns ${failedTurns}/${n} · request_logs rows ${rows.length} · credits ${bal0}→${bal1} · alerts ${alerts.length} · ${res.pass ? "PASS" : "FAIL"}`);
  rows.slice(0, 6).forEach((r) => console.log("  row", r));
  L.save(`e3/ts_pre_provider_${tag}.json`, res);
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
