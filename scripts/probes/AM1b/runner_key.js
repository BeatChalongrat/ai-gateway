#!/usr/bin/env node
// AM1b §4.3 — the runner's own gateway identity on LIVE: portal project
// "Agent Market runner" + one API key, created through the owner's signed-in
// desktop session (CDP 9333), and written straight into .env as
// AGENT_RUNNER_GATEWAY_API_KEY. The key is never printed, logged or saved
// anywhere else: this prints its prefix and length only.
//
//   node scripts/probes/AM1b/runner_key.js
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const L = require("../../smoke/lib.js");

const EXE_DIR = path.join(process.env.LOCALAPPDATA || "", "Programs", "sran-desktop");
const cfg = JSON.parse(fs.readFileSync(path.join(EXE_DIR, "resources", "desktop.config.json"), "utf8"));
const GW = cfg.gatewayUrl;
const WEB = (cfg.pageUrl || "https://localhost:3002/chat").replace(/\/chat.*$/, "");
const ENV = path.join(L.ROOT, ".env");
const KEY_NAME = "AGENT_RUNNER_GATEWAY_API_KEY";

function setEnv(key, value) {
  const text = fs.readFileSync(ENV, "utf8");
  const lines = text.split(/\r?\n/);
  const i = lines.findIndex((l) => l.startsWith(key + "="));
  if (i >= 0) lines[i] = `${key}=${value}`;
  else {
    if (lines[lines.length - 1] === "") lines.pop();
    lines.push(`${key}=${value}`, "");
  }
  fs.writeFileSync(ENV, lines.join(text.includes("\r\n") ? "\r\n" : "\n"));
}

async function main() {
  const { chromium } = require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9333", { timeout: 20000 });
  const page = browser.contexts()[0].pages().find((p) => p.url().startsWith(WEB));
  if (!page) throw new Error("no signed-in page");
  const res = await page.evaluate(async (gw) => {
    const h = { "Content-Type": "application/json" };
    const me = await (await fetch(`${gw}/portal/me`, { credentials: "include" })).json();
    let r = await fetch(`${gw}/portal/projects`, { credentials: "include" });
    const list = await r.json();
    let proj = (list.projects || list.items || list || []).find?.((p) => p.name === "Agent Market runner");
    let created = false;
    if (!proj) {
      r = await fetch(`${gw}/portal/projects`, { method: "POST", credentials: "include", headers: h,
        body: JSON.stringify({ name: "Agent Market runner", description: "agent-runner's own gateway identity (AM1b): its own rate limit and spend, revocable on its own" }) });
      if (!r.ok) return { error: `create project ${r.status} ${(await r.text()).slice(0, 200)}` };
      proj = await r.json();
      created = true;
    }
    const pid = proj.project_id || proj.id;
    r = await fetch(`${gw}/portal/projects/${pid}/keys`, { method: "POST", credentials: "include", headers: h,
      body: JSON.stringify({ name: `agent-runner ${new Date().toISOString().slice(0, 10)}` }) });
    if (!r.ok) return { error: `create key ${r.status} ${(await r.text()).slice(0, 200)}` };
    const k = await r.json();
    return { email: me.email || me.user?.email, project: pid, created, key: k.api_key, prefix: k.key_prefix };
  }, GW);
  await browser.close();
  if (res.error) throw new Error(res.error);
  if (!res.key) throw new Error("the portal returned no key");
  setEnv(KEY_NAME, res.key);
  console.log(JSON.stringify({ owner_is_signed_in: res.email === "chalongrat.t@gbtech.co.th", project: res.project, project_created: res.created,
    key_prefix: res.prefix, key_length: res.key.length, written_to: ".env " + KEY_NAME }));
}

main().catch((e) => { console.error("FAIL", e.message); process.exit(1); });
