// FIX128 — on the TEST stack: every model fails → the turn ends in an error row and costs 0 credits.
const { execFileSync } = require("node:child_process");
const crypto = require("node:crypto");
const API = "http://127.0.0.1:14000/api/v1", USER = "f1280000-0000-4000-8000-000000000011", TENANT = "f1280000-0000-4000-8000-000000000001";
const dx = (c, a) => execFileSync("docker", ["exec", c, ...a], { encoding: "utf8" }).trim();
const tsql = (q) => dx("sran-test-postgres-1", ["psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-c", q]);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function restart() { execFileSync("docker", ["restart", "sran-test-backend-1"]); for (let i = 0; i < 60; i++) { try { if ((await fetch("http://127.0.0.1:18081/ready")).ok) return; } catch {} await wait(2000); } }
function token() {
  const secret = dx("sran-test-chat-edge-api-1", ["env"]).match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const b = (o) => Buffer.from(JSON.stringify(o)).toString("base64url"); const now = Math.floor(Date.now() / 1000);
  const d = `${b({ alg: "HS256", typ: "JWT" })}.${b({ sub: USER, email: "ts-owner@fix128.test", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: 0, iat: now, exp: now + 3600, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}
(async () => {
  if (dx("sran-test-postgres-1", ["sh", "-c", "echo ok"]) !== "ok") throw new Error("no test stack");
  tsql("create table if not exists ts_saved_active2 as select model, is_active from model_configs");
  tsql("update model_configs set is_active = (model = 'ts-dead-model')");
  await restart();
  try {
    const tok = token(); const H = { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" };
    const c = await (await fetch(API + "/conversations", { method: "POST", headers: H, body: JSON.stringify({ model_id: "ts-dead-model" }) })).json();
    const before = Number(tsql(`select credit_balance from portal_users where id='${USER}'`));
    const r = await fetch(API + `/conversations/${c.id}/messages`, { method: "POST", headers: { ...H, Accept: "text/event-stream" }, body: JSON.stringify({ content: "[FIX128] failed turn", stream: true }) });
    const body = await r.text(); await wait(3000);
    const after = Number(tsql(`select credit_balance from portal_users where id='${USER}'`));
    const code = /"code":"([a-z_]+)"/.exec(body)?.[1];
    console.log(JSON.stringify({ conv: c.id, terminalCode: code, creditsBefore: before, creditsAfter: after, pass: !!code && before === after && before > 0 }));
  } finally {
    tsql("update model_configs m set is_active = s.is_active from ts_saved_active2 s where s.model = m.model"); tsql("drop table ts_saved_active2"); await restart();
  }
})().catch((e) => { console.error(e); process.exit(1); });
