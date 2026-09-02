#!/usr/bin/env node
// Capability errors, live: does the stack tell the truth when a request
// needs something the model cannot do?
//
//   node scripts/probes/capability-errors/probe.js
//
// Three shots through the running stack (gateway front :8080 with
// the chat-edge's gateway key; chat-edge :4000 with a minted owner JWT):
//
//   G1  explicit tools-blind model + tools → the gateway's error frame must
//       carry a specific code (model_no_tool_support), retryable=false, and
//       a message that says what to change — not "try again".
//   G2  auto + tools, pool restricted to a provider with no tools-capable
//       model → no_capable_model (400), not a doomed model.
//   G3  auto + tools through chat-edge → the model that answers supports
//       tools (message_complete.model_id ∈ tools-capable set).
//
// Results: scripts/probes/capability-errors/results/<timestamp>.json (ignored).
const fs = require("node:fs");
const path = require("node:path");
const { execSync } = require("node:child_process");
const crypto = require("node:crypto");

const REPO = path.resolve(__dirname, "..", "..", "..");
const OUT = path.join(__dirname, "results");
// :8080 is the Go gateway front (proxy + security) that chat-edge itself talks to
const GW = "http://localhost:8080/v1";
const EDGE = "http://localhost:4000/api/v1";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";

const sh = (cmd) => execSync(cmd, { encoding: "utf8", env: { ...process.env, MSYS_NO_PATHCONV: "1" } }).trim();
const containerEnv = (name) => sh(`docker exec ai-gateway-chat-edge-api printenv ${name}`);
const psql = (q) => sh(`docker exec ai-gateway-postgres-1 psql -U postgres -d ai_gateway -tAc "${q}"`);

function mint({ secret, tokenVersion }) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const claims = { sub: USER, email: "owner@local", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tokenVersion, iat: now, exp: now + 3600, iss: "sran-portal" };
  const data = `${b64({ alg: "HS256", typ: "JWT" })}.${b64(claims)}`;
  return `${data}.${crypto.createHmac("sha256", secret).update(data).digest("base64url")}`;
}

const TOOL = [{ type: "function", function: { name: "get_time", description: "Current time", parameters: { type: "object", properties: {} } } }];

async function gatewayStream(apiKey, body) {
  const res = await fetch(`${GW}/chat/completions`, { method: "POST", headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" }, body: JSON.stringify({ stream: true, ...body }) });
  const text = await res.text();
  const frames = text.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).filter((d) => d !== "[DONE]").map((d) => { try { return JSON.parse(d); } catch { return { raw: d }; } });
  let json = null; try { json = JSON.parse(text); } catch {}
  return { status: res.status, frames, json, error: frames.find((f) => f.error)?.error ?? json?.error ?? null };
}

async function edgeTurn(token, modelId, content) {
  const conv = await (await fetch(`${EDGE}/conversations`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ title: "capability probe", model_id: modelId }) })).json();
  const res = await fetch(`${EDGE}/conversations/${conv.id}/messages`, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ content, stream: true }) });
  const text = await res.text();
  const events = [];
  let ev = null;
  for (const line of text.split("\n")) {
    if (line.startsWith("event:")) ev = line.slice(6).trim();
    else if (line.startsWith("data:")) { let d = line.slice(5).trim(); try { d = JSON.parse(d); } catch {} events.push({ event: ev, data: d }); }
  }
  await fetch(`${EDGE}/conversations/${conv.id}`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } });
  return { status: res.status, conversation: conv.id, events };
}

(async () => {
  const apiKey = containerEnv("GATEWAY_API_KEY");
  const secret = containerEnv("PORTAL_JWT_SECRET");
  const tokenVersion = Number(psql(`select token_version from portal_users where id='${USER}'`)) || 0;
  const token = mint({ secret, tokenVersion });
  const toolsCapable = new Set(psql("select model from model_configs where is_active and supports_tools").split("\n").map((s) => s.trim()).filter(Boolean));

  const out = { captured_at: new Date().toISOString(), cases: {} };

  // G1 — explicit tools-blind model + tools at the gateway
  const g1 = await gatewayStream(apiKey, { model: "deepseek/deepseek-r1", messages: [{ role: "user", content: "What time is it? Use the tool." }], tools: TOOL, max_tokens: 64 });
  out.cases.G1_explicit_tools_blind = { status: g1.status, error: g1.error, frames: g1.frames.length };
  const g1ok = g1.error && g1.error.code === "model_no_tool_support" && g1.error.retryable === false && !/try again/i.test(g1.error.message);
  console.log(`G1 explicit tools-blind model + tools → ${g1.status} code=${g1.error?.code} retryable=${g1.error?.retryable} message="${g1.error?.message}"  ${g1ok ? "PASS" : "FAIL"}`);

  // G2 — auto + tools with a pool that has no tools-capable model
  const g2 = await gatewayStream(apiKey, { model: "auto", messages: [{ role: "user", content: "What time is it? Use the tool." }], tools: TOOL, max_tokens: 64, metadata: { allowed_providers: ["sranai"] } });
  out.cases.G2_auto_no_capable_pool = { status: g2.status, error: g2.error, frames: g2.frames.length };
  const g2ok = g2.error && g2.error.code === "no_capable_model" && g2.error.retryable === false;
  console.log(`G2 auto + tools, pool without tools → ${g2.status} code=${g2.error?.code} retryable=${g2.error?.retryable} message="${g2.error?.message}"  ${g2ok ? "PASS" : "FAIL"}`);

  // G3 — auto + tools through chat-edge: the answering model must support tools
  const g3 = await edgeTurn(token, "auto", "ตอนนี้กี่โมงแล้ว ใช้เครื่องมือช่วยดูให้หน่อย");
  const complete = g3.events.find((e) => e.event === "message_complete")?.data;
  const errorEv = g3.events.find((e) => e.event === "error")?.data;
  const resolved = g3.events.find((e) => e.event === "model_resolved")?.data;
  // The routed model: the one that answered, or — when the turn failed for
  // a reason of its own (a dead provider endpoint) — the first attempt.
  const routed = complete?.model_id || resolved?.model || errorEv?.attempts?.[0]?.model || null;
  out.cases.G3_auto_tools_via_chat_edge = { status: g3.status, routed_model: routed, completed: Boolean(complete), error: errorEv ?? null, events: g3.events.map((e) => e.event) };
  const g3ok = Boolean(routed) && toolsCapable.has(routed);
  console.log(`G3 auto + tools via chat-edge → routed=${routed} tools_capable=${routed ? toolsCapable.has(routed) : "-"}  ${g3ok ? "PASS" : "FAIL"}`);
  console.log(`   turn ${complete ? "completed" : "did not complete"}${errorEv ? ` — error ${errorEv.code}: ${errorEv.attempts?.map((a) => `${a.model} ${a.error_code}`).join("; ") ?? errorEv.message}` : ""}`);

  fs.mkdirSync(OUT, { recursive: true });
  const file = path.join(OUT, `${new Date().toISOString().replace(/[:.]/g, "-")}.json`);
  fs.writeFileSync(file, JSON.stringify(out, null, 2));
  console.log(`wrote ${path.relative(REPO, file)}`);
  process.exit(g1ok && g2ok && g3ok ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
