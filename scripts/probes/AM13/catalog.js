#!/usr/bin/env node
// AM13 follow-up — the catalogue through Agent Market's admin door (chat-edge /api/v1/admin/agent-market,
// behind its admin bearer; the bearer is read from .env by the smoke library and never printed).
//
//   list                                what the organisation's catalogue says: agent, version, state, installs, sample
//   publish <slug> [<slug>…] -- <why>   publish each; the reason is written beside the change in the admin audit log
//
// The script names itself as what it is — a script acting on the owner's instruction — so the audit log
// does not say a person did it.
//
//   SMOKE_TARGET=live NODE_EXTRA_CA_CERTS=…/rootCA.pem node scripts/probes/AM13/catalog.js list
"use strict";
const L = require("../../smoke/lib.js");

const WHO = { id: "ops-am13", email: "ops-am13@probe.invalid", name: "Ops script, on the owner's instruction (AM13)" };

async function door(method, path, body) {
  const key = L.env("CHAT_EDGE_ADMIN_API_KEY");
  if (!key) throw new Error("CHAT_EDGE_ADMIN_API_KEY is not in .env");
  return L.http(`${L.API_BASE.edge}/api/v1/admin/agent-market${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${key}`, "X-Admin-User-Id": WHO.id, "X-Admin-Email": WHO.email,
      "X-Admin-Name": encodeURIComponent(WHO.name), "X-Admin-Role": "admin", "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function list() {
  const r = await door("GET", "/catalog");
  if (r.status !== 200) throw new Error(`the catalogue was not read: ${r.status} ${r.json?.error?.code || ""}`);
  const rows = r.json.agents || r.json.items || r.json.catalog || r.json;
  for (const a of rows) {
    console.log([a.slug.padEnd(28), String(a.version).padEnd(7), String(a.status).padEnd(12), `installs ${a.installs ?? "?"}`,
      `sample ${a.sample ? "yes" : "no"}`].join(" · "));
  }
  return rows;
}

(async () => {
  const [verb, ...rest] = process.argv.slice(2);
  if (verb === "list") return void (await list());
  if (verb === "publish") {
    const cut = rest.indexOf("--");
    const slugs = cut < 0 ? rest : rest.slice(0, cut);
    const why = cut < 0 ? "" : rest.slice(cut + 1).join(" ");
    if (!slugs.length || !why) throw new Error("publish <slug> [<slug>…] -- <why>");
    for (const slug of slugs) {
      const r = await door("PUT", `/catalog/${encodeURIComponent(slug)}`, { status: "published", reason: why });
      console.log(`${slug}: ${r.status}${r.json?.error ? ` ${r.json.error.code} — ${r.json.error.message}` : ""}`);
    }
    return void (await list());
  }
  throw new Error("list | publish <slug> [<slug>…] -- <why>");
})().catch((e) => { console.error(String(e.message || e)); process.exit(1); });
