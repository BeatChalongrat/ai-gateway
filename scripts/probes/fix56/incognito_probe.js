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
// reply.
//
// FIX56.1 — the expected read side FLIPPED. A temporary chat now reads
// the saved memories and writes none (owner decision), so the probe
// grew three cases the earlier version could not have asked for:
//
//   • recall — a temporary chat is asked something only answerable from
//     a seeded memory, and the answer has to contain it. "injected=yes"
//     alone would pass on a build that put the block in the prompt and
//     then ignored it.
//   • traceless — sum(use_count) over the user's memories must be
//     UNCHANGED across a temporary recall and must RISE across a normal
//     one. Reading through the ordinary path stamps "used just now" on
//     the exact facts consulted in a chat the user believed was leaving
//     nothing behind.
// The sub-agent case is NOT here: run_subagent is a desktop tool, so
// "the switch cannot be escaped by delegating the work" is proven by the
// desktop probe (--probe-e4 fix56mem), not by this one.
//
// Usage:
//   node incognito_probe.js <label>
// Writes results/<label>.json next to this file. Row counts come from
// `docker exec ... psql` against the dev database, so this must run on
// the machine hosting the stack.

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

/** Sum of use_count over the user's active memories. The traceless-read
 *  check: this must not move when a temporary chat recalls something. */
function useCountSum() {
  const out = execFileSync("docker", [
    ...PG,
    `select coalesce(sum(use_count),0) from chat.user_memories where user_id='${USER}' and status='active';`,
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
//
// The fact carries a fresh random token every run, and that is not
// decoration. The first version of this probe asked for the same
// deployment window each time; by the second run the memory service
// recognised the duplicate and wrote no row, so the CONTROL reported
// "the normal chat saved nothing" — on a build whose save path was
// working perfectly. A control that quietly stops being a control is
// worse than no control, because it fails in the direction that reads
// as caution.
const savePrompt = () => {
  const tag = `WIN-${crypto.randomBytes(3).toString("hex").toUpperCase()}`;
  return {
    tag,
    text:
      `Please remember this for future conversations: my change-freeze tag is ${tag}. ` +
      `Confirm you have saved it.`,
  };
};

async function post(jwt, url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

/** A memory the recall case can ask for. Seeded through the ordinary
 *  public API (POST /memories, source_type user_explicit) so this is a
 *  real row, not a fixture, and carries a random token so no earlier run
 *  and no model's general knowledge can supply the answer. */
async function seedRecallMemory(jwt) {
  // Clear the codename rows an earlier run left behind FIRST. Without
  // this the store ends up holding several "internal release codename"
  // facts and the model answers with one of them — which was read as
  // "the chat cannot recall the seeded fact" when what actually happened
  // is that it recalled a different one perfectly. A recall fixture has
  // to have exactly one right answer.
  const listRes = await fetch(`${CHAT_EDGE}/memories?limit=200`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  const listed = await listRes.json();
  const rows = listed.memories || listed.items || listed || [];
  let cleared = 0;
  for (const m of Array.isArray(rows) ? rows : []) {
    if (!String(m.content || "").includes("internal release codename")) continue;
    const del = await fetch(`${CHAT_EDGE}/memories/${m.id}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${jwt}` },
    });
    if (del.ok) cleared++;
  }
  if (cleared) console.log(`cleared ${cleared} codename memor${cleared === 1 ? "y" : "ies"} from earlier runs`);

  const token = `RX-${crypto.randomBytes(3).toString("hex").toUpperCase()}`;
  const res = await fetch(`${CHAT_EDGE}/memories`, {
    method: "POST",
    headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      scope_type: "global_user",
      memory_type: "explicit_fact",
      category: "work",
      content: `The user's internal release codename is ${token}.`,
    }),
  });
  if (res.status !== 201) throw new Error(`seed failed: ${res.status} ${await res.text()}`);
  return token;
}

/** One turn in a fresh conversation. `temporary` PATCHes
 *  memory_enabled=false on the new row BEFORE the first send, which is
 *  the same order the web client uses. */
async function runTurn(jwt, { temporary, prompt }) {
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
  const useBefore = useCountSum();
  await post(jwt, `${CHAT_EDGE}/conversations/${conv.id}/messages`, {
    content: prompt, stream: false,
  });
  const after = memoryCount();
  const useAfter = useCountSum();

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
    use_count_before: useBefore,
    use_count_after: useAfter,
    use_count_added: useAfter - useBefore,
    memory_injected: sources.length > 0,
    memory_sources: sources.length,
    answer: String(last.content || ""),
    answer_chars: String(last.content || "").length,
  };
}

const line = (name, r) =>
  `${name.padEnd(11)}rows ${r.rows_before}->${r.rows_after} (+${r.rows_added})  ` +
  `use_count +${r.use_count_added}  injected=${r.memory_injected ? "yes" : "no"}`;

async function main() {
  const label = process.argv[2] || "run";
  const jwt = mintJWT();

  const token = await seedRecallMemory(jwt);
  const RECALL_PROMPT =
    "What is my internal release codename? Reply with the codename only, nothing else.";
  console.log(`seeded recall memory with codename ${token}\n`);

  // Normal first: it proves a save is detectable on this build. Without
  // it, "the temporary chat saved nothing" is true of a build that saves
  // nothing for anybody.
  const saveNormal = savePrompt();
  const normal = await runTurn(jwt, { temporary: false, prompt: saveNormal.text });
  console.log(line("normal", normal) + `  tag=${saveNormal.tag}`);

  const saveTemp = savePrompt();
  const temporary = await runTurn(jwt, { temporary: true, prompt: saveTemp.text });
  console.log(line("temporary", temporary) + `  tag=${saveTemp.tag}`);

  // The read half, asked as a question rather than inferred from the
  // presence of a prompt block.
  const recallNormal = await runTurn(jwt, { temporary: false, prompt: RECALL_PROMPT });
  console.log(line("recall/on", recallNormal) + `  answer=${JSON.stringify(recallNormal.answer.slice(0, 60))}`);

  const recallTemp = await runTurn(jwt, { temporary: true, prompt: RECALL_PROMPT });
  console.log(line("recall/temp", recallTemp) + `  answer=${JSON.stringify(recallTemp.answer.slice(0, 60))}`);

  // Read the stored contents once, so the verdict can name the row
  // rather than trusting a count that could have moved for any reason.
  const savedTags = String(execFileSync("docker", [
    ...PG,
    `select string_agg(content, ' | ') from chat.user_memories ` +
    `where user_id='${USER}' and status='active';`,
  ]));

  const verdict = {
    // The probe can see a save happen at all:
    save_is_detectable: normal.rows_added > 0,
    // Named directly, not just counted: the row that appeared has to be
    // the fact we asked for.
    saved_fact_is_ours: savedTags.includes(saveNormal.tag),
    // And the temporary chat's tag must be nowhere in the store.
    temporary_tag_absent: !savedTags.includes(saveTemp.tag),
    // ...and the temporary chat did not produce one:
    temporary_wrote_nothing: temporary.rows_added === 0,
    // FIX56.1: it DOES read — and not merely "the block was in the
    // prompt", but the fact came back in the answer.
    temporary_recalls: recallTemp.answer.includes(token),
    // The control for that: an ordinary chat recalls it too, so a
    // failure above is the temporary chat's and not the model's.
    normal_recalls: recallNormal.answer.includes(token),
    // ...and the temporary read left no mark on the memory rows, while
    // the ordinary read did. Both halves: a build that never bumps
    // use_count would pass the first on its own.
    temporary_read_is_traceless: recallTemp.use_count_added === 0,
    normal_read_is_accounted: recallNormal.use_count_added > 0,
  };
  console.log("\nSUMMARY", JSON.stringify(verdict));
  if (!verdict.save_is_detectable) {
    console.log(
      "NOTE: the normal chat saved nothing, so 'temporary wrote nothing' is\n" +
      "      not evidence of anything. Fix the control before reading the result.",
    );
  }
  if (!verdict.normal_recalls) {
    console.log(
      "NOTE: the ORDINARY chat could not recall the seeded fact either, so the\n" +
      "      temporary recall result says nothing about the temporary chat.",
    );
  }

  const outDir = path.join(__dirname, "results");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(
    path.join(outDir, `${label}.json`),
    JSON.stringify(
      {
        label, model: MODEL, generated_at: new Date().toISOString(),
        recall_token: token, save_tags: { normal: saveNormal.tag, temporary: saveTemp.tag },
        verdict,
        normal, temporary, recall_normal: recallNormal, recall_temporary: recallTemp,
      },
      null, 2,
    ),
  );
}

main();
