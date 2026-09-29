#!/usr/bin/env node
// AM6 — the invoice ↔ PO agent's name reaches a person whole: install it (named after its short
// name), run one round on its sample month, draft the round's e-mail, and read the subject. The
// install is removed afterwards and the round's evidence with it.
//
//   SMOKE_TARGET=live AM6_EMAIL=<owner> node scripts/probes/AM6/name_probe.js
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const L = require("../../smoke/lib.js");

const AM = `${L.API_BASE.edge}/api/v1/agent-market`;
const PO_DIR = path.join(L.ROOT, "backend", "agent_market", "packages", "po_matching", "sample-data");
const email = process.env.AM6_EMAIL || "";
const who = L.TARGET === "live" ? { id: L.gsql(`select id from portal_users where email='${email}'`).trim() } : L.TS_USER;
const session = L.bearerSession(L.mintPortalToken(who, 1800));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
async function am(p, init = {}) {
  const r = await L.http(AM + p, { timeoutMs: 60000, ...init, headers: { "Content-Type": "application/json", ...session.headers, ...(init.headers || {}) } });
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 200)}`);
  return r.json;
}
const post = (p, b) => am(p, { method: "POST", body: JSON.stringify(b || {}) });
async function waitFor(fn, ms, step = 1500) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() - t0 > ms) return null;
    await L.wait(step);
  }
}

(async () => {
  let inst = null;
  let run = null;
  try {
    const cat = await am("/catalog");
    const agent = (cat.agents || []).find((a) => a.slug === "sran.invoice-po-matching");
    log(`catalog: ${agent && agent.version} · short name ${agent && (agent.short_name || "")}`);
    inst = (await post("/installs", { slug: "sran.invoice-po-matching", idempotency_key: crypto.randomUUID(), self_test: false })).install;
    log(`install title: ${inst.title}`);
    await am(`/installs/${inst.id}/defaults/company`, { method: "PUT", body: JSON.stringify({ value: "บริษัท สยามตัวอย่างการค้า จำกัด" }) });
    const ids = [];
    for (const n of fs.readdirSync(PO_DIR)) {
      const fd = new FormData();
      fd.append("file", new Blob([fs.readFileSync(path.join(PO_DIR, n))]), n);
      const r = await fetch(`${AM}/installs/${inst.id}/files`, { method: "POST", headers: { ...session.headers }, body: fd });
      ids.push((await r.json()).file.id);
    }
    await post(`/installs/${inst.id}/messages`, { text: "[AM6] ชื่อ agent ในอีเมล", file_ids: ids, client_id: crypto.randomUUID() });
    run = await waitFor(async () => ((await am(`/installs/${inst.id}/runs`)).runs || []).find((r) => r.kind === "run"), 60000);
    const seen = new Set();
    for (;;) {
      const d = await waitFor(async () => {
        const r = await am(`/runs/${run.id}`);
        if (["succeeded", "failed", "cancelled"].includes(r.run.status)) return r;
        const open = (r.clarifications || []).filter((c) => c.status === "open" && !seen.has(c.id));
        return r.run.status === "awaiting_input" && open.length ? r : null;
      }, 600000);
      if (!d) throw new Error("the round did not finish");
      if (["succeeded", "failed", "cancelled"].includes(d.run.status)) { log(`round ${d.run.status} ${d.run.outcome || ""}`); break; }
      for (const c of d.clarifications.filter((x) => x.status === "open" && !seen.has(x.id))) {
        seen.add(c.id);
        const answers = {};
        for (const q of c.questions || []) answers[q.key] = { labels: [q.recommended_label || q.default_label] };
        await post(`/cards/${c.id}/answer`, { answers });
      }
    }
    const draft = (await post(`/runs/${run.id}/email-draft`)).draft;
    const ok = draft.subject.startsWith(`${inst.title} · `) && !draft.subject.includes("…");
    log(`${ok ? "PASS" : "FAIL"} the draft names the agent whole — subject: ${draft.subject}`);
    if (!ok) process.exitCode = 1;
  } finally {
    if (run) log(`evidence removed: ${(await L.http(`${AM}/admin/runs/${run.id}/remove-evidence`, { method: "POST", headers: { "Content-Type": "application/json", ...session.headers }, body: JSON.stringify({ reason: "[AM6] รอบทดสอบชื่อ agent ในอีเมล ไม่ใช่งานจริง" }) })).status}`);
    if (inst) log(`install removed: ${(await L.http(`${AM}/installs/${inst.id}`, { method: "DELETE", headers: { ...session.headers } })).status}`);
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
