// Memory-instruction probe — mechanical scoring, no model judge.
//
// Two questions decide whether saved memory is working, and neither
// needs a second model to answer:
//
//   1. was the memory injected?      -> context_sources on the reply
//   2. did the answer obey it?       -> a rule per instruction
//
// The four memory questions are the ones from the earlier measurement
// (kept verbatim so the numbers are comparable); the three control
// questions are new and exist to catch the opposite failure — a model
// that has been told to follow standing instructions may start
// dragging saved facts into answers that never asked for them.
//
// Usage:
//   node memory_probe.js <label>
// Writes results/<label>.json next to this file.

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const CHAT_EDGE = "http://localhost:4000/api/v1";
const USER = "f1848000-0000-4000-8000-000000000048"; // the seeded probe user
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const MODEL = "deepseek-v4-pro";
const SECRET = "dev-stable-portal-key-change-in-prod";

function mintJWT() {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const head = { alg: "HS256", typ: "JWT" };
  const claims = {
    sub: USER, email: "fix48-probe@local", type: "portal", token_use: "access",
    tenant_id: TENANT, tenant_role: "owner", token_version: 0,
    iat: now, exp: now + 7200, iss: "sran-portal",
  };
  const data = `${b64(head)}.${b64(claims)}`;
  const sig = crypto.createHmac("sha256", SECRET).update(data).digest("base64url");
  return `${data}.${sig}`;
}

// ── the questions ───────────────────────────────────────────────────
//
// `expects` names the saved memory the answer must honour; `check`
// returns {ok, why} from the answer text alone.

const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/;

function bulletLines(text) {
  return text.split("\n").filter((l) => BULLET.test(l));
}

/** The saved response_preference: short, bulleted, at most 5 points,
 *  and no introduction before the first bullet. */
function checkBulletPreference(text) {
  const lines = text.split("\n");
  const bullets = bulletLines(text);
  if (bullets.length === 0) return { ok: false, why: "no bulleted list" };
  if (bullets.length > 5) return { ok: false, why: `${bullets.length} bullets (max 5)` };
  const firstBulletAt = lines.findIndex((l) => BULLET.test(l));
  const preamble = lines.slice(0, firstBulletAt).join(" ").trim();
  // A short heading is fine; a sentence of introduction is what the
  // instruction forbids.
  if (preamble.length > 80) {
    return { ok: false, why: `introduction before the first bullet (${preamble.length} chars)` };
  }
  return { ok: true, why: `${bullets.length} bullets, no introduction` };
}

/** The saved confirmed_instruction: code examples in TypeScript, never
 *  JavaScript. */
function checkTypeScript(text) {
  const lower = text.toLowerCase();
  const tsFence = /```\s*(ts|typescript)/.test(lower);
  const jsFence = /```\s*(js|javascript)\b/.test(lower);
  const tsSignals = /:\s*(string|number|boolean|any|unknown)\b|interface\s+\w+|as\s+\w+;|\.ts\b/.test(text);
  if (jsFence && !tsFence) return { ok: false, why: "javascript code fence" };
  if (tsFence || tsSignals) return { ok: true, why: tsFence ? "typescript fence" : "typescript type annotations" };
  return { ok: false, why: "no typescript markers in the code" };
}

/** The saved explicit_fact: the company uses PostgreSQL, not MySQL. */
function checkPostgres(text) {
  const lower = text.toLowerCase();
  const pg = /pg_dump|postgres|psql/.test(lower);
  const my = /mysqldump|mysql/.test(lower);
  if (my && !pg) return { ok: false, why: "answered for MySQL" };
  if (pg) return { ok: true, why: "answered for PostgreSQL" };
  return { ok: false, why: "no database named" };
}

const MEMORY_QUESTIONS = [
  { id: "mem-1", lang: "th", expects: "response_preference (<=5 bullets, no intro)",
    prompt: "แนะนำร้านอาหารสำหรับเลี้ยงลูกค้ามื้อเย็นให้หน่อย 3 ร้าน พร้อมเหตุผลสั้น ๆ",
    check: checkBulletPreference },
  { id: "mem-2", lang: "th", expects: "response_preference (<=5 bullets, no intro)",
    prompt: "วิธีเตรียมตัวก่อนสัมภาษณ์งานตำแหน่งผู้จัดการโครงการ",
    check: checkBulletPreference },
  { id: "mem-3", lang: "en", expects: "confirmed_instruction (TypeScript, never JavaScript)",
    prompt: "Show me how to read a JSON file and print one field.",
    check: checkTypeScript },
  { id: "mem-4", lang: "th", expects: "explicit_fact (PostgreSQL, not MySQL)",
    prompt: "ช่วยเขียนคำสั่งสำรองฐานข้อมูลรายวันให้หน่อย",
    check: checkPostgres },
];

// Control questions: nothing here relates to any saved memory. The
// failure they catch is memory being dragged in where it does not
// belong — the reason the original block said "do not force it".
const SAVED_FACT_MARKERS = [
  "PRJ-7742", "CT-9031", "4,750,000", "db-prod-03", "Kiattisak",
  "#ops-red", "Somchai", "Pim", "Krit", "Kubernetes", "PostgreSQL",
  "pnpm", "14 พฤศจิกายน", "ฝ่ายการตลาด", "45 days",
];

function checkNoMemoryLeak(text) {
  const hits = SAVED_FACT_MARKERS.filter((m) => text.includes(m));
  return hits.length === 0
    ? { ok: true, why: "no saved fact appears" }
    : { ok: false, why: `saved facts forced in: ${hits.join(", ")}` };
}

const CONTROL_QUESTIONS = [
  { id: "ctl-1", lang: "en", expects: "no saved memory forced in",
    prompt: "What is the capital of Japan? Answer in one sentence.",
    check: checkNoMemoryLeak },
  { id: "ctl-2", lang: "th", expects: "no saved memory forced in",
    prompt: "อธิบายสั้น ๆ ว่าทำไมท้องฟ้าเป็นสีฟ้า",
    check: checkNoMemoryLeak },
  { id: "ctl-3", lang: "en", expects: "no saved memory forced in",
    prompt: "Convert 12 kilometres to miles and show the arithmetic.",
    check: checkNoMemoryLeak },
];

// ── driver ──────────────────────────────────────────────────────────

async function ask(jwt, prompt) {
  const convRes = await fetch(`${CHAT_EDGE}/conversations`, {
    method: "POST",
    headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model_id: MODEL }),
  });
  const conv = await convRes.json();

  const res = await fetch(`${CHAT_EDGE}/conversations/${conv.id}/messages`, {
    method: "POST",
    headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
    body: JSON.stringify({ content: prompt, stream: false }),
  });
  const body = await res.json();

  // Read the stored assistant turn: it carries context_sources, which
  // is how we know the memory was injected rather than assuming it.
  const msgsRes = await fetch(`${CHAT_EDGE}/conversations/${conv.id}/messages`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  const msgs = await msgsRes.json();
  const list = msgs.messages || msgs;
  const last = list[list.length - 1] || {};
  const sources = last.context_sources || [];
  return {
    conversation_id: conv.id,
    answer: String(last.content || body.content || ""),
    memory_sources: sources.filter((s) => String(s.source_type || "").includes("memory")).length,
    total_sources: sources.length,
  };
}

async function main() {
  const label = process.argv[2] || "run";
  const jwt = mintJWT();
  const rows = [];
  for (const q of [...MEMORY_QUESTIONS, ...CONTROL_QUESTIONS]) {
    let r;
    try {
      r = await ask(jwt, q.prompt);
    } catch (e) {
      rows.push({ id: q.id, error: String(e) });
      console.log(`${q.id}: ERROR ${e}`);
      continue;
    }
    const verdict = q.check(r.answer);
    rows.push({
      id: q.id, lang: q.lang, expects: q.expects,
      memory_injected: r.memory_sources > 0,
      memory_sources: r.memory_sources,
      followed: verdict.ok, why: verdict.why,
      answer_chars: r.answer.length,
      conversation_id: r.conversation_id,
    });
    console.log(
      `${q.id}  injected=${r.memory_sources > 0 ? "yes" : "NO "}(${r.memory_sources})  ` +
      `followed=${verdict.ok ? "YES" : "no "}  ${verdict.why}`,
    );
  }

  const mem = rows.filter((r) => r.id.startsWith("mem-"));
  const ctl = rows.filter((r) => r.id.startsWith("ctl-"));
  const summary = {
    label,
    model: MODEL,
    instructions_followed: `${mem.filter((r) => r.followed).length}/${mem.length}`,
    memory_injected: `${mem.filter((r) => r.memory_injected).length}/${mem.length}`,
    controls_clean: `${ctl.filter((r) => r.followed).length}/${ctl.length}`,
  };
  console.log("\nSUMMARY", JSON.stringify(summary));

  const outDir = path.join(__dirname, "results");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(
    path.join(outDir, `${label}.json`),
    JSON.stringify({ ...summary, generated_at: new Date().toISOString(), rows }, null, 2),
  );
}

main();
