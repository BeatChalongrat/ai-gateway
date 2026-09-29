#!/usr/bin/env node
// AM6 — the fresh-install test of every agent, on the TEST stack, through the API a client uses:
//
//   for every agent in the catalog: one-click install with its sample self-test, the self-test
//   round must succeed and ask nothing, and every file it delivers is downloaded to
//   results/fresh/<python package>/ — then, by hand:
//
//     AM_DELIVERED=scripts/probes/AM6/results/fresh bash scripts/agent-market/generic-check.sh
//
//   looks into the package and into what was delivered for anything of one organisation.
//   Run it on a stack whose agent_market schema was just created (test_stack.sh fresh).
//
//   SMOKE_TARGET=test node scripts/probes/AM6/fresh_install.js
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") {
  console.error("fresh_install.js runs on the test stack only (SMOKE_TARGET=test)");
  process.exit(2);
}
const AM = `${L.API_BASE.edge}/api/v1/agent-market`;
const OUT = path.join(__dirname, "results", "fresh");
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
const session = L.bearerSession(L.mintPortalToken(L.TS_USER, 7200));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const checks = [];
const check = (ok, what, detail = "") => {
  checks.push({ ok: !!ok, what, detail: String(detail).slice(0, 400) });
  log(`${ok ? "PASS" : "FAIL"} ${what}${detail ? ` — ${String(detail).slice(0, 220)}` : ""}`);
  if (!ok) process.exitCode = 1;
  return ok;
};
async function am(p, init = {}) {
  const r = await L.http(AM + p, { timeoutMs: 60000, ...init, headers: { "Content-Type": "application/json", ...session.headers, ...(init.headers || {}) } });
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 300)}`);
  return r.json;
}
async function waitFor(fn, ms, step = 2000) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() - t0 > ms) return null;
    await L.wait(step);
  }
}
// the python package of a slug, from the checkout's manifests
const PACKAGES = path.join(L.ROOT, "backend", "agent_market", "packages");
const bySlug = {};
for (const d of fs.readdirSync(PACKAGES)) {
  const m = path.join(PACKAGES, d, "agent.json");
  if (fs.existsSync(m)) bySlug[JSON.parse(fs.readFileSync(m, "utf8")).id] = d;
}

(async () => {
  const cat = await am("/catalog");
  const agents = (cat.agents || []).filter((a) => bySlug[a.slug]);
  check(agents.length === Object.keys(bySlug).length, "every agent of the checkout is in the catalog",
    `${agents.map((a) => `${a.slug}@${a.version}`).join(" · ")}`);
  const summary = [];
  for (const a of agents) {
    const pkg = bySlug[a.slug];
    const inst = (await am("/installs", { method: "POST", body: JSON.stringify({ slug: a.slug, idempotency_key: crypto.randomUUID(), self_test: true }) })).install.id;
    const st = await waitFor(async () => ((await am(`/installs/${inst}/runs`)).runs || [])
      .find((r) => r.kind === "self_test" && ["succeeded", "failed", "cancelled"].includes(r.status)), 900000);
    check(st && st.status === "succeeded", `${a.slug}: installed fresh, its self-test succeeds`, st ? `${st.status} ${st.status_reason || ""}` : "no self-test in 15 minutes");
    const detail = st ? await am(`/runs/${st.id}`) : {};
    const asked = (detail.clarifications || []).length;
    check(asked === 0, `${a.slug}: the self-test asks nothing`, `${asked} card(s)`);
    const items = ((await am(`/installs/${inst}/thread`)).items || []).filter((i) => i.kind === "outputs" && st && i.run_id === st.id);
    const arts = items.flatMap((i) => (i.payload && i.payload.artifacts) || []);
    const dir = path.join(OUT, pkg);
    fs.mkdirSync(dir, { recursive: true });
    for (const art of arts) {
      const r = await fetch(`${AM}/artifacts/${art.id}/download`, { headers: { ...session.headers } });
      const buf = Buffer.from(await r.arrayBuffer());
      fs.writeFileSync(path.join(dir, art.name), buf);
    }
    const c = await L.http(`${AM}/runs/${st && st.id}/computed`, { timeoutMs: 60000, headers: { ...session.headers } });
    if (c.status === 200) fs.writeFileSync(path.join(dir, "computed.json"), c.text);
    check(arts.length > 0, `${a.slug}: its deliverables are downloaded`, arts.map((x) => `${x.name} (${x.size})`).join(" · "));
    summary.push({ slug: a.slug, version: a.version, package: pkg, install: inst, self_test: st && st.id, status: st && st.status, files: arts.map((x) => x.name) });
    const del = await L.http(`${AM}/installs/${inst}`, { method: "DELETE", headers: { ...session.headers } });
    log(`install ${inst} removed: ${del.status}`);
  }
  fs.writeFileSync(path.join(OUT, "..", "fresh_install.json"), JSON.stringify({ agents: summary, checks }, null, 1));
  const failed = checks.filter((c) => !c.ok).length;
  log(`fresh install: ${checks.length - failed} pass, ${failed} fail`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
