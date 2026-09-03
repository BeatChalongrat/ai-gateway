// FIX48 · C1-A addendum — the compaction layer, actually crossed.
//
// The 16-turn threads in run_expA.js never reached the trigger: context-info
// reported used_ratio 0.13-0.15 against a 120,000-token budget with an 82,805
// threshold. Nothing was compacted, so the layer FIX33 spent a whole round on
// was not measured. This does cross it.
//
// Shape: the fact is planted in turn 1; turns 2..N each paste a long block
// (~4k tokens) and ask a one-line question, so the context grows fast while
// the OUTPUT stays small; the last turn asks for the planted fact back.
// The bare arm replays the identical array in one stateless call — it has the
// whole history verbatim and is the ceiling we are measuring against.
//
//   node scripts/probes/fix48/run_compaction.js [model]

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("./lib.js");

const MODEL = process.argv[2] || "google/gemini-2.5-flash";
const OUT = path.join(__dirname, "results");
fs.mkdirSync(OUT, { recursive: true });

// ~6,000 Thai characters per block. The chat-edge estimator counts Thai at
// ceil(2n/3), so one block is ~4,000 tokens.
const BLOCK_TH = ("นโยบายการจัดซื้อจัดจ้างของบริษัทกำหนดให้ทุกคำสั่งซื้อที่มีมูลค่าเกินกว่าที่ระเบียบกำหนดต้องผ่านการอนุมัติจากผู้มีอำนาจตามลำดับชั้น " +
  "และต้องมีการเปรียบเทียบราคาจากผู้ขายอย่างน้อยสามรายก่อนตัดสินใจ ทั้งนี้เพื่อความโปร่งใสและตรวจสอบได้ในภายหลัง ").repeat(30).slice(0, 6000);
const BLOCK_EN = ("The procurement policy requires that every purchase order above the stated threshold passes through the approval chain in order, " +
  "and that at least three vendor quotations are compared before a decision is made, so the record remains auditable afterwards. ").repeat(30).slice(0, 6000);

const THREADS = [
  { id: "cx-th", lang: "th", turns: 24, block: BLOCK_TH,
    plant: "จำสามอย่างนี้ไว้ให้ดี แล้วเดี๋ยวผมจะถามตอนท้าย: รหัสสัญญาคือ CT-9031 · ผู้อนุมัติคือคุณเกียรติศักดิ์ · วงเงินคือ 4,750,000 บาท",
    ask: (i) => `${BLOCK_TH}\n\n(ตอนที่ ${i}) จากข้อความข้างบน สรุปเป็นหนึ่งบรรทัดว่าต้องเทียบราคากี่ราย`,
    finalQ: "จบแล้วครับ ทีนี้ตอบสามอย่างที่ผมให้จำไว้ตอนต้น: รหัสสัญญา ผู้อนุมัติ และวงเงิน",
    checks: [["รหัสสัญญา CT-9031", (r) => /CT-?9031/i.test(r)],
             ["ผู้อนุมัติ เกียรติศักดิ์", (r) => /เกียรติศักดิ์/.test(r)],
             ["วงเงิน 4,750,000", (r) => /4[,.]?750[,.]?000|4\.75 ?ล้าน/.test(r)],
             ["ไม่แต่งขึ้นเมื่อจำไม่ได้", (r) => /CT-?9031/i.test(r) || /จำไม่ได้|ไม่พบ|ไม่มีข้อมูล|ถูกตัดออก|ไม่ปรากฏ/.test(r)]] },
  { id: "cx-en", lang: "en", turns: 24, block: BLOCK_EN,
    plant: "Hold on to three things and I will ask for them at the end: the contract id is CT-9031, the approver is Kiattisak, and the ceiling is 4,750,000 baht.",
    ask: (i) => `${BLOCK_EN}\n\n(part ${i}) From the text above, answer in one line: how many quotations must be compared?`,
    finalQ: "Done. Now give me back the three things I asked you to hold: the contract id, the approver, and the ceiling.",
    checks: [["contract id", (r) => /CT-?9031/i.test(r)],
             ["approver", (r) => /Kiattisak/i.test(r)],
             ["ceiling", (r) => /4[,.]?750[,.]?000|4\.75 ?million/i.test(r)],
             ["does not invent when it cannot recall", (r) => /CT-?9031/i.test(r) || /cannot recall|don't recall|not in|no longer|trimmed|compacted|do not have/i.test(r)]] },
];

function bareCall(messages) {
  const payload = JSON.stringify({ model: MODEL, messages, max_tokens: 800 });
  const t0 = Date.now();
  try {
    const out = execFileSync("docker", ["exec", "-i", "ai-gateway-backend-1", "python", "//tmp/bare_probe.py"],
      { input: payload, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["pipe", "pipe", "ignore"] });
    const line = out.trim().split("\n").filter((l) => l.trim().startsWith("{")).pop();
    const j = JSON.parse(line);
    return { ms: Date.now() - t0, text: j?.choices?.[0]?.message?.content ?? "", usage: j.usage || null, error: j.error || null };
  } catch (e) { return { ms: Date.now() - t0, text: "", error: { thrown: String(e.message).slice(0, 200) } }; }
}

const score = (checks, reply) => {
  const rows = checks.map(([n, f]) => { let ok = false; try { ok = Boolean(f(reply || "")); } catch {} return { name: n, ok }; });
  return { passed: rows.filter((r) => r.ok).length, total: rows.length, rows };
};

(async () => {
  const tok = L.token();
  const rows = [];
  for (const th of THREADS) {
    process.stderr.write(`\n[${th.id}] sran `);
    const cid = await L.sranConversation(tok, { title: `fix48 compaction ${th.id}`, model: MODEL });
    await L.sranTurn(tok, cid, th.plant);
    const trail = [];
    for (let i = 1; i <= th.turns; i++) {
      await L.sranTurn(tok, cid, th.ask(i));
      const ci = await L.api(tok, `/conversations/${cid}/context-info`);
      trail.push({ turn: i, used_ratio: ci.body.used_ratio, compacted: ci.body.compacted,
        omitted_older: ci.body.omitted_older, state_present: ci.body.state_present,
        sent: ci.body.messages_would_send, total: ci.body.messages_total });
      process.stderr.write(ci.body.compacted ? "C" : ".");
    }
    const t = await L.sranTurn(tok, cid, th.finalQ);
    const ciEnd = await L.api(tok, `/conversations/${cid}/context-info`);

    process.stderr.write(" bare ");
    const msgs = [{ role: "user", content: th.plant }, { role: "assistant", content: "รับทราบ จำไว้แล้ว" }];
    for (let i = 1; i <= th.turns; i++) { msgs.push({ role: "user", content: th.ask(i) }); msgs.push({ role: "assistant", content: "สามราย" }); }
    msgs.push({ role: "user", content: th.finalQ });
    const bare = bareCall(msgs);
    process.stderr.write("done\n");

    rows.push({
      id: th.id, lang: th.lang, kind: "compaction", turns: th.turns + 2,
      prompt: th.finalQ, plant: th.plant,
      trail, contextInfoEnd: ciEnd.body,
      bare: { ...bare, checks: score(th.checks, bare.text) },
      sran: { ms: t.ms, ttfbMs: t.ttfbMs, text: t.text, conv: cid, checks: score(th.checks, t.text) },
    });
    const r = rows[rows.length - 1];
    console.log(`\n${th.id}: crossed=${trail.some((x) => x.compacted)} maxRatio=${Math.max(...trail.map((x) => x.used_ratio)).toFixed(3)} ` +
      `omitted=${ciEnd.body.omitted_older} state=${ciEnd.body.state_present} | bare ${r.bare.checks.passed}/${r.bare.checks.total} sran ${r.sran.checks.passed}/${r.sran.checks.total}`);
  }
  const f = path.join(OUT, `compaction-${MODEL.replace(/[\/:]/g, "_")}.json`);
  fs.writeFileSync(f, JSON.stringify({ model: MODEL, captured_at: new Date().toISOString(), rows }, null, 1));
  console.log(`written ${f}`);
})();
