// FIX129 — LIVE light checks (owner account, [FIX129] chats, memory off, deleted by id).
//   node scripts/probes/FIX129/live_fix129.js turn        one turn works (deepseek-v4-flash), credit_deduction log line named
//   node scripts/probes/FIX129/live_fix129.js b1          upload → delete chat → /download and /text 404
//   node scripts/probes/FIX129/live_fix129.js b2          logout on "device 1" → the copied token is 401 on chat-edge + portal on "device 2" (ms)
//   node scripts/probes/FIX129/live_fix129.js heartbeat   an open web tab is sent to the login page within 5 s of a sign-out elsewhere
// Uses scripts/probes/NIGHT2/results/lib.js (minted owner JWT, Playwright context).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const lib = require("../NIGHT2/results/lib.js");
const { API, USER, TENANT, api, openContext, wait } = lib;

// lib.token() mints without a `jti`; a token without one cannot be revoked
// individually (the backend's own tokens always carry one), so mint here.
function token() {
  const crypto = require("node:crypto");
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c",
    `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "owner@local", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 7200, jti: crypto.randomUUID() })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}
const GATEWAY = "http://localhost:8080";
const MODEL = process.env.MODEL || "deepseek-v4-flash";
const OUT = path.join(__dirname, "results", "live");
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => { const s = a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" "); console.log(s); fs.appendFileSync(path.join(OUT, "log.txt"), s + "\n"); };

async function newChat(tok) {
  const c = await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ model_id: MODEL, title: "[FIX129] live check" }) });
  if (c.status !== 201 && c.status !== 200) throw new Error(`create chat: ${c.status} ${JSON.stringify(c.body).slice(0, 200)}`);
  await api(tok, `/conversations/${c.body.id}`, { method: "PATCH", body: JSON.stringify({ memory_enabled: false, title: "[FIX129] live check" }) });
  return c.body.id;
}
async function turn(tok, id, content) {
  const t0 = Date.now();
  const r = await fetch(API + `/conversations/${id}/messages`, { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify({ content, stream: true }) });
  const text = await r.text();
  const evs = [...new Set([...text.matchAll(/^event: (.+)$/gm)].map((m) => m[1]))];
  const deltas = [...text.matchAll(/^data: (\{.*"delta".*\})$/gm)].map((m) => { try { return JSON.parse(m[1]).delta || ""; } catch { return ""; } }).join("");
  return { status: r.status, events: evs, ms: Date.now() - t0, chars: deltas.length, head: deltas.slice(0, 80) };
}
const del = (tok, id) => api(tok, `/conversations/${id}`, { method: "DELETE" });

async function modeTurn() {
  const tok = token(); const id = await newChat(tok);
  const t = await turn(tok, id, "[FIX129] ตอบสั้น ๆ: วันนี้วันอะไร (ตอบเป็นภาษาไทย 1 ประโยค)");
  log("turn", { id, ...t });
  const lines = execFileSync("docker", ["logs", "--since", "3m", "ai-gateway-backend-1"], { encoding: "utf8" }).split("\n").filter((l) => l.includes('"event": "credit_deduction"')).slice(-1);
  log("credit_deduction:", lines.map((l) => { try { const d = JSON.parse(l); return { request_id: d.request_id, outcome: d.outcome, credits: d.credits, elapsed_ms: d.elapsed_ms, lock_wait_ms: d.lock_wait_ms, level: d.level }; } catch { return l.slice(0, 200); } }));
  log("delete", (await del(tok, id)).status, id);
  log("TURN", t.events.includes("message_complete") && t.chars > 0 ? "PASS" : "FAIL");
}

async function modeB1() {
  const tok = token(); const id = await newChat(tok);
  const fd = new FormData();
  fd.append("file", new Blob(["FIX129 live attachment — must be unreachable after the chat is deleted.\n"], { type: "text/plain" }), "fix129-live.txt");
  const up = await fetch(API + `/conversations/${id}/files`, { method: "POST", headers: { Authorization: `Bearer ${tok}` }, body: fd });
  const upBody = await up.json();
  const fileId = upBody.id || upBody.file?.id;
  const before = await fetch(API + `/files/${fileId}/download`, { headers: { Authorization: `Bearer ${tok}` } });
  log("upload", up.status, fileId, "| download before delete", before.status, (await before.text()).length, "bytes");
  log("delete chat", (await del(tok, id)).status, "| chat after delete", (await api(tok, `/conversations/${id}`)).status);
  const dl = await fetch(API + `/files/${fileId}/download`, { headers: { Authorization: `Bearer ${tok}` } });
  const tx = await fetch(API + `/files/${fileId}/text`, { headers: { Authorization: `Bearer ${tok}` } });
  const vs = await fetch(API + `/conversations/${id}/files/${fileId}/versions`, { headers: { Authorization: `Bearer ${tok}` } });
  const vsBody = await vs.text();
  log("AFTER delete: download", dl.status, "text", tx.status, "versions", vs.status, vsBody.slice(0, 80));
  log("B1", dl.status === 404 && tx.status === 404 ? "PASS" : "FAIL");
}

async function modeB2() {
  const a = token(); const b = token();
  const s0 = await api(a, "/auth/session"); const m0 = await fetch(GATEWAY + "/portal/me", { headers: { Authorization: `Bearer ${a}` } });
  log("device 2 before: chat-edge session", s0.status, "portal /me", m0.status);
  const t0 = Date.now();
  const lo = await fetch(GATEWAY + "/portal/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${a}`, "Content-Type": "application/json" } });
  const tLogout = Date.now() - t0;
  const s1 = await api(a, "/auth/session"); const m1 = await fetch(GATEWAY + "/portal/me", { headers: { Authorization: `Bearer ${a}` } });
  const c1 = await api(a, "/conversations"); const other = await api(b, "/auth/session");
  const tAll = Date.now() - t0;
  log(`device 1 logout ${lo.status} in ${tLogout} ms → device 2: chat-edge session ${s1.status}, portal /me ${m1.status}, conversations ${c1.status} (all within ${tAll} ms) | an unrelated token: ${other.status}`);
  log("B2", s1.status === 401 && m1.status === 401 && c1.status === 401 && other.status === 204 && m0.status === 200 ? "PASS" : "FAIL");
}

async function modeHeartbeat() {
  const tok = token();
  const { browser, ctx } = await openContext(tok);
  const page = await ctx.newPage();
  const beats = [];
  page.on("response", (r) => { if (r.url().endsWith("/auth/session")) beats.push({ t: Date.now(), status: r.status() }); });
  await page.goto(lib.PAGE + "/chat", { waitUntil: "domcontentloaded" });
  await wait(11_000);
  log("heartbeats in 11 s:", beats.length, beats.map((b) => b.status).join(","));
  const t0 = Date.now();
  await fetch(GATEWAY + "/portal/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" } });
  let redirectedAt = null;
  for (let i = 0; i < 80; i++) { await wait(100); if (page.url().includes("/login")) { redirectedAt = Date.now() - t0; break; } }
  log(`sign-out elsewhere → open tab on the login page after ${redirectedAt} ms (url ${page.url()})`);
  log("HEARTBEAT", redirectedAt !== null && redirectedAt <= 5000 && beats.length >= 2 ? "PASS" : "FAIL");
  await browser.close();
}

(async () => {
  const mode = process.argv[2] || "turn";
  log(`== ${mode} ${new Date().toISOString()} user=${USER}`);
  await ({ turn: modeTurn, b1: modeB1, b2: modeB2, heartbeat: modeHeartbeat })[mode]();
})().catch((e) => { log("ERROR", e.message); process.exit(1); });
