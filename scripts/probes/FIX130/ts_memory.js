#!/usr/bin/env node
// FIX130 A1/A2/A4 on the TEST stack — memory learned, memory off is off, no crossing accounts.
//
//   node scripts/probes/FIX130/ts_memory.js <tag> <case>[,<case>…]
//
// cases
//   off     user A has one saved memory (added by hand through the API, never by the
//           model): a memory-OFF chat, a TEMPORARY chat and a memory-ON chat ask the
//           same question. Off and temporary must not use it (answer + no saved_memory
//           source); on must.
//   learn   user B states facts in a short memory-ON chat (2 turns). A suggestion must
//           appear (pending), written in Thai, sourced from that chat.
//   learn20 the NIGHT2 shape: 11 turns (22 messages) in a memory-ON chat.
//   accept  accept B's suggestion(s) → a NEW chat (memory on) uses the fact; reject one →
//           gone; delete the accepted one → the next chat no longer uses it.
//   cross   user C (other tenant) and user A (same tenant as B) ask about B's fact in
//           memory-ON chats: neither may see it; their memory lists never contain it.
// No prompt asks the model to remember anything (the card forbids making it call remember).
"use strict";
const L = require("./ts_lib");

const FACT_Q = "[FIX130] ชื่อเล่นของฉันคืออะไร และฉันทำงานอาชีพอะไร ถ้าไม่มีข้อมูลให้ตอบว่า \"ไม่ทราบ\" สั้นๆ";
const savedSources = (msgs) => (msgs?.messages || msgs || []).filter((m) => m.role === "assistant")
  .flatMap((m) => (m.context_sources || []).filter((s) => s.source_type === "saved_memory"));

async function caseOff(tag) {
  const tok = L.token("A");
  const mem = await L.api(tok, "/memories", { method: "POST", body: JSON.stringify({ scope_type: "global_user", memory_type: "explicit_fact", content: "ผู้ใช้ชื่อเล่นว่า ต้นกล้า และทำงานเป็นนักบัญชี" }) });
  const out = { memory: { status: mem.status, id: mem.body?.id, st: mem.body?.status } };
  for (const [name, opts] of [["off", { memory: false }], ["temporary", { temporary: true }], ["on", { memory: true }]]) {
    const id = await L.newChat(tok, opts);
    const conv = (await L.api(tok, `/conversations/${id}`)).body;
    const t = await L.turn(tok, id, FACT_Q);
    const msgs = await L.messages(tok, id);
    const src = savedSources(msgs);
    out[name] = { id, memory_enabled: conv.memory_enabled, is_temporary: conv.is_temporary, term: t.term?.ev,
      answer: t.content.slice(0, 200), usesFact: /ต้นกล้า|นักบัญชี/.test(t.content), saved_memory_sources: src.length,
      events: t.events.filter((e) => /memory/.test(e)) };
    await L.deleteChat(tok, id);
  }
  await L.api(tok, `/memories/${out.memory.id}`, { method: "DELETE" });
  out.pass = !out.off.usesFact && out.off.saved_memory_sources === 0 && !out.temporary.usesFact && out.temporary.saved_memory_sources === 0
    && out.on.usesFact && out.on.saved_memory_sources > 0;
  return out;
}

const LEARN_TURNS = [
  "[FIX130] สวัสดีครับ ผมชื่อเล่นว่าภูผา ทำงานเป็นวิศวกรโยธาอยู่ที่เชียงใหม่ ช่วยแนะนำร้านกาแฟเงียบๆ สำหรับนั่งทำงานหน่อย ตอบสั้นๆ",
  "ขอบคุณครับ อีกเรื่องนะ ผมแพ้กุ้งมาก ช่วยแนะนำเมนูมื้อเย็นง่ายๆ ที่ไม่มีอาหารทะเล 3 อย่าง ตอบสั้นๆ",
];
const LEARN20_EXTRA = [
  "แล้วถ้าเป็นมื้อกลางวันล่ะครับ ตอบสั้นๆ", "ขอสูตรน้ำพริกที่ไม่ใส่กะปิ สั้นๆ", "ผมชอบคำตอบสั้นๆ เป็นข้อๆ นะครับ ขอเคล็ดลับจัดโต๊ะทำงาน",
  "ช่วยสรุปวิธีคำนวณคานรับน้ำหนักแบบง่ายๆ", "ขอบคุณครับ แนะนำหนังสือวิศวกรรมโครงสร้าง 3 เล่ม", "ขอคำแนะนำออกกำลังกายสำหรับคนนั่งนาน สั้นๆ",
  "สรุปข่าวดีๆ ที่ควรติดตามในวงการก่อสร้าง สั้นๆ", "ขอเช็คลิสต์ก่อนเข้าไซต์งาน 5 ข้อ", "ขอบคุณมากครับ ปิดท้ายด้วยคำคมให้กำลังใจสั้นๆ",
];

async function caseLearn(tag, turns, waitMs = 150000) {
  const tok = L.token("B");
  const before = L.memoriesOf(L.USERS.B.id, L.TENANT_A).length;
  const id = await L.newChat(tok, { memory: true });
  const results = [];
  for (const q of turns) { const t = await L.turn(tok, id, q); results.push({ term: t.term?.ev, chars: t.content.length }); }
  const count = Number(L.csql(`select count(*) from chat.messages_masked where conversation_id='${id}'`));
  let rows = [];
  for (let waited = 0; waited <= waitMs; waited += 10000) {
    rows = L.memoriesOf(L.USERS.B.id, L.TENANT_A).filter((r) => r.split("|")[4] === id);
    if (rows.length) break;
    await L.wait(10000);
  }
  const logs = require("node:child_process").execFileSync("bash", ["-c", "docker logs --since 15m sran-test-chat-edge-api-1 2>&1"], { encoding: "utf8", maxBuffer: 64 << 20 })
    .split("\n").filter((l) => /preference|memory_extraction|extract/i.test(l) && l.includes(id)).map((l) => l.slice(0, 300));
  return { id, turns: results, messages: count, memoriesBefore: before, suggestions: rows, extractorLog: logs,
    thai: rows.some((r) => /[฀-๿]/.test(r.split("|").slice(5).join("|"))), pass: rows.length > 0, kept_chat: id };
}

async function caseAccept(tag) {
  const tok = L.token("B");
  const pending = (await L.api(tok, "/memories?status=pending_confirmation&limit=50")).body;
  const list = pending?.memories || pending?.items || pending || [];
  const out = { pending: list.map((m) => ({ id: m.id, content: m.content, type: m.memory_type })) };
  const fact = list.find((m) => /ภูผา|วิศวกร|เชียงใหม่|กุ้ง/.test(m.content));
  const other = list.find((m) => m !== fact);
  if (!fact) { out.pass = false; out.reason = "no fact suggestion to accept"; return out; }
  out.accept = (await L.api(tok, `/memories/${fact.id}/confirm`, { method: "POST", body: JSON.stringify({ confirmed: true }) })).status;
  if (other) out.reject = { id: other.id, status: (await L.api(tok, `/memories/${other.id}`, { method: "DELETE" })).status };
  const active = ((await L.api(tok, "/memories?limit=100")).body?.memories || []).map((m) => ({ id: m.id, status: m.status, content: m.content }));
  out.activeAfterAccept = active;
  const Q = "[FIX130] จากที่คุณรู้เกี่ยวกับฉัน ฉันชื่อเล่นอะไร ทำงานอะไร และแพ้อะไร ถ้าไม่มีข้อมูลให้ตอบว่า \"ไม่ทราบ\" สั้นๆ";
  const id1 = await L.newChat(tok, { memory: true });
  const t1 = await L.turn(tok, id1, Q);
  out.chatB = { id: id1, answer: t1.content.slice(0, 240), saved_memory_sources: savedSources(await L.messages(tok, id1)).length, events: t1.events.filter((e) => /memory/.test(e)) };
  out.deleteAccepted = (await L.api(tok, `/memories/${fact.id}`, { method: "DELETE" })).status;
  const id2 = await L.newChat(tok, { memory: true });
  const t2 = await L.turn(tok, id2, Q);
  out.chatAfterDelete = { id: id2, answer: t2.content.slice(0, 240), saved_memory_sources: savedSources(await L.messages(tok, id2)).length };
  out.rows = L.memoriesOf(L.USERS.B.id, L.TENANT_A);
  const used = (s) => /ภูผา|วิศวกร|กุ้ง/.test(s);
  out.pass = out.accept === 200 && active.some((m) => m.id === fact.id && m.status === "active") && used(t1.content)
    && out.chatB.saved_memory_sources > 0 && !used(t2.content) && out.chatAfterDelete.saved_memory_sources === 0
    && (!other || !active.some((m) => m.id === other.id));
  await L.deleteChat(tok, id1); await L.deleteChat(tok, id2);
  return out;
}

async function caseCross(tag) {
  // B must hold an ACTIVE fact first (re-add by hand for this check).
  const tokB = L.token("B");
  const m = await L.api(tokB, "/memories", { method: "POST", body: JSON.stringify({ scope_type: "global_user", memory_type: "explicit_fact", content: "ผู้ใช้ชื่อเล่นว่า ภูผา เป็นวิศวกรโยธา" }) });
  const out = { bMemory: m.status };
  for (const who of ["A", "C"]) {
    const tok = L.token(who);
    const id = await L.newChat(tok, { memory: true });
    const t = await L.turn(tok, id, "[FIX130] คุณรู้จักคนชื่อภูผาที่เป็นวิศวกรโยธาไหม ฉันคือคนนั้นหรือเปล่า ตอบสั้นๆ ตามข้อมูลที่คุณมีเกี่ยวกับฉันเท่านั้น");
    const list = ((await L.api(tok, "/memories?limit=100")).body?.memories || []).map((x) => x.content);
    const get = await L.api(tok, `/memories/${m.body?.id}`);
    const del = await L.api(tok, `/memories/${m.body?.id}`, { method: "DELETE" });
    out[who] = { id, sawSource: savedSources(await L.messages(tok, id)).length, listHasIt: list.some((c) => /ภูผา/.test(c)), getStatus: get.status, deleteStatus: del.status, answer: t.content.slice(0, 160) };
    await L.deleteChat(tok, id);
  }
  out.bStillHasIt = L.memoriesOf(L.USERS.B.id, L.TENANT_A).some((r) => /ภูผา/.test(r) && r.startsWith("active|"));
  await L.api(tokB, `/memories/${m.body?.id}`, { method: "DELETE" });
  out.pass = ["A", "C"].every((w) => out[w].sawSource === 0 && !out[w].listHasIt && out[w].getStatus === 404 && out[w].deleteStatus === 404) && out.bStillHasIt;
  return out;
}

(async () => {
  L.assertTestStack();
  const [tag, cases] = [process.argv[2] || "run", (process.argv[3] || "off").split(",")];
  const res = {};
  for (const c of cases) {
    const t0 = Date.now();
    try {
      if (c === "off") res.off = await caseOff(tag);
      else if (c === "learn") res.learn = await caseLearn(tag, LEARN_TURNS);
      else if (c === "learn20") res.learn20 = await caseLearn(tag, [...LEARN_TURNS, ...LEARN20_EXTRA], 180000);
      else if (c === "accept") res.accept = await caseAccept(tag);
      else if (c === "cross") res.cross = await caseCross(tag);
    } catch (e) { res[c] = { error: String(e && e.stack || e) }; }
    if (res[c]) res[c].ms = Date.now() - t0;
    console.log(c, res[c]?.pass === true ? "PASS" : "FAIL", JSON.stringify(res[c]).slice(0, 600));
  }
  L.save(`memory/ts_memory_${tag}.json`, res);
})();
