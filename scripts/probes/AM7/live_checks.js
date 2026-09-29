#!/usr/bin/env node
// AM7 — read-only checks on the live stack between the two deploy steps, and after.
//
//   doors      step A (before phase 4): the chat app's admin door (as the organisation's Agent Market
//              admin, the owner, in a short-lived session of their own account) and the admin app's
//              door (behind chat-edge's admin bearer) read the same organisation — every field, the
//              people, the departments. Nothing is written.
//   publisher  a request naming the role agent_publisher to every forbidden endpoint of the admin
//              app's door (each must be refused before it changes anything), and to the three it may use
//   gone       after phase 4: the chat app's API has no admin route for that same owner session
//
//   SMOKE_TARGET=live node scripts/probes/AM7/live_checks.js doors|publisher|gone
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const L = require("../../smoke/lib.js");
const { door, WHO } = require("./admin_door.js");

const OUT = path.join(__dirname, "results", "live");
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log(`${new Date().toISOString().slice(11, 19)} ${a.join(" ")}`);
const out = (name, v) => fs.writeFileSync(path.join(OUT, name), JSON.stringify(v, null, 1));
const EDGE = `${L.API_BASE.edge}/api/v1`;

function ownerSession() {
  const email = process.env.OWNER_EMAIL || "";
  if (!email) throw new Error("OWNER_EMAIL is not set");
  const id = L.gsql(`select id from portal_users where email='${email.replace(/'/g, "''")}'`).trim();
  if (!id) throw new Error("the owner's portal account was not found");
  return L.bearerSession(L.mintPortalToken({ id, email }, 600), "minted-owner");
}

async function doors() {
  const s = ownerSession();
  const chat = await L.http(`${EDGE}/agent-market/admin/org`, { headers: s.headers });
  const admin = await door("GET", "/org");
  if (chat.status !== 200 || admin.status !== 200) throw new Error(`chat door ${chat.status}, admin door ${admin.status}`);
  const c = chat.json, a = admin.json;
  const fields = Object.keys(c.org).sort().map((f) => ({ field: f, same: JSON.stringify(c.org[f]) === JSON.stringify(a.org[f]) }));
  const res = {
    at: new Date().toISOString(), fields: fields.length, all_same: fields.every((f) => f.same), differing: fields.filter((f) => !f.same).map((f) => f.field),
    members_same: JSON.stringify(c.members) === JSON.stringify(a.members), members: (c.members || []).length,
    departments_same: JSON.stringify(c.departments) === JSON.stringify(a.departments), departments: (c.departments || []).length,
    egress: { chat: c.org.documents_may_leave, admin: a.org.documents_may_leave }, retention_years: { chat: c.org.retention_years, admin: a.org.retention_years },
  };
  out("doors.json", res);
  log(`live: ${res.fields} fields compared, all the same: ${res.all_same}${res.differing.length ? ` (differ: ${res.differing})` : ""}; people ${res.members} same=${res.members_same}; departments ${res.departments} same=${res.departments_same}`);
  log(`egress chat=${res.egress.chat} admin=${res.egress.admin} · retention chat=${res.retention_years.chat} admin=${res.retention_years.admin}`);
  if (!res.all_same || !res.members_same || !res.departments_same) throw new Error("the two doors disagree on live");
}

async function publisher() {
  const key = L.env("CHAT_EDGE_ADMIN_API_KEY");
  const as = (role) => async (method, p, body) => L.http(`${EDGE}/admin/agent-market${p}`, {
    method,
    headers: { Authorization: `Bearer ${key}`, "X-Admin-User-Id": "am7-publisher-probe", "X-Admin-Email": "am7-publisher-probe@probe.invalid",
      "X-Admin-Name": encodeURIComponent("AM7 publisher probe (script)"), "X-Admin-Role": role, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const pub = as("agent_publisher");
  const run = "00000000-0000-4000-8000-000000000001";
  const forbidden = [
    ["GET", "/org"], ["PUT", "/org", { documents_may_leave: true }], ["PUT", "/org", { access: "everyone" }], ["PUT", "/org", { retention_years: 1 }],
    ["PUT", "/members/someone@probe.invalid", { role: "admin" }], ["DELETE", "/members/someone@probe.invalid"],
    ["PUT", "/departments/probe", { monthly_credits: null }], ["DELETE", "/departments/probe"], ["GET", "/spend?days=30"], ["GET", "/runs"],
    ["POST", `/runs/${run}/open`, { reason: "probe of the publisher role" }], ["GET", `/runs/${run}`], ["GET", `/runs/${run}/computed`],
    ["GET", `/runs/${run}/artifacts/${run}/download`], ["GET", `/runs/${run}/files/${run}/download`],
    ["POST", `/runs/${run}/remove-evidence`, { reason: "probe of the publisher role" }],
    ["PUT", "/catalog/sran.financial-report", { status: "disabled", reason: "probe" }],
    ["GET", "/mail"], ["PUT", "/mail", { host: "evil.example", port: 25, tls_mode: "none", username: "", sender: "x@evil.example" }], ["POST", "/mail/test", { to: "x@probe.invalid" }],
  ];
  const results = [];
  for (const [m, p, b] of forbidden) {
    const r = await pub(m, p, b);
    results.push({ method: m, path: p, status: r.status, code: r.json?.error?.code || "" });
  }
  for (const [m, p] of [["GET", "/me"], ["GET", "/catalog"], ["GET", "/problems?status=all"]]) {
    const r = await pub(m, p);
    results.push({ method: m, path: p, status: r.status, code: r.json?.error?.code || "", allowed: true });
  }
  // chat-edge's other admin endpoints behind the same bearer
  for (const p of ["/conversations", "/users", "/analytics/usage?period=7d"]) {
    const r = await L.http(`${EDGE}/admin${p}`, { headers: { Authorization: `Bearer ${key}`, "X-Admin-Role": "agent_publisher" } });
    results.push({ method: "GET", path: `chat-edge /admin${p}`, status: r.status });
  }
  out("publisher.json", results);
  for (const r of results) log(`  agent_publisher ${r.method} ${r.path} → ${r.status} ${r.code}`);
  const bad = results.filter((r) => (r.allowed ? r.status !== 200 : r.status !== 403));
  if (bad.length) throw new Error("publisher boundary on live: " + JSON.stringify(bad));
}

async function gone() {
  const s = ownerSession();
  const res = [];
  for (const [m, p] of [["GET", "/admin/org"], ["PUT", "/admin/org"], ["GET", "/admin/catalog"], ["PUT", "/admin/catalog/sran.financial-report"],
    ["GET", "/admin/problems"], ["GET", "/admin/runs"], ["GET", "/admin/spend"], ["GET", "/admin/mail"], ["GET", "/availability"]]) {
    const r = await L.http(`${EDGE}/agent-market${p}`, { method: m, headers: { ...s.headers, "Content-Type": "application/json" },
      body: m === "GET" ? undefined : JSON.stringify({ documents_may_leave: true, status: "disabled", reason: "probe" }) });
    res.push({ method: m, path: p, status: r.status });
    log(`  the owner in the chat app ${m} ${p} → ${r.status}`);
  }
  out("gone.json", res);
  if (res.some((r) => (r.path === "/availability" ? r.status !== 200 : r.status !== 404))) throw new Error("the chat app still has an admin route");
}

(async () => {
  const f = { doors, publisher, gone }[process.argv[2]];
  if (!f) throw new Error("usage: live_checks.js doors|publisher|gone");
  void WHO;
  await f();
})().catch((e) => {
  console.error("FAILED:", e.message);
  process.exit(1);
});
