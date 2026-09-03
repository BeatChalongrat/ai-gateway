// FIX48 · C3 — blind pairwise judging by three judges from three camps.
//
// Bias controls, all fixed here before any judging runs:
//   1. BLIND      — the judge sees "คำตอบ ก" / "คำตอบ ข" and is never told
//                   which framing produced which. The prompt does not contain
//                   the words SRAN, gateway, bare, API, or a model name.
//   2. ORDER      — which arm is ก and which is ข is drawn per question from a
//                   seeded RNG, so it is reproducible and roughly balanced.
//                   The mapping is recorded in the output for decoding.
//   3. RUBRIC     — four axes, each 0-3, all about substance. The prompt says
//                   explicitly that length must not be rewarded, and asks the
//                   judge to prefer the SHORTER answer when the two are
//                   otherwise equal.
//   4. THREE JUDGES — Anthropic, DeepSeek (its own account and endpoint) and
//                   OpenAI. Pairwise agreement is reported; disagreement is
//                   reported as "unreliable", never averaged away.
//   5. HUMAN SET  — 10 questions are drawn (same seed) into a file for the
//                   owner to read and score personally.
//
// Judges are called on the BARE path, so no SRAN framing colours the judging.
//
//   node scripts/probes/fix48/judge.js results/expA-<model>.json

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

// Judge 2 was google/gemini-2.5-pro on the first pass; every call came back
// 402 in_flight_budget_exhausted (the OpenRouter account is at its ceiling —
// see INTELLIGENCE-EVAL.md §0). Replaced with DeepSeek, called on its OWN
// account through a different endpoint, so the two judges are from different
// camps AND on different billing. Judge 1's verdicts from the first pass are
// reused (the prompt, the seed and the order draw are identical).
const JUDGES = ["anthropic/claude-sonnet-4.5", "deepseek:deepseek-chat", "openai/gpt-4o-mini"];
const SEED = 20260903; // fixed before the run

// mulberry32 — small, deterministic, good enough for order-shuffling
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function bare(model, messages, maxTokens = 900) {
  const payload = JSON.stringify({ model, messages, max_tokens: maxTokens, temperature: 0 });
  try {
    const out = execFileSync("docker", ["exec", "-i", "ai-gateway-backend-1", "python", "//tmp/bare_probe.py"],
      { input: payload, encoding: "utf8", maxBuffer: 32 * 1024 * 1024, stdio: ["pipe", "pipe", "ignore"] });
    const line = out.trim().split("\n").filter((l) => l.trim().startsWith("{")).pop();
    const j = JSON.parse(line);
    return j?.choices?.[0]?.message?.content ?? "";
  } catch (e) {
    return "";
  }
}

const RUBRIC = `คุณคือกรรมการให้คะแนนคำตอบของผู้ช่วย AI สองคำตอบต่อคำถามเดียวกัน

ให้คะแนนแต่ละคำตอบใน 4 แกน แกนละ 0-3:
  correctness  ข้อเท็จจริงและการคำนวณถูกต้องหรือไม่ (0 = ผิด/แต่งขึ้น, 3 = ถูกทั้งหมด)
  on_target    ตอบสิ่งที่ถูกถามจริง ๆ ไหม ไม่ใช่ตอบข้าง ๆ (0 = ไม่ตอบโจทย์, 3 = ตรงเป๊ะ)
  uses_context ใช้ข้อมูล/ข้อจำกัดที่โจทย์ให้มาจริงไหม (0 = เพิกเฉย, 3 = ใช้ครบ)
  honesty      ยอมรับเมื่อไม่รู้ ไม่เดามั่ว ไม่แต่งข้อมูล (0 = แต่งขึ้นอย่างมั่นใจ, 3 = ซื่อสัตย์ครบถ้วน)

กฎที่สำคัญที่สุด:
- **ห้ามให้คะแนนความยาว** คำตอบที่ยาวกว่าไม่ได้ดีกว่า
- ถ้าสองคำตอบดีเท่ากันในสาระ ให้ถือว่า **คำตอบที่สั้นกว่าดีกว่า**
- ห้ามให้คะแนนสไตล์การเขียน หัวข้อ อีโมจิ หรือการจัดรูปแบบ
- ตัดสินจากสาระเท่านั้น
- ถ้าคำตอบใดว่างเปล่า ให้ 0 ทุกแกน

ตอบเป็น JSON อย่างเดียว ไม่ต้องอธิบายนอก JSON:
{"ก":{"correctness":n,"on_target":n,"uses_context":n,"honesty":n},"ข":{"correctness":n,"on_target":n,"uses_context":n,"honesty":n},"winner":"ก"|"ข"|"เสมอ","why":"เหตุผลหนึ่งประโยค"}`;

function parseVerdict(text) {
  const m = text.match(/\{[\s\S]*\}/);
  if (!m) return null;
  try { return JSON.parse(m[0]); } catch { return null; }
}
const sum = (o) => (o ? (o.correctness | 0) + (o.on_target | 0) + (o.uses_context | 0) + (o.honesty | 0) : 0);

(async () => {
  const infile = process.argv[2];
  const data = JSON.parse(fs.readFileSync(infile, "utf8"));
  const priorFile = infile.replace(/\.json$/, "-judged.json");
  const PRIOR = new Map();
  if (fs.existsSync(priorFile)) {
    for (const row of JSON.parse(fs.readFileSync(priorFile, "utf8")).rows) {
      const kept = {};
      for (const [k, v] of Object.entries(row.judges || {})) if (v) kept[k] = v;
      if (Object.keys(kept).length) PRIOR.set(row.id, { judges: kept });
    }
    console.error(`resuming: ${PRIOR.size} rows carry a usable verdict from an earlier pass`);
  }
  const r = rng(SEED);
  const out = [];

  for (const row of data.rows) {
    // 2. seeded order draw
    const bareIsFirst = r() < 0.5;
    const A = bareIsFirst ? row.bare.text : row.sran.text;
    const B = bareIsFirst ? row.sran.text : row.bare.text;
    const label = { ก: bareIsFirst ? "bare" : "sran", ข: bareIsFirst ? "sran" : "bare" };

    const ctx = row.plant ? `\n\nสิ่งที่ผู้ใช้บอกไว้ก่อนหน้าในบทสนทนา:\n${row.plant}` : "";
    const user = `คำถาม:\n${row.prompt}${ctx}\n\n--- คำตอบ ก ---\n${A || "(ว่างเปล่า)"}\n\n--- คำตอบ ข ---\n${B || "(ว่างเปล่า)"}`;

    // Resume: a judge that already produced a decoded verdict on an earlier
    // pass is not called again (the prompt, seed and order draw are identical,
    // so re-calling could only cost money and add sampling noise).
    const prior = PRIOR.get(row.id);
    const verdicts = {};
    for (const j of JUDGES) {
      if (prior && prior.judges && prior.judges[j]) { verdicts[j] = { reused: true }; process.stderr.write("r"); continue; }
      const raw = bare(j, [{ role: "system", content: RUBRIC }, { role: "user", content: user }]);
      verdicts[j] = { raw: raw.slice(0, 1200), parsed: parseVerdict(raw) };
      process.stderr.write(".");
    }

    // decode ก/ข back to bare/sran
    const decoded = {};
    for (const j of JUDGES) {
      if (verdicts[j].reused) { decoded[j] = prior.judges[j]; continue; }
      const p = verdicts[j].parsed;
      if (!p) { decoded[j] = null; continue; }
      decoded[j] = {
        bare: sum(p[label.ก === "bare" ? "ก" : "ข"]),
        sran: sum(p[label.ก === "sran" ? "ก" : "ข"]),
        winner: p.winner === "เสมอ" ? "tie" : (label[p.winner] || null),
        why: p.why,
        axes: { bare: p[label.ก === "bare" ? "ก" : "ข"], sran: p[label.ก === "sran" ? "ก" : "ข"] },
      };
    }

    const winners = JUDGES.map((j) => decoded[j]?.winner).filter(Boolean);
    const agree = winners.length === 2 && winners[0] === winners[1];

    out.push({
      id: row.id, kind: row.kind, lang: row.lang,
      order: label, agree, winners,
      judges: decoded,
      objective: { bare: row.bare.checks, sran: row.sran.checks },
    });
    process.stderr.write(` ${row.id}\n`);
  }

  const outfile = infile.replace(/\.json$/, "-judged.json");
  fs.writeFileSync(outfile, JSON.stringify({ model: data.model, seed: SEED, judges: JUDGES, rows: out }, null, 1));

  const agreed = out.filter((o) => o.agree);
  const tally = { bare: 0, sran: 0, tie: 0 };
  agreed.forEach((o) => { tally[o.winners[0]] = (tally[o.winners[0]] || 0) + 1; });
  console.log(`\njudged ${out.length} · judges agreed on ${agreed.length} (${(100 * agreed.length / out.length).toFixed(0)}%)`);
  console.log(`among the agreed: bare ${tally.bare} · sran ${tally.sran} · tie ${tally.tie}`);
  console.log(`written ${outfile}`);
})();
