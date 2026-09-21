// FIX122 probes — shared setup. Drives a page (the side stack's `next dev`
// on :3102, or the desktop over CDP) as the owner's account with a minted
// portal JWT, the way scripts/probes/stream-scroll does.
//
//   PAGE      page origin            (default http://localhost:3102)
//   API       chat-edge base         (default http://localhost:4100/api/v1)
//   EDGE      container to read PORTAL_JWT_SECRET from (default fix122-chat-edge)
"use strict";
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..", "..");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));

const PAGE = process.env.PAGE || "http://localhost:3102";
const API = process.env.API || "http://localhost:4100/api/v1";
const EDGE = process.env.EDGE || "fix122-chat-edge";
const GATEWAY = "http://localhost:8080";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function token() {
  const env = execFileSync("docker", ["exec", EDGE, "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c",
    `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "owner@local", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 7200, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}

async function api(tok, p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", ...(init.headers || {}) } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t }; }
}

/** A browser context signed in as the owner. The gateway's CORS lists :3002
 *  only, so /portal/* is answered from here (node fetch carries the cookie). */
async function openContext(tok, { viewport = { width: 1280, height: 800 }, headless = true } = {}) {
  const browser = await chromium.launch({ headless, channel: process.env.CHANNEL || undefined });
  const ctx = await browser.newContext({ viewport, locale: "th-TH" });
  await ctx.addCookies([{ name: "portal_access_token", value: tok, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  await ctx.route(`${GATEWAY}/**`, async (route) => {
    const req = route.request();
    const cors = { "access-control-allow-origin": PAGE, "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type,authorization,x-csrf-token", "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS" };
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    try {
      const r = await fetch(req.url(), { method: req.method(), headers: { cookie: `portal_access_token=${tok}`, "content-type": "application/json" }, body: ["GET", "HEAD"].includes(req.method()) ? undefined : req.postData() ?? undefined });
      const body = Buffer.from(await r.arrayBuffer());
      return route.fulfill({ status: r.status, headers: { ...cors, "content-type": r.headers.get("content-type") || "application/json" }, body });
    } catch (e) {
      return route.fulfill({ status: 502, headers: cors, body: String(e) });
    }
  });
  return { browser, ctx };
}

module.exports = { REPO, PAGE, API, USER, TENANT, chromium, token, api, openContext, wait };
