// node --test scripts/smoke/who.test.js
// A turn of the smoke says who was asked and who answered. On 9 Oct 2026 the in-house model stopped
// answering, every turn waited thirty seconds and was answered by another model, and the suite was
// green: these are that day's own lines (scripts/probes/FIX134/results/smoke/smoke_live_20261009T10*),
// held to the verdict they should have had — and the turns of a good day, which must still pass.
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const L = require("./lib.js");
const { webTurnVerdict } = require("./apps.js");
const { completionVerdict, idempotencyVerdict } = require("./api.js");
const { turnVerdict: desktopTurnVerdict } = require("./desktop.js");

const QWEN = "qwen3.8-27b-fp8";
const URL = "https://localhost:8080/v1/chat/completions";
const webTurn = (more) => ({ id: "76c92291-0000-4000-8000-000000000000", http: 200, terminal: "message_complete", code: null, content: "2", ttft: 1689, total: 1706, deleted: 204, served: QWEN, ...more });
const apiCall = (more) => ({ status: 200, ms: 1466, id: "req_ae2e73ff", replayed: null, finish: "stop", content: "ok", model: QWEN, cached: false, code: null, text: "", ...more });

test("the model asked, in good time, is a pass that names it", () => {
  const w = L.whoAnswered({ asked: QWEN, served: QWEN, ms: 1706 });
  assert.equal(w.ok, true);
  assert.equal(w.slow, false);
  assert.match(w.text, /^qwen3\.8-27b-fp8 answered in 1\.7 s$/);
  assert.deepEqual(w.turn, { asked: QWEN, served: QWEN, ms: 1706, expect_ms: L.TURN_EXPECT_MS });
});

test("another model is a failure that names both and the wait", () => {
  const w = L.whoAnswered({ asked: QWEN, served: "deepseek/deepseek-r1", ms: 35386 });
  assert.equal(w.ok, false);
  assert.equal(w.text, "asked qwen3.8-27b-fp8, answered deepseek/deepseek-r1, waited 35.4 s");
});

test("an answer that does not say who gave it is a failure, not a pass", () => {
  for (const served of [null, undefined, "", "  "]) {
    const w = L.whoAnswered({ asked: QWEN, served, ms: 900 });
    assert.equal(w.ok, false, `served=${JSON.stringify(served)}`);
    assert.match(w.text, /does not say which model gave it/);
  }
});

test("a provider in front of the name is the same model; a name that only looks like it is not", () => {
  assert.equal(L.sameModel("sranai/qwen3.8-27b-fp8", QWEN), true);
  assert.equal(L.sameModel("QWEN3.8-27B-FP8", QWEN), true);
  assert.equal(L.sameModel("Pathumma-ThaiLLM-qwen3-8b-think-3.0.0", QWEN), false); // answered api.completion on 9 Oct, 45.6 s
  assert.equal(L.sameModel("qwen3.8-27b-fp8-mini", QWEN), false);
  assert.equal(L.sameModel("xqwen3.8-27b-fp8", QWEN), false);
  assert.equal(L.sameModel("", ""), false);
});

test("the model asked, slower than expected, passes and says its number", () => {
  const w = L.whoAnswered({ asked: QWEN, served: QWEN, ms: 33208 });
  assert.equal(w.ok, true);
  assert.equal(w.slow, true);
  assert.equal(w.text, "qwen3.8-27b-fp8 answered in 33.2 s — expected within 15.0 s");
});

test("web turn of 9 Oct: thirty-three seconds, another model — red, where it was green", () => {
  const v = webTurnVerdict([webTurn({ ttft: 33157, total: 33208, served: "deepseek/deepseek-r1", fallbackFrom: QWEN })], QWEN);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, answered deepseek\/deepseek-r1, waited 33\.2 s — chat 76c92291: http=200 terminal=message_complete/);
  assert.deepEqual(v.turns, [{ asked: QWEN, served: "deepseek/deepseek-r1", ms: 33208, expect_ms: L.TURN_EXPECT_MS }]);
});

test("web turn of a good day passes and names the model", () => {
  const v = webTurnVerdict([webTurn()], QWEN);
  assert.equal(v.ok, true);
  assert.ok(!v.slow);
  assert.match(v.detail, /^qwen3\.8-27b-fp8 answered in 1\.7 s — chat 76c92291/);
});

test("web turn: a first try that ran out and a second answered by the model asked is a pass; by another model, not", () => {
  const out = webTurn({ terminal: "error", code: "output_limit_reached", content: "", served: null, total: 41000 });
  assert.equal(webTurnVerdict([out, webTurn()], QWEN).ok, true);
  const v = webTurnVerdict([out, webTurn({ served: "deepseek-v4-flash" })], QWEN);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, answered deepseek-v4-flash/);
});

test("web turn with no answer, or whose chat was not deleted, fails as before", () => {
  assert.equal(webTurnVerdict([webTurn({ terminal: "error", code: "upstream_unavailable", content: "", served: null })], QWEN).ok, false);
  assert.equal(webTurnVerdict([webTurn({ content: "  " })], QWEN).ok, false);
  assert.equal(webTurnVerdict([webTurn({ deleted: 500 })], QWEN).ok, false);
  assert.match(webTurnVerdict([webTurn({ terminal: null, content: "", served: null })], QWEN).detail, /^asked qwen3\.8-27b-fp8, no answer/);
});

test("web turn that completed without a word on who answered fails", () => {
  const v = webTurnVerdict([webTurn({ served: null })], QWEN);
  assert.equal(v.ok, false);
  assert.match(v.detail, /does not say which model gave it/);
});

test("API call of 9 Oct: 200 from deepseek-r1 after 35 s — red, where it was green", () => {
  const v = completionVerdict([apiCall({ ms: 35386, model: "deepseek/deepseek-r1" })], QWEN, URL);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, answered deepseek\/deepseek-r1, waited 35\.4 s — 200 id=req_ae2e73ff/);
});

test("API call of a good day passes and names the model", () => {
  const v = completionVerdict([apiCall()], QWEN, URL);
  assert.equal(v.ok, true);
  assert.match(v.detail, /^qwen3\.8-27b-fp8 answered in 1\.5 s — 200 id=req_ae2e73ff finish=stop content="ok"$/);
});

test("API call answered out of the gateway's cache is nobody answering now", () => {
  const v = completionVerdict([apiCall({ cached: true, ms: 40 })], QWEN, URL);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, answered out of the gateway's cache — nobody answered now/);
});

test("API call the model cannot be reached for fails, naming the model asked", () => {
  const v = completionVerdict([apiCall({ status: 503, code: "upstream_unavailable", id: null, model: null, content: "", ms: 30210 })], QWEN, URL);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, no answer: .* → 503 upstream_unavailable after 30\.2 s$/);
  assert.equal(v.turns[0].served, null);
});

test("API call that ran out of its budget is asked again of the same model, and says so", () => {
  const out = apiCall({ status: 502, code: "output_limit_reached", id: null, model: null, content: "", ms: 9000 });
  const v = completionVerdict([out, apiCall()], QWEN, URL);
  assert.equal(v.ok, true);
  assert.match(v.detail, /asked again of the same model after: 502 output_limit_reached after 9\.0 s/);
  assert.equal(completionVerdict([out, apiCall({ model: "deepseek-v4-flash" })], QWEN, URL).ok, false);
  assert.equal(completionVerdict([out, out], QWEN, URL).ok, false);
});

test("API call that is a 200 with no content, or no id, fails", () => {
  assert.equal(completionVerdict([apiCall({ content: "" })], QWEN, URL).ok, false);
  assert.equal(completionVerdict([apiCall({ id: null })], QWEN, URL).ok, false);
});

test("idempotency: the first call is a turn like any other; the second must be its replay", () => {
  const first = apiCall({ id: "req_c76a6dd0" });
  const replay = apiCall({ id: "req_c76a6dd0", replayed: "true", ms: 30 });
  assert.equal(idempotencyVerdict(first, replay, QWEN).ok, true);
  assert.equal(idempotencyVerdict(first, apiCall({ id: "req_other", replayed: null }), QWEN).ok, false);
  const rescued = idempotencyVerdict(apiCall({ id: "req_c76a6dd0", model: "deepseek/deepseek-r1", ms: 38374 }), replay, QWEN);
  assert.equal(rescued.ok, false); // 9 Oct: PASS in 38,374 ms
  assert.match(rescued.detail, /^asked qwen3\.8-27b-fp8, answered deepseek\/deepseek-r1, waited 38\.4 s/);
  assert.equal(idempotencyVerdict(apiCall({ cached: true }), replay, QWEN).ok, false);
  assert.equal(idempotencyVerdict(apiCall({ status: 503, code: "upstream_unavailable" }), replay, QWEN).ok, false);
});

test("desktop turn: the assistant row names who answered, and another model is red", () => {
  const base = { chat: "d3c07f3e-0000-4000-8000-000000000000", ended: 3121, text: "2", asked: QWEN };
  const good = desktopTurnVerdict({ ...base, row: `completed|completed|${QWEN}|` });
  assert.equal(good.ok, true);
  assert.match(good.detail, /^qwen3\.8-27b-fp8 answered in 3\.1 s — chat d3c07f3e: stream ended in 3121 ms, assistant row "completed\|completed"/);
  // 9 Oct 10:38Z: PASS, with "…llama-3.3-70b-instruct's rate" in the answer it read off the page
  const rescued = desktopTurnVerdict({ ...base, row: `completed|completed|meta-llama/llama-3.3-70b-instruct|${QWEN}` });
  assert.equal(rescued.ok, false);
  assert.match(rescued.detail, /^asked qwen3\.8-27b-fp8, answered meta-llama\/llama-3\.3-70b-instruct, waited 3\.1 s \(the row says it stood in for qwen3\.8-27b-fp8\)/);
  // a row that does not say who answered cannot pass
  assert.equal(desktopTurnVerdict({ ...base, row: "completed|completed" }).ok, false);
  // and what failed before still fails
  assert.equal(desktopTurnVerdict({ ...base, ended: null, row: "" }).ok, false);
  assert.equal(desktopTurnVerdict({ ...base, row: `failed|provider_error|${QWEN}|` }).ok, false);
  assert.equal(desktopTurnVerdict({ ...base, text: "", row: `completed|completed|${QWEN}|` }).ok, false);
});

test("the report prints SLOW for a slow pass, counts it as a pass and lists it; a failure is never slow", async () => {
  const lines = [];
  const write = process.stdout.write;
  process.stdout.write = (s) => { lines.push(String(s)); return true; };
  let R;
  try {
    R = new L.Report();
    await R.run("webchat.turn", async () => webTurnVerdict([webTurn({ ttft: 33157, total: 33208 })], QWEN));
    await R.run("api.completion", async () => completionVerdict([apiCall({ ms: 35386, model: "deepseek/deepseek-r1" })], QWEN, URL));
    await R.run("api.idempotency", async () => ({ ok: true, detail: "plain" }));
  } finally {
    process.stdout.write = write;
  }
  assert.match(lines[0], /^SLOW webchat\.turn +qwen3\.8-27b-fp8 answered in 33\.2 s — expected within 15\.0 s/);
  assert.match(lines[1], /^FAIL api\.completion +asked qwen3\.8-27b-fp8, answered deepseek\/deepseek-r1, waited 35\.4 s/);
  assert.match(lines[2], /^PASS api\.idempotency/);
  assert.deepEqual(R.counts(), { pass: 2, fail: 1, skip: 0 });
  assert.deepEqual(R.slow().map((c) => c.name), ["webchat.turn"]);
  assert.equal(R.checks[0].status, "PASS");
  assert.deepEqual(R.checks[1].turns, [{ asked: QWEN, served: "deepseek/deepseek-r1", ms: 35386, expect_ms: L.TURN_EXPECT_MS }]);
  assert.equal(R.checks[2].turns, undefined);
});
