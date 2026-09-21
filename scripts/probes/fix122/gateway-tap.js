#!/usr/bin/env node
// FIX122 Part B — a tap between the side chat-edge and the gateway.
// Forwards everything to the real gateway; writes one JSON line per
// chat-completions request (model, stream, tools, max_tokens, reasoning,
// metadata, body bytes, status, ms, usage) to results/gateway-tap.jsonl.
// While the file results/FAIL_TITLE exists, a title request (the one whose
// metadata.purpose is "title-generation") is answered 503 without
// reaching the gateway — "the model failed while naming the chat".
//
//   node scripts/probes/fix122/gateway-tap.js [listenPort=8093] [target=http://127.0.0.1:8080]
"use strict";
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const PORT = Number(process.argv[2] || 8093);
const TARGET = new URL(process.argv[3] || "http://127.0.0.1:8080");
const RESULTS = path.join(__dirname, "results");
const LOG = path.join(RESULTS, "gateway-tap.jsonl");
const FAIL_FLAG = path.join(RESULTS, "FAIL_TITLE");
fs.mkdirSync(RESULTS, { recursive: true });

http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const body = Buffer.concat(chunks);
    const t0 = Date.now();
    let rec = null;
    if (req.method === "POST" && req.url.includes("/chat/completions")) {
      try {
        const j = JSON.parse(body.toString("utf8"));
        const isTitle = j.metadata?.purpose === "title-generation" || (j.max_tokens === 64 && !j.stream);
        rec = { at: new Date().toISOString(), model: j.model, stream: !!j.stream, tools: (j.tools || []).length, max_tokens: j.max_tokens ?? null, reasoning: j.reasoning ?? null, metadata: j.metadata ?? null, messages: j.messages.length, bodyBytes: body.length, isTitle, conv: req.headers["x-conversation-id"] || null };
        if (isTitle && fs.existsSync(FAIL_FLAG)) {
          rec.status = 503; rec.ms = 0; rec.injected = true;
          fs.appendFileSync(LOG, JSON.stringify(rec) + "\n");
          res.writeHead(503, { "content-type": "application/json" });
          return res.end(JSON.stringify({ error: { code: "provider_unavailable", message: "FIX122 tap: injected title failure" } }));
        }
      } catch { /* not JSON — forward as is */ }
    }
    const up = http.request({ host: TARGET.hostname, port: TARGET.port, method: req.method, path: req.url, headers: { ...req.headers, host: TARGET.host } }, (ur) => {
      res.writeHead(ur.statusCode, ur.headers);
      const out = [];
      ur.on("data", (c) => { if (rec && !rec.stream) out.push(c); res.write(c); });
      ur.on("end", () => {
        res.end();
        if (!rec) return;
        rec.status = ur.statusCode; rec.ms = Date.now() - t0;
        if (!rec.stream) { try { const j = JSON.parse(Buffer.concat(out).toString("utf8")); rec.served = j.model; rec.usage = j.usage ?? null; rec.content = j.choices?.[0]?.message?.content ?? null; rec.error = j.error?.code ?? null; } catch { /* ignore */ } }
        fs.appendFileSync(LOG, JSON.stringify(rec) + "\n");
      });
    });
    up.on("error", (e) => { res.writeHead(502); res.end(String(e)); });
    up.end(body);
  });
}).listen(PORT, "0.0.0.0", () => console.log(`gateway tap :${PORT} -> ${TARGET.href}`));
