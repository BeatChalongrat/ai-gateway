#!/usr/bin/env node
// FIX128 C — destructive checks on the TEST stack (project sran-test).
//
//   node scripts/probes/FIX128/teststack.js <tag> <case>[,<case>…]
//
// cases
//   b3   Auto with the dead model as admin preference: the turn is answered by
//        another model in the same turn (fallback_from = ts-dead-model), a
//        health marker is set, and the NEXT Auto turn does not try it.
//   b2a  first turn fails (message_too_long), a later turn answers → the chat
//        gets a model title from the model that answered.
//   b2b  a chat answered by ts-mock-model; the mock's key is then pointed at a
//        closed port → the title is written by another model (FallbackUsed).
//   l1   a chat pinned to ts-dead-model: rescued by the closest model, the
//        row names both, the charge is at the rescuer's price (credits move by
//        the rescuer's cost), request_logs has the failed attempt + success.
// Talks ONLY to 127.0.0.1:14000 (test chat-edge) and the sran-test-* containers.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const OUT = path.join(__dirname, "results");
const API = "http://127.0.0.1:14000/api/v1";
const USER = "f1280000-0000-4000-8000-000000000011";
const TENANT = "f1280000-0000-4000-8000-000000000001";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const dx = (c, args) => execFileSync("docker", ["exec", c, ...args], { encoding: "utf8" }).trim();
const tsql = (q) => dx("sran-test-postgres-1", ["psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-F", "|", "-c", q]);
const csql = (q) => dx("sran-test-postgres-webchat-1", ["psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-F", "|", "-c", q]);
const redisKeys = () => { try { return dx("sran-test-redis-1", ["sh", "-c", 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "provider:*"']).split("\n").filter(Boolean); } catch { return []; } };

function assertTestStack() {
  for (const c of ["sran-test-postgres-1", "sran-test-chat-edge-api-1", "sran-test-redis-1"]) {
    const p = execFileSync("docker", ["inspect", c, "--format", '{{index .Config.Labels "com.docker.compose.project"}}'], { encoding: "utf8" }).trim();
    if (p !== "sran-test") throw new Error(`REFUSED: ${c} is in project ${p}`);
  }
}
function token() {
  const env = dx("sran-test-chat-edge-api-1", ["env"]);
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(tsql(`select token_version from portal_users where id='${USER}'`) || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "ts-owner@fix128.test", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 7200, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}
async function api(tok, p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", ...(init.headers || {}) } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t }; }
}
async function newChat(tok, model) {
  const c = await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ model_id: model }) });
  await api(tok, `/conversations/${c.body.id}`, { method: "PATCH", body: JSON.stringify({ memory_enabled: false }) });
  return c.body.id;
}
/** Sends one streamed turn; returns the terminal frame and the event names. */
async function turn(tok, id, content) {
  const r = await fetch(API + `/conversations/${id}/messages`, { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify({ content, stream: true }) });
  const text = await r.text();
  const frames = [];
  for (const block of text.split("\n\n")) {
    const ev = /^event: (.+)$/m.exec(block)?.[1];
    const data = /^data: (.+)$/m.exec(block)?.[1];
    if (ev) { let d = null; try { d = JSON.parse(data); } catch {} frames.push({ ev, d }); }
  }
  const term = frames.filter((f) => ["message_complete", "error", "cancelled"].includes(f.ev)).pop() || null;
  const deltas = frames.filter((f) => f.ev === "content_delta").map((f) => f.d?.delta || "").join("");
  return { status: r.status, events: [...new Set(frames.map((f) => f.ev))], term, chars: deltas.length };
}
const rows = (id) => csql(`select role, status, coalesce(model_id,''), coalesce(fallback_from,''), coalesce(error_code,''), length(content_masked) from chat.messages_masked where conversation_id='${id}' order by created_at`).split("\n").filter(Boolean);
// request_logs: model | status | error_code | fallback_used | original_model | estimated_cost_usd
const logs = (sinceIso) => tsql(`select model, status, coalesce(error_code,''), coalesce(fallback_used::text,''), coalesce(original_model,''), coalesce(estimated_cost_usd::text,'') from request_logs where created_at >= '${sinceIso}' order by created_at`).split("\n").filter(Boolean);
const balance = () => Number(tsql(`select credit_balance from portal_users where id='${USER}'`));

async function restartBackend() {
  execFileSync("docker", ["restart", "sran-test-backend-1"]);
  for (let i = 0; i < 60; i++) { try { const r = await fetch("http://127.0.0.1:18081/ready"); if (r.ok) return; } catch {} await wait(2000); }
}

async function b3(tok) {
  // Controlled pool (TEST DB only): the dead model, qwen, deepseek-v4-flash —
  // the dead one cheapest, so Auto reaches for it first. Restored at the end.
  tsql(`create table if not exists ts_saved_active as select model, is_active from model_configs`);
  tsql(`update model_configs set is_active = (model in ('ts-dead-model','qwen3.8-27b-fp8','deepseek-v4-flash'))`);
  tsql(`update model_configs set cost_tier='budget', cost_per_input_token=0, cost_per_output_token=0 where model='ts-dead-model'`);
  dx("sran-test-redis-1", ["sh", "-c", 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "provider:*" | xargs -r redis-cli -a "$REDIS_PASSWORD" --no-auth-warning del >/dev/null; echo ok']);
  await restartBackend();
  try { return await b3run(tok); } finally {
    tsql(`update model_configs m set is_active = s.is_active from ts_saved_active s where s.model = m.model`);
    tsql(`drop table ts_saved_active`);
    await restartBackend();
  }
}

async function b3run(tok) {
  const t0 = new Date().toISOString();
  const keysBefore = redisKeys();
  const id = await newChat(tok, "auto");
  const a = await turn(tok, id, "[FIX128] b3: ตอบสั้นๆ 3+4 เท่ากับเท่าไร");
  await wait(2500);
  const keysAfter1 = redisKeys();
  const logs1 = logs(t0);
  const t1 = new Date().toISOString();
  const id2 = await newChat(tok, "auto");
  const b = await turn(tok, id2, "[FIX128] b3 second: ตอบสั้นๆ เมืองหลวงของญี่ปุ่นคือ");
  await wait(2500);
  const logs2 = logs(t1);
  const out = { id, first: { term: a.term?.ev, fallback_from: a.term?.d?.fallback_from ?? null, model: a.term?.d?.model_id ?? null, chars: a.chars }, rows1: rows(id), keysBefore, keysAfter1, logs1,
    second: { id: id2, term: b.term?.ev, fallback_from: b.term?.d?.fallback_from ?? null, model: b.term?.d?.model_id ?? null, chars: b.chars }, rows2: rows(id2), logs2 };
  out.pass = a.term?.ev === "message_complete" && a.chars > 0 && out.first.fallback_from === "ts-dead-model"
    && keysAfter1.some((k) => k.includes("ts-dead") || k.includes("sranai")) && b.term?.ev === "message_complete" && !out.second.fallback_from
    && !logs2.some((l) => l.startsWith("ts-dead-model|") || l.split("|")[4] === "ts-dead-model");
  return out;
}

async function b2a(tok) {
  // Turn 1 fails for certain: the owner has no credits (a failed turn must
  // cost 0). Credits back, turn 2 answers → the chat gets a model title.
  const id = await newChat(tok, "qwen3.8-27b-fp8");
  const saved = balance();
  tsql(`update portal_users set credit_balance = 0 where id='${USER}'`);
  const f = await turn(tok, id, "[FIX128] b2a first: สวัสดี");
  const afterFail = balance();
  tsql(`update portal_users set credit_balance = ${saved} where id='${USER}'`);
  const titleAfterFail = (await api(tok, `/conversations/${id}`)).body.title;
  const ok = await turn(tok, id, "[FIX128] b2a: อธิบายสั้นๆ ว่าทำไมใบไม้เป็นสีเขียว");
  const g = await api(tok, `/conversations/${id}/generate-title`, { method: "POST" });
  const c = (await api(tok, `/conversations/${id}`)).body;
  const out = { id, firstTurn: { term: f.term?.ev, code: f.term?.d?.code, creditsAfterFail: afterFail }, titleAfterFail: (titleAfterFail || "").slice(0, 40), secondTurn: { term: ok.term?.ev, chars: ok.chars }, generate: g.body, title: c.title, rows: rows(id) };
  // turn 1 is refused before the stream opens (402): no SSE frame, an error row.
  const firstRow = out.rows[1] || "";
  out.pass = f.status === 402 && firstRow.startsWith("assistant|error|") && afterFail === 0 && ok.term?.ev === "message_complete" && g.body?.source === "model" && c.title && !c.title.startsWith("[FIX128]");
  out.firstTurn.http = f.status;
  return out;
}

async function b2b(tok) {
  // a clean start: no marker left on ts-mock-model by an earlier run
  dx("sran-test-redis-1", ["sh", "-c", 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning --scan --pattern "provider:*" | xargs -r redis-cli -a "$REDIS_PASSWORD" --no-auth-warning del >/dev/null; echo ok']);
  const id = await newChat(tok, "ts-mock-model");
  const a = await turn(tok, id, "[FIX128] b2b: อธิบายสั้นๆ ว่าฝนตกได้อย่างไร");
  const answeredBy = rows(id).find((r) => r.startsWith("assistant|"))?.split("|")[2];
  // the model that answered dies before the title is asked
  tsql(`update provider_keys set base_url='http://127.0.0.1:1/v1' where label='ts-mock'`);
  tsql(`update model_configs set served_by_keys = jsonb_set(served_by_keys, '{f1280000-0000-4000-8000-00000000a0c0,base_url}', '"http://127.0.0.1:1/v1"') where model='ts-mock-model'`);
  execFileSync("docker", ["restart", "sran-test-backend-1"]);
  for (let i = 0; i < 60; i++) { try { const r = await fetch("http://127.0.0.1:18081/ready"); if (r.ok) break; } catch {} await wait(2000); }
  const tTitle = new Date().toISOString();
  const g = await api(tok, `/conversations/${id}/generate-title`, { method: "POST" });
  const c = (await api(tok, `/conversations/${id}`)).body;
  const titleLogs = logs(tTitle);
  // put the mock back for the next run
  tsql(`update provider_keys set base_url='http://mock-provider:9090/v1' where label='ts-mock'`);
  tsql(`update model_configs set served_by_keys = jsonb_set(served_by_keys, '{f1280000-0000-4000-8000-00000000a0c0,base_url}', '"http://mock-provider:9090/v1"') where model='ts-mock-model'`);
  // Who wrote the title is the gateway's word (request_logs): the dead model is
  // asked, the gateway rescues the call with another model.
  const out = { id, turn: { term: a.term?.ev, chars: a.chars }, answeredBy, generate: g.body, title: c.title, titleLogs };
  const wrote = titleLogs.map((l) => l.split("|")).find((r) => r[4] === "ts-mock-model" && r[1] === "success");
  out.titleWrittenBy = wrote ? wrote[0] : null;
  out.pass = a.term?.ev === "message_complete" && answeredBy === "ts-mock-model" && g.body?.source === "model"
    && !!out.titleWrittenBy && out.titleWrittenBy !== "ts-mock-model" && !c.title.startsWith("[FIX128]");
  return out;
}

async function l1(tok) {
  const t0 = new Date().toISOString();
  const before = balance();
  const id = await newChat(tok, "ts-dead-model");
  const a = await turn(tok, id, "[FIX128] l1: ตอบสั้นๆ ว่าวันนี้วันอะไรในสัปดาห์ถ้าเมื่อวานเป็นวันจันทร์");
  await wait(3000);
  const after = balance();
  const r = rows(id);
  const asst = r.find((x) => x.startsWith("assistant|"))?.split("|") || [];
  const lg = logs(t0);
  const price = asst[2] ? tsql(`select cost_per_input_token||'/'||cost_per_output_token||'/'||cost_tier from model_configs where model='${asst[2]}'`) : null;
  const out = { id, term: a.term?.ev, fallback_from: a.term?.d?.fallback_from ?? null, answeredBy: asst[2] || null, rowFallbackFrom: asst[3] || null, rescuerPrice: price, credits: { before, after, spent: before - after }, request_logs: lg, rows: r };
  out.pass = a.term?.ev === "message_complete" && out.rowFallbackFrom === "ts-dead-model" && out.answeredBy && out.answeredBy !== "ts-dead-model"
    && lg.some((l) => l.split("|")[4] === "ts-dead-model" || l.startsWith("ts-dead-model|"));
  return out;
}

async function main() {
  const [tag, list] = process.argv.slice(2);
  fs.mkdirSync(OUT, { recursive: true });
  assertTestStack();
  const tok = token();
  const results = {};
  for (const c of list.split(",")) {
    const fn = { b3, b2a, b2b, l1 }[c];
    const t0 = Date.now();
    try { results[c] = await fn(tok); } catch (e) { results[c] = { error: String(e).slice(0, 500) }; }
    results[c].ms = Date.now() - t0;
    console.log(c, results[c].pass ? "PASS" : "FAIL", JSON.stringify(results[c]).slice(0, 900));
  }
  fs.writeFileSync(path.join(OUT, `${tag}-teststack.json`), JSON.stringify({ at: new Date().toISOString(), results }, null, 2));
}
main().catch((e) => { console.error(e); process.exit(1); });
