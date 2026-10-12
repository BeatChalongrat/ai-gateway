// node --test scripts/smoke/agent_market.test.js
// AM15 — the smoke says who answered an agent's model call too. The runner's own word (/model) and a
// sentence typed to an agent (the run's record of who read it), held to the verdicts they must have:
// the model asked answered — or the line names it, and who answered in its place or that nobody did.
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { modelWordVerdict, agentTurnVerdict } = require("./agent_market.js");

const QWEN = "qwen3.8-27b-fp8";
const AWAY = "gateway 504: {\"error\":{\"code\":\"provider_timeout\",\"message\":\"Provider 'sranai' gave no answer for 'qwen3.8-27b-fp8' within 30 s.\"}}";
const word = (more) => ({ asked: QWEN, answered: true, served: QWEN, ms: 1200, at: "2026-10-12T05:00:00Z", ...more });
const reply = (run, step) => ({
  run: { id: "270afee9-501a-48df-bff4-76d6c1bcc995", kind: "reply", status: "succeeded", status_reason: "", ...run },
  steps: step === null ? [] : [{ name: "instructions", status: "succeeded", input: { text: "[SMOKE] เรียงตามวันที่", models: [QWEN] },
    output: { text: "เรียงตามวันที่", apply: ["sort_by_date"], model: QWEN }, started_at: "2026-10-12T05:00:00Z", finished_at: "2026-10-12T05:00:04.500Z", ...step }],
});

test("the runner's word: the model asked answered", () => {
  const v = modelWordVerdict(word());
  assert.equal(v.ok, true);
  assert.match(v.detail, /^qwen3\.8-27b-fp8 answered in 1\.2 s — the runner's own word$/);
  assert.deepEqual(v.turns[0], { asked: QWEN, served: QWEN, ms: 1200, expect_ms: v.turns[0].expect_ms });
});

test("the runner's word: the model is away — what the test stack's gateway said on 12 Oct 2026", () => {
  const v = modelWordVerdict(word({ answered: false, served: "", ms: 30210, error: AWAY }));
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, no answer after 30\.2 s: gateway 504: .*provider_timeout/);
  assert.equal(v.turns[0].served, null);
});

test("the runner's word: another model answered in its place — both are named", () => {
  const v = modelWordVerdict(word({ answered: false, served: "deepseek-v4-flash", ms: 31000, error: "answered by deepseek-v4-flash instead of qwen3.8-27b-fp8" }));
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, answered deepseek-v4-flash, waited 31\.0 s/);
});

test("the runner's word: a page that says nothing is not a pass, and a slow word says its number", () => {
  assert.equal(modelWordVerdict(null, "500").ok, false);
  assert.equal(modelWordVerdict({}, "200").ok, false);
  const slow = modelWordVerdict(word({ ms: 22000 }));
  assert.equal(slow.ok, true);
  assert.equal(slow.slow, true);
  assert.match(slow.detail, /expected within 15\.0 s/);
  // a runner that says "answered" and names another model is not believed
  assert.equal(modelWordVerdict(word({ served: "deepseek-v4-flash" })).ok, false);
});

test("a sentence typed to an agent: read by the model the agent was allowed", () => {
  const v = agentTurnVerdict(reply(), 9000);
  assert.equal(v.ok, true);
  assert.match(v.detail, /^qwen3\.8-27b-fp8 answered in 4\.5 s, read as "sort_by_date" — reply 270afee9 succeeded after 9\.0 s$/);
  assert.deepEqual(v.turns, [{ asked: QWEN, served: QWEN, ms: 4500, expect_ms: v.turns[0].expect_ms }]);
});

test("a sentence typed to an agent whose model is away: the agent waits, and the check is red naming the model", () => {
  const v = agentTurnVerdict(reply({ status: "awaiting_input", status_reason: "model_unreachable" }, { status: "failed", output: null, finished_at: "2026-10-12T05:01:10Z" }), 74000);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, no answer: the agent waits for its model — reply 270afee9 awaiting_input \(model_unreachable\) after 74\.0 s$/);
  assert.equal(v.turns[0].served, null);
});

test("a sentence that came back unread — a runner from before AM15 with the model away: red, where the reply itself succeeded", () => {
  // the test stack, 12 Oct 2026: reply 270afee9 succeeded in 70 s, instructions unread=true tries=2
  const v = agentTurnVerdict(reply({}, { output: { text: "x", apply: [], unread: true, tries: 2, model: "" } }), 70000);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, no answer: the sentence came back unread after 2 reading\(s\)/);
});

test("a sentence read by another model than the one the agent was allowed is red, naming both", () => {
  const v = agentTurnVerdict(reply({}, { output: { apply: ["sort_by_date"], model: "deepseek-v4-flash" } }), 9000);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^asked qwen3\.8-27b-fp8, answered deepseek-v4-flash, waited 4\.5 s/);
});

test("no reading at all, no model allowed, a reply that did not finish: none of them passes", () => {
  assert.equal(agentTurnVerdict(null, 180000).ok, false);
  assert.equal(agentTurnVerdict(reply({}, null), 5000).ok, false);
  assert.equal(agentTurnVerdict(reply({}, { input: { text: "x", models: [] } }), 5000).ok, false);
  assert.equal(agentTurnVerdict(reply({ status: "failed", status_reason: "render_failed" }), 5000).ok, false);
  assert.equal(agentTurnVerdict(reply({}, { output: { apply: [] } }), 5000).ok, false); // read, by nobody named
});
