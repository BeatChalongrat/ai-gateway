// AM7 — a probe's way to Agent Market's admin door (chat-edge
// /api/v1/admin/agent-market, behind its admin bearer). Since phase 4 the
// chat app has no admin route: removing a walkthrough round's evidence, or
// reading what an admin reads, goes here. The bearer is read from .env and
// never printed; the probe names itself as what it is ("AM7 walkthrough
// (script)"), and every change it makes is in the admin audit log under that
// name like any admin's.
"use strict";
const L = require("../../smoke/lib.js");

const WHO = { id: "walkthrough-am7", email: "walkthrough-am7@probe.invalid", name: "AM7 walkthrough (script)" };

// opts.role (AM9): the admin-ui role the door is told the caller has — "admin" unless a probe checks
// that a person who is not an admin is refused there.
async function door(method, path, body, opts = {}) {
  const key = L.env("CHAT_EDGE_ADMIN_API_KEY");
  if (!key) throw new Error("CHAT_EDGE_ADMIN_API_KEY is not in .env");
  return L.http(`${L.API_BASE.edge}/api/v1/admin/agent-market${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${key}`,
      "X-Admin-User-Id": WHO.id,
      "X-Admin-Email": WHO.email,
      "X-Admin-Name": encodeURIComponent(WHO.name),
      "X-Admin-Role": opts.role || "admin",
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** Removes a finished round's files (the round's row stays), with the reason its owner reads. */
const removeEvidence = (runId, reason) => door("POST", `/runs/${runId}/remove-evidence`, { reason });

module.exports = { door, removeEvidence, WHO };
