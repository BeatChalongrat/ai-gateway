// FIX48 · C1-A — the SAME model, two framings.
//
//   ARM "bare"  : straight at OpenRouter. No system prompt, no memory, no
//                 tools, no DLP, no router, no context compiler. Runs inside
//                 the backend container (scripts/probes/fix48/bare_probe.py)
//                 so the provider key is decrypted with the app's own helper
//                 and never leaves that process.
//   ARM "sran"  : a real conversation through chat-edge :4000 — the product,
//                 every layer on.
//
// Both arms get byte-identical user text. The only variable is the framing.
//
// Long-context threads: the bare arm replays the whole 16-turn array every
// time (nothing to compact — it is a stateless call), the SRAN arm builds the
// conversation turn by turn and compacts if it decides to. That asymmetry IS
// the measurement.
//
//   node scripts/probes/fix48/run_expA.js [model] [outfile]
//
// Output: results/expA-<model>.json  — raw replies, kept for the judges.

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("./lib.js");
const { Q, THREADS, MEMORY } = require("./questions.js");

const MODEL = process.argv[2] || "google/gemini-2.5-flash";
const OUT = path.join(__dirname, "results");
fs.mkdirSync(OUT, { recursive: true });
const OUTFILE = process.argv[3] || path.join(OUT, `expA-${MODEL.replace(/[\/:]/g, "_")}.json`);

// ── bare arm ──────────────────────────────────────────────────────────────
function bareCall(messages, maxTokens = 1500) {
  const payload = JSON.stringify({ model: MODEL, messages, max_tokens: maxTokens });
  const t0 = Date.now();
  try {
    const out = execFileSync("docker", ["exec", "-i", "ai-gateway-backend-1", "python", "//tmp/bare_probe.py"],
      { input: payload, encoding: "utf8", maxBuffer: 32 * 1024 * 1024, stdio: ["pipe", "pipe", "ignore"] });
    const line = out.trim().split("\n").filter((l) => l.trim().startsWith("{")).pop();
    const j = JSON.parse(line);
    return {
      ms: Date.now() - t0,
      text: j?.choices?.[0]?.message?.content ?? "",
      usage: j.usage ? { prompt: j.usage.prompt_tokens, completion: j.usage.completion_tokens, cost: j.usage.cost } : null,
      error: j.error || null,
    };
  } catch (e) {
    return { ms: Date.now() - t0, text: "", usage: null, error: { thrown: String(e.message).slice(0, 300) } };
  }
}

// ── scoring: objective checks only (the judges score the rest) ────────────
function scoreChecks(checks, reply) {
  const rows = checks.map(([name, fn]) => {
    let ok = false;
    try { ok = Boolean(fn(reply || "")); } catch { ok = false; }
    return { name, ok };
  });
  return { passed: rows.filter((r) => r.ok).length, total: rows.length, rows };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const tok = L.token();
  const started = new Date().toISOString();
  const rows = [];

  // ── 1. single-turn questions ────────────────────────────────────────────
  for (const q of Q) {
    process.stderr.write(`[single] ${q.id} `);
    const bare = bareCall([{ role: "user", content: q.q }]);
    process.stderr.write("bare ");

    let sran;
    try {
      const cid = await L.sranConversation(tok, { title: `fix48 A ${q.id}`, model: MODEL });
      const t = await L.sranTurn(tok, cid, q.q);
      sran = { ms: t.ms, ttfbMs: t.ttfbMs, text: t.text, conv: cid, events: t.events };
    } catch (e) {
      sran = { ms: 0, text: "", error: String(e.message).slice(0, 300) };
    }
    process.stderr.write("sran\n");

    rows.push({
      id: q.id, kind: q.kind, lang: q.lang, prompt: q.q,
      bare: { ...bare, checks: scoreChecks(q.checks, bare.text) },
      sran: { ...sran, checks: scoreChecks(q.checks, sran.text) },
    });
    await sleep(400);
  }

  // ── 2. memory: plant in conversation #1, ask in a NEW conversation ──────
  // The bare arm has no cross-conversation memory by construction; it is
  // given the plant as a prior turn in the SAME array so the comparison is
  // "does our memory layer beat simply keeping the text in context".
  for (const m of MEMORY) {
    process.stderr.write(`[memory] ${m.id} `);
    const bare = bareCall([
      { role: "user", content: m.plant },
      { role: "assistant", content: "รับทราบครับ" },
      { role: "user", content: m.ask },
    ]);
    process.stderr.write("bare ");

    let sran;
    try {
      const c1 = await L.sranConversation(tok, { title: `fix48 A ${m.id} plant`, model: MODEL });
      await L.sranTurn(tok, c1, m.plant);
      await sleep(2500); // memory extraction is async on the server side
      const c2 = await L.sranConversation(tok, { title: `fix48 A ${m.id} ask`, model: MODEL });
      const t = await L.sranTurn(tok, c2, m.ask);
      sran = { ms: t.ms, ttfbMs: t.ttfbMs, text: t.text, conv: c2, plantConv: c1 };
    } catch (e) {
      sran = { ms: 0, text: "", error: String(e.message).slice(0, 300) };
    }
    process.stderr.write("sran\n");

    rows.push({
      id: m.id, kind: "memory", lang: m.lang, prompt: m.ask, plant: m.plant,
      bare: { ...bare, checks: scoreChecks(m.checks, bare.text) },
      sran: { ...sran, checks: scoreChecks(m.checks, sran.text) },
    });
    await sleep(400);
  }

  // ── 3. long context: 16 turns, the fact planted at turn 1 ───────────────
  for (const th of THREADS) {
    process.stderr.write(`[long] ${th.id} `);
    const msgs = [{ role: "user", content: th.plant }, { role: "assistant", content: "รับทราบครับ จำไว้แล้ว" }];
    for (const f of th.filler) {
      msgs.push({ role: "user", content: f });
      msgs.push({ role: "assistant", content: "(สรุปคำตอบไว้แล้ว)" });
    }
    msgs.push({ role: "user", content: th.finalQ });
    const bare = bareCall(msgs);
    process.stderr.write("bare ");

    let sran, ctxInfo = null;
    try {
      const cid = await L.sranConversation(tok, { title: `fix48 A ${th.id}`, model: MODEL });
      await L.sranTurn(tok, cid, th.plant);
      for (const f of th.filler) { await L.sranTurn(tok, cid, f); process.stderr.write("."); }
      const t = await L.sranTurn(tok, cid, th.finalQ);
      const ci = await L.api(tok, `/conversations/${cid}/context-info`);
      ctxInfo = ci.body;
      sran = { ms: t.ms, ttfbMs: t.ttfbMs, text: t.text, conv: cid };
    } catch (e) {
      sran = { ms: 0, text: "", error: String(e.message).slice(0, 300) };
    }
    process.stderr.write(" sran\n");

    rows.push({
      id: th.id, kind: "long_context", lang: th.lang, prompt: th.finalQ, plant: th.plant,
      turns: msgs.filter((m) => m.role === "user").length,
      bare: { ...bare, checks: scoreChecks(th.checks, bare.text) },
      sran: { ...sran, contextInfo: ctxInfo, checks: scoreChecks(th.checks, sran.text) },
    });
  }

  fs.writeFileSync(OUTFILE, JSON.stringify({ model: MODEL, started_at: started, finished_at: new Date().toISOString(), rows }, null, 1));
  const ok = (a) => rows.reduce((s, r) => s + r[a].checks.passed, 0);
  const tot = rows.reduce((s, r) => s + r.bare.checks.total, 0);
  console.log(`\n${MODEL}  rows=${rows.length}  objective checks: bare ${ok("bare")}/${tot}  sran ${ok("sran")}/${tot}`);
  console.log(`written ${OUTFILE}`);
})();
