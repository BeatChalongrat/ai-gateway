// FIX134 F — area 5: every background job's last success.
//
// backup       /backups/last_success.json in the backup container: newer than 26 h, drill pass,
//              off-site copy pass (or not configured — the detail says which)
// retention    chat-edge's "transcript retention: pass complete" in the last 26 h
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
    if (!line) return { ok: false, detail: `no "transcript retention: pass complete" in ${C.chatEdge} logs of the last ${MAX_BACKUP_AGE_H} h` };
    const m = /"erased"\s*:\s*(\d+)/.exec(line);
    const e = /"errors"\s*:\s*(\d+)/.exec(line);
    return { ok: !e || e[1] === "0", detail: `last pass ${agoText(timeOf(line))}${m ? `, erased ${m[1]}` : ""}${e ? `, errors ${e[1]}` : ""}` };
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
