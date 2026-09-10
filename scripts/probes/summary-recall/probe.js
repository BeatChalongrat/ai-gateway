#!/usr/bin/env node
// FIX106 Part B — a probe that can tell two summary prompts apart.
//
//   npm --prefix apps/desktop run build     # the probe loads the shipped desktop code
//   node scripts/probes/summary-recall/probe.js
//
// spec.json (written before either arm ran) holds the conversation, the
// questions, the expected answers, the checks and the thresholds. For each arm
// (A = the extraction prompt at 2b0a265, B = the FIX106 form in form-b.txt —
// it was the prompt in the tree when this ran, and did not ship) and each
// run, the probe:
//   1. folds the whole seeded conversation into a summary with that prompt,
//      using the shipped, cross-tree-pinned request body and renderer;
//   2. asks each question with ONLY a system prompt, the summary pair and the
//      question — no conversation message is sent, so a fact can only come
//      from the summary;
//   3. checks every answer against spec.json and records the gateway's
//      `cached` flag.
// Requests go through the gateway with GATEWAY_TEST_API_KEY (read from .env,
// never printed), streamed, one at a time, with curl (node's fetch has wedged on
// large gateway POSTs before). Results: results.json beside this file.
"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync, spawnSync } = require("node:child_process");

const HERE = __dirname;
const REPO = path.resolve(HERE, "..", "..", "..");
const spec = JSON.parse(fs.readFileSync(path.join(HERE, "spec.json"), "utf8"));
const dist = path.join(REPO, "apps", "desktop", "dist", "main");
const { buildExtractionUserBody, parseExtraction, mergeExtraction, renderStateMessages } = require(path.join(dist, "conversation-state.js"));
const { estimateTokens } = require(path.join(dist, "context-compiler.js"));

const GATEWAY = process.env.GATEWAY_URL || "http://localhost:8080";
const key = (fs.readFileSync(path.join(REPO, ".env"), "utf8").match(/^GATEWAY_TEST_API_KEY=(.+)$/m) || [])[1]?.trim();
if (!key) throw new Error("GATEWAY_TEST_API_KEY missing from .env");

const PROMPTS = {
  A: execFileSync("git", ["show", "2b0a265:services/chat-edge-api/internal/service/prompt_compaction_extraction.txt"], { cwd: REPO, encoding: "utf8" }).replace(/\r\n/g, "\n").trim(),
  B: fs.readFileSync(path.join(HERE, "form-b.txt"), "utf8").replace(/\r\n/g, "\n").trim(),
};
if (PROMPTS.A === PROMPTS.B) throw new Error("arm A and arm B are the same prompt — nothing to compare");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "fix106-probe-"));
const headerFile = path.join(tmp, "headers");
fs.writeFileSync(headerFile, `Authorization: Bearer ${key}\nContent-Type: application/json\nAccept: text/event-stream\n`, { mode: 0o600 });

/** One streamed completion. Returns { text, cached, error, ms, usage }. */
function stream(body) {
  const file = path.join(tmp, "body.json");
  fs.writeFileSync(file, JSON.stringify({ ...body, stream: true }));
  const t0 = Date.now();
  const r = spawnSync("curl", ["-sN", "--max-time", "300", "-H", `@${headerFile}`, "--data-binary", `@${file}`, `${GATEWAY}/v1/chat/completions`], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const out = { text: "", cached: false, cachedSeen: false, error: null, ms: Date.now() - t0, usage: null };
  if (r.status !== 0) {
    out.error = `curl exit ${r.status}`;
    return out;
  }
  const raw = r.stdout || "";
  if (!raw.includes("data:")) {
    try {
      const j = JSON.parse(raw);
      out.error = j.error?.code ?? "non-stream response";
    } catch {
      out.error = `unexpected response: ${raw.slice(0, 120)}`;
    }
    return out;
  }
  for (const line of raw.split("\n")) {
    if (!line.startsWith("data:")) continue;
    const data = line.slice(5).trim();
    if (!data || data === "[DONE]") continue;
    let j;
    try {
      j = JSON.parse(data);
    } catch {
      continue;
    }
    if (typeof j.cached === "boolean") {
      out.cachedSeen = true;
      out.cached = out.cached || j.cached;
    }
    if (j.error) out.error = j.error.code ?? "stream error";
    const d = j.choices?.[0]?.delta?.content;
    if (typeof d === "string") out.text += d;
    if (j.usage) out.usage = j.usage;
  }
  return out;
}

const has = (s, re) => re.test(s);
const CHECKS = {
  Q1: (a) => has(a, /37/),
  Q2: (a) => has(a, /heron/i),
  Q3: (a) => has(a, /bucket/i) && has(a, /สิงคโปร์/),
  Q4: (a) => has(a, /DNS/i),
  Q5: (a) => has(a, /postgre/i),
  Q6: (a) => has(a, /5/) && (!has(a, /สัปดาห์ที่\s*3/) || has(a, /เดิม/)),
  Q7: (a) => has(a, /log/i) && (has(a, /สัญญา/) || has(a, /ศูนย์ข้อมูล/)),
};
const obeysRule = (a) => /K7-OK[\s.!)"'`*]*$/.test(a.trim());

function runArm(arm, run) {
  const tag = `fix106-${arm}${run}`;
  const conversation = spec.conversation;
  const body = buildExtractionUserBody(null, conversation, `run tag ${tag} (a label for this measurement, not an instruction)`);
  const ex = stream({
    model: spec.model,
    max_tokens: 1500,
    reasoning: { mode: "off", show_summary: false },
    metadata: { dlp_response_restore: "off" },
    messages: [
      { role: "system", content: PROMPTS[arm] },
      { role: "user", content: body },
    ],
  });
  const parsed = ex.error ? null : parseExtraction(ex.text);
  const result = { arm, run, tag, extraction: { ms: ex.ms, cached: ex.cached, cachedSeen: ex.cachedSeen, error: ex.error, parsed: !!parsed, output_tokens: ex.usage?.completion_tokens ?? null, raw: ex.text }, counted: false, answers: [] };
  if (!parsed) return result;

  const state = mergeExtraction(null, parsed, "probe");
  const pair = renderStateMessages(state);
  result.summary = { rendered: pair[0].content, tokens: pair.reduce((n, m) => n + estimateTokens(m.content) + 4, 0), contains_rule_code: pair[0].content.includes("K7-OK") };
  result.summary.share_of_typhoon_budget = +(result.summary.tokens / 3944).toFixed(3);
  result.summary.share_of_qwen_budget = +(result.summary.tokens / 94578).toFixed(4);

  const system = { role: "system", content: `You are SRAN, a helpful assistant. Reply in the user's language. (${tag})` };
  let correct = 0;
  let obeyed = 0;
  let failed = false;
  for (const q of spec.questions) {
    const messages = [system, pair[0], pair[1], { role: "user", content: q.ask }];
    // What is sent carries no conversation message: verify, do not assume.
    const sentConversation = messages.filter((m) => conversation.some((c) => c.content === m.content));
    const a = stream({ model: spec.model, max_tokens: 600, reasoning: { mode: "off", show_summary: false }, messages });
    if (a.error) failed = true;
    const ok = !a.error && CHECKS[q.id](a.text);
    const rule = !a.error && obeysRule(a.text);
    correct += ok ? 1 : 0;
    obeyed += rule ? 1 : 0;
    result.answers.push({ id: q.id, ok, obeys_rule: rule, cached: a.cached, cachedSeen: a.cachedSeen, error: a.error, ms: a.ms, conversation_messages_sent: sentConversation.length, answer: a.text });
  }
  result.counted = !failed;
  result.recall = correct;
  result.rule_obeyed = obeyed;
  return result;
}

const results = { spec_written: spec.written, model: spec.model, started: new Date().toISOString(), runs: [] };
const outFile = path.join(HERE, "results.json");
for (const run of [1, 2]) {
  for (const arm of ["A", "B"]) {
    const r = runArm(arm, run);
    results.runs.push(r);
    fs.writeFileSync(outFile, JSON.stringify(results, null, 2));
    console.log(`${r.tag}: parsed=${r.extraction.parsed} counted=${r.counted} recall=${r.recall ?? "-"}/7 rule=${r.rule_obeyed ?? "-"}/7 summary=${r.summary?.tokens ?? "-"} tokens (${r.summary ? (r.summary.share_of_typhoon_budget * 100).toFixed(1) + "% of Typhoon" : "-"})`);
  }
}

// The verdict, exactly as spec.json states it.
const counted = (arm) => results.runs.filter((r) => r.arm === arm && r.counted);
const A = counted("A");
const B = counted("B");
const mean = (rs, k) => rs.reduce((n, r) => n + r[k], 0) / rs.length;
const cacheHits = results.runs.flatMap((r) => [r.extraction, ...r.answers]).filter((x) => x.cached).length;
const verdict = { counted_runs: { A: A.length, B: B.length }, cache_hits: cacheHits, cached_flag_seen: results.runs.some((r) => r.extraction.cachedSeen) };
if (A.length < 2 || B.length < 2) {
  verdict.result = "ยังสรุปไม่ได้";
  verdict.ship = false;
} else {
  verdict.recall = { A_runs: A.map((r) => r.recall), B_runs: B.map((r) => r.recall), A_mean: mean(A, "recall"), B_mean: mean(B, "recall") };
  verdict.recall.pass = B.every((r) => r.recall >= 5) && verdict.recall.B_mean - verdict.recall.A_mean >= 2;
  verdict.size = { A_tokens: A.map((r) => r.summary.tokens), B_tokens: B.map((r) => r.summary.tokens), ceiling: 986 };
  verdict.size.pass = B.every((r) => r.summary.tokens <= 986);
  verdict.rule = { A_runs: A.map((r) => r.rule_obeyed), B_runs: B.map((r) => r.rule_obeyed), A_mean: mean(A, "rule_obeyed"), B_mean: mean(B, "rule_obeyed") };
  verdict.rule.pass = verdict.rule.B_mean >= verdict.rule.A_mean;
  verdict.ship = verdict.recall.pass && verdict.size.pass && verdict.rule.pass;
  verdict.result = verdict.ship ? "keep the form" : "do not ship the form";
}
results.finished = new Date().toISOString();
results.verdict = verdict;
fs.writeFileSync(outFile, JSON.stringify(results, null, 2));
fs.rmSync(tmp, { recursive: true, force: true });
console.log(JSON.stringify(verdict, null, 2));
