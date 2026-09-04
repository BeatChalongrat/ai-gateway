// FIX59 Part 1 — the requests the project memory panel makes, against
// the running stack.
//
// The component test proves the panel SENDS scope_id. This proves the
// server answers those requests the way the panel assumes: one project's
// rows for a scoped list, a hard refusal for an unscoped one, and a
// delete that actually removes the row from that project's list.
//
// Usage: node panel_requests_probe.js
const crypto = require("node:crypto");

const CHAT_EDGE = "http://localhost:4000/api/v1";
const USER = crypto.randomUUID();
const TENANT = crypto.randomUUID();
const SECRET = process.env.PORTAL_JWT_SECRET || "dev-stable-portal-key-change-in-prod";

const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const now = Math.floor(Date.now() / 1000);
const claims = {
  sub: USER, email: "fix59-probe@local", type: "portal", token_use: "access",
  tenant_id: TENANT, tenant_role: "owner", token_version: 0,
  iat: now, exp: now + 7200, iss: "sran-portal",
};
const data = `${b64({ alg: "HS256", typ: "JWT" })}.${b64(claims)}`;
const jwt = `${data}.${crypto.createHmac("sha256", SECRET).update(data).digest("base64url")}`;
const H = { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" };

async function api(method, path, body) {
  const res = await fetch(`${CHAT_EDGE}${path}`, {
    method, headers: H, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* non-JSON */ }
  return { status: res.status, json, text: text.slice(0, 200) };
}

const results = [];
const check = (name, ok, detail) => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
};

(async () => {
  const pa = await api("POST", "/projects", { name: "FIX59 A", instructions: "" });
  const pb = await api("POST", "/projects", { name: "FIX59 B", instructions: "" });
  // POST /projects wraps the body — reading `.id` off the envelope is
  // the trap that made a FIX58 probe report a chat "in project B" that
  // was in no project at all.
  const A = pa.json?.project?.id ?? pa.json?.id;
  const B = pb.json?.project?.id ?? pb.json?.id;
  if (!A || !B) { console.error("no project ids", pa.text, pb.text); process.exit(2); }

  const TOK_A = `alpha-${crypto.randomBytes(3).toString("hex")}`;
  const TOK_B = `bravo-${crypto.randomBytes(3).toString("hex")}`;
  const mkA = await api("POST", "/memories", { content: `A codeword ${TOK_A}`, scope_type: "project", scope_id: A, memory_type: "explicit_fact" });
  await api("POST", "/memories", { content: `B codeword ${TOK_B}`, scope_type: "project", scope_id: B, memory_type: "explicit_fact" });

  // 1. the exact request the panel sends for project A
  const listA = await api("GET", `/memories?scope_type=project&scope_id=${A}&limit=100`);
  const contentsA = (listA.json?.memories ?? []).map((m) => m.content).join(" | ");
  check("opening project A lists A's memory", contentsA.includes(TOK_A), contentsA);
  check("opening project A does NOT list B's memory", !contentsA.includes(TOK_B), contentsA);

  // 2. and for project B
  const listB = await api("GET", `/memories?scope_type=project&scope_id=${B}&limit=100`);
  const contentsB = (listB.json?.memories ?? []).map((m) => m.content).join(" | ");
  check("opening project B lists only B's memory",
    contentsB.includes(TOK_B) && !contentsB.includes(TOK_A), contentsB);

  // 3. the shape the panel must never send
  const unscoped = await api("GET", "/memories?scope_type=project&limit=100");
  check("an unscoped project list is refused, not answered", unscoped.status === 400,
    `status=${unscoped.status} ${unscoped.text}`);

  // 4. delete one item, from that project's list
  const delId = mkA.json?.id;
  const del = await api("DELETE", `/memories/${delId}`);
  check("deleting one memory succeeds", del.status === 204 || del.status === 200, `status=${del.status}`);
  const afterA = await api("GET", `/memories?scope_type=project&scope_id=${A}&limit=100`);
  const afterContentsA = (afterA.json?.memories ?? []).map((m) => m.content).join(" | ");
  check("the deleted memory is gone from project A", !afterContentsA.includes(TOK_A),
    afterContentsA || "(empty)");
  const afterB = await api("GET", `/memories?scope_type=project&scope_id=${B}&limit=100`);
  check("project B is untouched by A's delete",
    (afterB.json?.memories ?? []).map((m) => m.content).join(" | ").includes(TOK_B));

  const passed = results.filter((r) => r.ok).length;
  console.log(`\nSUMMARY ${JSON.stringify({ passed: `${passed}/${results.length}` })}`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error("PROBE ERROR", e); process.exit(2); });
