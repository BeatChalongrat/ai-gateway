// Temporary-chat probe — does a chat with memory turned off actually
// leave nothing behind, and does a normal chat still save?
//
// Counting rows is the whole test. A reply that says "I won't remember
// this" proves nothing; the question is whether chat.user_memories grew.
// So each half runs the same prompt — one that asks the assistant to
// remember something — and the row count is read straight from the
// database on either side of the turn.
//
// Both halves matter. A build that saves nothing at all would pass the
// temporary half and look correct, which is why the normal half runs
// first and has to show the count going UP: it proves the probe can
// detect a save at all before the temporary half claims one didn't
// happen.
//
// The read side is checked too, from context_sources on the stored
// reply: a temporary chat should not have saved memory injected into it.
//
// Usage:
//   node incognito_probe.js <label>
// Writes results/<label>.json next to this file. Counting is done by
// the caller (a psql query) and handed in on stdin as JSON, so this
// script never needs database credentials:
//   node incognito_probe.js <label>

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const CHAT_EDGE = "http://localhost:4000/api/v1";
const USER = "f1848000-0000-4000-8000-000000000048";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const MODEL = "deepseek-v4-pro";
const SECRET = "dev-stable-portal-key-change-in-prod";

const PG = ["exec", "ai-gateway-postgres-webchat", "psql", "-U", "chatedge", "-d", "sran_ai", "-t", "-A", "-c"];

/** Rows in chat.user_memories for the probe user, read from the
 *  database rather than from anything the API reports about itself. */
function memoryCount() {
  const out = execFileSync("docker", [
    ...PG,
    `select count(*) from chat.user_memories where user_id='${USER}' and status='active';`,
  ]);
  return Number(String(out).trim());
}

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

// A prompt that asks for a save in as many words. If the model is going
// to write to memory at all, this is the turn where it happens.
const SAVE_PROMPT =
  "Please remember this for future conversations: my deployment window is " +
  "Tuesdays 02:00-04:00 Bangkok time, and I want release notes in Thai. " +
  "Confirm you have saved it.";

async function post(jwt, url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

/** One turn in a fresh conversation. `temporary` PATCHes
 *  memory_enabled=false on the new row BEFORE the first send, which is
 *  the same order the web client uses. */
async function runTurn(jwt, { temporary }) {
  const conv = await post(jwt, `${CHAT_EDGE}/conversations`, { model_id: MODEL });

  let flagged = null;
  if (temporary) {
    const res = await fetch(`${CHAT_EDGE}/conversations/${conv.id}`, {
      method: "PATCH",
      headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
      body: JSON.stringify({ memory_enabled: false }),
    });
    const patched = await res.json();
    flagged = patched.memory_enabled;
    if (flagged !== false) {
      throw new Error(`PATCH memory_enabled=false did not stick (got ${JSON.stringify(flagged)})`);
    }
  }

  const before = memoryCount();
  await post(jwt, `${CHAT_EDGE}/conversations/${conv.id}/messages`, {
    content: SAVE_PROMPT, stream: false,
  });
  const after = memoryCount();

  const msgsRes = await fetch(`${CHAT_EDGE}/conversations/${conv.id}/messages`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  const msgs = await msgsRes.json();
  const list = msgs.messages || msgs;
  const last = list[list.length - 1] || {};
  const sources = (last.context_sources || []).filter((s) =>
    String(s.source_type || "").includes("memory"),
  );

  return {
    conversation_id: conv.id,
    memory_enabled: temporary ? flagged : true,
    rows_before: before,
    rows_after: after,
    rows_added: after - before,
    memory_injected: sources.length > 0,
    memory_sources: sources.length,
    answer_chars: String(last.content || "").length,
  };
}

async function main() {
  const label = process.argv[2] || "run";
  const jwt = mintJWT();

  // Normal first: it proves a save is detectable on this build.
  const normal = await runTurn(jwt, { temporary: false });
  console.log(
    `normal     rows ${normal.rows_before}->${normal.rows_after} (+${normal.rows_added})  ` +
    `injected=${normal.memory_injected ? "yes" : "no"}`,
  );

  const temporary = await runTurn(jwt, { temporary: true });
  console.log(
    `temporary  rows ${temporary.rows_before}->${temporary.rows_after} (+${temporary.rows_added})  ` +
    `injected=${temporary.memory_injected ? "yes" : "no"}`,
  );

  const verdict = {
    // The probe can see a save happen at all:
    save_is_detectable: normal.rows_added > 0,
    // ...and the temporary chat did not produce one:
    temporary_wrote_nothing: temporary.rows_added === 0,
    // ...and did not read saved memory either:
    temporary_read_nothing: temporary.memory_injected === false,
  };
  console.log("\nSUMMARY", JSON.stringify(verdict));
  if (!verdict.save_is_detectable) {
    console.log(
      "NOTE: the normal chat saved nothing, so 'temporary wrote nothing' is\n" +
      "      not evidence of anything. Fix the control before reading the result.",
    );
  }

  const outDir = path.join(__dirname, "results");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(
    path.join(outDir, `${label}.json`),
    JSON.stringify(
      { label, model: MODEL, generated_at: new Date().toISOString(), verdict, normal, temporary },
      null, 2,
    ),
  );
}

main();
