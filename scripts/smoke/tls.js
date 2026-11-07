// FIX134 F — area 1 + 4: the front door. https on all five ports (page loads), the certificate
// chain valid WITHOUT a CA flag (the Windows store: PowerShell Invoke-WebRequest, or Python's
// default context), the plain-http ports behaving as documented (400 today; a redirect to
// https once FIX134 A4 is deployed — either passes, the detail says which), API docs off,
// and the plain loopback debug doors.
"use strict";
const { execFileSync } = require("node:child_process");
const L = require("./lib");

const PAGES = [
  // [name, key, path, want, describe]
  ["pages.web_version", "web", "/api/version", 200, (r) => `build=${r.json?.build || "?"}`],
  ["pages.portal_login", "portal", "/login", 200, (r) => `${r.text.length} bytes`],
  ["pages.admin_login", "admin", "/login", 200, (r) => `${r.text.length} bytes`],
  ["pages.chat_edge_health", "edge", "/health", 200, (r) => `status=${r.json?.status || "?"}`],
  ["pages.gateway_health", "gw", "/__gw/health", 200, (r) => `status=${r.json?.status || "?"}`],
];

/** The five https URLs checked by a client that trusts the OS store, not a CA file. */
function osStoreCheck(urls) {
  if (process.platform === "win32") {
    const script = [
      "$ProgressPreference='SilentlyContinue'",
      `foreach ($u in @(${urls.map((u) => `'${u}'`).join(",")})) {`,
      "  try { $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 15 -MaximumRedirection 0 -ErrorAction Stop; \"$u OK $($r.StatusCode)\" }",
      "  catch { $c = $null; try { $c = [int]$_.Exception.Response.StatusCode } catch {}; if ($c) { \"$u OK $c\" } else { \"$u ERR $($_.Exception.Message)\" } }",
      "}",
    ].join("\n");
    try {
      const out = execFileSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], { encoding: "utf8", timeout: 120000 });
      return { tool: "PowerShell Invoke-WebRequest (Windows store)", lines: out.trim().split(/\r?\n/) };
    } catch (e) {
      return { tool: "PowerShell", lines: [], error: L.errText(e) };
    }
  }
  const py = [
    "import ssl, sys, urllib.request",
    "ctx = ssl.create_default_context()",
    "for u in sys.argv[1:]:",
    "    try: print(u, 'OK', urllib.request.urlopen(u, context=ctx, timeout=15).status)",
    "    except urllib.error.HTTPError as e: print(u, 'OK', e.code)",
    "    except Exception as e: print(u, 'ERR', str(e)[:120])",
  ].join("\n");
  try {
    const out = execFileSync("python", ["-c", py, ...urls], { encoding: "utf8", timeout: 120000 });
    return { tool: "python ssl default context (OS store)", lines: out.trim().split(/\r?\n/) };
  } catch (e) {
    return { tool: "python", lines: [], error: L.errText(e) };
  }
}

module.exports = async function tls(R) {
  const base = L.API_BASE;
  const runningTip = L.T.containers.webChat ? L.dockerLabel(L.T.containers.webChat, "org.opencontainers.image.revision") : null;

  for (const [name, key, p, want, describe] of PAGES) {
    if (!base[key]) { R.skip(name, `no ${key} on the ${L.TARGET} target`); continue; }
    await R.run(name, async () => {
      const r = await L.tryHttp(base[key] + p, { timeoutMs: 20000 });
      if (r.status !== want) return { ok: false, detail: `${base[key]}${p} → ${r.status || r.error}${r.text ? " " + r.text.slice(0, 80) : ""}` };
      let detail = `${base[key]}${p} → ${r.status} ${describe(r)}`;
      if (name === "pages.web_version") {
        const build = r.json?.build;
        if (!build || build === "dev") return { ok: false, detail: `${detail} (no build id)` };
        if (runningTip && runningTip !== build) return { ok: false, detail: `${detail} but the web-chat image is revision ${runningTip}` };
        let hist = "";
        try { execFileSync("git", ["-C", L.ROOT, "cat-file", "-e", `${build}^{commit}`], { stdio: "ignore" }); hist = ", a commit of this repo"; } catch { hist = ", not a commit of this checkout"; }
        detail += ` (= image revision${hist})`;
      }
      return { ok: true, detail };
    });
  }
  await R.run("pages.backend_ready", async () => {
    const r = await L.tryHttp(L.T.plain.backend + "/ready", { timeoutMs: 20000 });
    const tq = r.json?.checks?.turn_queue;
    return { ok: r.status === 200, detail: `${L.T.plain.backend}/ready → ${r.status || r.error} status=${r.json?.status || "?"}${tq ? ` queue active=${tq.active} waiting=${tq.waiting}` : ""}` };
  });

  if (!L.T.hasFrontDoor) {
    R.skip("tls.chain", "no front door on the test stack");
    R.skip("tls.plain_http", "no front door on the test stack");
    R.skip("tls.debug_doors", "no front door on the test stack");
  } else {
    const f = L.T.front;
    await R.run("tls.chain", async () => {
      const urls = [`${f.admin}/login`, `${f.portal}/login`, `${f.web}/api/version`, `${f.edge}/health`, `${f.gw}/__gw/health`];
      const c = osStoreCheck(urls);
      if (c.error) return { ok: false, detail: `${c.tool}: ${c.error}` };
      const bad = c.lines.filter((l) => !/ OK \d+$/.test(l));
      if (bad.length || c.lines.length !== urls.length) return { ok: false, detail: `${c.tool}: ${bad.join(" | ") || c.lines.join(" | ")}` };
      return { ok: true, detail: `${c.tool}: 5/5 ports trusted without a CA flag` };
    });
    await R.run("tls.plain_http", async () => {
      const parts = [];
      let bad = 0;
      for (const port of [3000, 3001, 3002, 4000, 8080]) {
        const r = await L.tryHttp(`http://localhost:${port}/`, { timeoutMs: 10000 });
        const loc = r.headers.get ? r.headers.get("location") || "" : "";
        if (r.status >= 300 && r.status < 400 && /^https:/i.test(loc)) parts.push(`${port}=${r.status}→https`);
        else if (r.status === 400) parts.push(`${port}=400`);
        else if (r.status >= 400 && r.status < 500 && /https/i.test(r.text)) parts.push(`${port}=${r.status} json`);
        else { bad++; parts.push(`${port}=${r.status || r.error}!`); }
      }
      const shape = parts.every((p) => /=400$/.test(p)) ? "400 on every port (pre-A4 behaviour)" : parts.some((p) => /→https/.test(p)) ? "redirect to https (A4 behaviour)" : "mixed";
      return { ok: bad === 0, detail: `${parts.join(" ")} — ${shape}` };
    });
    await R.run("tls.debug_doors", async () => {
      const doors = [[L.T.plain.admin, "/login"], [L.T.plain.portal, "/login"], [L.T.plain.web, "/api/version"], [L.T.plain.edge, "/health"], [L.T.plain.gw, "/__gw/health"]];
      const parts = [];
      let bad = 0;
      for (const [u, p] of doors) {
        const r = await L.tryHttp(u + p, { timeoutMs: 10000 });
        if (r.status !== 200) bad++;
        parts.push(`${u.replace("http://127.0.0.1:", "")}=${r.status || r.error}`);
      }
      return { ok: bad === 0, detail: parts.join(" ") };
    });
  }

  await R.run("tls.docs_off", async () => {
    const a = await L.tryHttp(`${base.gw}/docs`, { timeoutMs: 10000 });
    const b = await L.tryHttp(`${base.gw}/openapi.json`, { timeoutMs: 10000 });
    return { ok: a.status === 404 && b.status === 404, detail: `/docs=${a.status || a.error} /openapi.json=${b.status || b.error} (want 404)` };
  });
};
