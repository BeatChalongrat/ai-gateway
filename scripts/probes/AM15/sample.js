#!/usr/bin/env node
// AM15 — make an agent's catalogue sample again, the way an admin does: sign in to the admin app, open
// Agent Market → Catalogue, press "make the sample again" on the agent's row. Then read the new sample's
// OWN RECORD, not its status: who wrote the commentary (a run that "succeeded" on 9 Oct 2026 had its
// commentary written by rules, the model being away — AM-14 reported the status and missed it).
//
//   SMOKE_TARGET=live node scripts/probes/AM15/sample.js remake sran.financial-report
//   SMOKE_TARGET=live node scripts/probes/AM15/sample.js look   sran.financial-report
//
// On live the admin is the walk account (QA_EMAIL / QA_PASSWORD of the settings file; never printed, one
// sign-in — the limit is 5 per address in 15 minutes). The sample belongs to no person's install.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const L = require("../../smoke/lib.js");

const LIVE = L.TARGET === "live";
const ADMIN = process.env.AM15_ADMIN || (LIVE ? L.T.front.admin : "http://localhost:13000");
const OUT = path.join(__dirname, "results", "sample", L.TARGET);
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const sql = (q) => execFileSync("docker", ["exec", "-i", L.T.containers.pgChat, "psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-F", "|", "-c", q], { encoding: "buffer" }).toString("utf8").trim();
const lit = (s) => `'${String(s).replace(/'/g, "''")}'`;

/** The newest sample of the agent's newest released version: the run and what its own record says. */
function sampleRecord(slug) {
  const row = sql(`
    select r.id, d.version, r.status, coalesce(r.status_reason, ''), coalesce(r.credits_spent, 0),
           to_char(r.created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
           coalesce(round(extract(epoch from (r.finished_at - r.created_at)))::text, ''),
           (select count(*) from agent_market.agent_output_artifacts a where a.run_id = r.id)
    from agent_market.catalog_samples c
    join agent_market.agent_definitions d on d.id = c.definition_id
    join agent_market.agent_runs r on r.id = c.run_id
    where d.slug = ${lit(slug)} and d.status = 'active'
    order by string_to_array(d.version, '.')::int[] desc, c.created_at desc limit 1`);
  if (!row) return null;
  const [run, version, status, reason, credits, at, seconds, files] = row.split("|");
  const step = sql(`
    select coalesce(s.output ->> 'model', ''), coalesce(s.output -> 'narrative' ->> 'model', ''),
           coalesce(left(s.output ->> 'tried', 160), ''), s.status
    from agent_market.agent_run_steps s where s.run_id = ${lit(run)} and s.name = 'narrative'
    order by s.started_at desc limit 1`).split("|");
  return {
    run, version, status, reason, credits: Number(credits), at, seconds: Number(seconds) || null, files: Number(files),
    commentary: step.length >= 4 ? { step: step[3], model: step[0], written_by: step[1], tried: step[2] } : null,
  };
}
const said = (r) => (r
  ? `sample of ${r.version}: run ${r.run.slice(0, 8)} ${r.status}${r.reason ? ` (${r.reason})` : ""}, made ${r.at}, ${r.files} file(s), ${Math.round(r.credits)} credits — commentary ${
    r.commentary ? (r.commentary.written_by ? `written by ${r.commentary.written_by}` : `written by RULES (no model: ${r.commentary.tried || "nothing tried"})`) : "step not there"}`
  : "no sample");

async function remake(slug) {
  const before = sampleRecord(slug);
  log(`before — ${said(before)}`);
  const email = LIVE ? L.env("QA_EMAIL") : process.env.AM15_ADMIN_EMAIL;
  const password = LIVE ? L.env("QA_PASSWORD") : process.env.AM15_ADMIN_PASSWORD;
  if (!email || !password) throw new Error("no admin account for this target (QA_EMAIL / QA_PASSWORD on live; AM15_ADMIN_EMAIL / AM15_ADMIN_PASSWORD on the test stack)");
  const pw = require(path.join(L.ROOT, "apps", "web-chat", "node_modules", "playwright-core"));
  const browser = await pw.chromium.launch();
  let pressed = "";
  try {
    const page = await (await browser.newContext({ viewport: { width: 1440, height: 1100 }, ignoreHTTPSErrors: true })).newPage();
    await page.goto(`${ADMIN}/login`, { waitUntil: "load" });
    await page.waitForFunction(() => { const f = document.querySelector("form"); return !!f && Object.keys(f).some((k) => k.startsWith("__reactProps") || k.startsWith("__reactFiber")); }, null, { timeout: 90000 });
    await page.locator('input[autocomplete="username"]').fill(email);
    await page.locator('input[type="password"]').fill(password);
    const answered = page.waitForResponse((r) => r.url().includes("/auth/session/login"), { timeout: 60000 });
    await page.locator('button[type="submit"]').click();
    const status = (await answered).status();
    if (status !== 200) throw new Error(`the admin sign-in was refused (${status})`);
    await page.waitForURL((u) => !String(u).includes("/login"), { timeout: 60000 });
    await page.goto(`${ADMIN}/agent-market?section=catalog`, { waitUntil: "load" });
    const row = page.locator(`[data-testid="am-catalog-row"][data-slug="${slug}"]`);
    await row.waitFor({ timeout: 60000 });
    await page.waitForTimeout(1000);
    log(`the row before: ${(await row.innerText()).replace(/\s+/g, " ").slice(0, 200)}`);
    const sent = page.waitForResponse((r) => r.url().includes(`/catalog/${encodeURIComponent(slug)}/sample`) && r.request().method() === "POST", { timeout: 60000 });
    await row.getByTestId("am-remake-sample").click();
    const res = await sent;
    pressed = `${res.status()}`;
    await page.waitForTimeout(1500);
    log(`pressed "make the sample again": ${pressed} — the row says: ${(await row.innerText()).replace(/\s+/g, " ").slice(-160)}`);
    await row.screenshot({ path: path.join(OUT, `${slug}-pressed.png`) });
    if (res.status() !== 202) throw new Error(`the admin door answered ${res.status()}`);

    // the runner makes it; wait for a NEW run to end, then read the page again
    let after = null;
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 5000));
      after = sampleRecord(slug);
      if (after && (!before || after.run !== before.run) && ["succeeded", "failed", "cancelled", "awaiting_input"].includes(after.status)) break;
    }
    log(`after  — ${said(after)}`);
    await page.reload({ waitUntil: "load" });
    await row.waitFor({ timeout: 60000 });
    await page.waitForTimeout(1500);
    const rowAfter = (await row.innerText()).replace(/\s+/g, " ");
    log(`the row after: ${rowAfter.slice(0, 200)}`);
    await row.screenshot({ path: path.join(OUT, `${slug}-after.png`) });
    const ok = !!after && (!before || after.run !== before.run) && after.status === "succeeded" && after.files > 0 && !!after.commentary && !!after.commentary.written_by;
    fs.writeFileSync(path.join(OUT, `${slug}.json`), JSON.stringify({ at: new Date().toISOString(), target: L.TARGET, slug, pressed, before, after, row_after: rowAfter, ok }, null, 1));
    log(ok ? "OK: a new sample, finished, with files, its commentary written by a model" : "NOT OK: read the lines above");
    if (!ok) process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

(async () => {
  const [cmd, slug] = process.argv.slice(2);
  if (!slug || !["remake", "look"].includes(cmd)) { console.error("usage: sample.js remake|look <agent slug>"); process.exit(2); }
  if (cmd === "look") { log(said(sampleRecord(slug))); return; }
  await remake(slug);
})().catch((e) => { console.error(`sample: ${L.errText(e)}`); process.exit(1); });
