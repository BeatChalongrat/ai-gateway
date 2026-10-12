// FIX134 F — area 5: every background job's last success.
//
// backup       /backups/last_success.json in the backup container: newer than 26 h, drill pass,
//              off-site copy pass (or not configured — the detail says which)
// retention    chat-edge's newest "transcript retention: pass complete" is for the latest 00:00 UTC, errors 0
// sweep        chat-edge's interrupted_turn_sweep in the last 10 min
// ops_watch    ops-watch's tick in the last 2 min (the watcher is alive)
// maintenance  no maintenance window left on (python -m app.ops.maintenance status)
"use strict";
const L = require("./lib");

const MAX_BACKUP_AGE_H = Number(L.env("OPS_WATCH_BACKUP_MAX_AGE_H", "26")) || 26;

function lastLine(text, re) {
  const lines = text.split(/\r?\n/).filter((l) => re.test(l));
  return lines.length ? lines[lines.length - 1] : null;
}
function timeOf(line) {
  const m = /"(?:time|ts)"\s*:\s*"([^"]+)"/.exec(line) || /^(\d{4}-\d{2}-\d{2}T[^ ]+)/.exec(line);
  const t = m ? Date.parse(m[1]) : NaN;
  return Number.isNaN(t) ? null : t;
}
const agoText = (ms) => (ms === null ? "time unknown" : `${L.ageText((Date.now() - ms) / 1000)} ago`);

// The pass starts within a minute of its hour (or of a start, or of the machine waking); for this long
// after 00:00 UTC the newest line may still be the one for the hour before.
const PASS_GRACE_MS = 5 * 60 * 1000;
const DAY_MS = 86_400_000;
/**
 * The retention job's last word, judged. chat-edge runs the pass it finds owed at every start and looks
 * at the clock every minute, and each pass says which 00:00 UTC it was for (`due`) — so a running
 * chat-edge always has a line, and its newest one is for the latest hour.
 *
 * Until 12 Oct 2026 this check passed on ANY line of the last 26 h and SKIPPED for a chat-edge up less
 * than 26 h: one restarted every day was never checked at all, and a pass that stopped running went
 * unseen for a day. Now: no line, a line that names no hour, or a newest line for an earlier hour is red.
 */
function retentionVerdict(line, nowMs = Date.now(), upText = "") {
  if (!line) return { ok: false, detail: `no "transcript retention: pass complete" in the chat-edge log of the last ${MAX_BACKUP_AGE_H} h${upText} — a start runs the pass it finds owed, so a running chat-edge always has one` };
  const due = Date.parse((/"due"\s*:\s*"([^"]+)"/.exec(line) || [])[1] || "");
  if (Number.isNaN(due)) return { ok: false, detail: `the newest pass does not say which hour it was for (a chat-edge from before 12 Oct 2026?)${upText}` };
  const d = new Date(nowMs);
  const latest = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const owed = nowMs - latest < PASS_GRACE_MS ? latest - DAY_MS : latest;
  const hour = (ms) => `${new Date(ms).toISOString().slice(0, 16)}Z`;
  if (due < owed) return { ok: false, detail: `the newest pass was for ${hour(due)}; the one for ${hour(latest)} has not run, ${L.ageText((nowMs - latest) / 1000)} after its hour${upText}` };
  const m = /"erased"\s*:\s*(\d+)/.exec(line);
  const e = /"errors"\s*:\s*(\d+)/.exec(line);
  const w = /"tenants_waiting"\s*:\s*(\d+)/.exec(line);
  const ran = timeOf(line);
  return {
    ok: !e || e[1] === "0",
    detail: `pass for ${hour(due)} ran ${ran === null ? "(time unknown)" : `${L.ageText((nowMs - ran) / 1000)} ago`}${m ? `, erased ${m[1]}` : ""}${e ? `, errors ${e[1]}` : ""}${w && w[1] !== "0" ? `, ${w[1]} tenant(s) waiting for the next pass` : ""}`,
  };
}

module.exports = async function jobs(R) {
  const C = L.T.containers;

  await R.run("jobs.backup", async () => {
    if (!L.containerRunning(C.backup)) return L.TARGET === "test" ? { skip: true, detail: `${C.backup} is not running on the test stack` } : { ok: false, detail: `${C.backup} is not running` };
    const r = L.dockerExec(C.backup, ["cat", "/backups/last_success.json"]);
    let rec = null;
    try { rec = JSON.parse(r.stdout); } catch { /* handled below */ }
    if (!rec) return L.TARGET === "test" ? { skip: true, detail: "no /backups/last_success.json on the test stack (no scheduled backup there)" } : { ok: false, detail: `no readable /backups/last_success.json: ${r.out.trim().slice(0, 120)}` };
    const age = Date.now() / 1000 - Number(rec.epoch || 0);
    const drill = rec.drill?.pass === true;
    const off = rec.offsite?.status || "unknown";
    const fresh = age < MAX_BACKUP_AGE_H * 3600;
    const okOff = off === "pass" || off === "not_configured";
    return { ok: fresh && drill && okOff, detail: `backup ${rec.backup_id} ${L.ageText(age)} old (limit ${MAX_BACKUP_AGE_H} h), drill ${drill ? "pass" : "FAIL"} ${rec.drill?.checks_total ? `${rec.drill.checks_total - (rec.drill.checks_failed || 0)}/${rec.drill.checks_total}` : ""}, off-site ${off}${rec.offsite?.snapshot ? " " + String(rec.offsite.snapshot).slice(0, 8) : ""}` };
  });

  await R.run("jobs.retention", async () => {
    const line = lastLine(L.dockerLogs(C.chatEdge, `${MAX_BACKUP_AGE_H}h`), /transcript retention: pass complete/);
    let up = "";
    try {
      const started = require("node:child_process").execFileSync("docker", ["inspect", C.chatEdge, "--format", "{{.State.StartedAt}}"], { encoding: "utf8" }).trim();
      up = ` (${C.chatEdge} up ${((Date.now() - Date.parse(started)) / 3_600_000).toFixed(1)} h)`;
    } catch { /* uptime unknown: the verdict does not depend on it */ }
    return retentionVerdict(line, Date.now(), up);
  });

  await R.run("jobs.sweep", async () => {
    const line = lastLine(L.dockerLogs(C.chatEdge, "10m"), /interrupted_turn_sweep/);
    if (!line) return { ok: false, detail: `no interrupted_turn_sweep in ${C.chatEdge} logs of the last 10 min` };
    const s = /"settled"\s*:\s*(\d+)/.exec(line);
    return { ok: true, detail: `last sweep ${agoText(timeOf(line))}${s ? `, settled ${s[1]}` : ""}` };
  });

  await R.run("jobs.ops_watch", async () => {
    if (!C.opsWatch) return { skip: true, detail: "no ops-watch service on the test stack" };
    if (!L.containerRunning(C.opsWatch)) return { ok: false, detail: `${C.opsWatch} is not running` };
    const line = lastLine(L.dockerLogs(C.opsWatch, "2m"), /"msg":\s*"tick"/);
    if (!line) return { ok: false, detail: `no tick in ${C.opsWatch} logs of the last 2 min` };
    const f = /"findings"\s*:\s*(\d+)/.exec(line);
    const open = /"open"\s*:\s*(\[[^\]]*\])/.exec(line);
    return { ok: true, detail: `last tick ${agoText(timeOf(line))}, findings ${f ? f[1] : "?"}${open && open[1] !== "[]" ? ` open=${open[1]}` : ""}` };
  });

  await R.run("jobs.maintenance", async () => {
    const container = C.opsWatch || C.backend;
    const r = L.dockerExec(container, ["python", "-m", "app.ops.maintenance", "status"]);
    const m = /\{"maintenance":\s*(.*)\}\s*$/m.exec(r.out);
    if (!m) return { ok: false, detail: `${container}: ${r.out.trim().slice(0, 140)}` };
    if (m[1].trim() === "null") return { ok: true, detail: "no maintenance window open" };
    return { ok: false, detail: `maintenance window left on: ${m[1].slice(0, 140)}` };
  });
};
module.exports.retentionVerdict = retentionVerdict;
