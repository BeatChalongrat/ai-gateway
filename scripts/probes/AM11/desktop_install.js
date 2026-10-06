#!/usr/bin/env node
// AM11 — the new desktop build installed over the one that is there, and nothing more: no debugging
// port, no page driven, nothing done as the person who is signed in (CARD-RULES §4 and §6b).
//
//   node scripts/probes/AM11/desktop_install.js            # install the checkout's version
//   node scripts/probes/AM11/desktop_install.js status     # what is installed and running, from the app's own log
//
//   1. the app's log is read: if it was used in the last 10 minutes it is NOT closed (exit 3);
//   2. the app is closed, the installer is run silently, and the app is started again the way a person
//      starts it — only if it was running before;
//   3. from the app's own log of that start: the version, that the stored session was restored, how
//      many re-authentication lines there are (a loop is a stop), and that port 9333 is closed.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { spawn, spawnSync } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..", "..", "..");
const VERSION = require(path.join(ROOT, "apps", "desktop", "package.json")).version;
const EXE = path.join(process.env.LOCALAPPDATA || "", "Programs", "sran-desktop", "SRAN Desktop.exe");
const LOGF = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "logs", "shell.log");
const SETUP = path.join(ROOT, "apps", "desktop", "release", `SRAN Desktop Setup ${VERSION}.exe`);
const PROCS = ["SRAN Desktop.exe", "SranDesktop.ToolsSidecar.exe", "SranDesktop.BrowserHost.exe"];
const SESSION_LINE = /BOOT:|SIGN-IN|SIGNED IN|TICKET|VIEW: signed|SESSION (RESTORED|ROTATED|EXPIRED)|RE-?AUTH|UPDATE CHECK|PAGE STATE|BRIDGE INBOUND: host\.query/;

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const say = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const running = () => /SRAN Desktop\.exe/i.test(spawnSync("tasklist", ["/FI", `IMAGENAME eq ${PROCS[0]}`, "/NH"], { encoding: "utf8" }).stdout || "");
const kill = () => { for (const p of PROCS) spawnSync("taskkill", ["/F", "/T", "/IM", p], { encoding: "utf8" }); };
const portOpen = (port) => new Promise((resolve) => {
  const s = net.connect({ host: "127.0.0.1", port, timeout: 1500 });
  s.once("connect", () => { s.destroy(); resolve(true); });
  s.once("error", () => resolve(false));
  s.once("timeout", () => { s.destroy(); resolve(false); });
});
function logSince(sinceIso) {
  const out = [];
  for (const f of [`${LOGF}.2`, `${LOGF}.1`, LOGF]) {
    try { out.push(...fs.readFileSync(f, "utf8").split(/\r?\n/).filter((l) => l.slice(0, 24) >= sinceIso)); } catch { /* not rotated */ }
  }
  return out;
}
async function until(fn, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await fn()) return true; await wait(500); }
  return false;
}
async function status(since) {
  const lines = logSince(since || "0000");
  const boot = (lines.filter((l) => /SRAN Desktop \S+ pid=/.test(l)).pop() || "").slice(24, 80).trim();
  const session = lines.filter((l) => SESSION_LINE.test(l)).map((l) => l.slice(24, 130).trim());
  const reauth = session.filter((l) => /RE-?AUTH|EXPIRED|SIGN-IN STARTED/i.test(l)).length;
  say(`running: ${running()} · ${boot || "no start in the log since then"}`);
  say(`session: ${session.slice(-4).join(" | ") || "(no line)"}`);
  say(`re-authentication lines: ${reauth} · port 9333 ${(await portOpen(9333)) ? "OPEN" : "closed"}`);
  return { boot, reauth };
}

(async () => {
  if (process.argv[2] === "status") { await status(new Date(Date.now() - 6 * 3600_000).toISOString()); return; }
  if (!fs.existsSync(SETUP)) { say(`no installer at ${SETUP}`); process.exit(2); }
  const was = running();
  const busy = logSince(new Date(Date.now() - 10 * 60_000).toISOString())
    .filter((l) => /turn|stream|message|tool|composer|send/i.test(l) && !SESSION_LINE.test(l));
  say(`the app is ${was ? "running" : "not running"}; lines of use in its log in the last 10 minutes: ${busy.length}`);
  if (was && busy.length) { say("the app was used in the last 10 minutes: not closing it"); process.exit(3); }
  if (was) { kill(); await until(async () => !running(), 15000); }
  const t0 = new Date().toISOString();
  const r = spawnSync(SETUP, ["/S"], { encoding: "utf8", timeout: 300000 });
  say(`installer ${VERSION} /S exit ${r.status}`);
  await wait(4000);
  // an installer may start the app by itself; it is started once, the way a person starts it
  kill();
  await until(async () => !running(), 15000);
  if (!was) { say("the app was not running before and is left closed"); return; }
  spawn(EXE, [], { detached: true, stdio: "ignore", windowsHide: false }).unref();
  await until(async () => running(), 30000);
  await wait(15000);
  const s = await status(t0);
  if (!s.boot.includes(VERSION)) { say(`NOT the new version: ${s.boot}`); process.exit(1); }
  if (s.reauth > 1) { say("a re-authentication loop: stop"); process.exit(1); }
})();
