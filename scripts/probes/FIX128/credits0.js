// FIX128 regression — a failed turn (message_too_long) charges 0 credits. API only.
process.env.PAGE = "http://localhost:3002"; process.env.API = "http://localhost:4000/api/v1"; process.env.EDGE = "ai-gateway-chat-edge-api";
const { token, api } = require("../fix122/lib");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs"), path = require("node:path");
const credit = () => Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-c",
  "select credit_balance from portal_users where id='56726cc2-47d2-4e81-95bd-4aba41d4d677'"], { encoding: "utf8" }).trim());
(async () => {
  const tok = token();
  const c = await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ model_id: "qwen3.8-27b-fp8" }) });
  const id = c.body.id;
  fs.appendFileSync(path.join(__dirname, "results", "rg1-chats.txt"), id + "\n");
  await api(tok, `/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ memory_enabled: false }) });
  const before = credit();
  const r = await fetch(process.env.API + `/conversations/${id}/messages`, { method: "POST", headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify({ content: "[FIX128] " + "ยาว ".repeat(60000), stream: true }) });
  const body = await r.text();
  await new Promise((res) => setTimeout(res, 4000));
  const after = credit();
  const rows = (await api(tok, `/conversations/${id}/messages?limit=5`)).body.messages.map((m) => `${m.role}|${m.status}|${m.error_code || ""}`);
  console.log(JSON.stringify({ id, codes: [...body.matchAll(/"code":"([a-z_]+)"/g)].map((m) => m[1]), rows, creditsBefore: before, creditsAfter: after, pass: rows.some((x) => x.startsWith("assistant|error")) && before === after }));
})();
