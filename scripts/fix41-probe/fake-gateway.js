// FIX41 G2 — a scripted stand-in for the gateway, so the LIVE chat-edge
// container can be driven through real HTTP for cases the dev box cannot
// otherwise produce.
//
// Why this exists: every provider key in .env on this box is the literal
// placeholder (OPENAI_API_KEY=sk-...), so a real turn dies at the provider
// with all_models_failed. The documented way to redirect the stack at the
// bundled mock (PROVIDER_BASE_URL_OVERRIDES) seeds the endpoint into
// system_settings on FIRST BOOT ONLY and clearing the variable does NOT
// undo it — so using it would leave a provider override in the owner's
// database that this task has no business putting there.
//
// Instead: run the SAME freshly built chat-edge image as a throwaway
// container against this script. Real binary, real HTTP, real postgres,
// real redis; the only substitution is who answers /v1/chat/completions,
// which is the same substitution the Go probes already document.
//
//   node scripts/fix41-probe/fake-gateway.js [port]
//
// Query knobs on the request URL (chat-edge forwards none of them, so they
// are set per-scenario by restarting this with a different DEFAULT below).

const http = require("node:http");

const PORT = Number(process.argv[2] || 9199);
// Milliseconds between content chunks. Slow enough that a cancel or a
// container restart can land mid-answer.
const GAP_MS = Number(process.env.FAKE_GW_GAP_MS || 300);
const CHUNKS = (process.env.FAKE_GW_TEXT || "หนึ่ง |สอง |สาม |สี่ |ห้า |หก |เจ็ด |แปด ").split("|");

function sse(res, obj) {
  res.write(`data: ${JSON.stringify(obj)}\n\n`);
}
function content(res, text) {
  sse(res, {
    id: "fake",
    object: "chat.completion.chunk",
    choices: [{ index: 0, delta: { content: text } }],
  });
}

const server = http.createServer((req, res) => {
  if (!req.url.includes("/chat/completions")) {
    res.writeHead(404).end();
    return;
  }
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", async () => {
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    // NOTE: listen on the RESPONSE, not the request. Node emits 'close' on
    // an incoming request once its body has been fully received, so
    // req.on('close') fires immediately and would end the answer after one
    // chunk — which it did, and cost a full G2 run to notice.
    let alive = true;
    res.on("close", () => (alive = false));

    // Phase markers interleaved with the answer — the shape a resume has
    // to reproduce in order.
    sse(res, { type: "model_resolved", model: "fake-gw-model" });
    sse(res, { type: "analysis_status", status: "started" });
    sse(res, { type: "reasoning_summary_delta", text: "คิดอยู่ครู่หนึ่ง " });
    sse(res, { type: "analysis_status", status: "completed" });

    for (let i = 0; i < CHUNKS.length; i++) {
      if (!alive) return;
      content(res, CHUNKS[i]);
      if (i === 2) {
        sse(res, {
          type: "fallback_info",
          fallback_info: { requested_model: "fake-gw-model", actual_model: "fake-gw-fallback" },
        });
      }
      await new Promise((r) => setTimeout(r, GAP_MS));
    }
    if (!alive) return;
    res.write("data: [DONE]\n\n");
    res.end();
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`fake gateway on :${PORT} (gap ${GAP_MS}ms, ${CHUNKS.length} chunks)`);
});
