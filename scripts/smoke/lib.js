// FIX134 F — shared helpers for the smoke suite (scripts/smoke.sh → run.js → one file per area).
//
// Targets (SMOKE_TARGET): `live` = the compose project ai-gateway behind the Caddy front door
// (https on 3000/3001/3002/4000/8080, plain loopback doors at port+20000, backend 8081);
// `test` = the compose project sran-test (no front door, no web apps, no ops-watch: plain
// ports 14000 chat-edge, 18080 gateway, 18081 backend). Nothing here touches a container of
// the other project, and the test target refuses to run when its containers belong to
// another compose project (the FIX130 ts_lib guard).
//
// Every chat this suite creates is "[SMOKE] …", memory OFF, and is deleted BY ID before the
// suite exits (also on a crash: run.js calls cleanup()). Secrets (.env values, tokens,
// cookies) are read in-process and never printed.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync, spawnSync } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..", "..");
const TARGET = process.env.SMOKE_TARGET || "live";
const FREE_MODEL = process.env.SMOKE_MODEL || "qwen3.8-27b-fp8";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const TARGETS = {
  live: {
    name: "live",
    project: "ai-gateway",
    // the Caddy front door (https, the machine's local CA)
    front: {
      admin: "https://localhost:3000", portal: "https://localhost:3001", web: "https://localhost:3002",
      edge: "https://localhost:4000", gw: "https://localhost:8080",
    },
    // plain loopback debug doors (scripts only)
    plain: {
      admin: "http://127.0.0.1:23000", portal: "http://127.0.0.1:23001", web: "http://127.0.0.1:23002",
      edge: "http://127.0.0.1:24000", gw: "http://127.0.0.1:28080", backend: "http://127.0.0.1:8081",
    },
    containers: {
      backend: "ai-gateway-backend-1", chatEdge: "ai-gateway-chat-edge-api", opsWatch: "ai-gateway-ops-watch-1",
      backup: "ai-gateway-backup", webChat: "ai-gateway-web-chat", pg: "ai-gateway-postgres-1", pgChat: "ai-gateway-postgres-webchat",
    },
    hasFrontDoor: true,
    hasWebApps: true,
    hasOpsWatch: true,
  },
  test: {
    name: "test",
    project: "sran-test",
    front: null,
    plain: { edge: "http://127.0.0.1:14000", gw: "http://127.0.0.1:18080", backend: "http://127.0.0.1:18081" },
    containers: {
      backend: "sran-test-backend-1", chatEdge: "sran-test-chat-edge-api-1", opsWatch: null,
      backup: "sran-test-backup-1", webChat: null, pg: "sran-test-postgres-1", pgChat: "sran-test-postgres-webchat-1",
    },
    hasFrontDoor: false,
    hasWebApps: false,
    hasOpsWatch: false,
  },
};
if (!TARGETS[TARGET]) throw new Error(`SMOKE_TARGET must be live or test (got ${TARGET})`);
const T = TARGETS[TARGET];
// The URLs the checks use for API calls: the front door on live (what users hit), the plain
// ports on the test stack. `T.plain` stays available for the debug-door checks.
const API_BASE = T.front || T.plain;

// ── .env (read, never printed) ──────────────────────────────────────────────
let envCache = null;
// SMOKE_ENV_FILE points a worktree run at the deployed checkout's .env (the file is gitignored).
const ENV_FILE = process.env.SMOKE_ENV_FILE || path.join(ROOT, ".env");
function dotenv() {
  if (envCache) return envCache;
  envCache = {};
  try {
    for (const line of fs.readFileSync(ENV_FILE, "utf8").split(/\r?\n/)) {
      const m = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
      if (!m) continue;
      let v = m[2].trim();
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
      envCache[m[1]] = v;
    }
  } catch { /* no .env: every credential-backed check skips */ }
  return envCache;
}
/** process.env first, then .env; "" when neither has it. */
function env(name, fallback = "") {
  const v = process.env[name];
  if (v !== undefined && v !== "") return v;
  const d = dotenv()[name];
  return d !== undefined && d !== "" ? d : fallback;
}

// ── docker (read-mostly) ─────────────────────────────────────────────────────
function dockerExec(container, args, { timeoutMs = 30000 } = {}) {
  const r = spawnSync("docker", ["exec", container, ...args], { encoding: "utf8", maxBuffer: 64 << 20, timeout: timeoutMs });
  return { code: r.status, out: (r.stdout || "") + (r.stderr || ""), stdout: r.stdout || "" };
}
/** Both streams of `docker logs --since <span>` (chat-edge and ops-watch log JSON to stdout, some to stderr). */
function dockerLogs(container, since, { timeoutMs = 30000 } = {}) {
  const r = spawnSync("docker", ["logs", "--since", since, container], { encoding: "utf8", maxBuffer: 256 << 20, timeout: timeoutMs });
  return (r.stdout || "") + (r.stderr || "");
}
const quiet = { encoding: "utf8", timeout: 15000, stdio: ["ignore", "pipe", "ignore"] }; // an absent container is an answer, not console noise
function dockerLabel(container, label) {
  try {
    return execFileSync("docker", ["inspect", container, "--format", `{{index .Config.Labels "${label}"}}`], quiet).trim();
  } catch { return null; }
}
function containerRunning(container) {
  try {
    return execFileSync("docker", ["inspect", container, "--format", "{{.State.Running}}"], quiet).trim() === "true";
  } catch { return false; }
}
/** Refuse to run against containers that belong to another compose project. */
function assertProject() {
  const names = Object.values(T.containers).filter(Boolean);
  const missing = [];
  for (const c of names) {
    const p = dockerLabel(c, "com.docker.compose.project");
    if (p === null || p === "") { missing.push(c); continue; }
    if (p !== T.project) throw new Error(`REFUSED: ${c} belongs to compose project ${p}, not ${T.project}`);
  }
  return missing;
}
const gsql = (q) => dockerExec(T.containers.pg, ["psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-F", "|", "-c", q]).stdout.trim();
const csql = (q) => dockerExec(T.containers.pgChat, ["psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-F", "|", "-c", q]).stdout.trim();

// ── http ─────────────────────────────────────────────────────────────────────
/** fetch with a timeout, no automatic redirects; returns {status, headers, text, json, setCookie}. */
async function http(url, { method = "GET", headers = {}, body, timeoutMs = 20000, redirect = "manual" } = {}) {
  const r = await fetch(url, { method, headers, body, redirect, signal: AbortSignal.timeout(timeoutMs) });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* not JSON */ }
  return { status: r.status, headers: r.headers, text, json, setCookie: typeof r.headers.getSetCookie === "function" ? r.headers.getSetCookie() : [] };
}
/** Same, but a network error becomes {status: 0, error} instead of a throw. */
async function tryHttp(url, opts) {
  try { return await http(url, opts); } catch (e) { return { status: 0, error: errText(e), headers: new Headers(), text: "", json: null, setCookie: [] }; }
}
function errText(e) {
  const c = e && (e.cause?.code || e.code);
  return `${e?.name || "Error"}${c ? ` ${c}` : ""}: ${String(e?.cause?.message || e?.message || e).slice(0, 140)}`;
}

// ── sessions ─────────────────────────────────────────────────────────────────
/** A session is {kind, headers} — headers carry the cookie (browser shape) or a Bearer token. */
function cookieSession(setCookie, kind = "portal-cookie") {
  const jar = {};
  for (const sc of setCookie || []) {
    const m = /^([^=]+)=([^;]*)/.exec(sc);
    if (m) jar[m[1].trim()] = m[2];
  }
  const names = Object.keys(jar);
  const cookie = names.map((k) => `${k}=${jar[k]}`).join("; ");
  return { kind, names, headers: cookie ? { Cookie: cookie } : {}, cookieHeaders: cookie ? { Cookie: cookie } : {}, accessToken: jar.portal_access_token || null };
}
/** A Bearer session; `cookieHeaders` carries the same JWT as the browser cookie for page loads. */
function bearerSession(token, kind = "minted") {
  return { kind, names: [], headers: { Authorization: `Bearer ${token}` }, cookieHeaders: { Cookie: `portal_access_token=${token}` }, accessToken: token };
}

// The test stack's seeded owner (scripts/test_stack_seed.py, FIX130 ts_lib): no password, so the
// suite mints its portal JWT from the chat-edge container's PORTAL_JWT_SECRET (read in-process).
const TS_USER = { id: "f1280000-0000-4000-8000-000000000011", email: "ts-owner@fix128.test", tenant: "f1280000-0000-4000-8000-000000000001", role: "owner" };
// The live smoke account (SMOKE_EMAIL / SMOKE_PASSWORD in .env; its own tenant). SMOKE_TOKEN_MODE=mint
// signs its JWT locally instead of logging in (for developing the authenticated checks while
// password login is off); the default `login` is the real sign-in.
const SMOKE_USER = {
  id: env("SMOKE_USER_ID", "f07d45d4-b7d6-4415-a992-98a7c5350aaa"),
  email: env("SMOKE_EMAIL"),
  tenant: env("SMOKE_TENANT_ID", "6262cdbf-b62c-4e56-a3f4-cf53c62cf118"),
  role: env("SMOKE_TENANT_ROLE", "owner"),
};
let jwtSecret = null;
function mintPortalToken(user = TS_USER, ttlS = 3600) {
  if (!jwtSecret) {
    const envOut = dockerExec(T.containers.chatEdge, ["env"]).stdout;
    jwtSecret = (/^PORTAL_JWT_SECRET=(.+)$/m.exec(envOut) || [])[1]?.trim();
    if (!jwtSecret) throw new Error(`PORTAL_JWT_SECRET not found in ${T.containers.chatEdge}`);
  }
  const row = gsql(`select coalesce(token_version,0), coalesce(email,''), coalesce(tenant_id::text,''), coalesce(tenant_role,'') from portal_users where id='${user.id}'`).split("|");
  if (row.length < 4 || !row[1]) throw new Error(`portal user ${user.id} not found in the ${T.name} gateway database`);
  user = { ...user, email: user.email || row[1], tenant: user.tenant || row[2], role: user.role || row[3] };
  const tv = Number(row[0]) || 1;
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: user.id, email: user.email, type: "portal", token_use: "access", tenant_id: user.tenant, tenant_role: user.role, token_version: tv, jti: crypto.randomUUID(), iat: now, exp: now + ttlS, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", jwtSecret).update(d).digest("base64url")}`;
}

// ── chat-edge ────────────────────────────────────────────────────────────────
const EDGE = () => `${API_BASE.edge}/api/v1`;
async function edge(session, p, init = {}) {
  return http(EDGE() + p, { ...init, headers: { "Content-Type": "application/json", ...session.headers, ...(init.headers || {}) } });
}
const created = []; // {id, session, note}
/** A "[SMOKE] …" chat, memory OFF, registered for deletion by id. */
async function newChat(session, model, note) {
  const title = `[SMOKE] ${note} ${new Date().toISOString().slice(0, 16)}`;
  const c = await edge(session, "/conversations", { method: "POST", body: JSON.stringify({ model_id: model, title }) });
  if (c.status >= 300 || !c.json?.id) throw new Error(`create chat ${c.status} ${c.text.slice(0, 160)}`);
  created.push({ id: c.json.id, session, note });
  const p = await edge(session, `/conversations/${c.json.id}`, { method: "PATCH", body: JSON.stringify({ title, memory_enabled: false }) });
  if (p.status >= 300) throw new Error(`patch chat ${p.status} ${p.text.slice(0, 160)}`);
  if (p.json && p.json.memory_enabled !== false) throw new Error(`memory_enabled is ${p.json.memory_enabled} after the patch`);
  return c.json.id;
}
async function deleteChat(session, id) {
  const d = await tryHttp(`${EDGE()}/conversations/${id}`, { method: "DELETE", headers: session.headers });
  return d.status;
}
/** Drop a chat from the cleanup list once its deletion was confirmed. */
function forget(id) {
  const i = created.findIndex((c) => c.id === id);
  if (i >= 0) created.splice(i, 1);
}
/** Delete every chat this process created (by id). Returns {created, deleted, failed:[...]}. */
async function cleanup() {
  const out = { created: created.length, deleted: 0, failed: [] };
  while (created.length) {
    const c = created.shift();
    const s = await deleteChat(c.session, c.id);
    if (s < 300 || s === 404) out.deleted++; else out.failed.push(`${c.id}:${s}`);
  }
  return out;
}
/** One streamed turn (SSE); the FIX131 parser. */
async function turn(session, convId, content, { timeoutMs = 240000, extra = {} } = {}) {
  const t0 = Date.now();
  let ttft = null, raw = "";
  const r = await fetch(`${EDGE()}/conversations/${convId}/messages`, {
    method: "POST", signal: AbortSignal.timeout(timeoutMs),
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...session.headers },
    body: JSON.stringify({ content, stream: true, ...extra }),
  });
  if (r.status >= 300 || !r.body) {
    const text = await r.text().catch(() => "");
    return { http: r.status, terminal: null, code: null, content: "", ttft: null, total: Date.now() - t0, events: [], error: text.slice(0, 200) };
  }
  const rd = r.body.getReader();
  const dec = new TextDecoder();
  for (;;) {
    const { done, value } = await rd.read();
    if (done) break;
    const s = dec.decode(value, { stream: true });
    raw += s;
    if (ttft === null && s.includes("content_delta")) ttft = Date.now() - t0;
  }
  const frames = [];
  for (const block of raw.split(/\r?\n\r?\n/)) {
    const ev = /^event: (.+)$/m.exec(block)?.[1];
    const data = /^data: (.+)$/m.exec(block)?.[1];
    if (ev) { let d = null; try { d = JSON.parse(data); } catch { /* keepalive or plain */ } frames.push({ ev, d }); }
  }
  const term = frames.filter((f) => ["message_complete", "error", "cancelled"].includes(f.ev)).pop();
  return {
    http: r.status, ttft, total: Date.now() - t0,
    terminal: term?.ev ?? null, code: term?.d?.code || term?.d?.error?.code || null, served: term?.d?.model_id || null,
    content: frames.filter((f) => f.ev === "content_delta").map((f) => f.d?.delta || "").join(""),
    events: [...new Set(frames.map((f) => f.ev))],
  };
}

// ── reporting ────────────────────────────────────────────────────────────────
class Report {
  constructor() { this.checks = []; }
  add(status, name, detail, ms) {
    const row = { status, name, detail: String(detail ?? "").replace(/\s+/g, " ").trim(), ms: ms ?? null };
    this.checks.push(row);
    process.stdout.write(`${status.padEnd(4)} ${name.padEnd(30)} ${row.detail}\n`);
    return row;
  }
  pass(name, detail, ms) { return this.add("PASS", name, detail, ms); }
  fail(name, detail, ms) { return this.add("FAIL", name, detail, ms); }
  skip(name, detail, ms) { return this.add("SKIP", name, detail, ms); }
  /** fn returns {ok, detail} or {skip, detail}; a throw is a FAIL with the error text. */
  async run(name, fn) {
    const t0 = Date.now();
    try {
      const r = await fn();
      const ms = Date.now() - t0;
      if (r && r.skip) return this.skip(name, r.detail, ms);
      return r && r.ok ? this.pass(name, r.detail, ms) : this.fail(name, r?.detail ?? "no result", ms);
    } catch (e) {
      return this.fail(name, errText(e), Date.now() - t0);
    }
  }
  counts() {
    const c = { pass: 0, fail: 0, skip: 0 };
    for (const r of this.checks) c[r.status.toLowerCase()]++;
    return c;
  }
}

const ageText = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);

module.exports = {
  ROOT, TARGET, T, API_BASE, FREE_MODEL, TS_USER, SMOKE_USER, wait, env, dotenv,
  dockerExec, dockerLogs, dockerLabel, containerRunning, assertProject, gsql, csql,
  http, tryHttp, errText, cookieSession, bearerSession, mintPortalToken,
  edge, EDGE, newChat, deleteChat, forget, cleanup, turn, created, Report, ageText,
};
