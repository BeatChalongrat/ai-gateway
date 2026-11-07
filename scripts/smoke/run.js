// FIX134 F — the smoke suite's orchestrator (started by scripts/smoke.sh).
//   SMOKE_TARGET=live|test   SMOKE_DESKTOP=1|0   SMOKE_ONLY=tls,apps,api,jobs,due,alert,desktop
//   SMOKE_RESULT=<json path> (set by smoke.sh)
// Prints one line per check (PASS|FAIL|SKIP <name> <detail>), a summary, writes the JSON
// result, deletes every chat it created, and exits 0 only when nothing FAILED.
"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("./lib");
const { countCheck } = require("./count");

const AREAS = ["tls", "apps", "api", "jobs", "alert", "desktop", "agent_market"];

async function main() {
  const t0 = Date.now();
  const R = new L.Report();
  // The dated reminders (due.js) are an area too. They are named here, not in the list above, and
  // registered beside tls below: both places are lines of the core suite, so the list and the
  // reminders can each change — or be taken out — without touching the other.
  const only = (process.env.SMOKE_ONLY || [...AREAS, "due"].join(",")).split(",").map((s) => s.trim()).filter(Boolean);
  const desktopOn = (process.env.SMOKE_DESKTOP ?? (L.TARGET === "live" ? "1" : "0")) === "1";
  let head = null;
  try { head = execFileSync("git", ["-C", L.ROOT, "rev-parse", "--short=7", "HEAD"], { encoding: "utf8" }).trim(); } catch { /* not a checkout */ }
  process.stdout.write(`smoke ${L.TARGET} (${L.T.project}) at ${new Date().toISOString()} checkout ${head || "?"} desktop=${desktopOn ? "on" : "off"}\n`);

  let setupError = null;
  try {
    const missing = L.assertProject();
    if (missing.length) {
      if (L.TARGET === "test" && missing.length === Object.values(L.T.containers).filter(Boolean).length) throw new Error(`the test stack is not running (scripts/test-stack.sh up): ${missing.join(", ")} absent`);
      R.fail("setup.containers", `absent: ${missing.join(", ")}`);
    } else R.pass("setup.containers", `${L.T.project}: ${Object.values(L.T.containers).filter(Boolean).length} containers belong to the project`);
  } catch (e) {
    setupError = L.errText(e);
    R.fail("setup.containers", setupError);
  }

  let session = null;
  if (!setupError) {
    if (only.includes("tls")) await require("./tls")(R);
    // things with a date on them: quiet before it, red from it until done (due.js)
    if (only.includes("due")) await require("./due")(R);
    if (only.includes("apps")) session = await require("./apps")(R);
    if (only.includes("api")) {
      if (!session && !only.includes("apps")) { try { session = (await require("./apps").signIn(R)).session; } catch (e) { R.fail("portal.session", L.errText(e)); } }
      await require("./api")(R, session);
    }
    if (only.includes("jobs")) await require("./jobs")(R);
    if (only.includes("agent_market")) await require("./agent_market")(R, session);
    if (only.includes("alert")) await require("./alert")(R);
    if (only.includes("desktop")) {
      if (desktopOn) await require("./desktop")(R);
      else require("./desktop").skipAll(R, `SMOKE_DESKTOP=0 (${L.TARGET === "test" ? "the installed app points at live, not the test stack" : "off by request"})`);
    }
  }

  const cleaned = await L.cleanup();
  if (cleaned.created) R.add(cleaned.failed.length ? "FAIL" : "PASS", "cleanup.chats", `${cleaned.deleted}/${cleaned.created} leftover [SMOKE] chats deleted by id${cleaned.failed.length ? `; failed: ${cleaned.failed.join(" ")}` : ""}`);

  // AM24 §3.7: the checks that ran, by name, against the last green run of the same suite (scripts/gate/ledger.py): a
  // check gone on its own is red, named; a run of some areas only (SMOKE_ONLY) is a suite of its own
  if (!setupError) R.add(...countCheck(R, only));

  const counts = R.counts();
  const ok = counts.fail === 0 && !setupError;
  // the turns that passed and took longer than expected: counted among the passes, and named here
  const slow = R.slow().map((c) => c.name);
  const result = {
    schema: 1, target: L.TARGET, project: L.T.project, at: new Date().toISOString(), epoch: Math.floor(Date.now() / 1000),
    host: os.hostname(), checkout: head, desktop: desktopOn, ok, counts, slow, model: L.FREE_MODEL, turn_expect_ms: L.TURN_EXPECT_MS,
    duration_s: Math.round((Date.now() - t0) / 1000), checks: R.checks,
  };
  const out = process.env.SMOKE_RESULT || path.join(L.ROOT, "scripts", "probes", "FIX134", "results", "smoke", `smoke_${L.TARGET}_${result.at.replace(/[-:]/g, "").slice(0, 15)}Z.json`);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, JSON.stringify(result, null, 1));
  process.stdout.write(`\n${ok ? "GREEN" : "RED"} ${L.TARGET}: ${counts.pass} pass${slow.length ? ` (${slow.length} slow)` : ""}, ${counts.fail} fail, ${counts.skip} skip in ${result.duration_s} s → ${out}\n`);
  if (counts.fail) process.stdout.write(`failed: ${R.checks.filter((c) => c.status === "FAIL").map((c) => c.name).join(", ")}\n`);
  if (slow.length) process.stdout.write(`slow: ${slow.join(", ")} (a turn is expected within ${L.secondsText(L.TURN_EXPECT_MS)})\n`);
  return ok ? 0 : 1;
}

main().then((rc) => setTimeout(() => process.exit(rc), 200)).catch(async (e) => {
  process.stdout.write(`FAIL suite ${L.errText(e)}\n`);
  try { await L.cleanup(); } catch { /* best effort */ }
  setTimeout(() => process.exit(2), 200);
});
