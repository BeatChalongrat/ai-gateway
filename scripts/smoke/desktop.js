// FIX134 F — area 7: the installed desktop app (e57+) over CDP, the desk3.js way.
//
// SMOKE_DESKTOP=1 (the default on live, off on the test stack, which the app cannot point at):
//   1. if the app is running without a debugging port it is closed (taskkill /F — a polite
//      close does not end the tray app) and relaunched at the end without the flag;
//   2. launch "%LOCALAPPDATA%\Programs\sran-desktop\SRAN Desktop.exe --remote-debugging-port=9333"
//      (SMOKE_DESKTOP_EXE overrides), wait for 9333;
//   3. desktop.startup — the app's own startup, read from its shell log
//      (%APPDATA%\SranDesktop-Electron\logs\shell.log, rotated on every start): config loaded,
//      the local CA pinned, the boot decision (session restored / "no stored session"), and no
//      TLS or network error from the calls it makes over https (session rotation, version,
//      models) — any UNABLE_TO_VERIFY_LEAF_SIGNATURE / CERT_* / ERR_CERT_* line is a FAIL;
//   4. desktop.session — the window is the signed-in page (https://localhost:3002/…) or the
//      shell's signed-out / signing-in view. Sign-in is Google-only in the system browser
//      (nonce-bound ticket; e57 opens it by itself at boot when no session is stored), so with
//      no session the stream part is SKIPPED with that reason — the suite never completes a
//      Google sign-in. With a session: desktop.turn — a "[SMOKE]" chat (memory off, created
//      through the page's own session), one turn typed into the composer, the answer read
//      back, the chat deleted by id;
//   5. desktop.closed — the app is closed and 9333 is closed again.
// Needs playwright-core (apps/web-chat/node_modules, no new dependency); SKIP when absent.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { spawn, spawnSync } = require("node:child_process");
const L = require("./lib");

const PORT = Number(L.env("SMOKE_DESKTOP_CDP_PORT", "9333")) || 9333;
const EXE = L.env("SMOKE_DESKTOP_EXE", path.join(process.env.LOCALAPPDATA || "", "Programs", "sran-desktop", "SRAN Desktop.exe"));
const LOG = path.join(process.env.APPDATA || "", "SranDesktop-Electron", "logs", "shell.log");
const PROCS = ["SRAN Desktop.exe", "SranDesktop.ToolsSidecar.exe", "SranDesktop.BrowserHost.exe"];
const COMPOSER = 'textarea[data-shortcut="composer"]';
const STOP = 'button[aria-label*="Stop"], button[aria-label*="หยุด"], [data-testid="stop-button"]';

const portOpen = (port) => new Promise((resolve) => {
  const s = net.connect({ host: "127.0.0.1", port, timeout: 1500 });
  s.once("connect", () => { s.destroy(); resolve(true); });
  s.once("error", () => resolve(false));
  s.once("timeout", () => { s.destroy(); resolve(false); });
});
async function waitFor(fn, ms, step = 500) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t0 > ms) return null;
    await L.wait(step);
  }
}
function running() {
  const out = spawnSync("tasklist", ["/FI", `IMAGENAME eq ${PROCS[0]}`, "/NH"], { encoding: "utf8" }).stdout || "";
  return /SRAN Desktop\.exe/i.test(out);
}
function kill() {
  for (const p of PROCS) spawnSync("taskkill", ["/F", "/T", "/IM", p], { encoding: "utf8" });
}
function launch(args) {
  const child = spawn(EXE, args, { detached: true, stdio: "ignore", windowsHide: false });
  child.unref();
}
/** The shell log rotates on every start (shell.log → .1 → .2), so a relaunch moves the lines
 * of the launch before it; read all three, oldest first, and keep what is newer than `sinceIso`. */
function logSince(sinceIso) {
  const out = [];
  for (const f of [`${LOG}.2`, `${LOG}.1`, LOG]) {
    try { out.push(...fs.readFileSync(f, "utf8").split(/\r?\n/).filter((l) => l.slice(0, 24) >= sinceIso)); } catch { /* not rotated that far */ }
  }
  return out;
}
const SESSION_LINE = /BOOT:|SIGN-IN|SIGNED IN|TICKET|VIEW: signed|SESSION (RESTORED|ROTATED|EXPIRED)|RE-?AUTH/;
function installedConfig() {
  try { return JSON.parse(fs.readFileSync(path.join(path.dirname(EXE), "resources", "desktop.config.json"), "utf8")); } catch { return {}; }
}

module.exports = async function desktop(R) {
  if (process.platform !== "win32") { R.skip("desktop.startup", "the desktop check runs on Windows only"); return; }
  if (!fs.existsSync(EXE)) { R.skip("desktop.startup", `not installed: ${EXE}`); return; }
  let chromium = null;
  const pwDirs = [L.env("SMOKE_PLAYWRIGHT_DIR"), path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"), path.join(L.ROOT, "apps", "desktop", "node_modules", "playwright-core")].filter(Boolean);
  for (const d of pwDirs) { try { ({ chromium } = require(d)); break; } catch { /* next */ } }
  if (!chromium) { R.skip("desktop.startup", `playwright-core not found (apps/web-chat/node_modules — npm install there — or SMOKE_PLAYWRIGHT_DIR)`); return; }
  const cfg = installedConfig();
  const pageUrl = cfg.pageUrl || "https://localhost:3002/chat";
  const chatEdgeUrl = cfg.chatEdgeUrl || "https://localhost:4000";

  const wasRunning = running();
  const hadPort = await portOpen(PORT);
  if (wasRunning && !hadPort) {
    // CARD-RULES §4: never close the owner's app while it is in use — a turn, a stream, a tool
    // or a composer line in its own log inside the last 10 minutes means SKIP, not taskkill.
    const busy = logSince(new Date(Date.now() - 10 * 60_000).toISOString()).filter((l) => /turn|stream|message|tool|composer|send/i.test(l) && !SESSION_LINE.test(l));
    if (busy.length) { R.skip("desktop.startup", `the app was used in the last 10 minutes (${busy.length} log lines); not closing it`); return; }
    kill(); await waitFor(async () => !running(), 15000);
  }
  const since = new Date(Date.now() - 1000).toISOString();
  if (!hadPort) launch([`--remote-debugging-port=${PORT}`]);
  let browser = null;
  let page = null;

  await R.run("desktop.startup", async () => {
    const up = await waitFor(() => portOpen(PORT), 45000);
    if (!up) return { ok: false, detail: `CDP port ${PORT} did not open within 45 s after launching ${path.basename(EXE)}; log: ${logSince(since).slice(-2).join(" | ").slice(0, 200)}` };
    // the app's own startup, from its log: config loaded, the local CA pinned, its boot decision
    // (a stored session restored, or "no stored session"), and no TLS / certificate error from
    // any call it makes over https (rotation, version, models)
    const isTlsError = (l) => /UNABLE_TO_VERIFY_LEAF_SIGNATURE|SELF_SIGNED_CERT|CERT_HAS_EXPIRED|ERR_CERT_|DEPTH_ZERO|certificate/i.test(l) && /error|ERR|fail|refus|reject/i.test(l);
    const booted = await waitFor(() => {
      const lines = logSince(since);
      const config = lines.find((l) => /config loaded from/.test(l));
      const boot = lines.find((l) => /BOOT:|SESSION RESTORED|SIGNED IN|SIGN-IN STARTED/.test(l));
      return config && boot ? { config, boot, lines } : null;
    }, 40000);
    const lines = logSince(since);
    if (!booted) return { ok: false, detail: `no boot decision in shell.log within 40 s (${lines.length} lines); tail: ${lines.slice(-3).map((l) => l.slice(24, 140)).join(" | ")}` };
    await L.wait(4000); // the calls that follow the boot decision
    const all = logSince(since);
    const tls = all.find(isTlsError);
    if (tls) return { ok: false, detail: `TLS error in the app's startup: ${tls.slice(24, 220)}` };
    const failedCall = all.find((l) => /https:\/\/|gateway|version|models|rotat|refresh/i.test(l) && /\b(ECONNREFUSED|ETIMEDOUT|ENOTFOUND|fetch failed|network:)/i.test(l));
    const ca = all.find((l) => /local CA pinned/.test(l));
    const calls = all.filter((l) => /(version|models|catalogue|ROTAT|refresh)/i.test(l) && /https:\/\/|gateway|answered|\b\d{3}\b/.test(l)).length;
    const version = /SRAN Desktop (\S+)/.exec(all.find((l) => /SRAN Desktop \d/.test(l)) || "")?.[1] || "?";
    return { ok: !failedCall, detail: `${path.basename(EXE)} ${version} up, CDP ${PORT} open, ${ca ? "local CA pinned" : "no local CA line"}, boot: "${booted.boot.slice(24).replace(/^\s*(INFO|WARN|ERROR)\s+/, "").slice(0, 90)}"; ${failedCall ? `a startup call failed: ${failedCall.slice(24, 200)}` : `no TLS or network error in ${all.length} log lines${calls ? "" : " (e57 logs no version/model call before sign-in)"}`}` };
  });

  let signedIn = false;
  await R.run("desktop.session", async () => {
    try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`, { timeout: 20000 }); } catch (e) { return { ok: false, detail: `connectOverCDP: ${L.errText(e)}` }; }
    const ctx = browser.contexts()[0];
    page = await waitFor(async () => (ctx.pages().find((p) => p.url().startsWith(pageUrl.replace(/\/chat.*$/, ""))) || ctx.pages().find((p) => /session\.html/.test(p.url())) || null), 30000);
    if (!page) return { ok: false, detail: `no app window found over CDP (pages: ${ctx.pages().map((p) => p.url().slice(0, 60)).join(", ") || "none"})` };
    const url = page.url();
    if (/session\.html/.test(url)) {
      const lines = logSince(since).filter((l) => SESSION_LINE.test(l)).map((l) => l.slice(24, 150).replace(/\s+/g, " "));
      const state = /state=([a-z-]+)/.exec(url)?.[1] || "signed-out";
      return { skip: true, detail: `no session: the shell shows its "${state}" view; sign-in is Google-only in the system browser (the app opens it itself at boot; the suite never completes it). Log: ${lines.slice(-2).join(" | ") || "none"}` };
    }
    signedIn = true;
    try { await page.waitForSelector(COMPOSER, { timeout: 60000 }); } catch { return { ok: false, detail: `signed in (${url.slice(0, 60)}) but the composer did not appear within 60 s` }; }
    return { ok: true, detail: `signed-in page ${url.slice(0, 60)} with the composer` };
  });

  if (signedIn && page) {
    await R.run("desktop.turn", async () => {
      // the chat through the page's own session (the host injects the cookies for chatEdgeUrl)
      const mk = await page.evaluate(async ([edge, model, title]) => {
        const h = { "Content-Type": "application/json" };
        const c = await fetch(`${edge}/api/v1/conversations`, { method: "POST", credentials: "include", headers: h, body: JSON.stringify({ model_id: model, title }) });
        const cj = await c.json().catch(() => ({}));
        if (!c.ok || !cj.id) return { status: c.status, id: null };
        const p = await fetch(`${edge}/api/v1/conversations/${cj.id}`, { method: "PATCH", credentials: "include", headers: h, body: JSON.stringify({ memory_enabled: false, title }) });
        const pj = await p.json().catch(() => ({}));
        return { status: c.status, id: cj.id, memory: pj.memory_enabled };
      }, [chatEdgeUrl, L.FREE_MODEL, `[SMOKE] desktop ${new Date().toISOString().slice(0, 16)}`]);
      if (!mk.id) return { ok: false, detail: `create chat through the app's session → ${mk.status}` };
      if (mk.memory !== false) return { ok: false, detail: `chat ${mk.id}: memory_enabled=${mk.memory} after the patch (not sent)` };
      let result = { ok: false, detail: "" };
      try {
        await page.goto(`${pageUrl.replace(/\/chat.*$/, "")}/chat/${mk.id}`, { waitUntil: "domcontentloaded" });
        await page.waitForSelector(COMPOSER, { timeout: 60000 });
        await L.wait(1500);
        await page.fill(COMPOSER, "[SMOKE] ตอบสั้นๆ เป็นตัวเลขอย่างเดียว: 1+1 เท่ากับเท่าไร");
        const t0 = Date.now();
        await page.keyboard.press("Enter");
        await L.wait(1500);
        const ended = await waitFor(async () => ((await page.locator(STOP).count()) === 0 ? Date.now() - t0 : null), 180000);
        const text = await page.evaluate(() => { const r = [...document.querySelectorAll("[data-msg-index]")]; return (r[r.length - 1]?.innerText || "").replace(/\s+/g, " ").trim(); });
        const row = L.csql(`select status, coalesce(terminal_cause,'') from chat.messages_masked where conversation_id='${mk.id}' and role='assistant' order by created_at desc limit 1`);
        result = { ok: ended !== null && /completed/.test(row) && text.length > 0, detail: `chat ${mk.id.slice(0, 8)}: stream ended ${ended === null ? "NOT within 180 s" : `in ${ended} ms`}, assistant row "${row}", answer "${text.slice(-40)}"` };
      } finally {
        const del = await page.evaluate(async ([edge, id]) => (await fetch(`${edge}/api/v1/conversations/${id}`, { method: "DELETE", credentials: "include" })).status, [chatEdgeUrl, mk.id]).catch(() => 0);
        result.detail += `, deleted=${del}`;
        if (!(del < 300 || del === 404)) result.ok = false;
      }
      return result;
    });
  } else if (!signedIn) {
    R.skip("desktop.turn", "no desktop session (see desktop.session)");
  }

  await R.run("desktop.closed", async () => {
    if (browser) await browser.close().catch(() => {});
    kill();
    const gone = await waitFor(async () => !(await portOpen(PORT)) && !running(), 20000);
    if (wasRunning && !hadPort) launch([]); // put the owner's tray app back, without the debugging port
    return { ok: !!gone, detail: `${path.basename(EXE)} closed, port ${PORT} ${gone ? "closed" : "STILL OPEN"}${wasRunning && !hadPort ? "; relaunched without the debugging port (it was running before the check)" : ""}` };
  });
};
