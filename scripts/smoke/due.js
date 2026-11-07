// Area: things with a date on them.
//
// A date in a notes file is missed; a check that speaks up is not. Each entry below is something that
// has to be done on or after a date. Until that date the check passes and says how many days are left.
// From that date on, for as long as the thing is still not done, the check FAILS — so the daily smoke is
// red, the pager says why, and the line says the one command that settles it. Done early is fine: the
// check then says so and passes.
//
// The entries read the env file by NAME through the smoke library; a value is never printed.
//
//   ops.old_audit_key   DLP_AUDIT_KEY was rotated on 2026-10-08 and the key that was in use kept as
//                       DLP_AUDIT_KEY_OLD: it alone opens "the prompt as typed" in rows written before the
//                       rotation. Those prompts are erased 90 days after they were written
//                       (dlp_raw_retention_days), so from 2027-01-06 the old key opens nothing that may
//                       exist — in the database or in any backup restored from then on.
"use strict";

const DAY = 86_400_000;

const DUE = [
  {
    id: "ops.old_audit_key",
    from: "2027-01-06",
    pending: (env) => !!String(env("DLP_AUDIT_KEY_OLD", "")).trim(),
    what: "DLP_AUDIT_KEY_OLD is still in the env file",
    command: "python scripts/ops/rotate_dlp_audit_key.py drop-old",
    done: "DLP_AUDIT_KEY_OLD is gone from the env file",
  },
];

/** `YYYY-MM-DD` of today in the machine's own time, or SMOKE_TODAY (a test's clock). */
function today(now = new Date()) {
  const forced = process.env.SMOKE_TODAY || "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(forced)) return forced;
  const p = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/** What one entry says on a given day: `{ok, detail}`. Pure — the env reader and the day are passed in. */
function verdict(entry, env, day) {
  if (!entry.pending(env)) return { ok: true, detail: entry.done };
  const left = Math.round((Date.parse(`${entry.from}T00:00:00Z`) - Date.parse(`${day}T00:00:00Z`)) / DAY);
  if (left > 0) return { ok: true, detail: `${entry.what} — due ${entry.from}, in ${left} day${left === 1 ? "" : "s"}; nothing to do yet` };
  return {
    ok: false,
    detail: `${entry.what} and has been due since ${entry.from}${left < 0 ? ` (${-left} day${left === -1 ? "" : "s"} ago)` : " (today)"} — run: ${entry.command}`,
  };
}

module.exports = async function due(R) {
  const L = require("./lib");
  for (const entry of DUE) {
    await R.run(entry.id, async () => {
      // the env file is live's: a smoke of the test stack has nothing to say about it
      if (L.TARGET !== "live") return { skip: true, detail: "a reminder about live's own env file; not asked of the test stack" };
      return verdict(entry, L.env, today());
    });
  }
};
module.exports.DUE = DUE;
module.exports.verdict = verdict;
module.exports.today = today;
