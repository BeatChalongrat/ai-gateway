// AM1 — area "agent_market": the feature is absent when its flag is off,
// and healthy when it is on.
//
// flag   .env AGENT_MARKET_ENABLED
//   off  → web-chat /api/agent-market says {enabled:false} and chat-edge has NO
//          route (/api/v1/agent-market/availability → 404): flag off means absent.
//   on   → availability answers 200 for the smoke account, and the runner's
//          heartbeat is fresh (skip, not fail, when the profile is not running).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const L = require("./lib");

// The runner's container and health port per target (docker-compose.agent-market.yml
// defaults on live; the test stack's runner is started with AM_CONTAINER_NAME /
// AM_RUNNER_PORT). The flag is read like every smoke setting: the shell's
// environment first (the test stack is recreated with it exported), then .env.
const RUNNER = L.TARGET === "test"
  ? { name: "sran-test-agent-runner", health: "http://127.0.0.1:18095/healthz", model: "http://127.0.0.1:18095/model" }
  : { name: "sran-agent-runner", health: "http://127.0.0.1:28095/healthz", model: "http://127.0.0.1:28095/model" };

// AM15 — the agent the smoke speaks to, and the sentence: an order its documents can be put in, so the
// agent's model is asked to read it (words that are only thanks are not put to a model at all).
const PACKAGES = path.join(L.ROOT, "backend", "agent_market", "packages");
const TURN_AGENT = {
  slug: "sran.bank-reconciliation", dir: "bank_reconciliation", ask: "เรียงตามวันที่",
  files: ["sample-data/statement_2569-09.xlsx", "sample-data/สมุดบัญชีเงินฝาก_2569-09.xlsx"],
};
const AM = () => `${L.API_BASE.edge}/api/v1/agent-market`;

/**
 * agent_market.model's verdict over the runner's word ({asked, answered, served, ms, error}): the model
 * asked answered — or the FAIL names it, and who answered in its place or that nobody did.
 */
function modelWordVerdict(w, status = "200") {
  if (!w || !w.asked) return { ok: false, detail: `the runner's /model page → ${status}: no word` };
  const turns = [{ asked: w.asked, served: w.served || null, ms: Math.round(w.ms || 0), expect_ms: L.TURN_EXPECT_MS }];
  if (!w.answered) {
    if (w.served) return { ok: false, turns, detail: `asked ${w.asked}, answered ${w.served}, waited ${L.secondsText(w.ms || 0)} — the runner's own word` };
    return { ok: false, turns, detail: `asked ${w.asked}, no answer after ${L.secondsText(w.ms || 0)}: ${String(w.error || "no reason given").slice(0, 160)} — the runner's own word` };
  }
  const who = L.whoAnswered({ asked: w.asked, served: w.served, ms: w.ms || 0 });
  return { ok: who.ok, slow: who.ok && who.slow, turns, detail: `${who.text} — the runner's own word${w.error ? ` (${String(w.error).slice(0, 80)})` : ""}` };
}

/**
 * agent_market.turn's verdict over the reply's run as the run page gives it ({run, steps}) and the time
 * the suite waited. The step that read the sentence names the models it was allowed and the one that
 * read it. A reply that waits for its model, words that came back unread, or another model's reading
 * is the FAIL — said first, naming the model asked.
 */
function agentTurnVerdict(ended, waitedMs) {
  const run = ended?.run;
  const step = (ended?.steps || []).filter((s) => s.name === "instructions").pop();
  const input = step?.input && typeof step.input === "object" ? step.input : {};
  const out = step?.output && typeof step.output === "object" ? step.output : {};
  const asked = (Array.isArray(input.models) && input.models[0]) || null;
  const took = step?.started_at && step?.finished_at ? Date.parse(step.finished_at) - Date.parse(step.started_at) : waitedMs;
  const served = out.model || null;
  const turns = [{ asked, served, ms: Math.round(took), expect_ms: L.TURN_EXPECT_MS }];
  const state = `reply ${String(run?.id || "").slice(0, 8)} ${run?.status || "did not end"}${run?.status_reason ? ` (${run.status_reason})` : ""} after ${L.secondsText(waitedMs)}`;
  if (!run || !step) return { ok: false, turns, detail: `asked ${asked || "the agent's model"}, no answer: the sentence was not read — ${state}` };
  if (!asked) return { ok: false, turns, detail: `the agent was allowed no model to read the sentence — ${state}` };
  if (run.status === "awaiting_input" && run.status_reason === "model_unreachable") {
    return { ok: false, turns, detail: `asked ${asked}, no answer: the agent waits for its model — ${state}` };
  }
  if (out.unread) return { ok: false, turns, detail: `asked ${asked}, no answer: the sentence came back unread after ${out.tries || 1} reading(s) — ${state}` };
  if (step.status !== "succeeded") return { ok: false, turns, detail: `asked ${asked}, no answer: the reading ${step.status} — ${state}` };
  const who = L.whoAnswered({ asked, served, ms: took });
  if (!who.ok) return { ok: false, turns, detail: `${who.text} — ${state}` };
  const read = Array.isArray(out.apply) ? out.apply.join(",") : "";
  if (run.status !== "succeeded") return { ok: false, turns, detail: `${who.text}, but the reply did not finish — ${state}` };
  return { ok: true, slow: who.slow, turns, detail: `${who.text}, read as "${read || "nothing to do"}" — ${state}` };
}

module.exports = async function agentMarket(R, session) {
  const on = String(L.env("AGENT_MARKET_ENABLED", "false")).toLowerCase() === "true";
  const edge = `${L.API_BASE.edge}/api/v1/agent-market/availability`;
  // AM15: the routes a person's screen calls, with the smoke's session
  const am = (method, p, body) => L.tryHttp(AM() + p, { method, timeoutMs: 60000, headers: { "Content-Type": "application/json", ...(session ? session.headers : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const upload = async (install, file) => {
    const form = new FormData();
    form.append("file", new Blob([fs.readFileSync(file)]), path.basename(file));
    const r = await fetch(`${AM()}/installs/${install}/files`, { method: "POST", headers: session.headers, body: form, signal: AbortSignal.timeout(120000) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.file?.id) throw new Error(`upload ${path.basename(file)} → ${r.status}`);
    return j.file.id;
  };
  /** A run as the run page gives it, once it has ended or waits (null: no such run; the last reading when time ran out). */
  const runEnds = async (id, ms) => {
    if (!id) return null;
    const t0 = Date.now();
    let last = null;
    for (;;) {
      const r = await am("GET", `/runs/${id}`);
      if (r.status === 200 && r.json?.run) last = r.json;
      if (last && ["succeeded", "failed", "cancelled", "awaiting_input"].includes(last.run.status)) return last;
      if (Date.now() - t0 > ms) return last;
      await L.wait(2000);
    }
  };

  await R.run("agent_market.flag", async () => {
    const headers = session ? session.headers : {};
    const r = await L.http(edge, { headers });
    if (!on) {
      return { ok: r.status === 404, detail: `flag off: chat-edge ${r.status} (want 404 — no route registered)` };
    }
    if (!session) return { skip: true, detail: "flag on but no signed-in session in this run" };
    return { ok: r.status === 200, detail: `flag on: availability ${r.status} available=${r.json?.available} runner healthy=${r.json?.runner?.healthy}` };
  });

  // AM1b: with an allowlist that leaves the smoke account out (the owner-only
  // pilot), that account must see no agent surface: availability says no and
  // the catalog refuses it (the web rail renders nothing when available=false).
  // AM3: the list lives in the database now (the organisation's admins keep
  // it), so the check asks the gate itself: whatever it says about this
  // account (access mode, on the list, admin) must match what it lets through.
  await R.run("agent_market.not_allowlisted", async () => {
    if (!on) return { skip: true, detail: "flag off (the flag check covers it)" };
    if (!session) return { skip: true, detail: "no signed-in session in this run" };
    const a = await L.http(edge, { headers: session.headers });
    const c = await L.http(`${L.API_BASE.edge}/api/v1/agent-market/catalog`, { headers: session.headers });
    const j = a.json || {};
    const shouldBeIn = j.admin === true || j.access === "everyone" || (j.access === "list" && j.member === true);
    if (a.status !== 200) return { ok: false, detail: `availability ${a.status}` };
    if (shouldBeIn) {
      return { ok: j.available === true && c.status === 200,
        detail: `the organisation lets this account in (access=${j.access} member=${j.member} admin=${j.admin}): available=${j.available}, catalog ${c.status}` };
    }
    return { ok: j.available === false && c.status === 403,
      detail: `not on the organisation's list (access=${j.access} member=${j.member}): availability available=${j.available}, catalog ${c.status} (want false / 403)` };
  });

  // AM7 — what affects anyone else lives in the admin app. chat-edge's admin door (behind its bearer)
  // exists when the flag is on and is absent when it is off, and refuses a call that names no admin;
  // the chat app's own API has no admin route at all; the backend's audit door answers only chat-edge.
  await R.run("agent_market.admin_door", async () => {
    const key = L.env("CHAT_EDGE_ADMIN_API_KEY");
    if (!key) return { skip: true, detail: "no CHAT_EDGE_ADMIN_API_KEY in .env" };
    const r = await L.http(`${L.API_BASE.edge}/api/v1/admin/agent-market/me`, { headers: { Authorization: `Bearer ${key}` } });
    if (!on) return { ok: r.status === 404, detail: `flag off: the admin door → ${r.status} (want 404 — absent)` };
    return { ok: r.status === 403 && r.json?.error?.code === "NOT_ADMIN", detail: `flag on: the admin door, no admin named → ${r.status} ${r.json?.error?.code || ""} (want 403 NOT_ADMIN)` };
  });

  await R.run("agent_market.no_admin_in_chat", async () => {
    if (!on) return { skip: true, detail: "flag off (the flag check covers it)" };
    if (!session) return { skip: true, detail: "no signed-in session in this run" };
    const bad = [];
    for (const p of ["/admin/org", "/admin/catalog", "/admin/runs", "/admin/problems", "/admin/mail"]) {
      const r = await L.http(`${L.API_BASE.edge}/api/v1/agent-market${p}`, { headers: session.headers });
      if (r.status !== 404) bad.push(`${p} ${r.status}`);
    }
    return { ok: bad.length === 0, detail: bad.length ? `the chat app's API still answers: ${bad.join(", ")}` : "the chat app's API has no Agent Market admin route (5 of 5 → 404)" };
  });

  await R.run("agent_market.audit_door", async () => {
    const base = L.T.plain.backend;
    if (!base) return { skip: true, detail: "no backend loopback door on this target" };
    const r = await L.tryHttp(`${base}/internal/admin-audit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries: [] }) });
    return { ok: r.status === 401, detail: `${base}/internal/admin-audit with no service token → ${r.status || r.error} (want 401: only chat-edge writes Agent Market's lines)` };
  });

  await R.run("agent_market.web_flag", async () => {
    if (!L.T.hasWebApps) return { skip: true, detail: "no web-chat on this target" };
    const web = L.T.front?.web || L.T.plain.web;
    const r = await L.http(`${web}/api/agent-market`);
    if (r.status !== 200) return { ok: false, detail: `web-chat /api/agent-market ${r.status}` };
    return { ok: r.json?.enabled === on, detail: `web-chat says enabled=${r.json?.enabled} (.env says ${on})` };
  });

  await R.run("agent_market.runner", async () => {
    if (!on) {
      const running = L.containerRunning(RUNNER.name);
      return { ok: !running, detail: running ? `flag off but ${RUNNER.name} is running` : "flag off: no runner container" };
    }
    if (!L.containerRunning(RUNNER.name)) return { skip: true, detail: "profile agent-market not running" };
    const r = await L.http(RUNNER.health);
    return { ok: r.status === 200 && r.json?.sandbox === true, detail: `runner ${r.status} sandbox=${r.json?.sandbox} model=${r.json?.model} engine=${r.json?.engine}` };
  });

  // AM15 — who answers the agents' model calls. The runner's /healthz says "model: true" when a model
  // is configured, and said so through the hours of 9 Oct 2026 when the in-house model answered
  // nobody. Its /model page asks the model one word — through the client, key and gateway every step
  // of a run uses — and says who answered: an answer from another model, or none, is the FAIL.
  await R.run("agent_market.model", async () => {
    if (!on) return { skip: true, detail: "flag off (the flag check covers it)" };
    if (!L.containerRunning(RUNNER.name)) return { skip: true, detail: "profile agent-market not running" };
    const r = await L.tryHttp(RUNNER.model, { timeoutMs: 100000 });
    if (r.status === 404) return { ok: false, detail: "the runner has no /model page (a runner from before AM15): who answers the agents' model calls cannot be seen" };
    return modelWordVerdict(r.status === 200 ? r.json : null, `${r.status || r.error}`);
  });

  // AM15 — and one turn of an agent the way a person makes it: a sentence typed to an agent whose run
  // has finished, read by the agent's model into what its documents can be asked. The run's own record
  // says which model read it. (An account the organisation has not let in can make no such turn: the
  // check says so and skips — on such a target agent_market.model above is what watches the model.)
  await R.run("agent_market.turn", async () => {
    if (!on) return { skip: true, detail: "flag off (the flag check covers it)" };
    if (!session) return { skip: true, detail: "no signed-in session in this run" };
    if (!L.containerRunning(RUNNER.name)) return { skip: true, detail: "profile agent-market not running" };
    const a = await am("GET", "/availability");
    if (a.json?.available !== true) {
      return { skip: true, detail: `this account is not let into Agent Market here (access=${a.json?.access} member=${a.json?.member}): it can make no agent turn — agent_market.model is the check of the agents' model on this target` };
    }
    // one install, the same every run (the install's own idempotency key), with one finished run to speak to
    const made = await am("POST", "/installs", { slug: TURN_AGENT.slug, idempotency_key: "smoke-agent-turn-1" });
    const install = made.json?.install?.id || made.json?.id;
    if (!install) return { ok: false, detail: `the smoke's own copy of ${TURN_AGENT.slug} could not be installed: ${made.status} ${made.json?.error?.code || made.text.slice(0, 120)}` };
    const runs = (await am("GET", `/installs/${install}/runs`)).json?.runs || [];
    if (!runs.some((x) => x.kind === "run" && x.status === "succeeded")) {
      // the first time only: a run from the package's own sample files, with no words typed (so no model is asked)
      const ids = [];
      for (const rel of TURN_AGENT.files) ids.push(await upload(install, path.join(PACKAGES, TURN_AGENT.dir, rel)));
      const first = await am("POST", `/installs/${install}/messages`, { file_ids: ids });
      const ended = await runEnds(first.json?.run_id, 300000);
      if (ended?.run?.status !== "succeeded") return { ok: false, detail: `the smoke's first run of ${TURN_AGENT.slug} did not finish: ${ended?.run?.status || "no run"} ${ended?.run?.status_reason || ""}` };
    }
    const t0 = Date.now();
    const words = `[SMOKE ${crypto.randomUUID().slice(0, 6)}] ${TURN_AGENT.ask}`;
    const sent = await am("POST", `/installs/${install}/messages`, { text: words });
    let reply = sent.json?.run_id || null;
    if (!reply && sent.json?.route === "intent") {
      // A sentence typed after a finished run is never guessed at: the screen asks whether it is about
      // that run or starts a new one. The person's second click — it is about the run that finished.
      const item = sent.json.item?.id;
      const chips = ((await am("GET", `/installs/${install}/thread`)).json?.items || []).filter((i) => i.kind === "intent" && i.payload?.item_id === item);
      const chip = chips[chips.length - 1];
      if (!chip) return { ok: false, detail: "the screen was to ask what the sentence is about, and the thread holds no such question" };
      const chosen = await am("POST", `/intents/${chip.id}`, { choice: "about_last" });
      reply = chosen.json?.run_id || null;
      if (!reply) {
        const typed = ((await am("GET", `/installs/${install}/thread`)).json?.items || []).find((i) => i.id === item);
        reply = typed?.payload?.run_id || typed?.run_id || null;
      }
      if (!reply) return { ok: false, detail: `"about the run that finished" started no reply: ${chosen.status} ${chosen.json?.error?.code || chosen.text.slice(0, 120)}` };
    }
    if (!reply) return { ok: false, detail: `the sentence was not taken: ${sent.status} route=${sent.json?.route || "-"} ${sent.json?.error?.code || ""}` };
    const ended = await runEnds(reply, 180000);
    const verdict = agentTurnVerdict(ended, Date.now() - t0);
    // a reply left waiting for a model that is away is cancelled: the suite leaves nothing waiting behind it
    if (ended?.run?.status === "awaiting_input") await am("POST", `/runs/${reply}/cancel`).catch(() => null);
    return verdict;
  });
};
module.exports.modelWordVerdict = modelWordVerdict;
module.exports.agentTurnVerdict = agentTurnVerdict;
