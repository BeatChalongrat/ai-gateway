// node --test scripts/smoke/due.test.js
// The dated reminders of the smoke: quiet before the date, loud from it, quiet again once the thing is done —
// and never a value of the env file in what they say.
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { DUE, verdict, today } = require("./due.js");

const SECRET = "0f".repeat(32);
const withOld = (name, fallback = "") => (name === "DLP_AUDIT_KEY_OLD" ? SECRET : fallback);
const without = (_name, fallback = "") => fallback;
const key = DUE.find((d) => d.id === "ops.old_audit_key");

test("the old audit key has an entry, dated 90 days after the rotation of 8 Oct 2026", () => {
  assert.ok(key);
  assert.equal(key.from, "2027-01-06");
  assert.equal((Date.parse("2027-01-06T00:00:00Z") - Date.parse("2026-10-08T00:00:00Z")) / 86_400_000, 90);
});

test("before the date it passes and says how long is left", () => {
  const v = verdict(key, withOld, "2026-10-09");
  assert.equal(v.ok, true);
  assert.match(v.detail, /due 2027-01-06, in 89 days; nothing to do yet/);
  assert.match(verdict(key, withOld, "2027-01-05").detail, /in 1 day;/);
});

test("from the date on it fails, and says the one command", () => {
  for (const [day, said] of [["2027-01-06", /due since 2027-01-06 \(today\)/], ["2027-01-07", /\(1 day ago\)/], ["2027-03-01", /\(54 days ago\)/]]) {
    const v = verdict(key, withOld, day);
    assert.equal(v.ok, false, day);
    assert.match(v.detail, said);
    assert.match(v.detail, /run: python scripts\/ops\/rotate_dlp_audit_key\.py drop-old/);
  }
});

test("once the line is gone it passes, before the date or after it", () => {
  for (const day of ["2026-12-01", "2027-01-06", "2028-01-01"]) {
    assert.deepEqual(verdict(key, without, day), { ok: true, detail: "DLP_AUDIT_KEY_OLD is gone from the env file" });
  }
  // a line left empty is a line that is gone
  assert.equal(verdict(key, (n, f = "") => (n === "DLP_AUDIT_KEY_OLD" ? "   " : f), "2027-02-01").ok, true);
});

test("nothing it says holds a value of the env file", () => {
  for (const day of ["2026-10-09", "2027-01-06", "2027-06-01"]) {
    assert.ok(!verdict(key, withOld, day).detail.includes(SECRET));
  }
});

test("today is the machine's day, or the day a test names", () => {
  assert.match(today(), /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(today(new Date(2027, 0, 6, 23, 59)), "2027-01-06");
  process.env.SMOKE_TODAY = "2027-01-06";
  try { assert.equal(today(), "2027-01-06"); } finally { delete process.env.SMOKE_TODAY; }
});
