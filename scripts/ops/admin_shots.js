#!/usr/bin/env node
// ops — photograph every page of the admin console, in both languages, both themes, at two widths.
//
// The pictures are the material a design pass works from: a person looking at thirty pages from memory
// ends up with three kinds of gear icon (AM19 found exactly that). This tool opens each page the way a
// person does — the built app, a real sign-in, the stored theme and language — and writes one PNG per
// page × language × theme × width, named so a page can be found without opening twenty:
//
//     <page>__<en|th>__<dark|light>__<width>.png        e.g. providers__th__light__390.png
//
// beside a manifest.json (per picture: url, heading, page height, horizontal overflow, console errors,
// failed requests) and fonts.json (what the visitor's browser fetched from a third party on each cold
// load, with timings — the admin console still @imports Inter and JetBrains Mono from Google at runtime).
//
//   node scripts/ops/admin_shots.js                                   # test stack's admin-ui on :13000
//   node scripts/ops/admin_shots.js --base http://localhost:13000 --out scripts/ops/results/admin-shots/<date>
//                                   --creds scripts/agent-market/results/ts_admins.json   (the seeded admin)
//                                   --ids run=<uuid>,conv=<uuid>,kb=<uuid>   (the three detail pages)
//                                   --only providers,models --langs en --themes dark --widths 1440
//
// Output goes under scripts/ops/results/ (ignored, like every tool's results). The sign-in happens once
// and its cookie is kept in the output folder as state.json: the admin door allows 5 attempts per IP per
// 15 minutes, so a run that fails after sign-in is re-run without signing in again. Nothing here writes
// to the stack: every page is opened with GET and nothing on it is clicked.
//
// Playwright is taken from apps/web-chat's node_modules (the one copy on this machine), as the AM7
// surfaces probe did.
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const pw = require(path.join(ROOT, "apps", "web-chat", "node_modules", "playwright-core"));

const args = (() => {
  const a = {};
  const v = process.argv.slice(2);
  for (let i = 0; i < v.length; i++) {
    if (v[i].startsWith("--")) a[v[i].slice(2)] = v[i + 1] && !v[i + 1].startsWith("--") ? v[++i] : "true";
  }
  return a;
})();

const BASE = (args.base || "http://localhost:13000").replace(/\/$/, "");
const today = new Date().toISOString().slice(0, 10);
const OUT = path.resolve(ROOT, args.out || path.join("scripts", "ops", "results", "admin-shots", today));
const CREDS = path.resolve(ROOT, args.creds || path.join("scripts", "agent-market", "results", "ts_admins.json"));
const LANGS = (args.langs || "en,th").split(",");
const THEMES = (args.themes || "dark,light").split(",");
const WIDTHS = (args.widths || "1440,390").split(",").map(Number);
const SETTLE_MS = Number(args.settle || 1500);
const MAX_HEIGHT = 12000; // a page taller than this is cut here: the picture is for looking at, not for archiving

const ids = Object.fromEntries((args.ids || "").split(",").filter(Boolean).map((kv) => kv.split("=")));

// The pages, in the sidebar's order, then the three detail pages and the door.
const PAGES = [
  ["dashboard", "/"],
  ["providers", "/providers"],
  ["models", "/models"],
  ["policies", "/policies"],
  ["dlp-detectors", "/dlp-detectors"],
  ["routing", "/routing"],
  ["fallback", "/fallback"],
  ["dlp-coverage", "/dlp-coverage"],
  ["search-settings", "/search-settings"],
  ["tenants", "/tenants"],
  ["projects", "/projects"],
  ["api-keys", "/api-keys"],
  ["portal-users", "/portal-users"],
  ["portal-settings", "/portal-settings"],
  ["portal-token-packs", "/portal-token-packs"],
  ["requests", "/requests"],
  ["usage", "/usage"],
  ["audit-log", "/audit-log"],
  ["accounts", "/accounts"],
  ["alerts-events", "/alerts/events"],
  ["ai-review", "/ai-review"],
  ["chat-analytics", "/chat-analytics"],
  ["chat-conversations", "/chat-conversations"],
  ["chat-conversation-detail", ids.conv ? `/chat-conversations/${ids.conv}` : null],
  ["chat-users", "/chat-users"],
  ["chat-knowledge", "/chat-knowledge"],
  ["chat-knowledge-detail", ids.kb ? `/chat-knowledge/${ids.kb}` : null],
  ["agent-market", "/agent-market"],
  ["agent-market-run", ids.run ? `/agent-market/runs/${ids.run}` : null],
  ["chat-retention", "/chat-retention"],
  ["login", "/login"], // opened without the cookie
  // --extra slug=/path,slug2=/path2 : screens behind a query string, e.g. the Agent Market's sections
  ...(args.extra || "").split(",").filter(Boolean).map((kv) => { const i = kv.indexOf("="); return [kv.slice(0, i), kv.slice(i + 1)]; }),
].filter(([slug, url]) => url && (!args.only || args.only.split(",").includes(slug)));

function readCreds() {
  const j = JSON.parse(fs.readFileSync(CREDS, "utf-8"));
  if (j.email && j.password) return j;
  // the seeded test-stack admins: { "<email>": { role, password, ... } } — take the admin
  for (const [email, v] of Object.entries(j)) if (v && v.role === "admin" && v.password) return { email, password: v.password };
  throw new Error(`no admin credentials in ${CREDS}`);
}

async function hydrated(page) {
  // a Next page before React owns the form submits it as a plain GET (AM7); wait for the props
  await page.waitForFunction(() => {
    const f = document.querySelector("form");
    return !!f && Object.keys(f).some((k) => k.startsWith("__reactProps"));
  }, null, { timeout: 20000 });
}

async function signIn(browser, statePath) {
  if (fs.existsSync(statePath)) {
    const ctx = await browser.newContext({ storageState: statePath, viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    const ok = !page.url().includes("/login");
    await ctx.close();
    if (ok) return console.log(`signed in already (${path.relative(ROOT, statePath)})`);
    console.log("stored sign-in is stale, signing in again");
  }
  const { email, password } = readCreds();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await hydrated(page);
  await page.fill('input[type="text"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  await ctx.storageState({ path: statePath });
  await ctx.close();
  console.log(`signed in as ${email}`);
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const statePath = path.join(OUT, "state.json");
  const manifest = [];
  const fonts = [];
  const browser = await pw.chromium.launch();
  try {
    await signIn(browser, statePath);
    for (const lang of LANGS) {
      for (const theme of THEMES) {
        for (const width of WIDTHS) {
          const height = width < 700 ? 844 : 900;
          const ctx = await browser.newContext({ storageState: statePath, viewport: { width, height }, locale: lang === "th" ? "th-TH" : "en-US" });
          await ctx.addInitScript(([l, t]) => {
            try { localStorage.setItem("sran-admin-locale", l); localStorage.setItem("sran-theme", t); } catch (e) {}
          }, [lang, theme]);
          const loggedOut = await browser.newContext({ viewport: { width, height }, locale: lang === "th" ? "th-TH" : "en-US" });
          await loggedOut.addInitScript(([l, t]) => {
            try { localStorage.setItem("sran-admin-locale", l); localStorage.setItem("sran-theme", t); } catch (e) {}
          }, [lang, theme]);
          let cold = true;
          for (const [slug, url] of PAGES) {
            const page = await (slug === "login" ? loggedOut : ctx).newPage();
            const errors = [];
            const failed = [];
            page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 300)); });
            page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url().replace(BASE, "")}`.slice(0, 200)); });
            page.on("requestfailed", (r) => {
              const why = r.failure() && r.failure().errorText;
              if (why === "net::ERR_ABORTED") return; // a prefetch the page cancelled, not something a person would see
              failed.push(`FAILED ${r.url().slice(0, 160)} ${why}`);
            });
            const t0 = Date.now();
            let navErr = null;
            try {
              await page.goto(`${BASE}${url}`, { waitUntil: "networkidle", timeout: 45000 });
            } catch (e) { navErr = String(e.message).split("\n")[0]; }
            await page.waitForTimeout(SETTLE_MS);
            const info = await page.evaluate(() => {
              const d = document.documentElement;
              const h1 = document.querySelector("h1");
              const res = performance.getEntriesByType("resource").filter((r) => /googleapis|gstatic/.test(r.name)).map((r) => ({ url: r.name, ms: Math.round(r.duration), start: Math.round(r.startTime), bytes: r.transferSize }));
              const nav = performance.getEntriesByType("navigation")[0];
              return {
                title: document.title, h1: h1 ? h1.textContent.trim().slice(0, 120) : null,
                lang: d.lang, theme: d.getAttribute("data-theme"), scrollHeight: d.scrollHeight, scrollWidth: d.scrollWidth, clientWidth: d.clientWidth,
                thirdParty: res, requests: performance.getEntriesByType("resource").length + 1,
                loadMs: nav ? Math.round(nav.loadEventEnd) : null,
              };
            }).catch(() => ({}));
            const name = `${slug}__${lang}__${theme}__${width}.png`;
            // The shell scrolls inside <main>, not the document: a full-page shot of the document is one
            // viewport tall. So the viewport is grown to the content's own height (capped) before the shot.
            const contentHeight = await page.evaluate(() => {
              const m = document.querySelector("main");
              const inner = m ? Math.max(m.scrollHeight, ...Array.from(m.querySelectorAll("*")).filter((e) => e.scrollHeight > e.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(e).overflowY)).map((e) => e.scrollHeight + e.getBoundingClientRect().top)) : 0;
              return Math.max(document.documentElement.scrollHeight, inner);
            }).catch(() => 0);
            const tall = contentHeight > MAX_HEIGHT;
            const shotHeight = Math.min(Math.max(height, contentHeight), MAX_HEIGHT);
            if (shotHeight !== height) { await page.setViewportSize({ width, height: shotHeight }); await page.waitForTimeout(400); }
            await page.screenshot({ path: path.join(OUT, name), fullPage: true }).catch((e) => errors.push("screenshot: " + e.message.split("\n")[0]));
            if (shotHeight !== height) await page.setViewportSize({ width, height });
            const row = { file: name, page: slug, url, lang, theme, width, finalUrl: page.url().replace(BASE, ""), ...info, contentHeight, cut: tall, navError: navErr, consoleErrors: errors, failedRequests: failed, wallMs: Date.now() - t0 };
            delete row.thirdParty;
            manifest.push(row);
            if (cold || (info.thirdParty || []).length) {
              fonts.push({ page: slug, lang, theme, width, cold, requests: info.requests, loadMs: info.loadMs, thirdParty: info.thirdParty || [] });
            }
            cold = false;
            const flag = (errors.length ? ` errors=${errors.length}` : "") + (failed.length ? ` failed=${failed.length}` : "") + (info.scrollWidth > info.clientWidth ? " OVERFLOW-X" : "") + (navErr ? ` NAV: ${navErr}` : "");
            console.log(`${name.padEnd(48)} h=${String(contentHeight || "?").padStart(5)}${flag}`);
            await page.close();
          }
          await ctx.close();
          await loggedOut.close();
        }
      }
    }
  } finally {
    await browser.close();
  }
  // a later run into the same folder (another page, a corrected id) adds to the manifest rather than replacing it
  const merge = (file, rows, key) => {
    const p = path.join(OUT, file);
    let kept = [];
    try { kept = JSON.parse(fs.readFileSync(p, "utf-8")).filter((r) => !rows.some((n) => key(n) === key(r))); } catch (e) { /* first run */ }
    fs.writeFileSync(p, JSON.stringify([...kept, ...rows], null, 1));
  };
  merge("manifest.json", manifest, (r) => r.file);
  merge("fonts.json", fonts, (r) => `${r.page}|${r.lang}|${r.theme}|${r.width}`);
  const withErrors = manifest.filter((m) => m.consoleErrors.length || m.failedRequests.length || m.navError);
  const overflow = manifest.filter((m) => m.scrollWidth > m.clientWidth);
  console.log(`\n${manifest.length} pictures in ${path.relative(ROOT, OUT)} · ${withErrors.length} with console errors or failed requests · ${overflow.length} with horizontal overflow`);
}

main().catch((e) => { console.error(e); process.exit(1); });
