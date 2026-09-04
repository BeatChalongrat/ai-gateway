// FIX58 — project-scoped memory, end to end against the running stack.
//
// The repository test proves the SQL separates projects. This proves
// the whole path does: real projects, real conversations, real turns on
// the model, and — the part that matters — a check against
// context_sources, which lists exactly which memories were injected
// into each turn. An answer that simply fails to mention project A is
// not evidence; a turn whose injected-source list does not contain
// project A's memory is.
//
// Usage: node project_memory_probe.js <label>
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const CHAT_EDGE = "http://localhost:4000/api/v1";
const USER = process.env.PROBE_USER || crypto.randomUUID();
const TENANT = process.env.PROBE_TENANT || crypto.randomUUID();
const MODEL = process.env.PROBE_MODEL || "qwen3.8-27b-fp8";
const SECRET = process.env.PORTAL_JWT_SECRET || "dev-stable-portal-key-change-in-prod";

function mintJWT() {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const claims = {
    sub: USER, email: "fix58-probe@local", type: "portal", token_use: "access",
    tenant_id: TENANT, tenant_role: "owner", token_version: 0,
    iat: now, exp: now + 7200, iss: "sran-portal",
  };
  const data = `${b64({ alg: "HS256", typ: "JWT" })}.${b64(claims)}`;
  return `${data}.${crypto.createHmac("sha256", SECRET).update(data).digest("base64url")}`;
}

const jwt = mintJWT();
const H = () => ({ Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" });

async function api(method, path, body) {
  const res = await fetch(`${CHAT_EDGE}${path}`, {
    method, headers: H(), body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* non-JSON error body */ }
  return { status: res.status, json, text: text.slice(0, 300) };
}

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}

async function askIn(projectId, prompt) {
  const conv = await api("POST", "/conversations", { model_id: MODEL });
  if (conv.status >= 300) throw new Error(`conversation create: ${conv.status} ${conv.text}`);
  const convId = conv.json.id;
  if (projectId) {
    const mv = await api("PUT", `/conversations/${convId}/project`, { project_id: projectId });
    if (mv.status >= 300) throw new Error(`move to project: ${mv.status} ${mv.text}`);
    // 204 alone does NOT mean the chat landed in the project — an absent
    // project_id un-assigns and answers 204 too. Read it back.
    const back = await api("GET", `/conversations/${convId}`);
    const got = back.json && (back.json.project_id ?? back.json.conversation?.project_id);
    if (got !== projectId) {
      throw new Error(`conversation ${convId} is in project ${got ?? "(none)"}, expected ${projectId}`);
    }
  }
  await api("POST", `/conversations/${convId}/messages`, { content: prompt, stream: false });
  const msgs = await api("GET", `/conversations/${convId}/messages`);
  const list = (msgs.json && (msgs.json.messages || msgs.json)) || [];
  const last = list[list.length - 1] || {};
  const sources = last.context_sources || [];
  return {
    convId,
    answer: String(last.content || ""),
    memorySources: sources.filter((s) => String(s.source_type || "").includes("memory")),
  };
}

(async () => {
  // Two projects, one general scope, one distinctive fact in each.
  const pa = await api("POST", "/projects", { name: "FIX58 client A", instructions: "" });
  const pb = await api("POST", "/projects", { name: "FIX58 client B", instructions: "" });
  if (pa.status >= 300 || pb.status >= 300) {
    console.error("could not create projects:", pa.status, pa.text, pb.status, pb.text);
    process.exit(2);
  }
  // POST /projects answers {"project": {...}} — reading `.id` off the
  // envelope silently yields undefined, JSON.stringify then DROPS the
  // key, and the move endpoint reads a missing project_id as "un-assign"
  // and answers 204. A probe that trusted the status code therefore
  // reported a chat "in project B" that was never in any project. Assert
  // the id instead of hoping for it.
  const A = pa.json.project?.id ?? pa.json.id;
  const B = pb.json.project?.id ?? pb.json.id;
  if (!A || !B) {
    console.error("could not read project ids:", JSON.stringify(pa.json).slice(0, 200));
    process.exit(2);
  }

  const TOK_A = `alpha-${crypto.randomBytes(3).toString("hex")}`;
  const TOK_B = `bravo-${crypto.randomBytes(3).toString("hex")}`;
  const TOK_G = `gamma-${crypto.randomBytes(3).toString("hex")}`;

  const mkA = await api("POST", "/memories", { content: `The project A codeword is ${TOK_A}.`, scope_type: "project", scope_id: A, memory_type: "explicit_fact" });
  const mkB = await api("POST", "/memories", { content: `The project B codeword is ${TOK_B}.`, scope_type: "project", scope_id: B, memory_type: "explicit_fact" });
  const mkG = await api("POST", "/memories", { content: `The general codeword is ${TOK_G}.`, scope_type: "global_user", memory_type: "explicit_fact" });
  check("a project-scoped memory can be created via the public API", mkA.status === 201 && mkB.status === 201, `A=${mkA.status} B=${mkB.status}`);
  check("a global memory can still be created", mkG.status === 201, `status=${mkG.status}`);

  // ── the separation, judged on injected sources, not on wording ────
  const inB = await askIn(B, "What codewords do you know? List every codeword you have been told, exactly.");
  const injectedB = inB.memorySources.map((s) => s.display_label || "").join(" | ");
  check(
    "a project B chat does NOT inject project A's memory",
    !injectedB.includes(TOK_A),
    `injected=[${injectedB}]`,
  );
  check(
    "a project B chat does NOT inject the global memory",
    !injectedB.includes(TOK_G),
    `injected=[${injectedB}]`,
  );
  check(
    "a project B chat DOES inject its own memory",
    injectedB.includes(TOK_B),
    `injected=[${injectedB}]`,
  );
  check(
    "project A's codeword does not appear in project B's answer either",
    !inB.answer.includes(TOK_A),
    `answer="${inB.answer.slice(0, 160)}"`,
  );

  const inG = await askIn(null, "What codewords do you know? List every codeword you have been told, exactly.");
  const injectedG = inG.memorySources.map((s) => s.display_label || "").join(" | ");
  check(
    "a general chat injects the global memory",
    injectedG.includes(TOK_G),
    `injected=[${injectedG}]`,
  );
  check(
    "a general chat injects NEITHER project's memory",
    !injectedG.includes(TOK_A) && !injectedG.includes(TOK_B),
    `injected=[${injectedG}]`,
  );

  // ── deleting a project erases its memories ────────────────────────
  const del = await api("DELETE", `/projects/${A}`);
  check("project A deleted", del.status === 204 || del.status === 200, `status=${del.status}`);
  const listA = await api("GET", `/memories?scope_type=project&scope_id=${A}&status=active&limit=50`);
  const remaining = (listA.json && listA.json.memories) || [];
  check(
    "deleting a project leaves none of its memories active",
    remaining.length === 0,
    `active_rows=${remaining.length}`,
  );

  // ── the API cannot be asked for "every project at once" ───────────
  const noScope = await api("GET", "/memories?scope_type=project&status=active&limit=50");
  check(
    "listing project memories without a scope_id is refused",
    noScope.status === 400,
    `status=${noScope.status} body=${noScope.text.slice(0, 120)}`,
  );

  const passed = results.filter((r) => r.ok).length;
  const summary = { label: process.argv[2] || "run", model: MODEL, passed, total: results.length, results };
  const dir = path.join(__dirname, "results");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${summary.label}.json`), JSON.stringify(summary, null, 2));
  console.log(`\nSUMMARY ${JSON.stringify({ label: summary.label, model: MODEL, passed: `${passed}/${results.length}` })}`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error("PROBE ERROR", e); process.exit(2); });
