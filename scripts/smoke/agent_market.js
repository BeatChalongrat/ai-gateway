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
