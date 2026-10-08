#!/usr/bin/env node
// AM13 follow-up — the owner asked (8 Oct 2026) for his own installs to be upgraded and self-tested for him:
// "Change nothing else on my account, don't open my chats or files, and say in the report exactly what
// you did." These routes belong to the install's owner, so the script acts as him for these calls only, with
// an access token of ten minutes minted the way the smoke library mints one for a web probe (never printed,
// never stored). It calls nothing but:
//
//   info <install-id>       GET  /installs/{id}/upgrade            versions and what changes — no thread, no files
//   upgrade <install-id>    POST /installs/{id}/upgrade            the newest version is tested first; the install
//                                                                  moves to it only if that test passes
//   selftest <install-id>   POST /installs/{id}/self-test          the installed version's own test
//
// Each prints the run it started; the run is then read by that exact id (status and what it concluded).
//
//   OWNER_EMAIL=… SMOKE_TARGET=live NODE_EXTRA_CA_CERTS=…/rootCA.pem node scripts/probes/AM13/owner_upgrade.js info <id>
"use strict";
const L = require("../../smoke/lib.js");

const EDGE = `${L.API_BASE.edge}/api/v1/agent-market`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sq = (s) => String(s).replace(/'/g, "''");

function owner() {
  const email = process.env.OWNER_EMAIL || "";
  if (!email) throw new Error("OWNER_EMAIL is not set");
  const id = L.gsql(`select id from portal_users where email='${sq(email)}'`).trim();
  if (!id) throw new Error("the owner's account was not found");
  return L.bearerSession(L.mintPortalToken({ id, email }, 600), "owner-asked-upgrade");
}
const call = (s, method, path, body) => L.http(`${EDGE}${path}`, {
  method, timeoutMs: 30000, headers: { ...s.headers, "Content-Type": "application/json", "Accept-Language": "en" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

// the run, by its own id: how it ended and the lines it concluded with (a self-test writes no file of the owner's)
async function follow(runId, minutes = 12) {
  const until = Date.now() + minutes * 60000;
  let last = "";
  for (;;) {
    const row = L.csql(`select status || '|' || coalesce(left(status_reason,200),'') from agent_market.agent_runs where id='${sq(runId)}'`).trim();
    const status = row.split("|")[0];
    if (status !== last) { console.log(`${new Date().toISOString().slice(11, 19)}Z run ${runId.slice(0, 8)}: ${row}`); last = status; }
    if (!["queued", "running"].includes(status)) return status;
    if (Date.now() > until) { console.log("still running after the wait; left as it is"); return status; }
    await sleep(3000);
  }
}

(async () => {
  const [verb, id] = process.argv.slice(2);
  if (!/^[0-9a-f-]{36}$/.test(id || "")) throw new Error("info|upgrade|selftest <install id (uuid)>");
  const s = owner();
  const info = await call(s, "GET", `/installs/${id}/upgrade`);
  if (info.status !== 200) throw new Error(`upgrade info: ${info.status} ${info.json?.error?.code || ""}`);
  const cur = info.json.current, to = info.json.available;
  console.log(`installed ${cur?.version} · newest offered ${to ? to.version : "(none — already the newest)"}${info.json.previous ? ` · previous ${info.json.previous.version}` : ""}`);
  if (verb === "info") {
    for (const c of [].concat(info.json.changes || [])) console.log("  change:", JSON.stringify(c).slice(0, 260));
    return;
  }
  if (verb === "upgrade") {
    if (!to) return void console.log("nothing to upgrade to");
    const r = await call(s, "POST", `/installs/${id}/upgrade`, { definition_id: to.id });
    if (r.status !== 202) throw new Error(`upgrade refused: ${r.status} ${r.json?.error?.code || ""} ${r.json?.error?.message || ""} ${JSON.stringify(r.json?.error?.detail || "")}`);
    console.log(`upgrade started: the test of ${to.version} before the move, run ${r.json.run.id}`);
    const end = await follow(r.json.run.id);
    const now = L.csql(`select definition_version || ' · ' || status from agent_market.agent_installs where id='${sq(id)}'`).trim();
    console.log(`test of ${to.version}: ${end} · the install now says: ${now}`);
    return;
  }
  if (verb === "selftest") {
    const r = await call(s, "POST", `/installs/${id}/self-test`, {});
    if (r.status !== 202) throw new Error(`self-test refused: ${r.status} ${r.json?.error?.code || ""} ${r.json?.error?.message || ""}`);
    console.log(`self-test started, run ${r.json.run.id}`);
    console.log(`self-test of the installed version: ${await follow(r.json.run.id)}`);
    return;
  }
  throw new Error("info|upgrade|selftest <install id>");
})().catch((e) => { console.error(String(e.message || e)); process.exit(1); });
