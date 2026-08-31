// FIX33 §6.2 probe harness — the FIX32 probe set (P1 instruction / P2 recall@3 /
// P3 recall@5 / P4 correction@12 / P5a draft / P5b reference) at 20/50/100/200/400
// turns, real model through the running stack.
//
//   node probe.js web  20,50,100,200,400     → chat-edge (the web path)
//   node probe.js desktop 20,50,100,200,400  → the desktop compile path
//       (apps/desktop/dist ContextManager + GatewayStateExtractor), driven in
//       Node against the gateway's PUBLIC path with the shared API key — the
//       same request shapes the installed app sends; NOT the installed app.
//
// Seeding: the long history is IMPORTED (chat-edge's desktop-import endpoint /
// a local chain), not generated turn by turn — so only the probes and the
// compaction extractions cost model calls. Composition follows FIX32 §1.1.
const fs = require("fs");
const path = require("path");
const { mint } = require("./mint-jwt");
const REPO = path.resolve(__dirname, "..", "..");

const API = "http://localhost:4000/api/v1";
const GATEWAY = "http://localhost:8080";
const USER = "c6e631e2-51ab-44a4-9eed-45d6fc1ce9ff";
const TENANT = "f65614b9-3328-5d45-af3a-9ebc2b6d3034";
const MODEL = process.env.PROBE_MODEL || "gpt-5.6-terra";
const OUT = path.join(__dirname, `probe-${process.argv[2]}.json`);
const GATEWAY_KEY = (() => {
  const env = fs.readFileSync(path.join(REPO, ".env"), "utf8");
  const m = env.match(/^GATEWAY_API_KEY=(.+)$/m);
  return m ? m[1].trim() : "";
})();

// ─── seed corpus (FIX32 §1.1 composition) ───
const THAI_DOC = ("ระบบบริหารจัดการโครงการต้องรองรับการวางแผน การติดตามความคืบหน้า และการรายงานผลอย่างต่อเนื่อง ทีมงานควรบันทึกความเสี่ยงและมาตรการรับมือให้ชัดเจน รวมถึงกำหนดผู้รับผิดชอบในแต่ละงานย่อย เพื่อให้การส่งมอบเป็นไปตามกำหนดเวลาและงบประมาณที่ตกลงกันไว้ ").repeat(14);
const CODE = "```python\ndef summarize(items):\n    total = sum(i.amount for i in items)\n    return {\"count\": len(items), \"total\": total}\n```";
const POOL = [
  ["ช่วยอธิบายเรื่องการตั้ง OKR หน่อย", "OKR คือการตั้งเป้าหมายกับผลลัพธ์ที่วัดได้ ตั้งเป้า 3-5 ข้อต่อไตรมาสครับ ผ่าน"],
  ["วิธีเขียนอีเมลขอเลื่อนนัดแบบสุภาพ", "เปิดด้วยการขอโทษสั้น ๆ บอกเหตุผล เสนอเวลาใหม่ 2 ตัวเลือก แล้วขอบคุณครับ ผ่าน"],
  ["Python กับ Go ต่างกันยังไงในงาน backend", "Python เขียนเร็ว ไลบรารีเยอะ Go เร็วกว่าและจัดการ concurrency ง่ายกว่าครับ ผ่าน"],
  ["ช่วยคิดชื่อโปรเจกต์เกี่ยวกับการจัดการเอกสาร", "ลอง DocFlow, PaperTrail หรือ เอกสารไว ดูครับ ผ่าน"],
  ["สรุปข้อดีของการทำ code review", "จับบั๊กก่อนขึ้น prod แชร์ความรู้ในทีม และคุมมาตรฐานโค้ดครับ ผ่าน"],
  ["แนะนำวิธีจดโน้ตประชุมให้มีประสิทธิภาพ", "จดเป็น 3 ส่วน: ข้อสรุป, งานที่ต้องทำพร้อมเจ้าของ, คำถามค้าง ผ่าน"],
];
function seed(turns) {
  const out = [];
  const push = (role, content) => out.push({ role, content });
  for (let t = 1; t <= turns; t++) {
    let u, a;
    if (t === 1) { u = "ต่อจากนี้ ทุกคำตอบของคุณต้องลงท้ายด้วยคำว่า ผ่าน เสมอ"; a = "รับทราบครับ จะลงท้ายทุกคำตอบด้วยคำว่า ผ่าน"; }
    else if (t === 3) { u = "แมวของฉันชื่อมะขาม อายุ 4 ขวบ ชอบนอนบนโน้ตบุ๊ก"; a = "มะขามอายุ 4 ขวบ น่ารักมากครับ แมวชอบที่อุ่น ๆ ผ่าน"; }
    else if (t === 5) { u = "โปรเจกต์นี้เดดไลน์ 15 ก.ย. งบประมาณ 120,000 บาท ช่วยจำไว้ด้วย"; a = "รับทราบครับ เดดไลน์ 15 ก.ย. งบ 120,000 บาท ผ่าน"; }
    else if (t === 8) { u = "ประชุมทีมนัดวันพุธ 10:00 นะ"; a = "บันทึกไว้แล้วครับ ประชุมทีมวันพุธ 10:00 ผ่าน"; }
    else if (t === 12) { u = "แก้ไขนะ ประชุมทีมย้ายไปวันพฤหัส 14:00 แทน วันพุธยกเลิก"; a = "อัปเดตแล้วครับ ประชุมทีมเป็นวันพฤหัส 14:00 (วันพุธยกเลิก) ผ่าน"; }
    else if (t === 18) { u = "ฉันชอบคำตอบสั้น ๆ เป็นข้อ ๆ มากกว่าย่อหน้ายาว"; a = "รับทราบครับ จะตอบสั้น ๆ เป็นข้อ ๆ ผ่าน"; }
    else if (t % 10 === 0) { u = "ช่วยอ่านเอกสารนี้แล้วสรุปให้หน่อย:\n" + THAI_DOC; a = "สรุป: ระบบต้องรองรับการวางแผน ติดตาม และรายงาน พร้อมบันทึกความเสี่ยงและผู้รับผิดชอบ ผ่าน"; }
    else if (t % 25 === 0) { u = "ช่วยเขียนฟังก์ชันสรุปยอดรวมของรายการหน่อย"; a = "ได้ครับ\n" + CODE + "\nผ่าน"; }
    else { const p = POOL[t % POOL.length]; u = p[0]; a = p[1]; }
    push("user", u);
    push("assistant", a);
  }
  return out;
}

// ─── probes (fixed before scoring; FIX32 §4) ───
const PROBES = [
  ["P1", "สรุปสั้น ๆ ว่าวันนี้เราคุยเรื่องอะไรกันบ้าง", (r) => /ผ่าน[\s.!。”"')]*$/.test(r.trim())],
  ["P2", "แมวของฉันชื่ออะไร อายุเท่าไหร่", (r) => r.includes("มะขาม") && /4|สี่/.test(r)],
  ["P3", "เดดไลน์กับงบประมาณของโปรเจกต์คือเท่าไหร่", (r) => /15/.test(r) && /ก\.ย\.|กันยายน|September|Sep/.test(r) && /120[,.]?000|120K|แสนสองหมื่น/.test(r)],
  ["P4", "ตกลงประชุมทีมนัดวันไหน กี่โมง", (r) => /พฤหัส|Thursday/.test(r) && /14[:.]00|บ่ายสอง|2 ?PM|14 ?น/.test(r) && !/พุธ|Wednesday/.test(r.replace(/ยกเลิก|ไม่ใช่วันพุธ|เดิมวันพุธ/g, ""))],
  ["P5a", "ช่วยร่างข้อความสั้น ๆ แจ้งทีมเรื่องนัดประชุมให้หน่อย", (r) => r.trim().length >= 60],
  ["P5b", "อันเมื่อกี้ เปลี่ยนเป็นวันศุกร์", (r) => /ศุกร์|Friday/.test(r)],
];

async function gatewayComplete(messages, maxTokens = 600) {
  const res = await fetch(`${GATEWAY}/v1/chat/completions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${GATEWAY_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: MODEL, messages, stream: false, max_tokens: maxTokens }),
  });
  if (!res.ok) throw new Error(`gateway ${res.status}: ${(await res.text()).slice(0, 300)}`);
  const j = await res.json();
  const c = j.choices?.[0]?.message?.content;
  return typeof c === "string" ? c : "";
}

// ─── WEB (chat-edge) ───
async function runWeb(marks) {
  const token = mint({ secret: "dev-stable-portal-key-change-in-prod", sub: USER, email: "probe@local", tenantId: TENANT });
  const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const api = async (p, init = {}) => {
    const r = await fetch(`${API}${p}`, { ...init, headers: { ...h, ...(init.headers ?? {}) } });
    const text = await r.text();
    if (!r.ok) throw new Error(`${init.method ?? "GET"} ${p} → ${r.status} ${text.slice(0, 300)}`);
    return text ? JSON.parse(text) : null;
  };
  const results = [];
  for (const turns of marks) {
    const t0 = Date.now();
    const conv = await api("/conversations", { method: "POST", body: JSON.stringify({ model_id: MODEL, title: `FIX33 probe web ${turns}` }) });
    const id = conv.id;
    const msgs = seed(turns);
    const base = Date.now() - 86_400_000;
    for (let i = 0; i < msgs.length; i += 200) {
      const batch = msgs.slice(i, i + 200).map((m, j) => ({ client_id: `seed-${turns}-${i + j}`, role: m.role, content: m.content, created_at: new Date(base + (i + j) * 1000).toISOString() }));
      await api(`/conversations/${id}/messages/import`, { method: "POST", body: JSON.stringify({ messages: batch }) });
    }
    const before = await api(`/conversations/${id}/context-info`);
    const probes = {};
    for (const [name, q, check] of PROBES) {
      let reply = "";
      try {
        const r = await api(`/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ content: q, stream: false }) });
        reply = r?.assistant_message?.content ?? r?.assistant?.content ?? r?.content ?? JSON.stringify(r).slice(0, 200);
      } catch (e) {
        reply = `ERROR ${e.message}`;
      }
      probes[name] = { pass: check(reply), reply: reply.slice(0, 240) };
      process.stderr.write(`web ${turns} ${name} ${probes[name].pass ? "✓" : "✗"}\n`);
    }
    const after = await api(`/conversations/${id}/context-info`);
    results.push({ surface: "web", turns, conversation: id, before, after, probes, seconds: Math.round((Date.now() - t0) / 1000) });
    fs.writeFileSync(OUT, JSON.stringify(results, null, 2));
  }
  return results;
}

// ─── DESKTOP (dist ContextManager, gateway public path) ───
async function runDesktop(marks) {
  const dist = path.join(REPO, "apps", "desktop", "dist", "main");
  const { ContextManager } = require(path.join(dist, "context-manager.js"));
  const { GatewayStateExtractor, MemoryConversationStateStore } = require(path.join(dist, "conversation-state.js"));
  const log = { info: (m) => process.stderr.write(`  [desk] ${m}\n`), warn: (m) => process.stderr.write(`  [desk][warn] ${m}\n`), error: (m) => process.stderr.write(`  [desk][error] ${m}\n`) };
  // The extractor's only dependency: a streamCompletion. Non-stream under the hood.
  const gateway = {
    async *streamCompletion(req) {
      try {
        const text = await gatewayComplete(req.messages, req.max_tokens ?? 1500);
        yield { kind: "content_delta", delta: text };
        yield { kind: "done", finish_reason: "stop" };
      } catch (e) {
        yield { kind: "error", code: "gateway", message: e.message };
      }
    },
  };
  const SYSTEM = "You are the SRAN Desktop assistant. Reply in the user's language (Thai stays Thai). Keep every standing instruction the user gave earlier in this conversation. Be concise.";
  const results = [];
  for (const turns of marks) {
    const t0 = Date.now();
    const events = [];
    const mgr = new ContextManager({ store: new MemoryConversationStateStore(), extractor: new GatewayStateExtractor(gateway, log), log, emit: (_c, e, d) => events.push({ e, d }) });
    const convId = `probe-${turns}`;
    let chain = seed(turns);
    const probes = {};
    let compileInfo = null;
    for (const [name, q, check] of PROBES) {
      const messages = [{ role: "system", content: SYSTEM }, ...chain, { role: "user", content: q }];
      let reply = "";
      try {
        const cc = await mgr.compile(convId, messages, MODEL, 0, new AbortController().signal);
        compileInfo = compileInfo ?? { compacted: cc.compacted, dropped: cc.dropped, sent: cc.sentMessages, total: cc.totalMessages, statePresent: cc.statePresent, extractionRan: cc.extractionRan, usedRatio: cc.budgetEvent.used_ratio };
        reply = await gatewayComplete(cc.messages, 600);
      } catch (e) {
        reply = `ERROR ${e.message}`;
      }
      chain = [...chain, { role: "user", content: q }, { role: "assistant", content: reply }];
      probes[name] = { pass: check(reply), reply: reply.slice(0, 240) };
      process.stderr.write(`desktop ${turns} ${name} ${probes[name].pass ? "✓" : "✗"}\n`);
    }
    results.push({ surface: "desktop-sim", turns, first_compile: compileInfo, events: events.filter((x) => x.e !== "context_budget").map((x) => x.e), last_budget: events.filter((x) => x.e === "context_budget").pop()?.d ?? null, state: mgr.state(convId), probes, seconds: Math.round((Date.now() - t0) / 1000) });
    fs.writeFileSync(OUT, JSON.stringify(results, null, 2));
  }
  return results;
}

(async () => {
  const surface = process.argv[2];
  const marks = (process.argv[3] || "20,50,100,200,400").split(",").map(Number);
  const results = surface === "web" ? await runWeb(marks) : await runDesktop(marks);
  console.log(`| mark | ${PROBES.map((p) => p[0]).join(" | ")} |`);
  for (const r of results) console.log(`| ${r.turns} | ${PROBES.map((p) => (r.probes[p[0]].pass ? "✓" : "✗")).join(" | ")} |`);
})().catch((e) => {
  console.error("FATAL", e);
  process.exit(1);
});
