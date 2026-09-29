// AM1 — area "agent_market": the feature is absent when its flag is off,
// and healthy when it is on.
//
// flag   .env AGENT_MARKET_ENABLED
//   off  → web-chat /api/agent-market says {enabled:false} and chat-edge has NO
//          route (/api/v1/agent-market/availability → 404): flag off means absent.
//   on   → availability answers 200 for the smoke account, and the runner's
//          heartbeat is fresh (skip, not fail, when the profile is not running).
"use strict";
const L = require("./lib");

// The runner's container and health port per target (docker-compose.agent-market.yml
// defaults on live; the test stack's runner is started with AM_CONTAINER_NAME /
// AM_RUNNER_PORT). The flag is read like every smoke setting: the shell's
// environment first (the test stack is recreated with it exported), then .env.
const RUNNER = L.TARGET === "test"
  ? { name: "sran-test-agent-runner", health: "http://127.0.0.1:18095/healthz" }
  : { name: "sran-agent-runner", health: "http://127.0.0.1:28095/healthz" };

module.exports = async function agentMarket(R, session) {
  const on = String(L.env("AGENT_MARKET_ENABLED", "false")).toLowerCase() === "true";
  const edge = `${L.API_BASE.edge}/api/v1/agent-market/availability`;

  await R.run("agent_market.flag", async () => {
    const headers = session ? session.headers : {};
    const r = await L.http(edge, { headers });
    if (!on) {
      return { ok: r.status === 404, detail: `flag off: chat-edge ${r.status} (want 404 — no route registered)` };
    }
    if (!session) return { skip: true, detail: "flag on but no signed-in session in this run" };
    return { ok: r.status === 200, detail: `flag on: availability ${r.status} available=${r.json?.available} runner healthy=${r.json?.runner?.healthy}` };
  });

  // AM1b: with an allowlist that leaves the smoke account out (the owner-only
  // pilot), that account must see no agent surface: availability says no and
  // the catalog refuses it (the web rail renders nothing when available=false).
  // AM3: the list lives in the database now (the organisation's admins keep
  // it), so the check asks the gate itself: whatever it says about this
  // account (access mode, on the list, admin) must match what it lets through.
  await R.run("agent_market.not_allowlisted", async () => {
    if (!on) return { skip: true, detail: "flag off (the flag check covers it)" };
    if (!session) return { skip: true, detail: "no signed-in session in this run" };
    const a = await L.http(edge, { headers: session.headers });
    const c = await L.http(`${L.API_BASE.edge}/api/v1/agent-market/catalog`, { headers: session.headers });
    const j = a.json || {};
    const shouldBeIn = j.admin === true || j.access === "everyone" || (j.access === "list" && j.member === true);
    if (a.status !== 200) return { ok: false, detail: `availability ${a.status}` };
    if (shouldBeIn) {
      return { ok: j.available === true && c.status === 200,
        detail: `the organisation lets this account in (access=${j.access} member=${j.member} admin=${j.admin}): available=${j.available}, catalog ${c.status}` };
    }
    return { ok: j.available === false && c.status === 403,
      detail: `not on the organisation's list (access=${j.access} member=${j.member}): availability available=${j.available}, catalog ${c.status} (want false / 403)` };
  });

  // AM7 — what affects anyone else lives in the admin app. chat-edge's admin door (behind its bearer)
  // exists when the flag is on and is absent when it is off, and refuses a call that names no admin;
  // the chat app's own API has no admin route at all; the backend's audit door answers only chat-edge.
  await R.run("agent_market.admin_door", async () => {
    const key = L.env("CHAT_EDGE_ADMIN_API_KEY");
    if (!key) return { skip: true, detail: "no CHAT_EDGE_ADMIN_API_KEY in .env" };
    const r = await L.http(`${L.API_BASE.edge}/api/v1/admin/agent-market/me`, { headers: { Authorization: `Bearer ${key}` } });
    if (!on) return { ok: r.status === 404, detail: `flag off: the admin door → ${r.status} (want 404 — absent)` };
    return { ok: r.status === 403 && r.json?.error?.code === "NOT_ADMIN", detail: `flag on: the admin door, no admin named → ${r.status} ${r.json?.error?.code || ""} (want 403 NOT_ADMIN)` };
  });

  await R.run("agent_market.no_admin_in_chat", async () => {
    if (!on) return { skip: true, detail: "flag off (the flag check covers it)" };
    if (!session) return { skip: true, detail: "no signed-in session in this run" };
    const bad = [];
    for (const p of ["/admin/org", "/admin/catalog", "/admin/runs", "/admin/problems", "/admin/mail"]) {
      const r = await L.http(`${L.API_BASE.edge}/api/v1/agent-market${p}`, { headers: session.headers });
      if (r.status !== 404) bad.push(`${p} ${r.status}`);
    }
    return { ok: bad.length === 0, detail: bad.length ? `the chat app's API still answers: ${bad.join(", ")}` : "the chat app's API has no Agent Market admin route (5 of 5 → 404)" };
  });

  await R.run("agent_market.audit_door", async () => {
    const base = L.T.plain.backend;
    if (!base) return { skip: true, detail: "no backend loopback door on this target" };
    const r = await L.tryHttp(`${base}/internal/admin-audit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries: [] }) });
    return { ok: r.status === 401, detail: `${base}/internal/admin-audit with no service token → ${r.status || r.error} (want 401: only chat-edge writes Agent Market's lines)` };
  });

  await R.run("agent_market.web_flag", async () => {
    if (!L.T.hasWebApps) return { skip: true, detail: "no web-chat on this target" };
    const web = L.T.front?.web || L.T.plain.web;
    const r = await L.http(`${web}/api/agent-market`);
    if (r.status !== 200) return { ok: false, detail: `web-chat /api/agent-market ${r.status}` };
    return { ok: r.json?.enabled === on, detail: `web-chat says enabled=${r.json?.enabled} (.env says ${on})` };
  });

  await R.run("agent_market.runner", async () => {
    if (!on) {
      const running = L.containerRunning(RUNNER.name);
      return { ok: !running, detail: running ? `flag off but ${RUNNER.name} is running` : "flag off: no runner container" };
    }
    if (!L.containerRunning(RUNNER.name)) return { skip: true, detail: "profile agent-market not running" };
    const r = await L.http(RUNNER.health);
    return { ok: r.status === 200 && r.json?.sandbox === true, detail: `runner ${r.status} sandbox=${r.json?.sandbox} model=${r.json?.model} engine=${r.json?.engine}` };
  });
};
