// FIX35 Part D — router comparison: OUR `auto` vs `openrouter/auto` vs a pinned
// control, on ONE question set, through the gateway's public path.
//
//   node scripts/probes/router-compare/eval.js            → all three arms
//   ARMS=auto,control node scripts/probes/router-compare/eval.js
//   CONTROL_MODEL=anthropic/claude-sonnet-4.5 (default)
//
// Scoring is FIXED HERE, before any run (each question carries its own
// checks; nothing is tuned after seeing answers). Per question we record the
// served model (response.model — Part B), latency, cost (usage.estimated_cost_usd
// / cost_thb the gateway attaches) and a 0..1 score = checks passed / checks.
// ≥ 1/3 of the questions are Thai, because that is who uses this system and an
// English-first classifier is exactly where a router would misfire.
//
// Results: scripts/probes/router-compare/results.json + results.md (table).

const fs = require("fs");
const path = require("path");

const REPO = path.resolve(__dirname, "..", "..", "..");
const GATEWAY = process.env.GATEWAY_URL || "http://localhost:8080";
const CONTROL = process.env.CONTROL_MODEL || "anthropic/claude-sonnet-4.5";
const ARMS = (process.env.ARMS || "auto,openrouter,control").split(",");
const KEY = (() => {
  const env = fs.readFileSync(path.join(REPO, ".env"), "utf8");
  const m = env.match(/^GATEWAY_API_KEY=(.+)$/m);
  return m ? m[1].trim().replace(/^"|"$/g, "") : "";
})();

// ─── question set + rubric (fixed before running) ───
// kind: the task family we EXPECT the classifier to see (reported, not scored)
// checks: [name, (reply) => boolean] — all must be true for score 1.0
const Q = [
  // — code —
  { id: "code-1", lang: "en", kind: "code_debug",
    q: "Find the bug in this Python and give the corrected function only:\n```python\ndef avg(xs):\n    return sum(xs) / len(xs) if xs else 0\nprint(avg([]))\nprint(avg([1,2,3]))\n```\nActually the bug is in this one:\n```python\ndef median(xs):\n    xs.sort()\n    return xs[len(xs)/2]\n```",
    checks: [["mentions integer division or // or int()", (r) => /\/\/|int\(|integer/i.test(r)], ["shows corrected code", (r) => /def median/.test(r)]] },
  { id: "code-2", lang: "th", kind: "code_generate",
    q: "เขียนฟังก์ชัน Python ชื่อ is_thai_id(s) ตรวจว่า s เป็นเลขบัตรประชาชนไทย 13 หลักที่ checksum ถูกต้อง (หลักที่ 13 = (11 - (Σ หลัก i × (13 - i) สำหรับ i=1..12) mod 11) mod 10) ตอบเป็นโค้ดอย่างเดียว",
    checks: [["defines is_thai_id", (r) => /def is_thai_id/.test(r)], ["uses mod 11", (r) => /%\s*11/.test(r)], ["checks length 13", (r) => /13/.test(r)]] },
  { id: "code-3", lang: "en", kind: "sql_query",
    q: "Write one PostgreSQL query: total revenue per month in 2025 from orders(id, created_at, amount_cents), months with no orders must still appear with 0.",
    checks: [["generate_series or calendar join", (r) => /generate_series|date_trunc/i.test(r)], ["left join / coalesce for empty months", (r) => /left join|coalesce/i.test(r)]] },
  // — math —
  { id: "math-1", lang: "th", kind: "math_calculation",
    q: "ร้านขายของราคา 1,250 บาท ลด 15% แล้วบวก VAT 7% ต้องจ่ายเท่าไหร่ ตอบเป็นตัวเลขทศนิยม 2 ตำแหน่งบรรทัดสุดท้าย",
    checks: [["1136.88 present", (r) => /1[,.]?136\.8[78]/.test(r)]] },
  { id: "math-2", lang: "en", kind: "logic_reasoning",
    q: "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost? Answer with the number only on the last line.",
    checks: [["0.05", (r) => /0\.05|5 cents|\$0\.05/.test(r)], ["not 0.10 as the answer", (r) => !/^\s*\$?0?\.10\s*$/m.test(r)]] },
  // — knowledge —
  { id: "kn-1", lang: "th", kind: "short_factual_qa",
    q: "เมืองหลวงของประเทศออสเตรเลียคือเมืองอะไร ตอบสั้น ๆ",
    checks: [["Canberra", (r) => /แคนเบอร์รา|แคนเบอร์ร่า|Canberra/i.test(r)], ["not Sydney as the answer", (r) => !/ซิดนีย์คือ|is Sydney/i.test(r)]] },
  { id: "kn-2", lang: "en", kind: "knowledge_explain",
    q: "Explain the difference between TCP and UDP in at most 4 bullet points.",
    checks: [["mentions reliability/ordering", (r) => /reliab|order|retransmi|handshake/i.test(r)], ["≤ 6 bullets", (r) => (r.match(/^\s*[-*•]/gm) || []).length <= 6]] },
  // — Thai document summary —
  { id: "sum-1", lang: "th", kind: "summarize_document",
    q: "สรุปข้อความนี้เป็น 3 ข้อสั้น ๆ:\n\"บริษัทประกาศผลประกอบการไตรมาส 2 รายได้ 4,200 ล้านบาท เพิ่มขึ้น 12% จากปีก่อน กำไรสุทธิ 380 ล้านบาท ลดลง 5% เนื่องจากต้นทุนวัตถุดิบสูงขึ้น บริษัทตั้งเป้าเปิดสาขาใหม่ 20 แห่งในครึ่งปีหลัง และจะจ่ายเงินปันผลระหว่างกาล 0.50 บาทต่อหุ้นในเดือนกันยายน\"",
    checks: [["revenue 4,200 / +12%", (r) => /4[,.]?200/.test(r) && /12\s*%/.test(r)], ["profit 380 / -5%", (r) => /380/.test(r) && /5\s*%/.test(r)], ["dividend 0.50", (r) => /0\.50|0\.5\s*บาท/.test(r)], ["3-ish bullets", (r) => { const n = (r.match(/^\s*(\d+[.)]|[-*•])/gm) || []).length; return n >= 2 && n <= 4; }]] },
  { id: "sum-2", lang: "th", kind: "summarize_document",
    q: "ย่อให้เหลือประโยคเดียว: \"ระบบบริหารจัดการโครงการต้องรองรับการวางแผน การติดตามความคืบหน้า และการรายงานผลอย่างต่อเนื่อง ทีมงานควรบันทึกความเสี่ยงและมาตรการรับมือให้ชัดเจน รวมถึงกำหนดผู้รับผิดชอบในแต่ละงานย่อย เพื่อให้การส่งมอบเป็นไปตามกำหนดเวลาและงบประมาณที่ตกลงกันไว้\"",
    checks: [["one sentence (≤ 2 line breaks, ≤ 200 chars)", (r) => r.trim().length <= 200 && (r.trim().match(/\n/g) || []).length <= 1], ["keeps plan/track/report or risk", (r) => /วางแผน|ติดตาม|รายงาน|ความเสี่ยง/.test(r)]] },
  // — writing —
  { id: "wr-1", lang: "th", kind: "business_writing",
    q: "ร่างอีเมลสั้น ๆ (ไม่เกิน 80 คำ) ถึงลูกค้าชื่อคุณสมชาย ขอเลื่อนนัดประชุมจากวันพุธเป็นวันศุกร์เวลาเดิม 14:00 น้ำเสียงสุภาพ",
    checks: [["addresses คุณสมชาย", (r) => /สมชาย/.test(r)], ["Friday 14:00", (r) => /ศุกร์/.test(r) && /14[:.]00|บ่ายสอง/.test(r)], ["polite closing", (r) => /ขอบคุณ|ขออภัย|ขอโทษ/.test(r)], ["≤ ~120 words-ish (≤ 700 chars)", (r) => r.length <= 700]] },
  { id: "wr-2", lang: "en", kind: "marketing_copy",
    q: "Write 3 short taglines (max 8 words each) for a reusable water bottle. One per line, nothing else.",
    checks: [["3 lines", (r) => r.trim().split(/\n+/).filter((l) => l.trim()).length === 3], ["each ≤ 8 words", (r) => r.trim().split(/\n+/).filter((l) => l.trim()).every((l) => l.replace(/^\s*(\d+[.)]|[-*•])\s*/, "").trim().split(/\s+/).length <= 8)]] },
  // — short answers —
  { id: "short-1", lang: "th", kind: "greeting_smalltalk",
    q: "สวัสดีครับ",
    checks: [["greets back in Thai", (r) => /สวัสดี|ยินดี/.test(r)], ["short (≤ 200 chars)", (r) => r.trim().length <= 200]] },
  { id: "short-2", lang: "en", kind: "translate",
    q: "Translate to Thai, translation only: \"The meeting has been moved to Friday at 2 PM.\"",
    checks: [["Friday", (r) => /ศุกร์/.test(r)], ["2 PM", (r) => /14[:.]00|บ่าย\s?2|บ่ายสอง|14\s?น/.test(r)], ["no English sentence echoed", (r) => !/The meeting has been moved/.test(r)]] },
  { id: "short-3", lang: "en", kind: "classify_label",
    q: "Classify the sentiment of this review as exactly one word — positive, negative or neutral: \"The battery died after two days and support never replied.\"",
    checks: [["negative", (r) => /negative/i.test(r)], ["one word-ish", (r) => r.trim().split(/\s+/).length <= 3]] },
  { id: "short-4", lang: "th", kind: "extract_structured",
    q: "ดึงข้อมูลเป็น JSON มีคีย์ name, phone, city จากข้อความ: \"ติดต่อคุณวิภา โทร 089-555-1234 อยู่เชียงใหม่\" ตอบเป็น JSON อย่างเดียว",
    checks: [["valid JSON with 3 keys", (r) => { try { const j = JSON.parse(r.replace(/^```json|```$/gm, "").trim()); return ["name", "phone", "city"].every((k) => k in j); } catch { return false; } }], ["city เชียงใหม่", (r) => /เชียงใหม่/.test(r)]] },
];

const thaiShare = Q.filter((x) => x.lang === "th").length / Q.length;

async function call(model, q, arm) {
  const t0 = Date.now();
  // The gateway's response cache is keyed on (RESOLVED model, messages): when
  // our `auto` falls back to the same model the control arm pins, the second
  // arm to ask would be served from cache (50 ms, $0) and the latency / cost
  // columns would lie. Every arm therefore carries a one-line system tag of
  // identical shape — same prompt cost for all three, no shared cache key.
  const messages = [{ role: "system", content: `eval-arm: ${arm}` }, { role: "user", content: q }];
  const res = await fetch(`${GATEWAY}/v1/chat/completions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages, max_tokens: 700, stream: false }),
  });
  const ms = Date.now() - t0;
  const j = await res.json().catch(() => ({}));
  const reply = j?.choices?.[0]?.message?.content ?? "";
  return {
    http: res.status,
    ms,
    served: j?.model ?? null,
    provider: j?.provider ?? null,
    fallback: res.headers.get("x-gateway-fallback") === "true",
    cached: j?.cached === true,
    cost_usd: j?.usage?.estimated_cost_usd ?? null,
    tokens: j?.usage?.total_tokens ?? null,
    error: j?.error?.code ?? (res.status >= 400 ? `http_${res.status}` : null),
    reply,
  };
}

(async () => {
  const armModel = { auto: "auto", openrouter: "openrouter/auto", control: CONTROL };
  const out = { control: CONTROL, thai_share: thaiShare, started_at: new Date().toISOString(), rows: [] };
  for (const item of Q) {
    for (const arm of ARMS) {
      const r = await call(armModel[arm], item.q, arm);
      const results = item.checks.map(([name, fn]) => ({ name, pass: !!(r.reply && safe(() => fn(r.reply))) }));
      const score = results.filter((x) => x.pass).length / results.length;
      out.rows.push({ id: item.id, lang: item.lang, kind: item.kind, arm, requested: armModel[arm], ...r, reply: r.reply.slice(0, 400), checks: results, score });
      process.stderr.write(`${item.id.padEnd(8)} ${arm.padEnd(10)} → ${(r.served ?? r.error ?? "?").padEnd(36)} ${String(r.ms).padStart(6)} ms  score=${score.toFixed(2)}\n`);
    }
  }
  fs.writeFileSync(path.join(__dirname, "results.json"), JSON.stringify(out, null, 2));
  fs.writeFileSync(path.join(__dirname, "results.md"), render(out));
  process.stderr.write(`\nwrote results.json / results.md (thai share ${(thaiShare * 100).toFixed(0)}%)\n`);
})();

function safe(fn) { try { return fn(); } catch { return false; } }

function render(out) {
  const arms = [...new Set(out.rows.map((r) => r.arm))];
  const byArm = Object.fromEntries(arms.map((a) => [a, out.rows.filter((r) => r.arm === a)]));
  const agg = (rows) => ({
    n: rows.length,
    score: (rows.reduce((s, r) => s + r.score, 0) / rows.length).toFixed(2),
    ms: Math.round(rows.reduce((s, r) => s + r.ms, 0) / rows.length),
    cost: rows.reduce((s, r) => s + (r.cost_usd || 0), 0).toFixed(5),
    errors: rows.filter((r) => r.error).length,
    cached: rows.filter((r) => r.cached).length,
    fallbacks: rows.filter((r) => r.fallback).length,
    th: (rows.filter((r) => r.lang === "th").reduce((s, r) => s + r.score, 0) / Math.max(1, rows.filter((r) => r.lang === "th").length)).toFixed(2),
    en: (rows.filter((r) => r.lang === "en").reduce((s, r) => s + r.score, 0) / Math.max(1, rows.filter((r) => r.lang === "en").length)).toFixed(2),
  });
  let md = `# FIX35 Part D — router comparison (${out.started_at})\n\ncontrol = \`${out.control}\` · Thai share ${(out.thai_share * 100).toFixed(0)}%\n\n`;
  md += `| arm | n | mean score | Thai | English | mean ms | Σ cost USD | errors | fallbacks |\n|---|---|---|---|---|---|---|---|---|\n`;
  for (const a of arms) { const g = agg(byArm[a]); md += `| ${a} | ${g.n} | ${g.score} | ${g.th} | ${g.en} | ${g.ms} | ${g.cost} | ${g.errors} | ${g.fallbacks} |\n`; }
  md += `\n## Per question\n\n| id | lang | kind | arm | served model | ms | cost | score | failed checks |\n|---|---|---|---|---|---|---|---|---|\n`;
  for (const r of out.rows) {
    md += `| ${r.id} | ${r.lang} | ${r.kind} | ${r.arm} | ${r.served ?? r.error ?? "—"}${r.fallback ? " (fb)" : ""} | ${r.ms} | ${r.cost_usd ?? "—"} | ${r.score.toFixed(2)} | ${r.checks.filter((c) => !c.pass).map((c) => c.name).join("; ") || "—"} |\n`;
  }
  return md;
}
