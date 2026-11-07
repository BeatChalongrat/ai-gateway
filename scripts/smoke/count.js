// AM24 §3.7 — the smoke's checks, by name, held to the last green run of the same suite (scripts/gate/ledger.py
// names): a check gone on its own is red, named; a run of some areas only (SMOKE_ONLY) is a suite of its own. A count
// that could not be compared is not one that held. count.test.js runs exactly this.
"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("./lib");

function countCheck(R, only) {
  const lane = `smoke_${L.TARGET}${process.env.SMOKE_ONLY ? `:${[...only].sort().join("+")}` : ""}`;
  const names = path.join(os.tmpdir(), `smoke_names_${process.pid}.json`);
  try {
    fs.writeFileSync(names, JSON.stringify(R.checks.map((c) => c.name)));
    let out = "";
    try {
      out = execFileSync(process.env.SMOKE_PYTHON || "python", [path.join(L.ROOT, "scripts", "gate", "ledger.py"), "names", lane, names],
        { encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" }, timeout: 60000 });
    } catch (e) { out = (e.stdout || "").toString(); if (!out.trim()) throw e; }
    const r = JSON.parse(out.trim().split("\n").pop());
    return [r.ok ? "PASS" : "FAIL", "smoke.count", `${lane}: ${r.detail}`];
  } catch (e) {
    // a count that could not be compared is not one that held
    return ["FAIL", "smoke.count", `${lane}: the checks were not compared with the last run — ${L.errText(e)}`];
  } finally { try { fs.unlinkSync(names); } catch { /* gone */ } }
}

module.exports = { countCheck };
