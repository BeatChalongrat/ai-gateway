#!/usr/bin/env node
// Stream-scroll geometry, measured from OUTSIDE the app on a running
// bundle (no code change needed): every animation frame during a turn,
// sample the scroller (scrollTop / clientHeight / scrollHeight), the
// turn spacer, the derived contentH and the shouldResumeFollow verdict
// (contentH >= scrollTop + clientHeight - 0.5), plus whether the user's
// bubble element was ever replaced (a DOM mark that survives only if the
// node does). A CDP screencast records the send moment frame by frame.
//
//   node scripts/probes/stream-scroll/probe.js <tag> [baseUrl|cdp:<port>]
//     tag      before | after — prefix of the output files
//     baseUrl  default http://localhost:3002; cdp:9333 attaches to the
//              desktop app launched with --remote-debugging-port=9333
//   MODEL=<id> picks the conversation model (default gpt-5-mini)
//   VIEW=WxH   browser viewport (default 1280x800; web only)
//   PROMPT=... the long question (default asks for ~1200 words)
//
// Output: docs/<SHOTS_DIR|shots>/<tag>-stream-scroll-<web|desktop>.json
//         (samples + summary) and <tag>-send-frames-<web|desktop>/NNN.jpg
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..", "..");
const OUT = path.join(REPO, "docs", process.env.SHOTS_DIR || "shots");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const API = "http://localhost:4000/api/v1";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677";
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const MODEL = process.env.MODEL || "gpt-5-mini";
const PROMPT = process.env.PROMPT ||
  "เขียนบทความภาษาไทยยาวประมาณ 1200 คำ เรื่องประวัติของรถไฟในประเทศไทย แบ่งเป็นหัวข้อย่อย 8 หัวข้อ แต่ละหัวข้อมีย่อหน้าละ 5-6 ประโยค ไม่ต้องมีตาราง";
const STREAMING_SEL = '[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="หยุด"]';
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function token() {
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c", `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "owner@local", type: "portal", token_use: "access", tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 3600, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}

async function api(tok, p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", ...(init.headers || {}) } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t }; }
}

// Installed in the page before the send: a rAF sampler + a DOM mark on
// every message row so a replaced row is detectable afterwards.
function sampler() {
  const S = (window.__ss = { samples: [], removed: [], marks: {}, t0: performance.now() });
  const scroller = () => document.querySelector('[data-testid="turn-spacer"]')?.parentElement;
  const mark = () => {
    for (const row of document.querySelectorAll("[data-msg-index]")) {
      if (!row.dataset.probeMark) row.dataset.probeMark = "m" + Math.random().toString(36).slice(2, 8);
      S.marks[row.dataset.msgIndex] = row.dataset.probeMark;
    }
  };
  mark();
  const el = scroller();
  if (el) {
    new MutationObserver((muts) => {
      for (const m of muts) {
        for (const n of m.removedNodes) {
          if (n.nodeType !== 1) continue;
          const row = n.matches("[data-msg-index]") ? n : n.querySelector("[data-msg-index]");
          if (!row) continue;
          S.removed.push({ t: Math.round(performance.now() - S.t0), idx: row.dataset.msgIndex, mark: row.dataset.probeMark, text: (row.textContent || "").slice(0, 40) });
        }
      }
      mark();
    }).observe(el, { childList: true, subtree: true });
  }
  let last = "";
  const tick = () => {
    const el = scroller();
    if (el) {
      const sp = document.querySelector('[data-testid="turn-spacer"]');
      const spacer = sp ? parseFloat(sp.style.height) || 0 : 0;
      const rows = document.querySelectorAll("[data-msg-index]");
      const lastRow = rows[rows.length - 1];
      const userRow = rows.length >= 2 ? rows[rows.length - 2] : null;
      const contentH = el.scrollHeight - spacer;
      const s = {
        t: Math.round(performance.now() - S.t0),
        scrollTop: Math.round(el.scrollTop * 10) / 10,
        clientHeight: el.clientHeight,
        scrollHeight: el.scrollHeight,
        spacer,
        contentH,
        resume: contentH >= el.scrollTop + el.clientHeight - 0.5,
        distBottom: Math.round(el.scrollHeight - el.scrollTop - el.clientHeight),
        rows: rows.length,
        lastLen: lastRow ? (lastRow.textContent || "").length : 0,
        userMark: userRow ? userRow.dataset.probeMark : null,
        marks: Array.from(rows, (r) => r.dataset.msgIndex + ":" + r.dataset.probeMark).join(","),
        // content-offset top / height of every row (rows above the sent one must not move)
        rowGeom: Array.from(rows, (r) => { const b = r.getBoundingClientRect(); return Math.round(b.top - el.getBoundingClientRect().top + el.scrollTop) + "/" + Math.round(b.height); }).join(" "),
        userTop: userRow ? Math.round(userRow.getBoundingClientRect().top - el.getBoundingClientRect().top) : null,
      };
      const key = [s.scrollTop, s.scrollHeight, s.spacer, s.rows, s.lastLen, s.userMark].join("|");
      if (key !== last) { S.samples.push(s); last = key; }
    }
    if (!S.stop) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

(async () => {
  const [tag = "before", target = "http://localhost:3002"] = process.argv.slice(2);
  const tok = token();
  const isDesktop = target.startsWith("cdp:");
  const kind = isDesktop ? "desktop" : "web";
  let browser, page;
  if (isDesktop) {
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${target.slice(4)}`);
    const ctx = browser.contexts()[0];
    page = ctx.pages().find((p) => /\/chat/.test(p.url())) || ctx.pages()[0];
  } else {
    browser = await chromium.launch({ headless: true });
    const [vw, vh] = (process.env.VIEW || "1280x800").split("x").map(Number);
    const ctx = await browser.newContext({ viewport: { width: vw, height: vh } });
    await ctx.addCookies([{ name: "portal_access_token", value: tok, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
    page = await ctx.newPage();
  }
  // console lines from in-app instrumentation (any "[A1]"-prefixed log)
  const consoleLines = [];
  page.on("console", (m) => { const t = m.text(); if (t.startsWith("[A1]")) consoleLines.push(t); });
  const conv = (await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ title: `stream-scroll ${tag}`, model_id: MODEL }) })).body;
  if (!conv?.id) throw new Error("conversation not created: " + JSON.stringify(conv));
  console.log("conversation", conv.id, "model", MODEL, "target", kind);
  const base = isDesktop ? "http://localhost:3002" : target;
  await page.goto(`${base}/chat/${conv.id}`, { waitUntil: "load" });
  await page.waitForSelector("textarea", { timeout: 60000 });
  await wait(1500);

  // turn 1 — a short exchange so turn 2 has a previous message to peek at
  await page.locator("textarea").first().fill("ตอบสั้น ๆ คำเดียว: เมืองหลวงของไทยคือ?");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelectorAll("[data-msg-index]").length >= 2, null, { timeout: 60000 });
  await wait(1000);
  await page.waitForFunction((s) => !document.querySelector(s), STREAMING_SEL, { timeout: 90000 }).catch(() => {});
  await wait(2000);

  // screencast of the send moment
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  cdp.on("Page.screencastFrame", async (f) => {
    frames.push({ t: Date.now(), data: f.data });
    await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 60, maxWidth: 1280, maxHeight: 800, everyNthFrame: 1 });

  await page.evaluate(sampler);
  await page.locator("textarea").first().fill(PROMPT);
  const sendAt = Date.now();
  await page.keyboard.press("Enter");
  await wait(5000);
  await cdp.send("Page.stopScreencast");
  await page.waitForFunction(() => document.querySelectorAll("[data-msg-index]").length >= 4, null, { timeout: 60000 });
  // SCROLL_UP=1: once the follow is on, one wheel notch up must park the
  // view and raise the "new messages" pill (criterion E2)
  let scrollUp = null;
  if (process.env.SCROLL_UP) {
    await page.waitForFunction(() => { const S = window.__ss; const last = S.samples[S.samples.length - 1]; return last && last.spacer === 0 && last.distBottom <= 2 && last.lastLen > 400; }, null, { timeout: 120000 }).catch(() => {});
    const box = await page.evaluate(() => { const b = document.querySelector('[data-testid="turn-spacer"]').parentElement.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; });
    await page.mouse.move(box.x, box.y);
    await page.mouse.wheel(0, -120);
    await wait(150);
    const parkedAt = await page.evaluate(() => document.querySelector('[data-testid="turn-spacer"]').parentElement.scrollTop);
    await wait(2500);
    scrollUp = await page.evaluate((parkedAt) => {
      const el = document.querySelector('[data-testid="turn-spacer"]').parentElement;
      const pill = Array.from(document.querySelectorAll("button")).find((b) => /new messages|ข้อความใหม่/i.test(b.textContent || ""));
      return { parkedAt, scrollTopAfter2s: el.scrollTop, stillStreaming: Boolean(document.querySelector('[aria-label*="Stop"], [aria-label*="หยุด"]')), pill: Boolean(pill), contentGrew: el.scrollHeight };
    }, parkedAt);
    console.log("scroll-up check", JSON.stringify(scrollUp));
  }
  await page.waitForFunction((s) => !document.querySelector(s), STREAMING_SEL, { timeout: 180000 }).catch(() => console.log("stop button still present after 180 s"));
  await wait(1500);
  const data = await page.evaluate(() => { window.__ss.stop = true; return window.__ss; });

  fs.mkdirSync(OUT, { recursive: true });
  const fdir = path.join(OUT, `${tag}-send-frames-${kind}`);
  fs.rmSync(fdir, { recursive: true, force: true });
  fs.mkdirSync(fdir, { recursive: true });
  frames.forEach((f, i) => fs.writeFileSync(path.join(fdir, `${String(i).padStart(3, "0")}_${f.t - sendAt}ms.jpg`), Buffer.from(f.data, "base64")));

  const s = data.samples;
  const first = s[0], last = s[s.length - 1];
  const everResume = s.findIndex((x) => x.resume);
  const maxSpacer = Math.max(...s.map((x) => x.spacer));
  const minSpacerAfterMax = Math.min(...s.slice(s.findIndex((x) => x.spacer === maxSpacer)).map((x) => x.spacer));
  const scrollTops = [...new Set(s.map((x) => x.scrollTop))];
  // the sent user row = index (rows at the end - 2); did its mark ever change?
  const sentIdx = last ? last.rows - 2 : -1;
  const userMarks = [...new Set(s.map((x) => (x.marks || "").split(",").find((p) => p.startsWith(sentIdx + ":"))).filter(Boolean))];
  const summary = {
    tag, kind, model: MODEL, samples: s.length, frames: frames.length,
    pill: await page.evaluate(() => Boolean(document.querySelector('button.bottom-6'))),
    viewport: { clientHeight: first?.clientHeight },
    scrollTop: { first: first?.scrollTop, last: last?.scrollTop, distinct: scrollTops.length, values: scrollTops.slice(0, 12) },
    scrollHeight: { first: first?.scrollHeight, last: last?.scrollHeight },
    spacer: { max: maxSpacer, minAfterMax: minSpacerAfterMax, last: last?.spacer },
    contentH: { last: last?.contentH },
    resume: { everTrue: everResume >= 0, firstTrueAt: everResume >= 0 ? s[everResume] : null },
    followedToBottom: last ? last.distBottom <= 100 : null,
    userRowMarks: userMarks, userRowReplaced: userMarks.length > 1,
    removedRows: data.removed,
    lastLenGrowthSamples: s.filter((x, i) => i > 0 && x.lastLen !== s[i - 1].lastLen).length,
    scrollUp,
  };
  if (consoleLines.length) {
    const lp = path.join(OUT, `${tag}-a1-effect-log-${kind}.txt`);
    fs.writeFileSync(lp, consoleLines.join("\n") + "\n");
    console.log("→", path.relative(REPO, lp), consoleLines.length, "lines");
  }
  const out = path.join(OUT, `${tag}-stream-scroll-${kind}.json`);
  fs.writeFileSync(out, JSON.stringify({ summary, samples: s }, null, 1));
  console.log(JSON.stringify(summary, null, 2));
  console.log("→", path.relative(REPO, out), "+", frames.length, "frames in", path.relative(REPO, fdir));
  if (!isDesktop) await browser.close();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
