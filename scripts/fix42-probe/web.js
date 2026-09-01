#!/usr/bin/env node
// FIX42 Part C — drive the REAL web page (web-chat :3002 → chat-edge :4000 →
// gateway → real provider) with a real Chromium, and keep the raw SSE frames
// the page itself saw.
//
// Why a script and not a hand: the Chrome extension bridge was not connected
// on this box, and a script leaves evidence a reader can re-run. The page is
// the production bundle inside the freshly built ai-gateway-web-chat image —
// nothing is mocked. Frames are captured by teeing every text/event-stream
// response body inside the page (addInitScript), so what lands in
// results/*.json is byte-for-byte what the page's parser consumed.
//
//   node scripts/fix42-probe/web.js c1|c2|c3 [--model <id>] [--headed]
//
// Auth: a dev portal JWT for the OWNER's user, set as the HttpOnly
// portal_access_token cookie — the same cookie the Portal login would set.
// Conversations are titled "FIX42 …" so they can be found and removed.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { execSync, execFileSync } = require("node:child_process");

// psql through docker WITHOUT a shell — Windows cmd mangles the quotes.
const psql = (container, user, db, sql) => execFileSync("docker", ["exec", container, "psql", "-U", user, "-d", db, "-tA", "-F", "|", "-c", sql], { encoding: "utf8" }).trim();

const REPO = path.resolve(__dirname, "..", "..");
const OUT = path.join(__dirname, "results");
const { chromium } = require(path.join(REPO, "apps", "web-chat", "node_modules", "playwright-core"));
const crypto = require("node:crypto");

const WEB = process.env.FIX42_WEB || "http://localhost:3002";
const API = process.env.FIX42_API || "http://localhost:4000/api/v1";
const USER = "56726cc2-47d2-4e81-95bd-4aba41d4d677"; // the owner's chat user
const TENANT = "169323ae-3aa2-4bc9-bb2a-e4467c716e51";
const EMAIL = "chalongrat.t@gbtech.co.th";

const argv = process.argv.slice(2);
const CASE = argv[0];
const arg = (k, d) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : d; };
const MODEL = arg("--model", null);
const HEADED = argv.includes("--headed");

// Same shape as scripts/fix33-probe/mint-jwt.js, plus token_version: the
// gateway's /portal/me rejects a token whose version is behind the row's
// (token_version_stale), and the owner's row is at 1, not 0.
function mint({ secret, sub, email, tenantId, tokenVersion, ttlSec = 3600 }) {
  const b64 = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const claims = { sub, email, type: "portal", token_use: "access", tenant_id: tenantId, tenant_role: "owner", token_version: tokenVersion, iat: now, exp: now + ttlSec, iss: "sran-portal" };
  const data = `${b64({ alg: "HS256", typ: "JWT" })}.${b64(claims)}`;
  return `${data}.${crypto.createHmac("sha256", secret).update(data).digest("base64url")}`;
}

function tokenVersion() {
  const v = psql("ai-gateway-postgres-1", "postgres", "ai_gateway", `select token_version from portal_users where id='${USER}'`);
  return Number(v || 0);
}

function secret() {
  const env = execSync("docker exec ai-gateway-chat-edge-api env", { encoding: "utf8" });
  const m = env.match(/^PORTAL_JWT_SECRET=(.+)$/m);
  if (!m) throw new Error("PORTAL_JWT_SECRET not found in the container env");
  return m[1].trim();
}

// Tee every SSE body the page reads into window.__sse — an array of
// {url, frames:[{t,event,data}]} in arrival order. Installed before any page
// script runs, so the app's own fetch goes through it.
function teeScript() {
  return String.raw`(() => {
  window.__sse = [];
  const orig = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const res = await orig(...args);
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("text/event-stream") || !res.body) return res;
    const url = typeof args[0] === "string" ? args[0] : args[0].url;
    const rec = { url, frames: [], t0: Date.now(), done: false };
    window.__sse.push(rec);
    const [a, b] = res.body.tee();
    (async () => {
      const r = b.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await r.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.search(/\r?\n\r?\n/)) >= 0) {
          const block = buf.slice(0, i);
          buf = buf.slice(i).replace(/^\r?\n\r?\n/, "");
          let ev = "message", data = "";
          for (const line of block.split(/\r?\n/)) {
            if (line.startsWith("event:")) ev = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          let parsed = data; try { parsed = JSON.parse(data); } catch (e) {}
          rec.frames.push({ t: Date.now() - rec.t0, event: ev, data: parsed });
        }
      }
      rec.done = true;
    })();
    return new Response(a, { status: res.status, statusText: res.statusText, headers: res.headers });
  };
})();`;
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function ctx(browser, token) {
  const c = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await c.addCookies([{ name: "portal_access_token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  await c.addInitScript(teeScript());
  return c;
}

async function apiFetch(token, p, init = {}) {
  const res = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(init.headers || {}) } });
  const text = await res.text();
  let body = text; try { body = JSON.parse(text); } catch (e) {}
  return { status: res.status, body };
}

async function newConversation(token, title, modelId) {
  const { status, body } = await apiFetch(token, "/conversations", { method: "POST", body: JSON.stringify({ title, ...(modelId ? { model_id: modelId } : {}) }) });
  if (status >= 300) throw new Error(`POST /conversations → ${status} ${JSON.stringify(body)}`);
  return body.id;
}

async function send(page, text) {
  const box = page.getByRole("textbox").first();
  await box.waitFor({ state: "visible", timeout: 30000 });
  await box.click();
  await box.fill(text);
  await page.getByRole("button", { name: /^(ส่ง|Send)$/ }).click();
}

// Snapshot of what the page shows: every bubble's role, text, status label.
async function bubbles(page) {
  return page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll(".message-enter")) {
      const role = el.classList.contains("justify-end") ? "user" : "assistant";
      const txt = el.innerText.replace(/\s+/g, " ").trim();
      const stopped = /หยุดแล้ว|\bstopped\b/.test(txt);
      const spinner = !!el.querySelector(".animate-spin, .animate-pulse, [class*='typing'], [class*='cursor-blink']");
      out.push({ role, stopped, spinner, len: txt.length, text: txt.slice(0, 600) });
    }
    return out;
  });
}

async function sse(page) { return page.evaluate(() => window.__sse); }

async function sendButtonLabel(page) {
  return page.evaluate(() => {
    const b = [...document.querySelectorAll("button[aria-label]")].find((x) => /^(ส่ง|Send|หยุด|Stop generation)$/.test(x.getAttribute("aria-label")));
    return b ? b.getAttribute("aria-label") : null;
  });
}

async function waitStreamingDone(page, timeoutMs = 180000) {
  const t0 = Date.now();
  for (;;) {
    const s = await sse(page);
    const last = s[s.length - 1];
    const label = await sendButtonLabel(page);
    if (last && last.done && !/หยุด|Stop/.test(label || "")) return true;
    if (Date.now() - t0 > timeoutMs) return false;
    await wait(300);
  }
}

function requestLogRows(n = 3) {
  // The gateway's per-request DLP record for the owner's tenant — the
  // authoritative "was this masked" answer. Raw columns are NOT selected.
  const sql = `select request_id, model, dlp_input_action, dlp_input_findings, left(input_masked, 400), created_at from request_logs where tenant_id::text like '169323ae%' order by created_at desc limit ${n}`;
  const out = psql("ai-gateway-postgres-1", "postgres", "ai_gateway", sql);
  return out.split("\n").filter(Boolean).map((l) => {
    const [request_id, model, dlp_input_action, dlp_input_findings, input_masked_head, created_at] = l.split("|");
    return { request_id, model, dlp_input_action, dlp_input_findings, input_masked_head, created_at };
  });
}

function save(name, obj) {
  fs.mkdirSync(OUT, { recursive: true });
  const p = path.join(OUT, name);
  fs.writeFileSync(p, JSON.stringify(obj, null, 2));
  console.log("→", path.relative(REPO, p));
}

const seqOf = (f) => (f.data && typeof f.data === "object" ? f.data.seq : undefined);
const orderOf = (r) => r.frames.map((f) => `${seqOf(f) ?? "?"}:${f.event}`);
const summarize = (recs) => recs.map((r) => ({ url: r.url, n: r.frames.length, done: r.done, events: [...new Set(r.frames.map((f) => f.event))], last3: r.frames.slice(-3) }));

async function main() {
  const token = mint({ secret: secret(), sub: USER, email: EMAIL, tenantId: TENANT, tokenVersion: tokenVersion(), ttlSec: 3600 });
  const browser = await chromium.launch({ headless: !HEADED });
  try {
    if (CASE === "c1") await c1(browser, token);
    else if (CASE === "c3") await c3(browser, token);
    else if (CASE === "c2") await c2(browser, token);
    else throw new Error("case must be c1|c2|c3");
  } finally {
    await browser.close();
  }
}

// ── C1 — FIX40 on the rebuilt stack, through the page ────────────────────
async function c1(browser, token) {
  const cases = [
    { key: "glass", text: "ลูกค้าสั่งแก้วน้ำ 12 ใบ แตก 3 ใบ ระหว่างจัดส่ง สรุปให้หน่อยว่าสั่งกี่ใบและแตกกี่ใบ", expect: "no masking at all" },
    { key: "khun-kaew", text: "คุณแก้วโทรมาบอกว่าแก้วน้ำที่สั่งไปแตกสองใบ", expect: "<<PERSON_A>> for the name only; แก้วน้ำ survives" },
  ];
  const results = [];
  for (const c of cases) {
    const convId = await newConversation(token, `FIX42 C1 ${c.key}`, MODEL);
    const context = await ctx(browser, token);
    const page = await context.newPage();
    await page.goto(`${WEB}/chat/${convId}`, { waitUntil: "load" });
    await send(page, c.text);
    const finished = await waitStreamingDone(page);
    await wait(1500);
    const shown = await bubbles(page);
    const frames = await sse(page);
    await page.screenshot({ path: path.join(OUT, `C1-${c.key}.png`), fullPage: true });
    await context.close();
    await wait(3000); // let the gateway's request log land
    const logs = requestLogRows(2);
    const answer = shown.filter((b) => b.role === "assistant").map((b) => b.text).join("\n");
    results.push({ ...c, conversation_id: convId, finished, answer, shown, streams: summarize(frames), request_logs: logs });
    console.log(`C1 ${c.key}: finished=${finished} answer="${answer.slice(0, 200)}"`);
    console.log(`   dlp: ${JSON.stringify(logs[0])}`);
  }
  save("C1-fix40-on-rebuilt-stack.json", { captured_at: new Date().toISOString(), model: MODEL, results });
}

// ── C3 — stop mid-turn on the page; a second tab watching the same turn ──
async function c3(browser, token) {
  const convId = await newConversation(token, "FIX42 C3 cancel", MODEL);
  const context = await ctx(browser, token);
  const A = await context.newPage();
  await A.goto(`${WEB}/chat/${convId}`, { waitUntil: "load" });
  await send(A, "เล่าประวัติศาสตร์ของกาแฟอย่างละเอียดตั้งแต่เอธิโอเปียจนถึงร้านกาแฟสมัยใหม่ แบ่งเป็น 12 ยุค แต่ละยุคมีหัวข้อและอย่างน้อย 3 ย่อหน้า ไม่ต้องถามกลับ เริ่มเขียนได้เลย");
  const t0 = Date.now();
  for (;;) {
    const a = await bubbles(A);
    const asst = a.find((b) => b.role === "assistant");
    if (asst && asst.len > 80) break;
    if (Date.now() - t0 > 60000) throw new Error("tab A: no answer text after 60 s");
    await wait(250);
  }
  // second tab, same conversation — must attach to the live turn (resume)
  const B = await context.newPage();
  await B.goto(`${WEB}/chat/${convId}`, { waitUntil: "load" });
  const t1 = Date.now();
  for (;;) {
    const s = await sse(B);
    if (s.length && s[0].frames.length > 3) break;
    if (Date.now() - t1 > 30000) {
      const dbg = { A_label: await sendButtonLabel(A), A_streams: summarize(await sse(A)), B_streams: summarize(s), B_bubbles: await bubbles(B) };
      save("C3-debug-B-never-attached.json", dbg);
      throw new Error("tab B: never attached to the live stream");
    }
    await wait(250);
  }
  const bBefore = await bubbles(B);
  const aBefore = await bubbles(A);
  const labelAtStop = await sendButtonLabel(A);
  console.log(`tab A send-button label at stop time: ${labelAtStop}; A frames so far: ${(await sse(A))[0]?.frames.length}; B frames: ${(await sse(B))[0]?.frames.length}`);
  if (!/หยุด|Stop/.test(labelAtStop || "")) throw new Error("turn already finished before stop could be pressed — use a longer prompt");
  await A.getByRole("button", { name: /^(หยุด|Stop generation)$/ }).click({ timeout: 5000 });
  await wait(4000);
  const aAfter = await bubbles(A);
  const bAfter = await bubbles(B);
  const aLabel = await sendButtonLabel(A);
  const bLabel = await sendButtonLabel(B);
  const aFrames = await sse(A);
  const bFrames = await sse(B);
  await A.screenshot({ path: path.join(OUT, "C3-tabA-after-stop.png"), fullPage: true });
  await B.screenshot({ path: path.join(OUT, "C3-tabB-after-stop.png"), fullPage: true });
  // /cancel again, twice — from the page's own origin, with the page's cookie
  const again = async () => A.evaluate(async (api) => {
    const r = await fetch(`${api}/conversations/${location.pathname.split("/").pop()}/cancel`, { method: "POST", credentials: "include" });
    return { status: r.status, body: await r.text() };
  }, API);
  const second = await again();
  const third = await again();
  await context.close();
  const out = {
    captured_at: new Date().toISOString(), model: MODEL, conversation_id: convId,
    tabA: { before: aBefore, after: aAfter, send_button_after: aLabel, streams: summarize(aFrames) },
    tabB: { before: bBefore, after: bAfter, send_button_after: bLabel, streams: summarize(bFrames) },
    cancel_again: { second, third },
    verdict: {
      A_shows_stopped: aAfter.some((b) => b.role === "assistant" && b.stopped),
      A_spinner_left: aAfter.some((b) => b.role === "assistant" && b.spinner),
      A_send_button_back: /ส่ง|Send/.test(aLabel || ""),
      B_shows_stopped: bAfter.some((b) => b.role === "assistant" && b.stopped),
      B_spinner_left: bAfter.some((b) => b.role === "assistant" && b.spinner),
      B_send_button_back: /ส่ง|Send/.test(bLabel || ""),
      B_got_cancelled: bFrames.some((r) => r.frames.some((f) => f.event === "cancelled")),
      B_got_message_complete: bFrames.some((r) => r.frames.some((f) => f.event === "message_complete")),
      A_got_cancelled: aFrames.some((r) => r.frames.some((f) => f.event === "cancelled")),
      cancel_again_not_500: second.status !== 500 && third.status !== 500,
    },
  };
  console.log(JSON.stringify(out.verdict, null, 2));
  console.log("cancel again:", second.status, third.status);
  save("C3-web-cancel-two-tabs.json", out);
}

// ── C2 — a real tool turn through a real provider, then reattach mid-tool ─
async function c2(browser, token) {
  const prompt = arg("--prompt", "ช่วยใช้เครื่องมือรันโค้ด Python คำนวณผลรวมของจำนวนเฉพาะที่น้อยกว่า 10000 แล้วบอกผลลัพธ์ พร้อมอธิบายสั้น ๆ ว่าคำนวณอย่างไร");
  const convId = await newConversation(token, "FIX42 C2 tool turn", MODEL);
  const context = await ctx(browser, token);
  const A = await context.newPage();
  await A.goto(`${WEB}/chat/${convId}`, { waitUntil: "load" });
  await send(A, prompt);
  const isTool = (e) => e === "tool_activity" || e === "code_execution";
  const t0 = Date.now();
  let sawTool = false;
  for (;;) {
    const s = await sse(A);
    if (s.some((r) => r.frames.some((f) => isTool(f.event)))) { sawTool = true; break; }
    if (s.some((r) => r.done)) break;
    if (Date.now() - t0 > 120000) break;
    await wait(150);
  }
  let B = null, bFrames = [], bShown = [], cursorResume = null;
  if (sawTool) {
    // (a) a fresh tab — cursor 0, full replay then live tail (what a user
    //     opening the conversation in a second tab gets);
    B = await context.newPage();
    await B.goto(`${WEB}/chat/${convId}`, { waitUntil: "load" });
    // (b) a client that ALREADY rendered up to the frame before the first
    //     tool frame and reattaches with that cursor — starting_after > 0,
    //     mid-turn, exactly the FIX41 §4 case. Fired from Node with the
    //     same bearer, read to the end, frames kept verbatim.
    const live0 = (await sse(A))[0];
    const firstTool = live0.frames.find((f) => isTool(f.event));
    const msgId = live0.frames[0].data.message_id;
    const after = seqOf(firstTool) - 1;
    cursorResume = { starting_after: after, message_id: msgId, frames: [] };
    cursorResume.promise = (async () => {
      const r = await fetch(`${API}/conversations/${convId}/messages/${msgId}/stream?starting_after=${after}`, { headers: { Authorization: `Bearer ${token}` } });
      cursorResume.status = r.status;
      const text = await r.text();
      for (const block of text.split(/\r?\n\r?\n/)) {
        let ev = null, data = "";
        for (const line of block.split(/\r?\n/)) {
          if (line.startsWith("event:")) ev = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!ev) continue;
        let parsed = data; try { parsed = JSON.parse(data); } catch (e) {}
        cursorResume.frames.push({ event: ev, data: parsed });
      }
    })();
  }
  await waitStreamingDone(A, 240000);
  if (B) {
    await waitStreamingDone(B, 240000);
    await wait(1000);
    bFrames = await sse(B);
    bShown = await bubbles(B);
    await B.screenshot({ path: path.join(OUT, "C2-tabB-reattached.png"), fullPage: true });
  }
  await wait(1000);
  if (cursorResume) { await cursorResume.promise; delete cursorResume.promise; }
  const aFrames = await sse(A);
  const aShown = await bubbles(A);
  await A.screenshot({ path: path.join(OUT, "C2-tabA.png"), fullPage: true });
  await context.close();
  const contiguous = (arr) => arr.every((s, i) => i === 0 || s === arr[i - 1] + 1);
  const live = aFrames[0];
  const resumeRec = bFrames.find((r) => /\/stream/.test(r.url));
  const liveTerminal = live ? live.frames[live.frames.length - 1] : null;
  const toolList = (r) => r.frames.filter((f) => isTool(f.event)).map((f) => ({ seq: seqOf(f), event: f.event, data: f.data }));
  const out = {
    captured_at: new Date().toISOString(), model: MODEL, prompt, conversation_id: convId,
    saw_tool_frames: sawTool,
    live: live ? { url: live.url, n: live.frames.length, order: orderOf(live), seq_contiguous: contiguous(live.frames.map(seqOf).filter((x) => x != null)), terminal: liveTerminal, tool_events: toolList(live) } : null,
    reattach: resumeRec ? { url: resumeRec.url, n: resumeRec.frames.length, order: orderOf(resumeRec), terminal: resumeRec.frames[resumeRec.frames.length - 1], tool_events: toolList(resumeRec) } : null,
    cursor_resume: cursorResume && { starting_after: cursorResume.starting_after, status: cursorResume.status, order: orderOf(cursorResume), terminal: cursorResume.frames[cursorResume.frames.length - 1], tool_events: toolList(cursorResume) },
    tabA_shown: aShown, tabB_shown: bShown,
    streams_A: summarize(aFrames), streams_B: summarize(bFrames),
  };
  if (out.live && out.cursor_resume) {
    const liveTail = out.live.order.filter((x) => Number(x.split(":")[0]) > out.cursor_resume.starting_after);
    out.cursor_resume.live_tail = liveTail;
    out.cursor_resume.same_order_as_live = JSON.stringify(liveTail) === JSON.stringify(out.cursor_resume.order);
  }
  if (out.live && out.reattach && out.reattach.order.length) {
    // The replayed frames must be the live tail in the same order, under the
    // same numbers — the P1b property, now against a real tool turn.
    const firstReplaySeq = Number(out.reattach.order[0].split(":")[0]);
    const liveTail = out.live.order.filter((x) => Number(x.split(":")[0]) >= firstReplaySeq);
    out.reattach.live_tail = liveTail;
    out.reattach.same_order_as_live = JSON.stringify(liveTail) === JSON.stringify(out.reattach.order);
  }
  console.log(JSON.stringify({
    saw_tool_frames: sawTool,
    n_live: live && live.frames.length,
    live_seq_contiguous: out.live && out.live.seq_contiguous,
    live_terminal: liveTerminal && { event: liveTerminal.event, seq: seqOf(liveTerminal), final_seq: liveTerminal.data && liveTerminal.data.final_seq },
    tool_events_live: out.live && out.live.tool_events.length,
    reattach: out.reattach && { url: out.reattach.url, n: out.reattach.n, same_order_as_live: out.reattach.same_order_as_live, tool_events: out.reattach.tool_events.length },
    cursor_resume: out.cursor_resume && { starting_after: out.cursor_resume.starting_after, status: out.cursor_resume.status, order: out.cursor_resume.order, same_order_as_live: out.cursor_resume.same_order_as_live },
  }, null, 2));
  save("C2-real-tool-turn.json", out);
}

main().catch((e) => { console.error(e); process.exit(1); });
