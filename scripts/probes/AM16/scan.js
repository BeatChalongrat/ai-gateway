#!/usr/bin/env node
// AM16 — what the tax-invoice agent gets back when it reads a SCANNED invoice, by where the page is sent
// and whether the gateway masks: the measurement the policy change rests on ("masking the tax id and
// the address is what stops the tax-invoice agent working"). TEST stack only, the test stack's own
// account, through the routes a person's screen calls; the agent's two switches are set as an admin sets
// them — in the admin table, by SQL, because this probe measures the gateway and the runner, not the
// admin page (the page has its own walk).
//
//   run <tag> [--leave on|off] [--masked on|off] [--model <id>] [--file <path>] [--agent <dir>] [--say <words>] [--lang en]
//       [--recipients <key,key>|none]
//       a new copy of the agent, the scan, one run; prints, for every step that asked a model: the model,
//       what the gateway said it did (masking), and what came back for the fields a tax invoice is read
//       for; then the questions the run asks the person, and the gateway's own rows for those calls
//   cleanup            the installs this probe made, and the switches as they were
//
//   SMOKE_TARGET=test node scripts/probes/AM16/scan.js run before-inside --leave off
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") { console.error("scan.js runs on the test stack only (SMOKE_TARGET=test)"); process.exit(2); }
const OUT = path.join(__dirname, "results", "scan");
fs.mkdirSync(OUT, { recursive: true });
const STATE = path.join(OUT, "state.json");
const state = (() => { try { return JSON.parse(fs.readFileSync(STATE, "utf8")); } catch { return { installs: [], switches: null }; } })();
const save = () => fs.writeFileSync(STATE, JSON.stringify(state, null, 1));
const PKG = path.join(L.ROOT, "backend", "agent_market", "packages");
const SCAN = path.join(L.ROOT, "backend", "agent_market", "fixtures", "e2", "docs", "s01_scan300_full_tax_invoice.pdf");
const AM = `${L.API_BASE.edge}/api/v1/agent-market`;
const psql = (container, user, db, q) => execFileSync("docker", ["exec", "-i", container, "psql", "-U", user, "-d", db, "-At", "-F", "|", "-c", q], { encoding: "buffer" }).toString("utf8").trim();
const chat = (q) => psql(L.T.containers.pgChat, "chatedge", "sran_ai", q);
const gw = (q) => psql(L.T.containers.pg, "postgres", "ai_gateway", q);
const lit = (s) => `'${String(s).replace(/'/g, "''")}'`;
const has = (table, col) => chat(`select count(*) from information_schema.columns where table_schema='agent_market' and table_name=${lit(table)} and column_name=${lit(col)}`) === "1";

let session = null;
async function call(method, p, body) {
  const r = await L.tryHttp(AM + p, { method, timeoutMs: 60000, headers: { "Content-Type": "application/json", ...session.headers }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (r.status >= 400 || r.status === 0) throw new Error(`${method} ${p} → ${r.status} ${(r.text || "").slice(0, 240)}`);
  return r.json || {};
}
async function upload(installId, file) {
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(file)]), path.basename(file));
  const r = await fetch(`${AM}/installs/${installId}/files`, { method: "POST", headers: session.headers, body: form, signal: AbortSignal.timeout(120000) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.file?.id) throw new Error(`upload ${path.basename(file)} → ${r.status} ${JSON.stringify(j).slice(0, 200)}`);
  return j.file.id;
}

/** The agent's two switches, as the admin's table holds them (masked only once the column exists). */
function setSwitches(slug, leave, masked) {
  const org = chat("select org_id from agent_market.org_settings limit 1") || "default";
  if (!state.switches) {
    state.switches = { org, org_leave: chat(`select documents_may_leave from agent_market.org_settings where org_id=${lit(org)}`), rows: {} };
  }
  if (!(slug in state.switches.rows)) {
    state.switches.rows[slug] = chat(`select row_to_json(e) from agent_market.agent_egress e where org_id=${lit(org)} and slug=${lit(slug)}`) || null;
  }
  save();
  chat(`update agent_market.org_settings set documents_may_leave = true where org_id=${lit(org)}`);
  const maskedCol = has("agent_egress", "masked");
  if (masked !== undefined && !maskedCol) throw new Error("this stack has no masking switch yet (agent_egress.masked): --masked cannot be set");
  chat(`insert into agent_market.agent_egress (org_id, slug, documents_may_leave, updated_by${maskedCol && masked !== undefined ? ", masked" : ""})
        values (${lit(org)}, ${lit(slug)}, ${leave}, 'am16-probe'${maskedCol && masked !== undefined ? `, ${masked}` : ""})
        on conflict (org_id, slug) do update set documents_may_leave = excluded.documents_may_leave, updated_by = excluded.updated_by, updated_at = now()${maskedCol && masked !== undefined ? ", masked = excluded.masked" : ""}`);
  return chat(`select row_to_json(e) from agent_market.agent_egress e where org_id=${lit(org)} and slug=${lit(slug)}`);
}

// --recipients "<key,key>" | none: who outside the organisation the agent's documents may reach — named
// for the organisation and for the agent, as an admin's ticks would (rows marked as this probe's, and
// only those are ever removed). Not given: the two lists are left as they are.
const hasTable = (table) => chat(`select count(*) from information_schema.tables where table_schema='agent_market' and table_name=${lit(table)}`) === "1";
function setRecipients(slug, keys) {
  if (!hasTable("org_recipients")) throw new Error("this stack has no recipient lists yet (migration 011): --recipients cannot be set");
  const org = chat("select org_id from agent_market.org_settings order by updated_at desc limit 1") || "default";
  chat(`delete from agent_market.org_recipients where org_id=${lit(org)} and updated_by='am16-probe'`);
  chat(`delete from agent_market.agent_recipients where org_id=${lit(org)} and slug=${lit(slug)} and updated_by='am16-probe'`);
  for (const k of keys) {
    chat(`insert into agent_market.org_recipients (org_id, recipient, updated_by) values (${lit(org)}, ${lit(k)}, 'am16-probe') on conflict do nothing`);
    chat(`insert into agent_market.agent_recipients (org_id, slug, recipient, updated_by) values (${lit(org)}, ${lit(slug)}, ${lit(k)}, 'am16-probe') on conflict do nothing`);
  }
  state.recipients = true;
  save();
  return `organisation: [${chat(`select coalesce(string_agg(recipient, ', ' order by recipient), '') from agent_market.org_recipients where org_id=${lit(org)}`)}] · ` +
    `agent: [${chat(`select coalesce(string_agg(recipient, ', ' order by recipient), '') from agent_market.agent_recipients where org_id=${lit(org)} and slug=${lit(slug)}`)}]`;
}

const FIELDS = ["seller_tax_id", "buyer_tax_id", "seller_name", "buyer_name", "seller_address", "buyer_address", "invoice_no", "total"];
function fieldsOf(o) {
  // wherever the reading keeps them: a flat object, or under proposals / fields / pages[]
  const found = {};
  const walk = (v, depth) => {
    if (!v || typeof v !== "object" || depth > 5) return;
    if (Array.isArray(v)) { v.forEach((x) => walk(x, depth + 1)); return; }
    for (const [k, x] of Object.entries(v)) {
      if (FIELDS.includes(k) && found[k] === undefined) found[k] = typeof x === "object" && x !== null ? (x.value ?? x.text ?? JSON.stringify(x).slice(0, 80)) : x;
      else walk(x, depth + 1);
    }
  };
  walk(o, 0);
  return found;
}

async function run(tag, opt) {
  const dir = opt.agent || "invoice_processing";
  const m = JSON.parse(fs.readFileSync(path.join(PKG, dir, "agent.json"), "utf8"));
  const st = JSON.parse(fs.readFileSync(path.join(PKG, dir, "self-test.json"), "utf8"));
  const sw = setSwitches(m.id, opt.leave === "on", opt.masked === undefined ? undefined : opt.masked === "on");
  console.log(`switches for ${m.id}: ${sw}`);
  if (opt.recipients !== undefined) {
    console.log(`recipients for ${m.id}: ${setRecipients(m.id, opt.recipients === "none" ? [] : opt.recipients.split(",").map((k) => k.trim()).filter(Boolean))}`);
  }
  const t0 = Date.now();
  const startedAt = new Date(Date.now() - 2000).toISOString();
  const made = await call("POST", "/installs", { slug: m.id, idempotency_key: `am16-${crypto.randomUUID()}`, settings: st.settings || {} });
  const installId = made.install?.id || made.id;
  state.installs.push({ id: installId, slug: m.id, tag, at: new Date().toISOString() });
  save();
  if (opt.model) {
    const menu = await call("GET", `/installs/${installId}/models`).catch((e) => ({ error: String(e.message) }));
    const r = await L.tryHttp(`${AM}/installs/${installId}/model`, { method: "PUT", timeoutMs: 30000, headers: { "Content-Type": "application/json", ...session.headers }, body: JSON.stringify({ choice: opt.model }) });
    console.log(`model choice ${opt.model}: ${r.status}${r.status >= 400 ? ` ${(r.text || "").slice(0, 200)}` : ""}${menu.error ? ` (menu: ${menu.error})` : ""}`);
    if (r.status >= 400) { fs.writeFileSync(path.join(OUT, `${tag}.json`), JSON.stringify({ tag, opt, switches: sw, refused: { status: r.status, body: (r.text || "").slice(0, 400) } }, null, 1)); return; }
  }
  // the scan by default; an agent named with --agent and no --file gets its own sample files
  const files = opt.file ? [path.resolve(opt.file)] : opt.agent ? (st.files || []).map((rel) => path.join(PKG, dir, rel)) : [SCAN];
  const file = files[0];
  const ids = [];
  for (const f of files) ids.push(await upload(installId, f));
  const sent = await call("POST", `/installs/${installId}/messages`, { text: opt.say || "", file_ids: ids });
  const runId = sent.run_id || sent.run?.id;
  if (!runId) throw new Error(`no run started: ${JSON.stringify(sent).slice(0, 240)}`);
  console.log(`${tag}: ${m.id} ${m.version}, install ${installId.slice(0, 8)}, run ${runId.slice(0, 8)}, file ${path.basename(file)}`);
  let d = null, last = "";
  for (;;) {
    d = await call("GET", `/runs/${runId}`);
    const now = `${d.run.status}/${d.run.status_reason || ""}/${(d.steps || []).length}`;
    if (now !== last) { console.log(`  +${Math.round((Date.now() - t0) / 1000)} s ${d.run.status}${d.run.status_reason ? ` (${d.run.status_reason})` : ""}, ${(d.steps || []).length} step(s)`); last = now; }
    if (["succeeded", "failed", "cancelled", "awaiting_input"].includes(d.run.status)) break;
    if (Date.now() - t0 > 8 * 60_000) { console.log("  still running after 8 min — reading it as it is"); break; }
    await L.wait(3000);
  }
  await L.wait(2000);
  d = await call("GET", `/runs/${runId}`);
  const steps = (d.steps || []).map((s) => {
    const o = s.output && typeof s.output === "object" ? s.output : {};
    return { name: s.name, status: s.status, model_id: s.model_id || "", masking: o.masking ?? null, left_unmasked: o.sent_out_unmasked ?? null, model: o.model ?? null,
      fields: fieldsOf(o), error: (s.error || o.error || "").toString().slice(0, 300) || undefined, raw: JSON.stringify(o).slice(0, 700) };
  });
  const asks = (d.clarifications || []).map((c) => ({ status: c.status, kind: c.kind || c.payload?.kind, questions: (c.questions || c.payload?.questions || []).map((q) => `${q.reason || q.kind || ""}:${q.field || q.id || ""}`).slice(0, 12) }));
  // the gateway's own rows for the runner's calls since this run began
  let rows = [];
  try {
    const cols = gw("select string_agg(column_name, ',') from information_schema.columns where table_name='request_logs'").split(",");
    const want = ["created_at", "model", "provider", "status_code", "error_code", "policy_action", "dlp_input_action", "dlp_input_findings", "dlp_output_findings", "dlp_categories", "fallback_used"].filter((c) => cols.includes(c));
    rows = gw(`select ${want.map((c) => `coalesce(${c}::text,'')`).join(" || '|' || ")} from request_logs where created_at > ${lit(startedAt)} order by created_at`).split(/\r?\n/).filter(Boolean)
      .map((l) => Object.fromEntries(l.split("|").map((v, i) => [want[i], v])));
  } catch (e) { rows = [{ error: String(e.message).slice(0, 200) }]; }
  // what the run's own cards say, as the thread holds them
  const thread = await call("GET", `/installs/${installId}/thread`).catch(() => ({}));
  const items = (thread.items || thread.thread?.items || []).filter((i) => (i.payload?.run_id || i.run_id) === runId);
  const progress = items.find((i) => i.kind === "progress")?.payload || {};
  const outputs = items.filter((i) => i.kind === "outputs").pop()?.payload || null;
  const cards = { progress_sent_out_unmasked: progress.sent_out_unmasked === true, outputs_model: outputs ? { model: outputs.model?.model, vision_model: outputs.model?.vision_model, masking: outputs.model?.masking, sent_out_unmasked: outputs.model?.sent_out_unmasked === true } : null,
    notices: items.filter((i) => i.kind === "notice").map((i) => `${i.payload?.reason || ""}: ${String(i.payload?.text?.th || i.payload?.text || "").slice(0, 110)}`) };
  const out = { tag, at: new Date().toISOString(), opt, switches: sw, agent: m.id, version: m.version, install: installId, seconds: Math.round((Date.now() - t0) / 1000),
    run: { id: runId, status: d.run.status, reason: d.run.status_reason, model_used: d.run.model_used, model_self_hosted: d.run.model_self_hosted }, steps, asks, cards, gateway_rows: rows };
  fs.writeFileSync(path.join(OUT, `${tag}.json`), JSON.stringify(out, null, 1));
  console.log(`\n${tag}: run ${runId.slice(0, 8)} ${d.run.status}${d.run.status_reason ? ` (${d.run.status_reason})` : ""} in ${out.seconds} s`);
  for (const s of steps) {
    if (!s.model_id && !s.masking && !s.left_unmasked && !Object.keys(s.fields).length && !s.error) continue;
    console.log(`  step ${s.name.slice(0, 44).padEnd(44)} ${String(s.status).padEnd(9)} model="${s.model_id}" masking=${s.masking}${s.left_unmasked !== null ? ` sent_out_unmasked=${s.left_unmasked}` : ""}${s.error ? ` error=${s.error.slice(0, 160)}` : ""}`);
    if (Object.keys(s.fields).length) console.log(`       read: ${JSON.stringify(s.fields).slice(0, 420)}`);
  }
  console.log(`  the run's card says its documents were sent outside unmasked: ${cards.progress_sent_out_unmasked}${cards.outputs_model ? ` · the files card: ${JSON.stringify(cards.outputs_model)}` : ""}`);
  for (const n of cards.notices) console.log(`  notice ${n}`);
  console.log(`  asks the person: ${asks.reduce((n, a) => n + a.questions.length, 0)} question(s)`);
  for (const r of rows) console.log(`  gateway: ${Object.entries(r).filter(([k]) => k !== "created_at").map(([k, v]) => `${k}=${v}`).join(" ").slice(0, 260)}`);
}

async function cleanup() {
  for (const i of state.installs) {
    const r = await L.tryHttp(`${AM}/installs/${i.id}`, { method: "DELETE", timeoutMs: 30000, headers: session.headers });
    console.log(`install ${i.id.slice(0, 8)} (${i.tag}) removed: ${r.status}`);
  }
  state.installs = [];
  if (state.switches) {
    const s = state.switches;
    chat(`update agent_market.org_settings set documents_may_leave = ${s.org_leave === "t"} where org_id=${lit(s.org)}`);
    for (const [slug, was] of Object.entries(s.rows)) {
      if (was === null) chat(`delete from agent_market.agent_egress where org_id=${lit(s.org)} and slug=${lit(slug)} and updated_by='am16-probe'`);
      else { const w = JSON.parse(was); chat(`update agent_market.agent_egress set documents_may_leave=${w.documents_may_leave}, updated_by=${lit(w.updated_by)}${"masked" in w ? `, masked=${w.masked}` : ""} where org_id=${lit(s.org)} and slug=${lit(slug)}`); }
      console.log(`switch row for ${slug} put back: ${was === null ? "(no row)" : was}`);
    }
    console.log(`organisation's switch put back: ${s.org_leave}`);
    state.switches = null;
  }
  if (state.recipients && hasTable("org_recipients")) {
    chat("delete from agent_market.agent_recipients where updated_by='am16-probe'");
    chat("delete from agent_market.org_recipients where updated_by='am16-probe'");
    console.log(`recipients this probe named: removed (left: organisation ${chat("select count(*) from agent_market.org_recipients")}, agents ${chat("select count(*) from agent_market.agent_recipients")})`);
    state.recipients = false;
  }
  save();
}

(async () => {
  const [cmd, tag, ...rest] = process.argv.slice(2);
  session = L.bearerSession(L.mintPortalToken(L.TS_USER, 3600), "minted");
  if (cmd === "cleanup") return cleanup();
  if (cmd !== "run" || !tag) { console.error("usage: scan.js run <tag> [--leave on|off] [--masked on|off] [--model <id>] [--file <path>] [--agent <dir>] [--say <words>] [--lang en] [--recipients <key,key>|none] | cleanup"); process.exit(2); }
  const opt = {};
  for (let i = 0; i < rest.length; i += 2) opt[rest[i].replace(/^--/, "")] = rest[i + 1];
  if (!["on", "off"].includes(opt.leave || "off")) throw new Error("--leave on|off");
  opt.leave = opt.leave || "off";
  // --lang en: the run is started from an English screen (the one language tag this product's clients send)
  if (opt.lang) session.headers = { ...session.headers, "Accept-Language": opt.lang };
  await run(tag, opt);
})().catch((e) => { console.error(`scan: ${L.errText(e)}`); process.exit(1); });
