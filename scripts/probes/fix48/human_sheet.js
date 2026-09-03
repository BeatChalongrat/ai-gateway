// FIX48 · C3.5 + C1-B — two sheets for the owner.
//
//   HUMAN-CHECK.md  10 questions drawn with the SAME seed as the judging,
//                   with both answers unlabelled, for the owner to score by
//                   hand. If the owner disagrees with the judges, the rubric
//                   is wrong — that is the point of the exercise.
//   CLAUDE-SHEET.md the 10 questions for the manual run against real Claude
//                   (option B1: no Anthropic API key exists on this
//                   deployment, so the only honest comparison is the owner
//                   pasting these into claude.ai).
//
//   node scripts/probes/fix48/human_sheet.js results/expA-<model>.json <outdir>

const fs = require("node:fs");
const path = require("node:path");

const SEED = 20260903; // the judging seed, reused so the draw is reproducible
function rng(seed) {
  let a = seed >>> 0;
  return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

const infile = process.argv[2];
const outdir = process.argv[3];
const data = JSON.parse(fs.readFileSync(infile, "utf8"));
const judged = JSON.parse(fs.readFileSync(infile.replace(/\.json$/, "-judged.json"), "utf8"));

// Draw 10, spread across kinds so one category cannot dominate the sheet.
const r = rng(SEED + 1);
const byKind = {};
for (const row of data.rows) (byKind[row.kind] ??= []).push(row);
const kinds = Object.keys(byKind);
const picked = [];
let guard = 0;
while (picked.length < 10 && guard++ < 500) {
  const k = kinds[Math.floor(r() * kinds.length)];
  const pool = byKind[k].filter((x) => !picked.includes(x));
  if (!pool.length) continue;
  picked.push(pool[Math.floor(r() * pool.length)]);
}

const clip = (s, n) => { const t = (s || "(ว่างเปล่า)").trim(); return t.length > n ? t.slice(0, n) + "\n…(ตัดที่ " + n + " ตัวอักษร)" : t; };

// ── sheet 1: blind human check ────────────────────────────────────────────
let h = `# FIX48 · HUMAN-CHECK — 10 ข้อให้เจ้าของอ่านเอง

**ทำไมต้องมีแผ่นนี้:** กรรมการที่เป็น LLM ให้ผลไปทางเดียวกันทั้งสามตัว แต่ **ตัวไหนชนะรายข้อกลับไม่ตรงกัน (24–71%)**
ถ้าคนอ่านแล้วไม่เห็นด้วยกับกรรมการ แปลว่า rubric ผิด ไม่ใช่ระบบผิด

**วิธีทำ:** อ่านสองคำตอบ แล้วเลือกว่าอันไหนดีกว่า **โดยยังไม่ต้องดูเฉลย** ที่ท้ายไฟล์
เกณฑ์: ถูกต้อง · ตรงคำถาม · ใช้ข้อมูลที่ให้มา · ยอมรับเมื่อไม่รู้ · **ห้ามให้คะแนนความยาว**

| # | ข้อ | หมวด | คุณเลือก (ก/ข/เสมอ) |
|---|---|---|---|
${picked.map((p, i) => `| ${i + 1} | ${p.id} | ${p.kind} | |`).join("\n")}

---

`;

const key = [];
picked.forEach((p, i) => {
  const flip = r() < 0.5; // independent draw from the judges' draw
  const A = flip ? p.bare.text : p.sran.text;
  const B = flip ? p.sran.text : p.bare.text;
  key.push({ n: i + 1, id: p.id, ก: flip ? "API เปล่า" : "ผ่าน SRAN", ข: flip ? "ผ่าน SRAN" : "API เปล่า" });
  h += `## ${i + 1}. ${p.id} — ${p.kind}\n\n**คำถาม:** ${p.prompt}\n\n`;
  if (p.plant) h += `**สิ่งที่ผู้ใช้บอกไว้ก่อนหน้า:** ${p.plant}\n\n`;
  h += `### คำตอบ ก\n\n\`\`\`\n${clip(A, 1800)}\n\`\`\`\n\n### คำตอบ ข\n\n\`\`\`\n${clip(B, 1800)}\n\`\`\`\n\n---\n\n`;
});

h += `## เฉลย (อ่านหลังเลือกครบแล้ว)\n\n| # | ข้อ | ก คือ | ข คือ | กรรมการว่าอย่างไร |\n|---|---|---|---|---|\n`;
for (const k of key) {
  const jr = judged.rows.find((x) => x.id === k.id);
  const votes = judged.judges.map((jn) => jr && jr.judges[jn] ? `${jn.split("/").pop()}:${jr.judges[jn].winner === "bare" ? "API เปล่า" : jr.judges[jn].winner === "sran" ? "SRAN" : "เสมอ"}` : null).filter(Boolean);
  h += `| ${k.n} | ${k.id} | ${k["ก"]} | ${k["ข"]} | ${votes.join(" · ") || "ไม่มีคำตัดสิน"} |\n`;
}
fs.writeFileSync(path.join(outdir, "HUMAN-CHECK.md"), h);

// ── sheet 2: the manual Claude run ────────────────────────────────────────
let c = `# FIX48 · CLAUDE-SHEET — 10 คำถามสำหรับรันมือกับ Claude ตัวจริง (การทดลอง B)

**ทำไมต้องทำมือ:** deployment นี้ **ไม่มี Anthropic API key ที่ใช้ได้** (แถว \`provider_keys\` ของ anthropic คือ
\`is_active=false\` ยาว 52 ตัว = placeholder; probe ล่าสุดข้าม provider นี้ทั้ง provider)
และ \`anthropic/claude-sonnet-4.5\` ที่เรามีคือ **เส้นทาง OpenRouter = โมเดลเปล่า ไม่ใช่สินค้า Claude**
เอามาสวมรอยเป็น "Claude" ไม่ได้ตามกติกาของรอบนี้ → เหลือทางเดียวคือ B1 รันมือ

**วิธีทำ**
1. เปิด claude.ai (แชทใหม่ ไม่มี project / ไม่มี custom instructions — ให้ใกล้ค่าเริ่มต้นที่สุด)
2. วางคำถามทีละข้อ **แชทใหม่ต่อหนึ่งข้อ** (ยกเว้นข้อที่บอกว่าให้ทำสองเทิร์น)
3. ก๊อปคำตอบกลับมาวางใต้แต่ละข้อในไฟล์นี้
4. ส่งไฟล์คืน แล้วผมจะให้กรรมการชุดเดิม (ปิดชื่อ สลับลำดับ rubric เดิม) ตัดสินเทียบกับคำตอบของ SRAN

**ข้อจำกัดที่จะเขียนไว้ในรายงาน:** N=10 · Claude อาจเป็นคนละรุ่นกับที่ SRAN ใช้ → **ผลนี้บอกว่า "สินค้าเทียบสินค้า" เป็นยังไง แต่แยกไม่ได้ว่าต่างเพราะโมเดลหรือเพราะกรอบ** (การทดลอง A คือตัวที่แยกได้)

---

`;
picked.forEach((p, i) => {
  c += `## ${i + 1}. ${p.id} (${p.kind})\n\n`;
  if (p.plant) c += `**เทิร์นที่ 1 — พิมพ์ข้อความนี้ก่อน:**\n\n> ${p.plant}\n\n**เทิร์นที่ 2 — ${p.kind === "memory" ? "เปิดแชทใหม่ แล้วพิมพ์:" : "พิมพ์ต่อในแชทเดิม:"}**\n\n> ${p.prompt}\n\n`;
  else c += `**พิมพ์ข้อความนี้:**\n\n> ${p.prompt}\n\n`;
  c += `**คำตอบของ Claude:**\n\n\`\`\`\n(วางที่นี่)\n\`\`\`\n\n**เวลาจนเห็นตัวอักษรแรก (คร่าว ๆ):** ___ วินาที\n\n---\n\n`;
});
fs.writeFileSync(path.join(outdir, "CLAUDE-SHEET.md"), c);

console.log("picked:", picked.map((p) => p.id).join(", "));
console.log("written HUMAN-CHECK.md + CLAUDE-SHEET.md in", outdir);
