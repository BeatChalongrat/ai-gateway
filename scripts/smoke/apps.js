// FIX134 F — area 2 + 8: sign-in with the smoke account and one real action per app, and the
// Portal's proxied paths (the rows the audit found broken).
//
// Portal: POST /portal/auth/login on the gateway front door (what the LoginForm does through
// the portal's /api proxy), then /portal/me, the billing API and page, and the proxied paths
// /api/health, /api/portal/me (401 unauthenticated, 200 with the session), /static/*.
// web-chat: the chat-edge session from the portal cookie, a "[SMOKE]" chat with memory off,
// one short turn on the free model (message_complete + content), deleted by id.
// admin-ui: sign in only with SMOKE_ADMIN_EMAIL / SMOKE_ADMIN_PASSWORD (never the owner);
// without them, the request-logs API must answer 401 unauthenticated.
// MCP: the smoke token verifies at the mcp-gateway (profile desktop; SKIP when absent).
//
// Test stack: no portal / admin-ui / web-chat containers, and the seeded owner has no
// password — the portal token is minted (lib.mintPortalToken) and the page checks SKIP.
"use strict";
const crypto = require("node:crypto");
const L = require("./lib");

const summarizeUser = (j) => j?.user?.email || j?.email || (j?.user ? JSON.stringify(j.user).slice(0, 60) : "");

/** The smoke session: {session, how} — how = login | mint | none. */
async function signIn(R) {
  const base = L.API_BASE;
  if (L.TARGET === "test") {
    const tok = L.mintPortalToken(L.TS_USER);
    R.skip("portal.login", `test stack: token minted for ${L.TS_USER.email} (seeded owner has no password)`);
    return { session: L.bearerSession(tok, "minted-test"), how: "mint" };
  }
  const email = L.env("SMOKE_EMAIL");
  const password = L.env("SMOKE_PASSWORD");
  const mode = L.env("SMOKE_TOKEN_MODE", "login");
  let session = null;
  if (!email || !password) {
    R.skip("portal.login", "SMOKE_EMAIL / SMOKE_PASSWORD not set in .env");
  } else {
    await R.run("portal.login", async () => {
      const r = await L.tryHttp(`${base.gw}/portal/auth/login`, {
        method: "POST", headers: { "Content-Type": "application/json", Origin: base.portal }, body: JSON.stringify({ email, password }), timeoutMs: 20000,
      });
      if (r.status === 200) {
        const s = L.cookieSession(r.setCookie, "login-cookie");
        if (!s.accessToken && r.json?.access_token) session = L.bearerSession(r.json.access_token, "login-body");
        else if (s.accessToken) session = s;
        if (!session) return { ok: false, detail: `200 but neither a portal_access_token cookie nor access_token in the body (cookies: ${s.names.join(",") || "none"})` };
        return { ok: true, detail: `200 as ${summarizeUser(r.json) || email}; token via ${session.kind === "login-body" ? "body" : `cookies ${s.names.join(",")}`}` };
      }
      const code = r.json?.error?.code || r.json?.code || "";
      return { ok: false, detail: `${r.status || r.error} ${code}${code === "password_login_disabled" ? " (PASSWORD_LOGIN_ENABLED=false; FIX134 adds PASSWORD_LOGIN_ALLOWED_EMAILS for the smoke account)" : ""}` };
    });
  }
  if (session) return { session, how: "login" };
  if (mode === "mint") {
    try {
      const tok = L.mintPortalToken({ ...L.SMOKE_USER, email: L.SMOKE_USER.email || email });
      R.skip("portal.session", `SMOKE_TOKEN_MODE=mint: token minted for the smoke user (login not used)`);
      return { session: L.bearerSession(tok, "minted-live"), how: "mint" };
    } catch (e) {
      R.fail("portal.session", `SMOKE_TOKEN_MODE=mint: ${L.errText(e)}`);
      return { session: null, how: "none" };
    }
  }
  return { session: null, how: "none" };
}

module.exports = async function apps(R) {
  const base = L.API_BASE;
  const { session } = await signIn(R);
  const noSession = { skip: true, detail: "no session (sign-in did not produce one)" };

  // ── Portal ──────────────────────────────────────────────────────────────────
  await R.run("portal.me", async () => {
    if (!session) return noSession;
    const r = await L.tryHttp(`${base.gw}/portal/me`, { headers: session.headers });
    const who = r.json?.email || r.json?.user?.email || "";
    return { ok: r.status === 200, detail: `${base.gw}/portal/me → ${r.status || r.error} ${who}` };
  });
  await R.run("portal.billing_api", async () => {
    if (!session) return noSession;
    const r = await L.tryHttp(`${base.gw}/portal/billing/packs`, { headers: session.headers });
    const n = Array.isArray(r.json) ? r.json.length : Array.isArray(r.json?.packs) ? r.json.packs.length : null;
    return { ok: r.status === 200, detail: `${base.gw}/portal/billing/packs → ${r.status || r.error}${n !== null ? ` ${n} packs` : ""}` };
  });

  if (!L.T.hasWebApps) {
    for (const n of ["portal.proxy_health", "portal.proxy_me_unauth", "portal.proxy_me_auth", "portal.proxy_static", "portal.proxy_billing", "portal.billing_page"]) R.skip(n, "no portal container on the test stack");
  } else {
    const P = base.portal;
    await R.run("portal.proxy_health", async () => {
      const r = await L.tryHttp(`${P}/api/health`);
      return { ok: r.status === 200, detail: `${P}/api/health → ${r.status || r.error} ${r.text.slice(0, 60)}` };
    });
    await R.run("portal.proxy_me_unauth", async () => {
      const r = await L.tryHttp(`${P}/api/portal/me`);
      return { ok: r.status === 401, detail: `${P}/api/portal/me (no session) → ${r.status || r.error} (want 401)${r.status >= 500 ? " — the portal's /api rewrite does not reach the backend (FIX134 A2)" : ""}` };
    });
    await R.run("portal.proxy_me_auth", async () => {
      if (!session) return noSession;
      const r = await L.tryHttp(`${P}/api/portal/me`, { headers: session.cookieHeaders });
      return { ok: r.status === 200, detail: `${P}/api/portal/me (session cookie) → ${r.status || r.error} ${r.json?.email || ""}` };
    });
    await R.run("portal.proxy_static", async () => {
      const r = await L.tryHttp(`${P}/static/smoke-does-not-exist.txt`);
      const fromBackend = r.status === 404 && !/<html/i.test(r.text);
      return { ok: fromBackend, detail: `${P}/static/<missing> → ${r.status || r.error} ${fromBackend ? "(backend 404: proxied)" : /<html/i.test(r.text) ? "(Next's own 404 page: not proxied)" : ""}` };
    });
    await R.run("portal.proxy_billing", async () => {
      if (!session) return noSession;
      const r = await L.tryHttp(`${P}/api/portal/billing/packs`, { headers: session.cookieHeaders });
      return { ok: r.status === 200, detail: `${P}/api/portal/billing/packs (session cookie) → ${r.status || r.error}` };
    });
    await R.run("portal.billing_page", async () => {
      if (!session) return noSession;
      const r = await L.tryHttp(`${P}/billing`, { headers: session.cookieHeaders, redirect: "manual" });
      const loc = r.headers.get ? r.headers.get("location") || "" : "";
      return { ok: r.status === 200, detail: `${P}/billing (session cookie) → ${r.status || r.error}${loc ? ` → ${loc}` : ""}` };
    });
  }

  // ── web-chat ────────────────────────────────────────────────────────────────
  await R.run("webchat.session", async () => {
    if (!session) return noSession;
    // the web app's session heartbeat: chat-edge answers 204 for a valid portal token, 401 otherwise
    const r = await L.tryHttp(`${L.EDGE()}/auth/session`, { headers: session.headers });
    const bad = await L.tryHttp(`${L.EDGE()}/auth/session`);
    return { ok: (r.status === 204 || r.status === 200) && bad.status === 401, detail: `${L.EDGE()}/auth/session → ${r.status || r.error} with the session, ${bad.status || bad.error} without (want 204 / 401)` };
  });
  await R.run("webchat.turn", async () => {
    if (!session) return noSession;
    const prompt = "[SMOKE] ตอบสั้นๆ เป็นตัวเลขอย่างเดียว: 1+1 เท่ากับเท่าไร";
    const tries = [];
    for (let i = 1; i <= 2; i++) {
      const id = await L.newChat(session, L.FREE_MODEL, `web-chat turn ${i}`);
      const t = await L.turn(session, id, prompt, { timeoutMs: 240000 });
      const del = await L.deleteChat(session, id);
      if (del < 300 || del === 404) L.forget(id);
      tries.push({ id, ...t, deleted: del });
      if (t.terminal === "message_complete" && t.content.trim()) break;
    }
    const last = tries[tries.length - 1];
    const detail = tries.map((t) => `chat ${t.id.slice(0, 8)}: http=${t.http} terminal=${t.terminal || "none"}${t.code ? ` code=${t.code}` : ""} chars=${t.content.length} ttft=${t.ttft ?? "-"}ms total=${t.total}ms deleted=${t.deleted}${t.error ? ` ${t.error}` : ""}`).join(" | ");
    return { ok: last.terminal === "message_complete" && !!last.content.trim() && last.deleted < 300, detail: `${L.FREE_MODEL} ${detail} answer="${last.content.trim().slice(0, 30)}"` };
  });

  // ── admin-ui ────────────────────────────────────────────────────────────────
  const adminEmail = L.env("SMOKE_ADMIN_EMAIL");
  const adminPassword = L.env("SMOKE_ADMIN_PASSWORD");
  let adminSession = null;
  if (!adminEmail || !adminPassword) {
    R.skip("admin.login", "no SMOKE_ADMIN_EMAIL / SMOKE_ADMIN_PASSWORD in .env (the owner's admin account is never used)");
  } else {
    await R.run("admin.login", async () => {
      const r = await L.tryHttp(`${base.gw}/auth/session/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: adminEmail, password: adminPassword }) });
      if (r.status !== 200) return { ok: false, detail: `${base.gw}/auth/session/login → ${r.status || r.error} ${r.json?.error?.code || ""}` };
      const s = L.cookieSession(r.setCookie, "admin-cookie");
      adminSession = s;
      // The smoke admin is a least-privilege "viewer": it signs in and reads its own
      // session, and the admin surface answers it 403 (RBAC) — never 401 (no session).
      const me = await L.tryHttp(`${base.gw}/auth/session/me`, { headers: s.headers });
      const q = await L.tryHttp(`${base.gw}/admin/requests?limit=1`, { headers: s.headers });
      const role = me.json?.role || me.json?.user?.role || "?";
      return { ok: me.status === 200 && (q.status === 200 || q.status === 403), detail: `login 200 (cookies ${s.names.join(",")}); /auth/session/me → ${me.status || me.error} role=${role}; /admin/requests?limit=1 → ${q.status || q.error} (200 admin / 403 viewer)` };
    });
  }
  // AM7 — Agent Market's admin page is in the admin app: the page is served, and the proxy's road to
  // chat-edge's admin door knows the roles (the smoke viewer is refused there, never let through).
  await R.run("admin.agent_market", async () => {
    if (!L.T.hasWebApps || !base.admin) return { skip: true, detail: "no admin-ui on this target" };
    if (!adminSession) return { skip: true, detail: "no admin session in this run" };
    const page = await L.tryHttp(`${base.admin}/agent-market`, { headers: adminSession.headers });
    const door = await L.tryHttp(`${base.admin}/api/chat-edge/agent-market/me`, { headers: adminSession.headers });
    // no session: the admin app's middleware sends the request to sign in before the proxy sees it
    const none = await L.tryHttp(`${base.admin}/api/chat-edge/agent-market/me`);
    const toLogin = none.status === 307 && /\/login/.test(none.headers.get("location") || "");
    return { ok: page.status === 200 && door.status === 403 && (none.status === 401 || toLogin),
      detail: `${base.admin}/agent-market → ${page.status || page.error} (want 200); its API as the viewer → ${door.status || door.error} ${door.json?.error?.code || ""} (want 403); with no session → ${none.status || none.error}${toLogin ? " to sign-in" : ""} (want 401 or 307 to sign-in)` };
  });
  await R.run("admin.requests_unauth", async () => {
    const r = await L.tryHttp(`${base.gw}/admin/requests?limit=1`);
    return { ok: r.status === 401, detail: `${base.gw}/admin/requests?limit=1 (no session) → ${r.status || r.error} (want 401)` };
  });

  // ── MCP token verification (mcp-gateway, compose profile desktop) ──────────
  await R.run("mcp.token", async () => {
    if (L.TARGET !== "live") return { skip: true, detail: "mcp-gateway is not part of the test stack" };
    if (!L.containerRunning("ai-gateway-mcp-gateway-1")) return { skip: true, detail: "ai-gateway-mcp-gateway-1 is not running (profile desktop)" };
    if (!session) return noSession;
    const url = `http://127.0.0.1:3200/mcp/servers/${L.env("SMOKE_MCP_SERVER", "filesystem")}/mcp`;
    const body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" });
    const hdr = { "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
    let good = await L.tryHttp(url, { method: "POST", headers: { ...hdr, Authorization: `Bearer ${session.accessToken}` }, body, timeoutMs: 15000 });
    if (good.status === 502) { await L.wait(3000); good = await L.tryHttp(url, { method: "POST", headers: { ...hdr, Authorization: `Bearer ${session.accessToken}` }, body, timeoutMs: 15000 }); } // the child server needs a few seconds after the gateway starts
    const bogus = await L.tryHttp(url, { method: "POST", headers: { ...hdr, Authorization: `Bearer ${crypto.randomBytes(24).toString("base64url")}` }, body, timeoutMs: 15000 });
    const tools = Array.isArray(good.json?.result?.tools) ? good.json.result.tools.length : null;
    return { ok: good.status !== 401 && good.status !== 0 && bogus.status === 401, detail: `${url.replace("http://127.0.0.1:3200", "")}: smoke token → ${good.status || good.error}${tools !== null ? ` (${tools} tools)` : ""}; bogus token → ${bogus.status || bogus.error} (want 401)` };
  });

  return session;
};
module.exports.signIn = signIn;
