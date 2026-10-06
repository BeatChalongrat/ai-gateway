#!/usr/bin/env node
// AM11 (E) — a stand-in for a provider's consent page and token endpoint, so the page a connector's
// consent flow ends on can be reached the way a person reaches it: through the backend's own
// ticket → start → callback, in a real browser, with no Google client. (AM9's fake_google.js stands
// in for the broker and Gmail's send API; it has no consent page.)
//
//   GET  /authorize?redirect_uri=…&state=…   a page with two links: Allow, Deny
//   POST /token                               a fixed access token (and a refresh token)
//   GET  /health
//
// The test stack's backend is pointed here by two settings (CONNECTOR_OAUTH_GOOGLE_AUTHORIZE_URL,
// CONNECTOR_OAUTH_GOOGLE_TOKEN_URL) and a made-up client id and secret; see consent.sh. Nothing here
// is, or talks to, a real provider.
"use strict";
const http = require("node:http");

const PORT = Number(process.env.PORT || 8099);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

http
  .createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (url.pathname === "/health") {
      res.writeHead(200, { "content-type": "text/plain" });
      return res.end("ok");
    }
    if (req.method === "GET" && url.pathname === "/authorize") {
      const redirect = url.searchParams.get("redirect_uri") || "";
      const state = url.searchParams.get("state") || "";
      if (!/^https?:\/\//.test(redirect) || !state) {
        res.writeHead(400, { "content-type": "text/plain" });
        return res.end("redirect_uri and state are required");
      }
      const back = (q) => `${redirect}${redirect.includes("?") ? "&" : "?"}${q}&state=${encodeURIComponent(state)}`;
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      return res.end(
        `<!doctype html><meta charset="utf-8"><title>Stand-in consent</title>` +
          `<body style="font-family:system-ui;max-width:420px;margin:15vh auto;text-align:center">` +
          `<h1 style="font-size:18px">Stand-in consent page (test stack)</h1>` +
          `<p>scope: <code>${esc(url.searchParams.get("scope") || "")}</code></p>` +
          `<p><a id="allow" href="${esc(back("code=standin-code"))}">Allow</a> · ` +
          `<a id="deny" href="${esc(back("error=access_denied&error_description=the+person+said+no"))}">Deny</a></p></body>`,
      );
    }
    if (req.method === "POST" && url.pathname === "/token") {
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ access_token: "standin-access-token", refresh_token: "standin-refresh-token", expires_in: 3600, token_type: "Bearer", scope: "https://www.googleapis.com/auth/gmail.send" }));
      });
      return;
    }
    res.writeHead(404, { "content-type": "text/plain" });
    res.end("no such page");
  })
  .listen(PORT, "0.0.0.0", () => console.log(`stand-in consent on :${PORT}`));
