// FIX129 C — TEST STACK checks (refuses the live project).
//   node scripts/probes/FIX129/ts_fix129.js turn        one [FIX129] turn on ts-mock-model, credit_deduction log line
//   node scripts/probes/FIX129/ts_fix129.js b1          upload → delete chat → /download and /text must 404
//   node scripts/probes/FIX129/ts_fix129.js b2          logout → the same token is 401 on chat-edge and the portal within ms; another token still works
//   node scripts/probes/FIX129/ts_fix129.js alerts      kill the mock key, 12 failing turns → no_successful_turn + error_rate fire; restore
// Every chat is [FIX129]-prefixed, memory off, and deleted BY ID at the end.
const { execFileSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const API = "http://127.0.0.1:14000/api/v1";
const PORTAL = "http://127.0.0.1:18081";
const TENANT = "f1280000-0000-4000-8000-000000000001";
const USER = "f1280000-0000-4000-8000-000000000011";
const MOCK_MODEL = "ts-mock-model";
const OUT = path.join(__dirname, "results", "ts_fix129");
fs.mkdirSync(OUT, { recursive: true });

const dx = (c, args) => execFileSync("docker", ["exec", c, ...args], { encoding: "utf8" }).trim();
const tsql = (q) => dx("sran-test-postgres-1", ["psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-F", "|", "-c", q]);
function assertTestStack() {
  for (const c of ["sran-test-postgres-1", "sran-test-chat-edge-api-1", "sran-test-backend-1"]) {
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
  const jti = crypto.randomUUID();
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "ts-owner@fix128.test", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 7200, jti })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}
async function api(tok, p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${tok}`, ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(init.headers || {}) } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t), text: t }; } catch { return { status: r.status, body: null, text: t }; }
}
async function newChat(tok, model) {
  const c = await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ model_id: model, title: "[FIX129] test" }) });
  if (c.status !== 201 && c.status !== 200) throw new Error(`create chat: ${c.status} ${c.text.slice(0, 200)}`);
  await api(tok, `/conversations/${c.body.id}`, { method: "PATCH", body: JSON.stringify({ memory_enabled: false, title: "[FIX129] test" }) });
  return c.body.id;
}
async function turn(tok, id, content) {
  const t0 = Date.now();
  const r = await fetch(API + `/conversations/${id}/messages`, { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify({ content, stream: true }) });
  const text = await r.text();
  const evs = [...new Set([...text.matchAll(/^event: (.+)$/gm)].map((m) => m[1]))];
  const term = evs.includes("message_complete") ? "message_complete" : evs.includes("error") ? "error" : "none";
  const errLine = /^data: (\{.*"code".*\})$/m.exec(text)?.[1] || "";
  return { status: r.status, term, events: evs, ms: Date.now() - t0, err: errLine.slice(0, 160) };
}
const del = (tok, id) => api(tok, `/conversations/${id}`, { method: "DELETE" });
const log = (...a) => { const s = a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" "); console.log(s); fs.appendFileSync(path.join(OUT, "log.txt"), s + "\n"); };

async function modeTurn() {
  const tok = token();
  const id = await newChat(tok, MOCK_MODEL);
  const t = await turn(tok, id, "[FIX129] say hello in five words");
  log("turn", { id, ...t });
  const lines = execFileSync("docker", ["logs", "--since", "2m", "sran-test-backend-1"], { encoding: "utf8" }).split("\n").filter((l) => l.includes("credit_deduction")).slice(-2);
  log("credit_deduction log lines:", lines.map((l) => l.slice(0, 300)));
  log("delete", (await del(tok, id)).status);
}

async function modeB1() {
  const tok = token();
  const id = await newChat(tok, MOCK_MODEL);
  const fd = new FormData();
  fd.append("file", new Blob(["FIX129 attachment body — must be unreachable after the chat is deleted.\n"], { type: "text/plain" }), "fix129.txt");
  const up = await api(tok, `/conversations/${id}/files`, { method: "POST", body: fd });
  const fileId = up.body?.id || up.body?.file?.id;
  log("upload", up.status, fileId);
  const before = await fetch(API + `/files/${fileId}/download`, { headers: { Authorization: `Bearer ${tok}` } });
  log("download before delete", before.status, (await before.text()).length, "bytes");
  log("delete chat", (await del(tok, id)).status);
  log("chat after delete", (await api(tok, `/conversations/${id}`)).status);
  const dl = await fetch(API + `/files/${fileId}/download`, { headers: { Authorization: `Bearer ${tok}` } });
  const tx = await fetch(API + `/files/${fileId}/text`, { headers: { Authorization: `Bearer ${tok}` } });
  const ls = await fetch(API + `/conversations/${id}/files/${fileId}/versions`, { headers: { Authorization: `Bearer ${tok}` } });
  log("AFTER delete: download", dl.status, "text", tx.status, "versions", ls.status, "(want 404 404 404)");
  log("B1", dl.status === 404 && tx.status === 404 ? "PASS" : "FAIL");
}

async function modeB2() {
  const a = token(); const b = token();
  const before = await api(a, "/auth/session");
  const me = await fetch(PORTAL + "/portal/me", { headers: { Authorization: `Bearer ${a}` } });
  log("before logout: chat-edge session", before.status, "portal /me", me.status);
  const t0 = Date.now();
  const lo = await fetch(PORTAL + "/portal/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${a}`, "Content-Type": "application/json" } });
  const tLogout = Date.now() - t0;
  const afterEdge = await api(a, "/auth/session");
  const afterMe = await fetch(PORTAL + "/portal/me", { headers: { Authorization: `Bearer ${a}` } });
  const afterConv = await api(a, "/conversations");
  const other = await api(b, "/auth/session");
  const tAll = Date.now() - t0;
  log("logout", lo.status, `${tLogout} ms; AFTER: chat-edge session`, afterEdge.status, "portal /me", afterMe.status, "conversations", afterConv.status, `(all within ${tAll} ms)`, "| other token session", other.status);
  const edgeLog = execFileSync("docker", ["logs", "--since", "1m", "sran-test-backend-1"], { encoding: "utf8" }).split("\n").filter((l) => l.includes("chat_edge_revok")).slice(-1).map((l) => l.slice(0, 200));
  log("backend log:", edgeLog);
  log("B2", afterEdge.status === 401 && afterMe.status === 401 && afterConv.status === 401 && other.status === 204 ? "PASS" : "FAIL");
}

async function modeAlerts() {
  const keyRow = tsql("select id, base_url from provider_keys where label='ts-mock'").split("|");
  log("mock key", keyRow[0], keyRow[1]);
  tsql(`update provider_keys set base_url='http://127.0.0.1:1/v1' where id='${keyRow[0]}'`);
  execFileSync("docker", ["restart", "sran-test-backend-1"]);        // the 5-min catalogue cache
  await new Promise((r) => setTimeout(r, 35_000));
  const tok = token();
  const id = await newChat(tok, MOCK_MODEL);
  const outcomes = [];
  for (let i = 0; i < 12; i++) outcomes.push((await turn(tok, id, `[FIX129] alert probe ${i}`)).term);
  log("12 turns with every model dead:", outcomes.join(","));
  await new Promise((r) => setTimeout(r, 20_000));                  // sweep every 5 s on the test stack
  const ev = tsql("select alert_type||' | '||severity||' | '||delivery_status||' | '||left(message,120) from alert_events where fired_at > now() - interval '3 minutes' order by fired_at");
  log("alert_events:", ev.split("\n"));
  const rl = tsql("select status, count(*) from request_logs where created_at > now() - interval '3 minutes' group by 1");
  log("request_logs (3 min):", rl.split("\n"));
  tsql(`update provider_keys set base_url='${keyRow[1]}' where id='${keyRow[0]}'`);
  execFileSync("docker", ["restart", "sran-test-backend-1"]);
  log("delete", (await del(tok, id)).status, "| key restored + backend restarted");
  log("ALERTS", ev.includes("no_successful_turn") && ev.includes("error_rate") ? "PASS" : "FAIL");
}

(async () => {
  assertTestStack();
  const mode = process.argv[2] || "turn";
  log(`== ${mode} ${new Date().toISOString()}`);
  await ({ turn: modeTurn, b1: modeB1, b2: modeB2, alerts: modeAlerts })[mode]();
})().catch((e) => { log("ERROR", e.message); process.exit(1); });
