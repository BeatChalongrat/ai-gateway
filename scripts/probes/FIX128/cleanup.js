// FIX128 — delete this card's live test chats BY ID (files first). Refuses any chat whose first user message is not [FIX128].
process.env.PAGE = "http://localhost:3002"; process.env.API = "http://localhost:4000/api/v1"; process.env.EDGE = "ai-gateway-chat-edge-api";
const fs = require("node:fs"), path = require("node:path");
const { token, api } = require("../fix122/lib");
(async () => {
  const tok = token();
  const ids = fs.readFileSync(path.join(__dirname, "results", "cleanup-ids.txt"), "utf8").split(/\r?\n/).filter(Boolean);
  let chats = 0, files = 0, gone = 0; const skipped = [];
  for (const id of ids) {
    const c = await api(tok, `/conversations/${id}`);
    if (c.status === 404) { gone++; continue; }
    const msgs = (await api(tok, `/conversations/${id}/messages?limit=50`)).body.messages || [];
    const firstUser = msgs.find((m) => m.role === "user")?.content || "";
    if (msgs.length && !firstUser.startsWith("[FIX128]")) { skipped.push(id); continue; }
    for (const f of (await api(tok, `/conversations/${id}/files`)).body.files || []) { if ((await api(tok, `/files/${f.id}`, { method: "DELETE" })).status === 204) files++; }
    if ((await api(tok, `/conversations/${id}`, { method: "DELETE" })).status < 300) chats++;
  }
  console.log(JSON.stringify({ ids: ids.length, alreadyGone: gone, chatsDeleted: chats, filesDeleted: files, skippedNotFix128: skipped }));
})();
