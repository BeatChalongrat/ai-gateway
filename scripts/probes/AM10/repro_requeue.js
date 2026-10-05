#!/usr/bin/env node
// AM10 — an answer given after delivery, watched on whatever version a stack runs.
//
// Before this card, answering a question that had not made the round wait (the report agent's
// "ปี พ.ศ./ค.ศ.") after its files were delivered put the round back in the queue beside its own
// re-render; the re-render was claimed first and refused a round that "has not finished". This
// probe does what a person does and writes down what happens — it changes nothing of the stack:
//
//   1. another copy of the report agent (the wizard's "ติดตั้งอีกชุด"; a person's own install is
//      never opened), tagged [AM10]
//   2. one round on the sample ledger with every seventh date typed with a ค.ศ. year
//      (repro_requeue_input.py; checked to raise that one question and nothing that waits)
//   3. the round delivers on its default; the card is still open and blocks nothing
//   4. the card is answered on screen ("ค.ศ.")
//   5. every second: the round and its jobs as the database has them (SELECT only), until they
//      settle; then the thread's failure cards and the round's files
//   6. the copy is removed and the round's evidence with it, both by id
//
// The result says what was seen: the round re-queued or not, the re-render failed or not, a red
// card on the thread or not. It is the same probe on the version before the card (re-queued, failed,
// red) and on this card's build (stays finished, re-render succeeds, nothing red).
//
//   OWNER_EMAIL=<account> SMOKE_TARGET=live node scripts/probes/AM10/repro_requeue.js
//   SMOKE_TARGET=test node scripts/probes/AM10/repro_requeue.js          (the test stack's owner)
"use strict";
const fs = require("node:fs");
const path = require("node:path");

process.env.AM6_TAG = process.env.AM6_TAG || "[AM10]";
const TARGET = process.env.SMOKE_TARGET || "test";
process.env.AM6_OUT = path.join(__dirname, "results", "repro", TARGET);
const W = require("../AM6/walkthrough.js");
const { L, state, save, log, api, WEB, waitFor, TAG } = W;

const SLUG = "sran.financial-report";
const CARD = "รายงานผลประกอบการ";
const CODE = "year_convention_mixed";
const SAMPLE = path.join(L.ROOT, "backend", "agent_market", "packages", "financial_report", "sample-data");
const FILES = [
  { name: "GL_2569-09_AM10-mixed-years.xlsx", src: path.join(__dirname, "results", "repro", "GL_2569-09_AM10-mixed-years.xlsx") },
  { name: "GL_2569-08.xlsx", src: path.join(SAMPLE, "GL_2569-08.xlsx") },
  { name: "งบประมาณ_2569.xlsx", src: path.join(SAMPLE, "งบประมาณ_2569.xlsx") },
];
const UUID = /^[0-9a-f-]{36}$/;
const label = (c) => { try { return L.dockerLabel(c, "org.opencontainers.image.revision"); } catch { return ""; } };

/** The round and the jobs hanging off it, as the database has them now (SELECT only). */
function rows(runId) {
  if (!UUID.test(runId)) throw new Error("not a run id");
  const out = L.csql(`select kind, status, coalesce(status_reason, ''), attempts, coalesce(params->>'why', ''), coalesce(params->>'formats', '')
    from agent_market.agent_runs where id = '${runId}' or parent_run_id = '${runId}' order by created_at`);
  return out.split(/\r?\n/).filter(Boolean).map((l) => {
    const [kind, status, reason, attempts, why, formats] = l.split("|");
    return { kind, status, reason, attempts: Number(attempts), why, formats };
  });
}
const said = (rs) => rs.map((r) => `${r.kind}${r.why ? `(${r.why})` : ""}=${r.status}${r.reason ? `/${r.reason}` : ""}#${r.attempts}`).join("  ");

(async () => {
  const x = await W.session("web", "requeue");
  const { page } = x;
  const result = { target: TARGET, at: new Date().toISOString(), build: { chat_edge: label(L.T.containers.chatEdge), runner: label(TARGET === "live" ? "sran-agent-runner" : "sran-test-agent-runner") } };
  let installId = null;
  let runId = null;
  try {
    await page.goto(`${WEB()}/chat`, { waitUntil: "load" });
    await page.locator('a[href="/chat/agents"]').first().waitFor({ state: "visible", timeout: 90000 });
    await L.wait(1200);
    const has = ((await api(page, "/installs")).body?.installs || []).some((i) => i.slug === SLUG);
    installId = has ? await W.installAnother(x, SLUG, CARD) : (await W.installFromMarket(x, CARD)).installId;
    state.installs = [...new Set([...(state.installs || []), installId])];
    save();
    await page.goto(`${WEB()}/chat/agents/i/${installId}`, { waitUntil: "load" });
    await page.waitForSelector('[data-testid="composer-root"]', { timeout: 60000 });
    await L.wait(1500);

    const r1 = await W.sendFiles(x, installId, FILES, `${TAG} รายงานผลประกอบการเดือนกันยายน 2569 (รอบทดสอบ ไม่ใช่งานจริง)`, 1);
    runId = r1.runId;
    state.runs = [...new Set([...(state.runs || []), runId])];
    save();
    const d1 = await W.finish(x, runId, 1);
    x.check(d1.run.status === "succeeded", "the round delivers without waiting for the question", `${d1.run.status} · cards answered on the way ${d1.asked.length}`);
    const detail = (await api(page, `/runs/${runId}`)).body;
    const card = (detail.clarifications || []).find((c) => (c.questions || []).some((q) => q.code === CODE));
    x.check(!!card && card.status === "open" && !card.blocking, "the year question is on a card that is open and blocks nothing",
      card ? `${card.status} blocking=${card.blocking}` : "no such card");
    if (!card || card.status !== "open" || card.blocking || d1.run.status !== "succeeded") throw new Error("the path was not reached: nothing to observe");

    const failures = () => page.locator('[data-testid="am-failed-round"]').count();
    const files = (d) => (d.artifacts || []).map((a) => `${a.format} v${a.version}`).sort().join(", ");
    result.before = { rows: said(rows(runId)), files: files(detail), failure_cards: await failures() };
    log(`before the answer: ${result.before.rows} · files ${result.before.files} · failure cards ${result.before.failure_cards}`);
    const cardEl = page.locator(`[data-card-id="${card.id}"]`).first();
    await cardEl.scrollIntoViewIfNeeded();
    await L.wait(600);
    await x.shot("02-the-open-card-after-delivery");

    // the person answers: ค.ศ. (not the default the round ran on)
    await x.click(cardEl.locator('[data-testid="question-option"]').filter({ hasText: "ค.ศ." }), "ค.ศ.");
    await x.click(cardEl.locator('[data-testid="question-submit"]'), "ส่งคำตอบ");
    const t0 = Date.now();
    const timeline = [];
    let last = "";
    let lastChange = t0;
    let requeued = false;
    while (Date.now() - t0 < 300000) {
      const rs = rows(runId);
      const now = said(rs);
      if (now !== last) {
        timeline.push({ s: Number(((Date.now() - t0) / 1000).toFixed(1)), rows: now });
        log(`+${timeline[timeline.length - 1].s}s ${now}`);
        last = now;
        lastChange = Date.now();
      }
      const round = rs.find((r) => r.kind === "run");
      if (round && round.status !== "succeeded") requeued = true;
      const settled = rs.every((r) => W.TERMINAL.includes(r.status));
      if (settled && Date.now() - lastChange > 25000 && Date.now() - t0 > 40000) break;
      await L.wait(1000);
    }
    await L.wait(2000);
    const after = (await api(page, `/runs/${runId}`)).body;
    const end = rows(runId);
    const renders = end.filter((r) => r.kind === "render");
    await page.locator('[data-testid="am-failed-round"]').last().scrollIntoViewIfNeeded().catch(() => {});
    await L.wait(500);
    await x.shot("03-after-the-answer");
    const words = await x.words(page.locator('[data-testid="am-failed-round"]').last());
    result.after = { rows: said(end), files: files(after), failure_cards: await failures(), failure_says: words.slice(0, 300) };
    result.timeline = timeline;
    result.seen = {
      round_went_back_into_the_queue: requeued,
      round_attempts: (end.find((r) => r.kind === "run") || {}).attempts,
      re_renders: renders.map((r) => `${r.status}${r.reason ? `/${r.reason}` : ""}`),
      a_re_render_failed: renders.some((r) => r.status === "failed"),
      red_card_on_the_thread: result.after.failure_cards > result.before.failure_cards,
    };
    result.verdict = result.seen.round_went_back_into_the_queue && result.seen.a_re_render_failed
      ? "THE BUG IS HERE: the round was re-queued and its re-render failed"
      : (!result.seen.round_went_back_into_the_queue && renders.length > 0 && renders.every((r) => r.status === "succeeded") && !result.seen.red_card_on_the_thread
        ? "NOT HERE: the round stayed finished and its re-render succeeded"
        : "UNCLEAR: read the timeline");
    log(`after: ${result.after.rows} · files ${result.after.files} · failure cards ${result.after.failure_cards}`);
    log(result.verdict);
  } catch (e) {
    result.error = String(e && e.message ? e.message : e).slice(0, 400);
    log(`stopped: ${result.error}`);
    process.exitCode = 1;
  } finally {
    // what this probe made, removed by id
    result.cleanup = {};
    try {
      if (runId) result.cleanup.evidence = (await require("../AM7/admin_door.js").removeEvidence(runId, `${TAG} รอบทดสอบของการทำซ้ำ ไม่ใช่งานจริง`)).status;
      if (installId) result.cleanup.install = (await api(page, `/installs/${installId}`, { method: "DELETE" })).status;
      state.installs = (state.installs || []).filter((i) => i !== installId);
      state.runs = (state.runs || []).filter((r) => r !== runId);
      save();
    } catch (e) {
      result.cleanup.error = String(e && e.message ? e.message : e).slice(0, 300);
    }
    log(`cleanup: evidence ${result.cleanup.evidence} · install ${result.cleanup.install}${result.cleanup.error ? ` · ${result.cleanup.error}` : ""}`);
    result.checks = x.checks;
    fs.writeFileSync(path.join(x.dir, "requeue.json"), JSON.stringify(result, null, 1));
    await x.s.close();
  }
})();
