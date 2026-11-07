// FIX134 F — area 3: the API with a key, and the limits.
//
// completion   one non-stream /v1/chat/completions on the free model through the front door —
//              answered by that model, now (lib.whoAnswered): an answer from another model or out
//              of the gateway's response cache is a FAIL that names both
// idempotency  the same request twice with one Idempotency-Key → the second is a replay
//              (Idempotent-Replayed: true, same id) — FIX133 F2; the first is a turn like any other
// rate_limit   a client that loops gets 429 instead of taking the system down: chat-edge's
//              send limit (30/min per user, FIX133 F1) hit with EMPTY bodies on a chat id that
//              does not exist — the limiter counts the request before the handler validates
//              it, so nothing reaches a model and no message is written. The smoke user is
//              then rate-limited for up to a minute, which is why this runs after the app
//              checks and before nothing that sends.
"use strict";
const crypto = require("node:crypto");
const L = require("./lib");

// The model's own refusals of a turn that ran out of its output budget (docs/TESTING_MODELS.md: the reasoning of
// the free model shares the budget and its length is not stable — a model outcome, not a defect).
const OUT_OF_BUDGET = /output_limit_reached|empty_completion/i;

/**
 * api.completion's verdict over the calls it made. The last call must be a 200 with an id and content,
 * answered now (not out of the cache) by the model asked. A call that ran out of the output budget may
 * be asked once more — of the same model: no other model ever stands in for it here.
 */
function completionVerdict(tries, asked, url) {
  const last = tries[tries.length - 1];
  const before = tries.slice(0, -1).map((t) => `${t.status} ${t.code || ""} after ${L.secondsText(t.ms)}`).join("; ");
  const again = before ? ` (asked again of the same model after: ${before})` : "";
  const turns = tries.map((t) => ({ asked, served: t.status === 200 ? t.model : null, ms: Math.round(t.ms), expect_ms: L.TURN_EXPECT_MS }));
  if (last.status !== 200) {
    return { ok: false, turns, detail: `asked ${asked}, no answer: ${url} → ${last.status} ${last.code || String(last.text || "").slice(0, 120)} after ${L.secondsText(last.ms)}${again}` };
  }
  const shape = `200 id=${last.id} finish=${last.finish} content="${String(last.content || "").slice(0, 24)}"`;
  if (last.cached) {
    return { ok: false, turns, detail: `asked ${asked}, answered out of the gateway's cache — nobody answered now (${shape})${again}` };
  }
  const who = L.whoAnswered({ asked, served: last.model, ms: last.ms });
  if (!who.ok) return { ok: false, turns, detail: `${who.text} — ${shape}${again}` };
  if (!last.id || !last.content) return { ok: false, turns, detail: `${who.text}, but the answer has ${last.id ? "no content" : "no id"} — ${shape}${again}` };
  return { ok: true, slow: who.slow, turns, detail: `${who.text} — ${shape}${again}` };
}

/** api.idempotency's verdict: the first call is a turn like any other; the second is its replay. */
function idempotencyVerdict(first, second, asked) {
  const turns = [{ asked, served: first.status === 200 ? first.model : null, ms: Math.round(first.ms), expect_ms: L.TURN_EXPECT_MS }];
  if (first.status !== 200) return { ok: false, turns, detail: `asked ${asked}, no answer: first → ${first.status} ${first.code || String(first.text || "").slice(0, 100)} after ${L.secondsText(first.ms)}` };
  if (first.cached) return { ok: false, turns, detail: `asked ${asked}, the first call was answered out of the gateway's cache — nobody answered now (id=${first.id})` };
  const who = L.whoAnswered({ asked, served: first.model, ms: first.ms });
  const replay = String(second.replayed).toLowerCase() === "true" || (!!first.id && first.id === second.id);
  const pair = `first 200 id=${first.id}; second ${second.status} id=${second.id} Idempotent-Replayed=${second.replayed}`;
  if (!who.ok) return { ok: false, turns, detail: `${who.text} — ${pair}` };
  return { ok: second.status === 200 && replay, slow: who.slow, turns, detail: `${who.text} — ${pair}` };
}

// The test stack has no API key in the env file, and for that reason these two checks were skipped on
// every one of its 54 runs to 9 Oct 2026 — they had never run where a deploy is gated. So there the
// suite makes its own: a key for the seeded owner through the test backend's own door, held in memory
// (never printed or written) and revoked when the area ends; one left by a run that died is revoked first.
const TEST_KEY_NAME = "[SMOKE] api checks";
async function testStackKey() {
  const backend = L.T.plain.backend;
  const headers = { Authorization: `Bearer ${L.mintPortalToken(L.TS_USER)}`, "Content-Type": "application/json" };
  const list = await L.http(`${backend}/portal/projects`, { headers });
  const p = (list.json?.projects || [])[0];
  const pid = p?.project_id || p?.id;
  if (!pid) throw new Error(`the seeded owner has no project (${backend}/portal/projects → ${list.status})`);
  const revoke = async (id) => (await L.tryHttp(`${backend}/portal/projects/${pid}/keys/${id}`, { method: "DELETE", headers })).status;
  const old = await L.http(`${backend}/portal/projects/${pid}/keys`, { headers });
  for (const k of old.json?.keys || []) if (k.name === TEST_KEY_NAME && k.status === "active") await revoke(k.key_id);
  const made = await L.http(`${backend}/portal/projects/${pid}/keys`, { method: "POST", headers, body: JSON.stringify({ name: TEST_KEY_NAME }) });
  if (!made.json?.api_key) throw new Error(`no key was made (${backend}/portal/projects/…/keys → ${made.status})`);
  return { key: made.json.api_key, revoke: () => revoke(made.json.key_id) };
}

module.exports = async function api(R, session) {
  const gw = L.API_BASE.gw;
  let key = L.TARGET === "live" ? (L.env("SMOKE_API_KEY") || L.env("GATEWAY_TEST_API_KEY")) : (L.env("SMOKE_TS_API_KEY") || L.env("TS_GATEWAY_TEST_API_KEY"));
  const keyName = L.TARGET === "live" ? "SMOKE_API_KEY / GATEWAY_TEST_API_KEY" : "SMOKE_TS_API_KEY / TS_GATEWAY_TEST_API_KEY";
  let noKey = { skip: true, detail: `no API key (${keyName} in .env)` };
  let own = null;
  if (!key && L.TARGET === "test") {
    try { own = await testStackKey(); key = own.key; } catch (e) { noKey = { ok: false, detail: `no API key on the test stack and none could be made: ${L.errText(e)}` }; }
  }
  // A sentence no earlier run has sent: the gateway keeps a non-stream answer for an hour under
  // (model, messages, key), and an answer out of that cache says nothing about who answers now.
  const run = crypto.randomUUID().slice(0, 8);
  const body = (n) => JSON.stringify({ model: L.FREE_MODEL, messages: [{ role: "user", content: `[SMOKE ${run}] ${n} ตอบว่า ok` }], stream: false, max_tokens: 256 });
  const call = async (bodyText, extra = {}) => {
    const t0 = Date.now();
    const r = await L.tryHttp(`${gw}/v1/chat/completions`, { method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", ...extra }, body: bodyText, timeoutMs: 180000 });
    const c = r.json?.choices?.[0];
    return { status: r.status || r.error, ms: Date.now() - t0, id: r.json?.id || null, replayed: r.headers.get ? r.headers.get("idempotent-replayed") : null, finish: c?.finish_reason || null, content: (c?.message?.content || "").trim(), model: r.json?.model || null, cached: r.json?.cached === true, code: r.json?.error?.code || r.json?.error?.type || null, text: r.text };
  };

  await R.run("api.completion", async () => {
    if (!key) return noKey;
    const tries = [];
    for (let i = 1; i <= 2; i++) {
      const r = await call(body(i === 1 ? "completion" : "completion again"));
      tries.push(r);
      if (r.status === 200 || !OUT_OF_BUDGET.test(`${r.code} ${r.text}`)) break;
    }
    return completionVerdict(tries, L.FREE_MODEL, `${gw}/v1/chat/completions`);
  });
  await R.run("api.idempotency", async () => {
    if (!key) return noKey;
    const ik = `smoke-${crypto.randomUUID().slice(0, 12)}`;
    const b = body("idempotency");
    const a = await call(b, { "Idempotency-Key": ik });
    if (a.status !== 200) return idempotencyVerdict(a, a, L.FREE_MODEL);
    const c = await call(b, { "Idempotency-Key": ik });
    return idempotencyVerdict(a, c, L.FREE_MODEL);
  });
  if (own) {
    const s = await own.revoke();
    if (!((s >= 200 && s < 300) || s === 404)) R.fail("api.test_key", `the key this run made on the test stack was not revoked (${s}) — it is named "${TEST_KEY_NAME}"`);
  }
  await R.run("api.rate_limit_429", async () => {
    if (!session) return { skip: true, detail: "no chat-edge session" };
    const codes = {};
    let first429 = null;
    const ghost = crypto.randomUUID();
    const max = 70; // 30/min bucket floored to the minute: a loop that straddles the boundary needs up to 60
    for (let i = 1; i <= max; i++) {
      const r = await L.tryHttp(`${L.EDGE()}/conversations/${ghost}/messages`, { method: "POST", headers: { "Content-Type": "application/json", ...session.headers }, body: "{}", timeoutMs: 15000 });
      const k = `${r.status || r.error}${r.json?.error?.code ? ":" + r.json.error.code : ""}`;
      codes[k] = (codes[k] || 0) + 1;
      if (r.status === 429) { first429 = i; break; }
      if (r.status === 0 || r.status === 401) break;
      if (r.status === 200 || r.status === 201 || r.status === 202) break; // never: an empty body must not start a turn
    }
    const seen = Object.entries(codes).map(([k, v]) => `${k}×${v}`).join(" ");
    return { ok: first429 !== null && !codes["200"] && !codes["201"] && !codes["202"], detail: `first 429 at call ${first429 ?? "none in " + max}: ${seen}` };
  });
};
module.exports.completionVerdict = completionVerdict;
module.exports.idempotencyVerdict = idempotencyVerdict;
