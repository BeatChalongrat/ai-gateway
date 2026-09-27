#!/usr/bin/env node
// AM3 probes — self-serve, through the real HTTP API (chat-edge control
// plane + agent-runner + doc-converter), on the TEST stack only.
//
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js seed      # three more people in the test gateway (member, manager, second member)
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js setup     # the admin: access list, departments, budgets, publish, samples, data sheets
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js stranger  # a member: catalog → sample → install (own tax ID) → self-test files → a real run
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js signoff   # share with the manager (may review) → the manager signs off → stamped files
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js budget    # the member's month runs out → the run waits, names who to ask → raised → finishes
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js disable   # the admin switches an agent off → refused with the reason → back on
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js problem   # "report a problem" → the admins' list and the runner's gauge → closed
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js rail      # pins, recently used, waiting counts, collapsed state
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js spend     # spend per person and per agent
//   SMOKE_TARGET=test node scripts/probes/AM3/am3.js cleanup   # remove what the probe made (installs by id, the extra people)
//
// Uses the smoke suite's helpers (token minting from the test chat-edge,
// never printed). Results go to results/e2e/.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { execFileSync } = require("child_process");
const L = require("../../smoke/lib.js");

if (L.TARGET !== "test") {
  console.error("AM3 probes run on the test stack only (SMOKE_TARGET=test)");
  process.exit(2);
}
const RES = path.join(__dirname, "results", "e2e");
fs.mkdirSync(RES, { recursive: true });
const PKG = path.join(L.ROOT, "backend", "agent_market", "packages");
const FIXTURES = path.join(L.ROOT, "backend", "agent_market", "fixtures");

// The people: the seeded owner is the organisation's admin (AGENT_MARKET_ADMIN_EMAILS on the test chat-edge).
const TENANT = L.TS_USER.tenant;
const PEOPLE = {
  admin: L.TS_USER,
  member: { id: "a3a30000-0000-4000-8000-000000000021", email: "am3-member@fix128.test", tenant: TENANT, role: "member", name: "สมหญิง ทดลองใช้" },
  manager: { id: "a3a30000-0000-4000-8000-000000000022", email: "am3-manager@fix128.test", tenant: TENANT, role: "member", name: "สมชาย ผู้จัดการ" },
  member2: { id: "a3a30000-0000-4000-8000-000000000023", email: "am3-member2@fix128.test", tenant: TENANT, role: "member", name: "มานี บัญชี" },
};
const sessions = {};
const sess = (who) => (sessions[who] ||= L.bearerSession(L.mintPortalToken(PEOPLE[who])));
const EDGE = () => `${L.API_BASE.edge}/api/v1/agent-market`;
async function call(who, p, init = {}) {
  return L.http(EDGE() + p, { timeoutMs: 60000, ...init,
    headers: { "Content-Type": "application/json", ...sess(who).headers, ...(init.headers || {}) } });
}
async function j(who, p, init = {}) {
  const r = await call(who, p, init);
  if (r.status >= 400) throw new Error(`${who} ${init.method || "GET"} ${p} → ${r.status} ${r.text.slice(0, 400)}`);
  return r.json;
}
const send = (who, method, p, body) => j(who, p, { method, body: JSON.stringify(body || {}) });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const saveJSON = (name, obj) => fs.writeFileSync(path.join(RES, name), JSON.stringify(obj, null, 1));
const state = () => { try { return JSON.parse(fs.readFileSync(path.join(RES, "state.json"), "utf8")); } catch { return {}; } };
const remember = (k, v) => { const s = state(); s[k] = v; saveJSON("state.json", s); };
const check = (ok, what) => { log(`${ok ? "PASS" : "FAIL"} ${what}`); if (!ok) process.exitCode = 1; return ok; };

async function upload(who, installId, file) {
  const fd = new FormData();
  fd.append("file", new Blob([fs.readFileSync(file)]), path.basename(file));
  const r = await fetch(`${EDGE()}/installs/${installId}/files`, { method: "POST", headers: { ...sess(who).headers }, body: fd });
  const t = await r.json();
  if (r.status >= 400) throw new Error(`upload ${path.basename(file)}: ${r.status} ${JSON.stringify(t)}`);
  return t.file;
}
async function waitRun(who, runId, pred, timeoutS = 600) {
  const t0 = Date.now();
  let last = "";
  for (;;) {
    const d = await j(who, `/runs/${runId}`);
    const s = `${d.run.status}${d.run.status_reason ? "/" + d.run.status_reason : ""}`;
    if (s !== last) { log(`run ${runId.slice(0, 8)} ${s}`); last = s; }
    if (pred(d)) return d;
    if ((Date.now() - t0) / 1000 > timeoutS) throw new Error(`timeout waiting run ${runId}: ${s}`);
    await L.wait(1000);
  }
}
const terminal = (d) => ["succeeded", "failed", "cancelled"].includes(d.run.status);
const parked = (d) => d.run.status === "awaiting_input";
const openCards = (d) => (d.clarifications || []).filter((c) => c.status === "open");
async function finishWithDefaults(who, runId) {
  let d = await waitRun(who, runId, (x) => parked(x) || terminal(x));
  for (let i = 0; i < 8 && parked(d) && !String(d.run.status_reason).startsWith("budget_"); i++) {
    const cards = openCards(d);
    for (const c of cards) {
      const answers = {};
      for (const q of c.questions) answers[q.key] = q.default_label ? { labels: [q.default_label] } : { skipped: true };
      await send(who, "POST", `/cards/${c.id}/answer`, { answers });
    }
    const seen = new Set(cards.map((c) => c.id));
    d = await waitRun(who, runId, (x) => terminal(x) || (parked(x) && openCards(x).some((c) => !seen.has(c.id))));
  }
  return d;
}
async function download(who, url, file) {
  const r = await fetch(url, { headers: { ...sess(who).headers } });
  const buf = Buffer.from(await r.arrayBuffer());
  if (file) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, buf);
  }
  return { status: r.status, bytes: buf.length, buf };
}
// the text of an OOXML file's parts (python's zipfile on the host)
function ooxmlText(file) {
  const py = "import sys,zipfile,re;z=zipfile.ZipFile(sys.argv[1]);print('\\n'.join(re.sub(r'<[^>]+>',' ',z.read(n).decode('utf-8','ignore')) for n in z.namelist() if n.endswith('.xml')))";
  return execFileSync(process.env.PYTHON || "python", ["-c", py, file], { encoding: "utf8", maxBuffer: 64 << 20, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
}

const cmds = {
  // three more people in the test gateway (portal_users) — test stack only
  async seed() {
    for (const who of ["member", "manager", "member2"]) {
      const p = PEOPLE[who];
      L.gsql(`insert into portal_users (id, email, password_hash, display_name, credit_balance, credit_used, plan, status,
          email_verified, created_at, updated_at, tenant_id, tenant_role, token_version)
        values ('${p.id}', '${p.email}', '!', '${p.name}', 1000000, 0, 'free', 'active', true, now(), now(), '${TENANT}', 'member', 0)
        on conflict (id) do nothing`);
      log(`seeded ${who} ${p.email}`);
    }
  },

  async setup() {
    const out = {};
    const av = await j("admin", "/availability");
    check(av.admin === true, `the seeded owner is the organisation's admin (access=${av.access})`);
    out.org = (await send("admin", "PUT", "/admin/org", { access: "list", domains: ["fix128.test"], contact_name: "ผู้ดูแลทดสอบ",
      contact_email: L.TS_USER.email, user_monthly_credits: 3000000 })).org;
    await send("admin", "PUT", `/admin/departments/${encodeURIComponent("บัญชี")}`, { monthly_credits: 6000000, contact_name: "หัวหน้าบัญชี",
      contact_email: PEOPLE.manager.email });
    for (const who of ["member", "member2"]) {
      await send("admin", "PUT", `/admin/members/${PEOPLE[who].email}`, { display_name: PEOPLE[who].name, department: "บัญชี", role: "member" });
    }
    const avm = await j("member", "/availability");
    check(avm.available === true && avm.role === "member", "the member is in");
    const avg = await j("manager", "/availability");
    check(avg.available === false, "the manager is not on the list (he reads shared runs only)");
    const cat = await j("admin", "/admin/catalog");
    for (const a of cat.agents) {
      if (a.status !== "published") await send("admin", "PUT", `/admin/catalog/${a.slug}`, { status: "published" });
    }
    // the runner makes every released version's sample on its own; wait for them
    const t0 = Date.now();
    let agents = [];
    for (;;) {
      agents = (await j("member", "/catalog")).agents;
      const pending = agents.filter((a) => !a.sample || !["succeeded", "failed", "cancelled"].includes(a.sample.status));
      if (!pending.length || Date.now() - t0 > 900e3) break;
      log(`samples pending: ${pending.map((a) => a.slug).join(", ")}`);
      await L.wait(10000);
    }
    out.samples = {};
    for (const a of agents) {
      const s = await j("member", `/catalog/${a.slug}/sample`);
      const files = [];
      for (const x of s.artifacts) {
        const dl = await download("member", `${EDGE()}/sample-artifacts/${x.id}/download`, path.join(RES, "samples", a.slug, x.name));
        files.push({ name: x.name, bytes: dl.bytes, status: dl.status });
      }
      const inputs = [];
      for (const f of s.files) {
        const dl = await download("member", `${EDGE()}/sample-files/${f.id}/download`, path.join(RES, "samples", a.slug, "inputs", f.name));
        inputs.push({ name: f.name, bytes: dl.bytes, status: dl.status });
      }
      const ds = await j("member", `/catalog/${a.slug}/datasheet`);
      out.samples[a.slug] = { version: s.sample.version, status: s.sample.status, credits: s.sample.credits, seconds: s.sample.seconds,
        outputs: files, inputs, models: ds.models, no_model: ds.no_model, storage: ds.storage };
      check(s.sample.status === "succeeded" && files.length > 0 && files.every((f) => f.status === 200),
        `${a.slug} ${s.sample.version}: a real sample (${files.map((f) => f.name).join(", ")}) in ${Math.round(s.sample.seconds || 0)} s, ${Math.round(s.sample.credits)} credits`);
    }
    saveJSON("setup.json", out);
  },

  // A member who never saw it: catalog → sample → install with their OWN company → self-test → a real round.
  async stranger() {
    const out = {};
    const detail = await j("member", "/catalog/sran.invoice-processing");
    out.guide = !!(detail.manifest && detail.manifest.guide);
    check(out.guide, `the invoice agent's page has a guide for a stranger (v${detail.version})`);
    // their own company (not the sample's): the self-test must still ask nothing
    const r = await send("member", "POST", "/installs", { slug: "sran.invoice-processing", idempotency_key: `am3-${Date.now()}`, self_test: true,
      settings: { company: "บริษัท ของฉันเอง จำกัด", company_tax_id: "0105556012341", company_branch: "00000" } });
    remember("member_invoice", r.install.id);
    const st = await waitRun("member", r.self_test_run_id, (d) => terminal(d) || parked(d));
    out.self_test = { status: st.run.status, cards: (st.clarifications || []).length, artifacts: st.artifacts.map((a) => a.name) };
    check(st.run.status === "succeeded" && (st.clarifications || []).length === 0,
      `the self-test with the member's own tax ID asks nothing and delivers ${st.artifacts.length} file(s)`);
    // a real round: two digital documents of their own
    const ids = [];
    for (const f of ["d01_clean_full_tax_invoice.pdf", "d05_wht_service_invoice.pdf"]) ids.push((await upload("member", r.install.id, path.join(FIXTURES, "e2", "docs", f))).id);
    const m = await send("member", "POST", `/installs/${r.install.id}/messages`, { text: "[AM3] ทำทะเบียนซื้อ", file_ids: ids, client_id: crypto.randomUUID() });
    const d = await finishWithDefaults("member", m.run_id);
    out.run = { id: m.run_id, status: d.run.status, reason: d.run.status_reason, cards: (d.clarifications || []).length,
      credits: d.run.credits_spent, artifacts: d.artifacts.map((a) => a.name) };
    remember("member_run", m.run_id);
    check(d.run.status === "succeeded", `the member's own round: ${d.run.status} (${out.run.cards} card(s)) → ${out.run.artifacts.join(", ")}`);
    saveJSON("stranger.json", out);
  },

  async signoff() {
    const runId = state().member_run;
    if (!runId) throw new Error("run `stranger` first");
    const out = {};
    const bad = await call("member", `/runs/${runId}/shares`, { method: "POST", body: JSON.stringify({ email: "someone@elsewhere.test" }) });
    check(bad.status === 400 && bad.json?.error?.code === "NOT_IN_ORG", `sharing outside the organisation is refused: ${bad.json?.error?.message}`);
    const sh = await send("member", "POST", `/runs/${runId}/shares`, { email: PEOPLE.manager.email, can_review: true, expires_days: 30 });
    remember("share", sh.share.id);
    const nosy = await call("member2", `/shared/${sh.share.id}`);
    check(nosy.status === 403, `a colleague the run was not shared with gets ${nosy.status}: ${nosy.json?.error?.message}`);
    const view = await j("manager", `/shared/${sh.share.id}`);
    check(view.artifacts.length > 0, `the manager (not on the access list) opens it: ${view.artifacts.map((a) => a.name).join(", ")}`);
    const rev = await send("manager", "POST", `/shared/${sh.share.id}/review`, { name: PEOPLE.manager.name, note: "[AM3] ตรวจกับใบกำกับต้นฉบับแล้ว" });
    const job = rev.job.id;
    const t0 = Date.now();
    let reviews;
    for (;;) {
      reviews = (await j("member", `/runs/${runId}/reviews`)).reviews;
      const last = reviews[reviews.length - 1];
      if (["succeeded", "failed", "cancelled"].includes(last.stamp_status) || Date.now() - t0 > 300e3) break;
      await L.wait(2000);
    }
    const last = reviews[reviews.length - 1];
    out.review = { job, stamp_status: last.stamp_status, versions: last.artifact_versions };
    check(last.stamp_status === "succeeded", `the stamp job: ${last.stamp_status} → ${JSON.stringify(last.artifact_versions)}`);
    const d = await j("member", `/runs/${runId}`);
    const stamped = d.artifacts.filter((a) => /_reviewed\./.test(a.name));
    out.files = [];
    for (const a of stamped) {
      const f = path.join(RES, "signoff", a.name);
      const dl = await download("member", `${EDGE()}/artifacts/${a.id}/download`, f);
      let found = null;
      if (/\.(docx|pptx|xlsx)$/.test(a.name)) found = ooxmlText(f).includes(PEOPLE.manager.name);
      out.files.push({ name: a.name, bytes: dl.bytes, reviewer_on_face: found, validation_review: !!(a.validation || {}).review });
      check(found !== false, `${a.name}: the reviewer's name is on the file`);
    }
    check(stamped.length > 0, `${stamped.length} stamped file(s); the unsigned ones kept: ${d.artifacts.filter((a) => !/_reviewed\./.test(a.name)).map((a) => a.name).join(", ")}`);
    saveJSON("signoff.json", out);
  },

  async budget() {
    const inst = state().member_invoice;
    if (!inst) throw new Error("run `stranger` first");
    const out = {};
    await send("admin", "PUT", `/admin/members/${PEOPLE.member.email}`, { monthly_credits: 1 });
    const ids = [(await upload("member", inst, path.join(FIXTURES, "e2", "docs", "d02_classic_hyphen_taxid.pdf"))).id];
    const m = await send("member", "POST", `/installs/${inst}/messages`, { text: "[AM3] ทำรอบใหม่ งบหมด", file_ids: ids, client_id: crypto.randomUUID() });
    let d = await waitRun("member", m.run_id, (x) => parked(x) || terminal(x));
    out.parked = { status: d.run.status, reason: d.run.status_reason };
    check(d.run.status === "awaiting_input" && d.run.status_reason === "budget_user", `the run waits for budget: ${d.run.status}/${d.run.status_reason}`);
    const th = await j("member", `/installs/${inst}/thread`);
    const notice = th.items.filter((i) => i.kind === "notice" && i.payload.action === "resume_budget").pop();
    out.notice = notice && notice.payload.text;
    check(!!notice && /ผู้ดูแลทดสอบ/.test(notice.payload.text), `the notice names who to ask: ${out.notice}`);
    const again = await call("member", `/runs/${m.run_id}/resume`, { method: "POST" });
    check(again.status === 409, `"ลองต่อ" while it is still out: ${again.json?.error?.message}`);
    const raised = await send("admin", "PUT", `/admin/members/${PEOPLE.member.email}`, { monthly_credits: null });
    check(raised.resumed >= 1, `raising the budget resumes it (${raised.resumed})`);
    d = await finishWithDefaults("member", m.run_id);
    out.after = { status: d.run.status };
    check(d.run.status === "succeeded", `after the budget was raised: ${d.run.status}`);
    saveJSON("budget.json", out);
  },

  async disable() {
    const inst = state().member_invoice;
    const out = {};
    const off = await send("admin", "PUT", "/admin/catalog/sran.invoice-processing", { status: "disabled", reason: "[AM3] ทดสอบการปิดชั่วคราว" });
    out.stopped = off.stopped_runs;
    const r = await call("member", `/installs/${inst}/messages`, { method: "POST", body: JSON.stringify({ text: "ทำรอบใหม่", client_id: crypto.randomUUID() }) });
    out.refused = { status: r.status, message: r.json?.error?.message };
    check(r.status === 409 && /ทดสอบการปิดชั่วคราว/.test(r.json?.error?.message || ""), `a disabled agent refuses with the reason: ${out.refused.message}`);
    const runs = await j("member", `/installs/${inst}/runs`);
    check(runs.runs.length > 0, `history stays readable (${runs.runs.length} runs)`);
    await send("admin", "PUT", "/admin/catalog/sran.invoice-processing", { status: "published" });
    saveJSON("disable.json", out);
  },

  async problem() {
    const runId = state().member_run;
    const out = {};
    const p = await send("member", "POST", `/runs/${runId}/problem`, { text: "[AM3] ยอดรวมใบที่สองไม่ตรงกับเอกสาร", page: `/chat/agents/runs/${runId}` });
    out.report = { ref: p.report.ref, step: p.report.step, status: p.report.run_status };
    check(!!p.report.ref && /#/.test(p.message), `reported: ${p.message}`);
    const list = await j("admin", "/admin/problems?status=open");
    check(list.problems.some((x) => x.id === p.report.id), "the admins see it");
    const m = await L.http(L.TARGET === "test" ? "http://127.0.0.1:18095/metrics" : "http://127.0.0.1:28095/metrics");
    out.gauge = (/agent_market_problem_reports_open (\d+)/.exec(m.text || "") || [])[1];
    check(Number(out.gauge) >= 1, `the runner's gauge (the pager's alert reads it): ${out.gauge}`);
    await send("admin", "POST", `/admin/problems/${p.report.id}/close`, { note: "[AM3] probe" });
    saveJSON("problem.json", out);
  },

  async rail() {
    const inst = state().member_invoice;
    await send("member", "PUT", `/installs/${inst}/pin`);
    await send("member", "PUT", "/prefs", { rail_collapsed: true });
    const r = await j("member", "/rail");
    const row = r.installs.find((i) => i.id === inst);
    check(!!row.pinned_at && r.prefs.rail_collapsed === true, `pinned and collapsed, remembered (${r.installs.length} agent(s), ${r.waiting_total} waiting)`);
    await send("member", "PUT", "/prefs", { rail_collapsed: false });
    saveJSON("rail.json", r);
  },

  async spend() {
    const s = await j("admin", "/admin/spend?days=30");
    for (const u of s.by_user) log(`  ${u.email || u.key}: ${u.runs} runs (${u.succeeded} ok, ${u.failed} failed, ${u.waiting} waiting), ${Math.round(u.credits)} credits`);
    for (const a of s.by_agent) log(`  ${a.key}: ${a.runs} runs by ${a.users} people, ${Math.round(a.credits)} credits`);
    check(s.by_user.length >= 1, `spend per person (${s.by_user.length}) and per agent (${s.by_agent.length}); catalog samples ${Math.round(s.catalog_samples_credits)} credits`);
    saveJSON("spend.json", s);
  },

  async cleanup() {
    const s = state();
    for (const k of ["member_invoice"]) {
      if (s[k]) {
        const r = await call("member", `/installs/${s[k]}`, { method: "DELETE" });
        log(`removed ${k} ${s[k]}: ${r.status}`);
      }
    }
    for (const who of ["member", "manager", "member2"]) {
      L.gsql(`delete from portal_users where id = '${PEOPLE[who].id}'`);
    }
    log("people removed from the test gateway");
  },
};

const cmd = process.argv[2];
if (!cmds[cmd]) {
  console.log("usage: am3.js seed | setup | stranger | signoff | budget | disable | problem | rail | spend | cleanup");
  process.exit(2);
}
cmds[cmd]().catch((e) => {
  console.error(e);
  process.exit(1);
});
