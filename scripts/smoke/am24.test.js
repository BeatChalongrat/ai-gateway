// AM24 — the smoke's own guards, each with its defect made on purpose (CARD-RULES §6d):
//   the desktop's four checks are always on record (with the desktop off, three used to be missing);
//   its boot-line pattern reads a real boot line (it held a backspace for ten cards and matched nothing);
//   portal.billing_api over no pack is a skip, never a pass, and a pack without its price is red;
//   smoke.count: a check gone since the last green run is red and named; a count that could not be compared is red.
//     node --test scripts/smoke/am24.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "am24-smoke-"));
process.env.GATE_LEDGER = path.join(tmp, "ledger.jsonl");
process.env.GATE_REMOVED = path.join(tmp, "removed.txt");
process.env.GATE_COMMIT = "abc1234";
process.env.GATE_DIRTY = "0";
fs.writeFileSync(process.env.GATE_REMOVED, "# none\n");

const L = require("./lib");
const desktop = require("./desktop.js");
const { billingVerdict } = require("./apps.js");
const { countCheck } = require("./count.js");

function report() {
  const R = new L.Report();
  R.quiet = true;
  return R;
}

test("the desktop off is four skips, each named — never three checks missing", () => {
  const R = report();
  desktop.skipAll(R, "SMOKE_DESKTOP=0 (off by request)");
  assert.deepStrictEqual(R.checks.map((c) => [c.name, c.status]),
    desktop.CHECKS.map((n) => [n, "SKIP"]));
  assert.deepStrictEqual(desktop.CHECKS, ["desktop.startup", "desktop.session", "desktop.turn", "desktop.closed"]);
});

test("the boot-line pattern reads the app's own first line, and holds no control character", () => {
  assert.ok(desktop.BOOT_LINE.test("2026-10-17T16:04:41.512Z INFO  SRAN Desktop 1.9.0 pid=14212"));
  assert.ok(!desktop.BOOT_LINE.test("2026-10-17T16:04:41.512Z INFO  SIGNED IN as someone"));
  assert.ok(!/[\u0000-\u0008]/.test(desktop.BOOT_LINE.source), "a backspace where \\b was meant");
});

test("billing: no pack is a skip; packs are each checked; a pack without its price is red", () => {
  const url = "https://localhost/portal/billing/packs";
  assert.strictEqual(billingVerdict(url, { status: 200, json: [] }).skip, true);
  const ok = billingVerdict(url, { status: 200, json: [{ id: "p1", tokens: 1000, price_usd: 5 }] });
  assert.strictEqual(ok.ok, true);
  const bad = billingVerdict(url, { status: 200, json: [{ id: "p1", tokens: 1000 }] });
  assert.strictEqual(bad.ok, false);
  assert.match(bad.detail, /without an id, tokens or a price/);
  assert.strictEqual(billingVerdict(url, { status: 500, json: null }).ok, false);
});

test("smoke.count: held to the last green run — a check gone is red and named", () => {
  const R = report();
  for (const n of ["tls.cert", "desktop.startup", "desktop.session", "desktop.turn", "desktop.closed"]) R.pass(n, "");
  const first = countCheck(R, []);
  assert.strictEqual(first[0], "PASS", first[2]);
  const R2 = report();
  for (const n of ["tls.cert", "desktop.startup"]) R2.pass(n, "");
  const gone = countCheck(R2, []);
  assert.strictEqual(gone[0], "FAIL", gone[2]);
  assert.match(gone[2], /gone: desktop\.session, desktop\.turn, desktop\.closed/);
});

test("smoke.count: a count that could not be compared is red, not a pass", () => {
  const before = process.env.SMOKE_PYTHON;
  process.env.SMOKE_PYTHON = path.join(tmp, "no-such-python");
  try {
    const R = report();
    R.pass("tls.cert", "");
    const r = countCheck(R, []);
    assert.strictEqual(r[0], "FAIL");
    assert.match(r[2], /were not compared/);
  } finally {
    if (before === undefined) delete process.env.SMOKE_PYTHON; else process.env.SMOKE_PYTHON = before;
  }
});
