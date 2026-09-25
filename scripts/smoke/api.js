// FIX134 F — area 3: the API with a key, and the limits.
//
// completion   one non-stream /v1/chat/completions on the free model through the front door
// idempotency  the same request twice with one Idempotency-Key → the second is a replay
//              (Idempotent-Replayed: true, same id) — FIX133 F2
// rate_limit   a client that loops gets 429 instead of taking the system down: chat-edge's
//              send limit (30/min per user, FIX133 F1) hit with EMPTY bodies on a chat id that
//              does not exist — the limiter counts the request before the handler validates
//              it, so nothing reaches a model and no message is written. The smoke user is
//              then rate-limited for up to a minute, which is why this runs after the app
//              checks and before nothing that sends.
"use strict";
const crypto = require("node:crypto");
const L = require("./lib");

module.exports = async function api(R, session) {
  const gw = L.API_BASE.gw;
  const key = L.TARGET === "live" ? (L.env("SMOKE_API_KEY") || L.env("GATEWAY_TEST_API_KEY")) : (L.env("SMOKE_TS_API_KEY") || L.env("TS_GATEWAY_TEST_API_KEY"));
  const keyName = L.TARGET === "live" ? "SMOKE_API_KEY / GATEWAY_TEST_API_KEY" : "SMOKE_TS_API_KEY / TS_GATEWAY_TEST_API_KEY";
  const noKey = { skip: true, detail: `no API key (${keyName} in .env)` };
  const body = (n) => JSON.stringify({ model: L.FREE_MODEL, messages: [{ role: "user", content: `[SMOKE] ${n} ตอบว่า ok` }], stream: false, max_tokens: 256 });
  const call = async (bodyText, extra = {}) => {
    const r = await L.tryHttp(`${gw}/v1/chat/completions`, { method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", ...extra }, body: bodyText, timeoutMs: 180000 });
    const c = r.json?.choices?.[0];
    return { status: r.status || r.error, id: r.json?.id || null, replayed: r.headers.get ? r.headers.get("idempotent-replayed") : null, finish: c?.finish_reason || null, content: (c?.message?.content || "").trim(), model: r.json?.model || null, code: r.json?.error?.code || r.json?.error?.type || null, text: r.text };
  };

  await R.run("api.completion", async () => {
    if (!key) return noKey;
    const r = await call(body("completion"));
    if (r.status !== 200) return { ok: false, detail: `${gw}/v1/chat/completions → ${r.status} ${r.code || r.text.slice(0, 120)}` };
    return { ok: !!r.id && Array.isArray(JSON.parse(r.text).choices), detail: `200 id=${r.id} model=${r.model} finish=${r.finish} content="${r.content.slice(0, 24)}"${r.content ? "" : " (empty: the reasoning used the budget)"}` };
  });
  await R.run("api.idempotency", async () => {
    if (!key) return noKey;
    const ik = `smoke-${crypto.randomUUID().slice(0, 12)}`;
    const b = body("idempotency");
    const a = await call(b, { "Idempotency-Key": ik });
    if (a.status !== 200) return { ok: false, detail: `first → ${a.status} ${a.code || a.text.slice(0, 100)}` };
    const c = await call(b, { "Idempotency-Key": ik });
    const replay = String(c.replayed).toLowerCase() === "true" || (!!a.id && a.id === c.id);
    return { ok: c.status === 200 && replay, detail: `first 200 id=${a.id}; second ${c.status} id=${c.id} Idempotent-Replayed=${c.replayed}` };
  });
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
