#!/usr/bin/env node
// AM15 §B — what an accounting agent's run does when the in-house model cannot be reached, and when
// it is back. On the TEST stack only, with the test stack's own account, through the routes a person's
// screen calls (install, files, a typed message, the run, the thread) — no shortcut into the runner.
//
//   run <agent_dir> [th|en] [file…]
//                              a new copy of the agent, its own sample files (or the files named) and one typed instruction;
//                              waits for the run to end, to wait, or to do neither, and writes down what
//                              it did: status and reason, every step with the model it names, what was
//                              said in the thread, the files delivered, what was charged
//   look <run id>              the same reading of a run that exists (a waiting run after the model is back)
//   cleanup                    the installs this probe made, by id
//
//   SMOKE_TARGET=test node scripts/smoke/primary.js down      # the model taken away (and `up` after)
//   SMOKE_TARGET=test node scripts/probes/AM15/model_down.js run bank_reconciliation
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") { console.error("model_down.js runs on the test stack only (SMOKE_TARGET=test)"); process.exit(2); }
const OUT = path.join(__dirname, "results", "model_down");
fs.mkdirSync(OUT, { recursive: true });
const STATE = path.join(OUT, "state.json");
const state = (() => { try { return JSON.parse(fs.readFileSync(STATE, "utf8")); } catch { return { installs: [] }; } })();
const save = () => fs.writeFileSync(STATE, JSON.stringify(state, null, 1));
const PKG = path.join(L.ROOT, "backend", "agent_market", "packages");
const AM = `${L.API_BASE.edge}/api/v1/agent-market`;
// one instruction every package can be asked about its documents (an order), so each run asks the model
// — and a few characters no earlier run has sent: the gateway keeps a non-stream answer for an hour, and
// the first run of this probe with the model down was "read by the model" out of that cache
const NONCE = crypto.randomUUID().slice(0, 6);
const ASK = { th: `[AM15 ${NONCE}] เรียงรายการตามจำนวนเงินจากมากไปน้อย`, en: `[AM15 ${NONCE}] sort the rows by amount, largest first` };
const TIMEOUT_MS = Number(process.env.AM15_RUN_TIMEOUT_MS) || 9 * 60_000;

let session = null;
const head = (lang) => ({ ...session.headers, ...(lang === "en" ? { "Accept-Language": "en" } : {}) });
async function call(method, p, body, lang) {
  const r = await L.tryHttp(AM + p, { method, timeoutMs: 60000, headers: { "Content-Type": "application/json", ...head(lang) }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (r.status >= 400 || r.status === 0) throw new Error(`${method} ${p} → ${r.status || r.error} ${r.text.slice(0, 200)}`);
  return r.json;
}
async function upload(installId, file, lang) {
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(file)]), path.basename(file));
  const r = await fetch(`${AM}/installs/${installId}/files`, { method: "POST", headers: head(lang), body: form, signal: AbortSignal.timeout(120000) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.file?.id) throw new Error(`upload ${path.basename(file)} → ${r.status} ${JSON.stringify(j).slice(0, 200)}`);
  return j.file.id;
}

const words = (v) => (typeof v === "string" ? v : v && typeof v === "object" ? (v.text || v.th || v.en || JSON.stringify(v)) : String(v ?? ""));
/** A run as a person and the run page see it. */
async function reading(runId, installId, lang) {
  const d = await call("GET", `/runs/${runId}`, undefined, lang);
  const t = await call("GET", `/installs/${installId}/thread`, undefined, lang);
  const items = (t.items || t.thread?.items || []).filter((i) => !i.run_id || i.run_id === runId || (d.children || []).some((c) => c.id === i.run_id));
  const steps = (d.steps || []).map((s) => {
    const o = s.output && typeof s.output === "object" ? s.output : {};
    return { name: s.name, status: s.status, model_id: s.model_id || "", model: o.model ?? o.read_by ?? null, unread: o.unread, tries: o.tries, written_by: o.written_by,
      tried: o.tried, error: (s.error || o.error || "").toString().slice(0, 240) || undefined };
  });
  const said = items.filter((i) => ["agent_text", "notice", "text", "failure", "outputs"].includes(i.kind) || i.role === "agent")
    .map((i) => ({ kind: i.kind, tone: i.payload?.tone || i.tone, reason: i.payload?.reason, action: i.payload?.action, text: words(i.payload?.text ?? i.text ?? "").slice(0, 400) }))
    .filter((i) => i.text || i.kind === "outputs");
  return {
    run: { id: d.run.id, kind: d.run.kind, status: d.run.status, reason: d.run.status_reason, credits_spent: d.run.credits_spent, credits_cost: d.run.credits_cost,
      model_used: d.run.model_used, gate_open: d.run.gate_open, delivered_at: d.run.delivered_at || null },
    steps, artifacts: (d.artifacts || []).map((a) => a.name), open_cards: (d.clarifications || []).filter((c) => c.status === "open" || c.status === "pending").length,
    children: (d.children || []).map((c) => ({ id: c.id, kind: c.kind, status: c.status, reason: c.status_reason })), said,
  };
}
function print(tag, r) {
  console.log(`\n${tag}: run ${r.run.id.slice(0, 8)} ${r.run.status}${r.run.reason ? ` (${r.run.reason})` : ""} · files delivered ${r.artifacts.length} · charged ${r.run.credits_cost ?? "?"} (spent on steps ${r.run.credits_spent}) · model_used ${r.run.model_used || "-"} · open cards ${r.open_cards}`);
  for (const s of r.steps) console.log(`  step ${s.name.padEnd(13)} ${String(s.status).padEnd(9)} model_id="${s.model_id}"${s.model !== null ? ` output.model="${s.model}"` : ""}${s.unread !== undefined ? ` unread=${s.unread}` : ""}${s.tries !== undefined ? ` tries=${s.tries}` : ""}${s.written_by ? ` written_by=${s.written_by}` : ""}${s.tried ? ` tried=${JSON.stringify(s.tried).slice(0, 200)}` : ""}${s.error ? ` error="${s.error.slice(0, 160)}"` : ""}`);
  for (const s of r.said) console.log(`  said [${s.kind}${s.tone ? ` ${s.tone}` : ""}${s.reason ? ` ${s.reason}` : ""}${s.action ? ` action=${s.action}` : ""}] ${s.text.slice(0, 300)}`);
}

async function run(dir, lang = "th", own = []) {
  const m = JSON.parse(fs.readFileSync(path.join(PKG, dir, "agent.json"), "utf8"));
  const st = JSON.parse(fs.readFileSync(path.join(PKG, dir, "self-test.json"), "utf8"));
  const t0 = Date.now();
  const made = await call("POST", "/installs", { slug: m.id, idempotency_key: `am15-${crypto.randomUUID()}`, settings: st.settings || {} }, lang);
  const installId = made.install?.id || made.id;
  if (!installId) throw new Error(`install ${m.id}: no id in ${JSON.stringify(made).slice(0, 200)}`);
  state.installs.push({ id: installId, slug: m.id, at: new Date().toISOString() });
  save();
  const ids = [];
  // the package's own sample files — or the files named on the command line (a scan, for the reading by eye)
  const files = own.length ? own.map((f) => path.resolve(f)) : (st.files || []).map((rel) => path.join(PKG, dir, rel));
  for (const f of files) ids.push(await upload(installId, f, lang));
  const sent = await call("POST", `/installs/${installId}/messages`, { text: ASK[lang], file_ids: ids }, lang);
  const runId = sent.run_id || sent.run?.id;
  if (!runId) throw new Error(`no run started: ${JSON.stringify(sent).slice(0, 240)}`);
  console.log(`${dir} ${m.version} (${lang}): install ${installId.slice(0, 8)}, ${ids.length} file(s), run ${runId.slice(0, 8)} — "${ASK[lang]}"`);
  let r = null, last = "";
  for (;;) {
    r = await reading(runId, installId, lang);
    const now = `${r.run.status}/${r.run.reason || ""}/${r.steps.length}`;
    if (now !== last) { console.log(`  +${Math.round((Date.now() - t0) / 1000)} s ${r.run.status}${r.run.reason ? ` (${r.run.reason})` : ""}, ${r.steps.length} step(s)${r.steps.length ? `, last ${r.steps[r.steps.length - 1].name}:${r.steps[r.steps.length - 1].status}` : ""}`); last = now; }
    if (["succeeded", "failed", "cancelled", "awaiting_input"].includes(r.run.status)) break;
    if (Date.now() - t0 > TIMEOUT_MS) { r.hung = true; break; }
    await L.wait(3000);
  }
  await L.wait(2500); // what is said after the run ends
  const end = await reading(runId, installId, lang);
  end.hung = !!r.hung; end.seconds = Math.round((Date.now() - t0) / 1000); end.agent = dir; end.version = m.version; end.lang = lang; end.install = installId;
  const tag = process.env.AM15_TAG || "run";
  fs.writeFileSync(path.join(OUT, `${tag}_${dir}_${lang}.json`), JSON.stringify(end, null, 1));
  print(`${dir} (${lang})${end.hung ? ` — STILL ${end.run.status} after ${end.seconds} s` : ` in ${end.seconds} s`}`, end);
  return end;
}

async function main() {
  session = L.bearerSession(L.mintPortalToken(L.TS_USER), "minted-test");
  const [cmd, a, b] = process.argv.slice(2);
  if (cmd === "run") { await run(a, b || "th", process.argv.slice(5)); return; }
  if (cmd === "look") {
    const d = await call("GET", `/runs/${a}`);
    const r = await reading(a, d.run.install_id, b);
    r.install = d.run.install_id;
    fs.writeFileSync(path.join(OUT, `${process.env.AM15_TAG || "look"}_${a.slice(0, 8)}.json`), JSON.stringify(r, null, 1));
    print(`run ${a.slice(0, 8)}`, r);
    return;
  }
  if (cmd === "say") {
    // one typed message on an install whose run has ended: a question, or an instruction about its documents
    const lang = process.env.AM15_LANG || "th";
    const text = `[AM15 ${NONCE}] ${process.argv.slice(4).join(" ")}`;
    const t0 = Date.now();
    const sent = await call("POST", `/installs/${a}/messages`, { text }, lang);
    console.log(`"${text}" → route ${sent.route}, job ${String(sent.run_id || "").slice(0, 8) || "-"}`);
    if (!sent.run_id) return;
    let r;
    for (;;) {
      r = await reading(sent.run_id, a, lang);
      if (["succeeded", "failed", "cancelled", "awaiting_input"].includes(r.run.status) || Date.now() - t0 > TIMEOUT_MS) break;
      await L.wait(3000);
    }
    await L.wait(2500);
    r = await reading(sent.run_id, a, lang);
    r.seconds = Math.round((Date.now() - t0) / 1000); r.text = text;
    fs.writeFileSync(path.join(OUT, `${process.env.AM15_TAG || "say"}_${sent.run_id.slice(0, 8)}.json`), JSON.stringify(r, null, 1));
    print(`reply in ${r.seconds} s`, r);
    return;
  }
  if (cmd === "cleanup") {
    for (const i of state.installs.splice(0)) {
      const r = await L.tryHttp(`${AM}/installs/${i.id}`, { method: "DELETE", headers: session.headers });
      console.log(`removed ${i.slug} ${i.id.slice(0, 8)} → ${r.status}`);
    }
    save();
    return;
  }
  console.error("usage: SMOKE_TARGET=test node scripts/probes/AM15/model_down.js run <agent_dir> [th|en] | look <run id> | cleanup");
  process.exit(2);
}
main().catch((e) => { console.error(String(e && e.message || e)); process.exit(1); });
