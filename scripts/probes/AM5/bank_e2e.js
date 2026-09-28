#!/usr/bin/env node
// AM5 phase E end to end on the TEST stack, through the API a client uses —
// the built chat-edge and runner images, the real sandbox:
//
//   1. the agent is in the catalog; one-click install; its sample self-test passes;
//   2. a month that ties: succeeded, ten pairs by rule, the two adjusted balances equal;
//   3. the same two files again: the same computed table, byte for byte (its matching groups too);
//   4. a month with two payments the rules will not match: two question cards;
//      confirmed → tied, the pairs say a person confirmed them;
//   5. a month that does not tie: the round is NOT a success, says how far off it is,
//      delivers the draft, and cannot be signed — by its preparer or anyone;
//   6. a month that ties cannot be signed by its preparer (a different reviewer can);
//   7. the install's own tolerance is kept and an unusable one refused;
//   8. nothing a person reads in the thread is technical.
//
//   SMOKE_TARGET=test node scripts/probes/AM5/bank_e2e.js
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") {
  console.error("bank_e2e.js runs on the test stack only (SMOKE_TARGET=test)");
  process.exit(2);
}
const AM = `${L.API_BASE.edge}/api/v1/agent-market`;
const FIX = path.join(L.ROOT, "backend", "agent_market", "packages", "bank_reconciliation", "evals", "fixtures");
const OUT = path.join(__dirname, "results", "e2e");
const SLUG = "sran.bank-reconciliation";
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
async function call(p, init = {}) {
  const r = await L.http(AM + p, { timeoutMs: 60000, ...init, headers: { "Content-Type": "application/json", ...session.headers, ...(init.headers || {}) } });
  return { status: r.status, json: r.json, text: r.text };
}
async function am(p, init = {}) {
  const r = await call(p, init);
  if (r.status >= 400) throw new Error(`${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 300)}`);
  return r.json;
}
const post = (p, body) => am(p, { method: "POST", body: JSON.stringify(body || {}) });
async function waitFor(fn, ms, step = 1000) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() - t0 > ms) return null;
    await L.wait(step);
  }
}
async function upload(inst, src, name) {
  const fd = new FormData();
  fd.append("file", new Blob([fs.readFileSync(path.join(FIX, src))]), name);
  const r = await fetch(`${AM}/installs/${inst}/files`, { method: "POST", headers: { ...session.headers }, body: fd });
  const t = await r.json();
  if (r.status >= 400) throw new Error(`upload ${name}: ${r.status} ${JSON.stringify(t)}`);
  return t.file;
}
// one round; cards are answered with `answer(question)` (null: the card's default, as a skipped card records)
async function round(inst, files, text, answer = () => null) {
  const before = ((await am(`/installs/${inst}/runs`)).runs || []).map((r) => r.id);
  const ids = [];
  for (const [src, name] of files) ids.push((await upload(inst, src, name)).id);
  const m = await post(`/installs/${inst}/messages`, { text, file_ids: ids, client_id: crypto.randomUUID() });
  if (m.route === "intent") {
    // after a finished round the agent asks instead of guessing: a new round
    const chip = (await thread(inst)).filter((i) => i.kind === "intent").pop();
    await post(`/intents/${chip.id}`, { choice: "new_round" });
  }
  const run = await waitFor(async () => ((await am(`/installs/${inst}/runs`)).runs || []).find((r) => r.kind === "run" && !before.includes(r.id)), 90000);
  if (!run) throw new Error("no round started");
  const asked = [];
  const seen = new Set();
  for (;;) {
    const d = await waitFor(async () => {
      const r = await am(`/runs/${run.id}`);
      if (["succeeded", "failed", "cancelled"].includes(r.run.status)) return r;
      const open = (r.clarifications || []).filter((c) => c.status === "open" && !seen.has(c.id));
      return r.run.status === "awaiting_input" && open.length ? r : null;
    }, 600000, 1500);
    if (!d) throw new Error("the round did not finish in 10 minutes");
    if (["succeeded", "failed", "cancelled"].includes(d.run.status)) return { run: d.run, detail: d, asked };
    for (const c of d.clarifications.filter((x) => x.status === "open" && !seen.has(x.id))) {
      seen.add(c.id);
      const answers = {};
      for (const q of c.questions || []) {
        asked.push(q);
        const want = answer(q);
        const opt = (q.options || []).find((o) => o.value === want);
        answers[q.key] = opt ? { labels: [opt.label] } : { labels: [q.default_label], skipped: true };
      }
      await post(`/cards/${c.id}/answer`, { answers });
    }
  }
}
const thread = async (inst) => (await am(`/installs/${inst}/thread`)).items || [];
const outputsOf = async (inst, runId) => (await thread(inst)).filter((i) => i.kind === "outputs" && i.run_id === runId).map((i) => i.payload);
const failureOf = async (inst, runId) => (await thread(inst)).filter((i) => i.kind === "agent_text" && i.run_id === runId && i.payload && i.payload.tone === "error").map((i) => i.payload).pop();
async function computed(runId) {
  const r = await L.http(`${AM}/runs/${runId}/computed`, { timeoutMs: 60000, headers: { ...session.headers } });
  if (r.status !== 200) throw new Error(`computed ${runId}: ${r.status}`);
  return r.text;
}
const TECHNICAL = /\b[A-Z][A-Za-z]*(?:Error|Exception)\b|NoneType|Traceback|not iterable|engine step failed|sandbox:|dial tcp|SQLSTATE|\b[a-z]+(?:_[a-z0-9]+)*_failed\b|not_tied|gate_not_passed|fallback_narrative/;

(async () => {
  // 1. the catalog, the install, the self-test
  const cat = await am("/catalog");
  const agent = (cat.agents || []).find((a) => a.slug === SLUG);
  check(!!agent, "the bank reconciliation is in the catalog", agent ? `${agent.version}` : JSON.stringify((cat.agents || []).map((a) => a.slug)));
  const inst = (await post("/installs", { slug: SLUG, idempotency_key: crypto.randomUUID(), self_test: true })).install.id;
  fs.writeFileSync(path.join(OUT, "state.json"), JSON.stringify({ install: inst }, null, 1));
  const st = await waitFor(async () => ((await am(`/installs/${inst}/runs`)).runs || []).find((r) => r.kind === "self_test" && ["succeeded", "failed"].includes(r.status)), 600000, 2000);
  check(st && st.status === "succeeded", "its sample self-test passes (ties, asks nothing)", st ? `${st.status} ${st.status_reason || ""}` : "no self-test");
  const defaults = (await am(`/installs/${inst}`)).defaults || [];
  const match = defaults.filter((d) => d.key.startsWith("match_"));
  check(match.length === 6 && match.every((d) => d.label && d.label !== d.key), "the tolerances are the install's settings, each named in words", match.map((d) => `${d.label}=${JSON.stringify(d.value)}`).join(" · "));

  // 2. a month that ties
  const tie = await round(inst, [["kbank_sep.xlsx", "Statement กสิกร ก.ย.69.xlsx"], ["ledger_sep.xlsx", "สมุดบัญชีเงินฝาก ก.ย.69.xlsx"]], "[AM5] กระทบยอดธนาคาร ก.ย. 2569");
  check(tie.run.status === "succeeded" && tie.asked.length === 0, "a month that ties is a success and asks nothing", `${tie.run.status} · ${tie.asked.length} question(s)`);
  const o1 = (await outputsOf(inst, tie.run.id)).pop() || {};
  const s1 = o1.summary || {};
  check(s1.tied === true && s1.adjusted_bank === "1538519.65" && s1.adjusted_book === "1538519.65" && s1.difference === "0.00", "the two adjusted balances are equal, at the scenario's figure",
    `${s1.adjusted_bank} / ${s1.adjusted_book} / ${s1.difference}`);
  check(s1.matched === 10 && s1.by_rule === 10 && s1.by_person === 0, "ten pairs, each by a rule of the exact tier", `${s1.matched} · ${s1.by_rule} · ${s1.by_person}`);
  check(!o1.draft && o1.gate && o1.gate.passed === true, "the files card is not a draft", JSON.stringify(o1.gate));
  const c1 = await computed(tie.run.id);
  const g1 = JSON.parse(c1).match.groups;
  check(g1.matched.length === 10 && g1.missing.length === 3 && g1.extra.length === 4 && g1.suspect.length === 0, "the four groups", `${g1.matched.length} ตรงกัน · ${g1.missing.length} ขาด · ${g1.extra.length} เกิน · ${g1.suspect.length} น่าสงสัย`);
  check([...g1.matched.flatMap((m) => [...m.left, ...m.right]), ...g1.missing, ...g1.extra].every((r) => r.file && r.sheet && r.row), "every row says the file, sheet and row it came from");

  // 3. the same input twice
  const again = await round(inst, [["kbank_sep.xlsx", "Statement กสิกร ก.ย.69.xlsx"], ["ledger_sep.xlsx", "สมุดบัญชีเงินฝาก ก.ย.69.xlsx"]], "[AM5] กระทบยอดธนาคาร ก.ย. 2569 อีกครั้ง");
  const c2 = await computed(again.run.id);
  const sha = (s) => crypto.createHash("sha256").update(s).digest("hex").slice(0, 16);
  fs.writeFileSync(path.join(OUT, "computed_first.json"), c1);
  fs.writeFileSync(path.join(OUT, "computed_again.json"), c2);
  check(c1 === c2, "the same input twice: the same computed table, byte for byte", `${c1.length} bytes sha256 ${sha(c1)} · ${c2.length} bytes sha256 ${sha(c2)}`);
  check(JSON.stringify(g1) === JSON.stringify(JSON.parse(c2).match.groups), "and the same matching groups", `sha256 ${sha(JSON.stringify(g1))}`);

  // 4. candidates a person confirms
  const cand = await round(inst, [["kbank_sep_candidates.xlsx", "Statement ก.ย.69 (2).xlsx"], ["ledger_sep_candidates.xlsx", "สมุดบัญชี ก.ย.69 (2).xlsx"]], "[AM5] กระทบยอดธนาคาร ก.ย. 2569 ชุดที่สอง",
    (q) => (q.code === "e3_candidate" ? "match" : null));
  check(cand.asked.length === 2 && cand.asked.every((q) => q.code === "e3_candidate"), "two payments the rules will not match are two questions", cand.asked.map((q) => q.question.slice(0, 80)).join(" | "));
  check(cand.asked.every((q) => q.default_label === "ยังไม่แน่ใจ"), "a skipped card would match nothing", cand.asked.map((q) => q.default_label).join(" · "));
  check(cand.asked.filter((q) => q.recommended_label === "เป็นรายการเดียวกัน").length === 1, "only the pair equal to the satang is recommended", cand.asked.map((q) => q.recommended_label || "—").join(" · "));
  const o3 = (await outputsOf(inst, cand.run.id)).pop() || {};
  check(cand.run.status === "succeeded" && (o3.summary || {}).tied === true && (o3.summary || {}).by_person === 2, "confirmed: it ties, and two pairs say a person confirmed them",
    `${cand.run.status} · by_person ${(o3.summary || {}).by_person}`);
  const g3 = JSON.parse(await computed(cand.run.id)).match.groups.matched.filter((m) => m.tier !== "exact");
  check(g3.length === 2 && g3.every((m) => m.confirmed === "person" && m.pass && m.decision_key), "each records its pass, its tier and the answer that confirmed it", g3.map((m) => `${m.pass}/${m.tier}/${m.confirmed}`).join(" · "));

  // 5. a month that does not tie
  const loose = await round(inst, [["kbank_sep_page_missing.xlsx", "Statement ก.ย.69 (ขาดหน้า).xlsx"], ["ledger_sep.xlsx", "สมุดบัญชีเงินฝาก ก.ย.69.xlsx"]], "[AM5] กระทบยอดธนาคาร ก.ย. 2569 รายการเดินบัญชีขาดหน้า");
  check(loose.run.status !== "succeeded", "a month that does not tie is NOT a success", `${loose.run.status} ${loose.run.status_reason}`);
  check(loose.run.status === "failed" && loose.run.status_reason === "not_tied", "it ends with the gate's reason", `${loose.run.status} ${loose.run.status_reason}`);
  const f = (await failureOf(inst, loose.run.id)) || {};
  const fail = f.failure || {};
  check(fail.what === "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท", "it says how far off it is", fail.what);
  check(!!fail.next && !!fail.files && !!fail.charged && fail.can_retry === false, "what to do next, the files, the charge — and not to try the same files again", `${fail.next} | ${fail.files} | ${fail.charged}`);
  const o4 = (await outputsOf(inst, loose.run.id)).pop() || {};
  check(o4.draft === true && (o4.artifacts || []).length === 1 && o4.gate && o4.gate.what === fail.what, "the working paper is delivered as a draft", JSON.stringify(o4.gate));
  const sign = await call(`/runs/${loose.run.id}/review`, { method: "POST", body: "{}" });
  check(sign.status === 409, "a reconciliation that does not tie cannot be signed", `${sign.status} ${sign.json && sign.json.error && sign.json.error.message}`);
  const done = (await thread(inst)).filter((i) => i.run_id === loose.run.id && i.kind === "agent_text").map((i) => i.payload.text || "");
  check(!done.some((t) => t.includes("เสร็จแล้วครับ")), "nobody is told the round is done");
  const exq = await am(`/runs/${loose.run.id}/exceptions`).catch(() => ({}));
  const rows = exq.exceptions || exq.rows || [];
  check(rows.some((r) => r.reason_code === "statement_gap" && r.status === "open"), "the break in the statement's balance is an exception for a person", rows.map((r) => `${r.reason_code}:${r.status}`).join(" · "));

  // 6. a month that ties is signed by someone else
  const own = await call(`/runs/${tie.run.id}/review`, { method: "POST", body: JSON.stringify({ name: "คนอื่น" }) });
  check(own.status === 409 && own.json.error.code === "SAME_PERSON", "the preparer cannot sign their own reconciliation, whatever name is typed", `${own.status} ${own.json && own.json.error && own.json.error.message}`);

  // 7. the install's own tolerance
  const put = (key, value) => call(`/installs/${inst}/defaults/${key}`, { method: "PUT", body: JSON.stringify({ value }) });
  const bad = await put("match_date_window_days", "400");
  check(bad.status === 400 && bad.json.error.code === "BAD_VALUE", "a window the engine cannot use is refused where it is typed", `${bad.status} ${bad.json && bad.json.error && bad.json.error.message}`);
  const ok = await put("match_date_window_days", "7");
  check(ok.status === 200, "the install's own window is kept", String(ok.status));
  const wide = await round(inst, [["kbank_sep_window.xlsx", "Statement ก.ย.69 (3).xlsx"], ["ledger_sep_window.xlsx", "สมุดบัญชี ก.ย.69 (3).xlsx"]], "[AM5] กระทบยอดธนาคาร ก.ย. 2569 ชุดที่สาม",
    (q) => (q.code === "e3_candidate" ? "match" : null));
  const cw = JSON.parse(await computed(wide.run.id));
  check(wide.asked.length === 1 && cw.settings.matching.date_window_days === 7 && cw.statement.tied, "with seven days the pair five days apart is proposed, confirmed, and it ties",
    `${wide.asked.length} question(s) · window ${cw.settings.matching.date_window_days} · tied ${cw.statement.tied}`);

  // 8. nothing technical in the thread
  const texts = [];
  for (const it of await thread(inst)) {
    const p = it.payload || {};
    if (it.kind === "agent_text" || it.kind === "notice") texts.push(p.text || "", ...Object.values(p.failure || {}).filter((v) => typeof v === "string" && v !== (p.failure || {}).reason));
    if (it.kind === "progress") for (const s of p.steps || []) texts.push(s.label || "", s.detail || "");
    if (it.kind === "card") for (const q of p.questions || []) texts.push((q.question || "").replace(/“[^”]*”/g, ""), ...(q.options || []).map((o) => `${o.label} ${o.description || ""}`));
  }
  const hits = texts.filter((t) => TECHNICAL.test(t));
  check(hits.length === 0, "nothing a person reads in the thread is technical", hits.slice(0, 3).join(" | "));

  fs.writeFileSync(path.join(OUT, "bank_e2e.json"), JSON.stringify({ install: inst, runs: { tie: tie.run.id, again: again.run.id, cand: cand.run.id, loose: loose.run.id, wide: wide.run.id }, checks }, null, 1));
  const del = await call(`/installs/${inst}`, { method: "DELETE" });
  log(`install ${inst} removed: ${del.status}`);
  const failed = checks.filter((c) => !c.ok).length;
  log(`bank e2e: ${checks.length - failed} pass, ${failed} fail`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
