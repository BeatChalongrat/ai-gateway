// The retention check's verdict (jobs.retention): which line of chat-edge's log is a pass, and which is not.
//   node --test scripts/smoke/jobs.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { retentionVerdict } = require("./jobs");

const NOW = Date.parse("2026-10-13T03:18:00Z"); // 10:18 in Bangkok, three hours after the hour
const line = (over = {}) => JSON.stringify({
  time: "2026-10-13T00:00:41Z", level: "INFO", msg: "transcript retention: pass complete",
  due: "2026-10-13T00:00:00Z", tenants: 3, tenants_waiting: 0, seen: 0, erased: 0, errors: 0, ...over,
});

test("the pass for the latest hour ran: green, saying which hour and when", () => {
  const v = retentionVerdict(line(), NOW);
  assert.equal(v.ok, true);
  assert.match(v.detail, /^pass for 2026-10-13T00:00Z ran 3\.3 h ago, erased 0, errors 0$/);
});

test("no line at all is red — a start runs the pass it finds owed, so there is always one", () => {
  const v = retentionVerdict(null, NOW, " (edge up 2.0 h)");
  assert.equal(v.ok, false);
  assert.match(v.detail, /^no "transcript retention: pass complete" .* \(edge up 2\.0 h\) — a start runs the pass/);
});

test("a line of the last 26 h that is for YESTERDAY's hour is red: today's pass has not run", () => {
  // this is the line the check used to pass on
  const v = retentionVerdict(line({ time: "2026-10-12T00:00:41Z", due: "2026-10-12T00:00:00Z" }), NOW);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^the newest pass was for 2026-10-12T00:00Z; the one for 2026-10-13T00:00Z has not run, 3\.3 h after its hour$/);
});

test("in the first five minutes after the hour, the line for the hour before is not yet late", () => {
  const justAfter = Date.parse("2026-10-13T00:02:00Z");
  assert.equal(retentionVerdict(line({ time: "2026-10-12T00:00:41Z", due: "2026-10-12T00:00:00Z" }), justAfter).ok, true);
  // … but the one for two hours before is
  assert.equal(retentionVerdict(line({ time: "2026-10-11T00:00:41Z", due: "2026-10-11T00:00:00Z" }), justAfter).ok, false);
  // and after the five minutes it is late
  assert.equal(retentionVerdict(line({ time: "2026-10-12T00:00:41Z", due: "2026-10-12T00:00:00Z" }), Date.parse("2026-10-13T00:06:00Z")).ok, false);
});

test("a pass made good late for the latest hour is green", () => {
  const v = retentionVerdict(line({ time: "2026-10-13T03:17:30Z" }), NOW);
  assert.equal(v.ok, true);
  assert.match(v.detail, /^pass for 2026-10-13T00:00Z ran 30 s ago/);
});

test("a line that names no hour is red (a chat-edge from before the pass said its hour)", () => {
  const old = JSON.stringify({ time: "2026-10-13T00:00:41Z", msg: "transcript retention: pass complete", seen: 0, erased: 0, errors: 0 });
  const v = retentionVerdict(old, NOW);
  assert.equal(v.ok, false);
  assert.match(v.detail, /^the newest pass does not say which hour it was for/);
});

test("a pass that ran with errors is red, and one that left a tenant waiting says so", () => {
  assert.equal(retentionVerdict(line({ errors: 2 }), NOW).ok, false);
  const v = retentionVerdict(line({ tenants_waiting: 1 }), NOW);
  assert.equal(v.ok, true);
  assert.match(v.detail, /, 1 tenant\(s\) waiting for the next pass$/);
});
