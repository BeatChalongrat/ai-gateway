// AM9 (I) — a stand-in for the two things outside this system that "send as me" talks to, for the
// end-to-end probe (mail_e2e.sh): the backend's connector broker (what the person connected, and their
// mailbox token for the gateway) and the Gmail API's send endpoint. It keeps what it was sent in /out.
"use strict";
const http = require("http");
const fs = require("fs");

const TOKEN = "ya29.e2e-person-mailbox-token";
http.createServer((req, res) => {
  let body = "";
  req.on("data", (d) => { body += d; });
  req.on("end", () => {
    const send = (code, obj) => { res.statusCode = code; res.setHeader("content-type", "application/json"); res.end(JSON.stringify(obj)); };
    const auth = req.headers.authorization || "";
    if (req.method === "GET" && req.url === "/portal/connectors") {
      return send(200, { connections: [{ connector_id: "mail", provider: "google", connected: true }], available: ["mail"] });
    }
    if (req.method === "POST" && req.url === "/portal/connectors/mail/token") {
      fs.appendFileSync("/out/broker.log", `${new Date().toISOString()} token asked with a bearer: ${auth.startsWith("Bearer ") && auth.length > 40}\n`);
      return send(200, { access_token: TOKEN, expires_at: null });
    }
    if (req.method === "POST" && req.url.startsWith("/upload/gmail/v1/users/me/messages/send")) {
      fs.writeFileSync("/out/raw.eml", body, "utf8");
      fs.writeFileSync("/out/request.json", JSON.stringify({ url: req.url, type: req.headers["content-type"], bearer_is_person_token: auth === `Bearer ${TOKEN}` }));
      return send(200, { id: "18f0e2e0", threadId: "18f0e2e0", labelIds: ["SENT"] });
    }
    send(404, { error: "not here" });
  });
}).listen(8099, () => console.log("fake google on :8099"));
