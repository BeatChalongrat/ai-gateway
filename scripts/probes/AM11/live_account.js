#!/usr/bin/env node
// AM11 — the walk's own account on live (CARD-RULES §6b: never the owner's). The deployment's smoke
// account (SMOKE_EMAIL in .env: its own tenant, made for tests) is put on Agent Market's access list
// for the length of the walks and taken off again; both changes go through the admin door the admin
// app uses and are in the admin audit log under the probe's own name.
//
//   SMOKE_TARGET=live node scripts/probes/AM11/live_account.js status   # on the list? what a member sees
//   SMOKE_TARGET=live node scripts/probes/AM11/live_account.js grant    # a member, named "[AM11] walk account"
//   SMOKE_TARGET=live node scripts/probes/AM11/live_account.js revoke   # off the list again
//
// On live the admin door is https through Caddy: export NODE_EXTRA_CA_CERTS=infra/caddy/certs/rootCA.pem.
// The address is printed masked.
"use strict";
process.env.SMOKE_TARGET = process.env.SMOKE_TARGET || "live";
const L = require("../../smoke/lib.js");
const { door } = require("../AM7/admin_door.js");

const email = String(L.env("SMOKE_EMAIL") || "").trim().toLowerCase();
const mask = (e) => e.replace(/^(.).*(@.*)$/, "$1***$2");
if (!email) { console.error("SMOKE_EMAIL is not in .env"); process.exit(2); }

async function status() {
  const m = await door("GET", "/org");
  const list = (m.json && (m.json.members || (m.json.org && m.json.org.members))) || [];
  const row = Array.isArray(list) ? list.find((x) => String(x.email || "").toLowerCase() === email) : null;
  console.log(`access list: ${Array.isArray(list) ? list.length : "?"} people (status ${m.status}) · ${mask(email)}: ${row ? `on it, role ${row.role}` : "not on it"}`);
  const tok = L.mintPortalToken(L.SMOKE_USER);
  const a = await L.http(`${L.API_BASE.edge}/api/v1/agent-market/availability`, { headers: { Authorization: `Bearer ${tok}` } });
  console.log(`as that account: availability ${a.status} available=${a.json && a.json.available} access=${a.json && a.json.access} role=${a.json && a.json.role}`);
  if (a.json && a.json.available) {
    const c = await L.http(`${L.API_BASE.edge}/api/v1/agent-market/catalog`, { headers: { Authorization: `Bearer ${tok}` } });
    const agents = (c.json && (c.json.agents || c.json.catalog)) || [];
    console.log(`catalogue as that account: ${agents.map((x) => `${x.slug} ${x.version} ${x.status}`).join(" · ")}`);
    const i = await L.http(`${L.API_BASE.edge}/api/v1/agent-market/installs`, { headers: { Authorization: `Bearer ${tok}` } });
    console.log(`its installs: ${((i.json && i.json.installs) || []).filter((x) => x.status !== "removed").length}`);
  }
  return !!row;
}

(async () => {
  const cmd = process.argv[2] || "status";
  if (cmd === "grant") {
    const r = await door("PUT", `/members/${email}`, { display_name: "[AM11] walk account", role: "member" });
    console.log(`grant ${mask(email)}: ${r.status}${r.status === 200 ? "" : ` ${String(r.text || "").slice(0, 120)}`}`);
  } else if (cmd === "revoke") {
    const r = await door("DELETE", `/members/${email}`);
    console.log(`revoke ${mask(email)}: ${r.status}`);
  } else if (cmd !== "status") {
    console.log("usage: live_account.js status | grant | revoke");
    process.exit(2);
  }
  await status();
})().catch((e) => { console.error(String((e && e.message) || e).slice(0, 300)); process.exit(1); });
