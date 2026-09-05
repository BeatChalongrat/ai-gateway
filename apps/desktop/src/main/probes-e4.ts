// E4 probes (dev only, `--probe-e4 <name>`). They drive web-chat through its
// own composer (DOM automation in the page, never OS input), answer consent
// prompts by clicking the page's ConsentCard buttons, and read the host's
// own log and tool-call ledger for evidence. Screenshots go to the evidence
// folder as PNGs.
//
//   refusal   a sidecar call without _meta is refused (E4 §5.2)
//   chain     tool-chain history: banana-47-tiger in a raw result only (§3.1)
//   consent   deny → allow-once (prompts again) → allow-session (no re-prompt) (§3.2)
//   audit     a turn with a local tool + DLP-detectable content, for the log comparison (§3.3)
//   s1..s6    the six Phase 6a scenarios, each a conversation (§4)
//   cancel    a running run_command is really stopped (§5.7)
//   rss       total app RSS + sidecar RSS on demand (§5.8)

import { app, type BrowserWindow } from "electron";
import fs from "node:fs";
import path from "node:path";
import type { BridgeChannel } from "./bridge";
import type { Logger } from "./logger";
import type { SidecarClient } from "./sidecar-client";
import type { TurnCoordinator } from "./turn-coordinator";
import type { GrantStore } from "./grants";

export interface ProbeContext {
  win: BrowserWindow;
  bridge: BridgeChannel;
  coordinator: TurnCoordinator;
  sidecar: SidecarClient;
  grants: GrantStore;
  log: Logger;
  evidenceDir: string;
  homeDir: string;
  quit: (code: number) => void;
}

type Check = { name: string; pass: boolean; evidence: string };
type ConsentPolicy = "allow-once" | "allow-session" | "deny" | ((n: number) => "allow-once" | "allow-session" | "deny");

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const TYPE_AND_SEND = (text: string) => `(() => {
  const ta = document.querySelector('textarea');
  if (!ta) return 'no-textarea';
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, ${JSON.stringify(text)});
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  ta.focus();
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
  return 'sent';
})()`;
const CLICK_CONSENT = (testid: string) => `(() => { const b = document.querySelector('[data-testid="${testid}"]'); if (!b) return false; b.click(); return true; })()`;
const NEW_CHAT = `(() => { const b = [...document.querySelectorAll('button, a')].find(x => /new chat|แชทใหม่/i.test((x.textContent || '') + ' ' + (x.getAttribute('aria-label') || ''))); if (b) { b.click(); return 'clicked'; } return 'no-new-chat'; })()`;
// The sidebar's temporary-chat launcher. Clicking the real control is the
// point: it exercises the path a user takes, including the store flag that
// the lazy create consumes.
const NEW_TEMP_CHAT = `(() => { const b = [...document.querySelectorAll('button, a')].find(x => /temporary chat|แชทชั่วคราว/i.test(x.textContent || '')); if (b) { b.click(); return 'clicked'; } return 'no-temp-chat'; })()`;
// Reads the always-visible strip, so the probe can prove the user was TOLD.
const READ_TEMP_BANNER = `(() => { const el = [...document.querySelectorAll('[role="status"]')].find(x => /temporary chat|แชทชั่วคราว/i.test(x.textContent || '')); return el ? (el.textContent || '').trim() : ''; })()`;
// The narrow-window reading, as one snippet so the width and the two
// signals come from the SAME layout pass.
//
// "Is the element in the DOM" is the wrong question and the first version
// of this asked it: `hidden md:flex` is a CSS class, so the header chip is
// in the document at every width and closest() found it happily — the
// check reported the chip "present" at 640px, where it is invisible. What
// matters is whether the user can SEE it, so both signals are measured by
// their layout box. innerWidth is reported too, because BrowserWindow has
// minWidth 640: asking for 420 gets 640, and a claim about "420px" would
// otherwise be a claim about a width the window never had.
const READ_NARROW_STATE = `(() => {
  const shown = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const banner = [...document.querySelectorAll('[role="status"]')].find(x => /temporary chat|แชทชั่วคราว/i.test(x.textContent || ''));
  const chipRow = document.querySelector('.md\\\\:flex');
  return JSON.stringify({
    innerWidth: window.innerWidth,
    bannerVisible: shown(banner),
    bannerText: banner ? (banner.textContent || '').trim() : '',
    chipRowVisible: shown(chipRow),
  });
})()`;

export async function runE4Probe(ctx: ProbeContext, name: string): Promise<void> {
  const { win, bridge, coordinator: co, sidecar, log } = ctx;
  const wc = win.webContents;
  const js = (code: string) => wc.executeJavaScript(code) as Promise<unknown>;
  const checks: Check[] = [];
  const check = (n: string, pass: boolean, evidence: string) => {
    checks.push({ name: n, pass, evidence });
    log.info(`[${pass ? "PASS" : "FAIL"}] ${n} — ${evidence}`);
  };
  const lastLine = (re: RegExp) => [...log.lines].reverse().find((l) => re.test(l)) ?? "(no log line)";
  const since = (from: number, re: RegExp) => log.lines.slice(from).filter((l) => re.test(l));
  fs.mkdirSync(ctx.evidenceDir, { recursive: true });
  let shots = 0;
  const shot = async (label: string) => {
    try {
      const img = await wc.capturePage();
      const p = path.join(ctx.evidenceDir, `${name}-${String(++shots).padStart(2, "0")}-${label}.png`);
      fs.writeFileSync(p, img.toPNG());
      log.info(`SCREENSHOT ${p}`);
    } catch (err) {
      log.warn(`screenshot failed: ${(err as Error).message}`);
    }
  };

  /** Send a turn, answer consent prompts per policy, wait for the host to finish. */
  let consentCounter = 0;
  const turn = async (text: string, policy: ConsentPolicy = "allow-once", timeoutMs = 300_000): Promise<{ convId: string; reply: string; dom: string; consents: number }> => {
    const accepted = bridge.accepted.length;
    const sent = await js(TYPE_AND_SEND(text));
    if (sent !== "sent") throw new Error(`composer not found (${String(sent)})`);
    const t0 = Date.now();
    while (!bridge.accepted.slice(accepted).some((a) => a.type === "turn.submit")) {
      if (Date.now() - t0 > 20_000) throw new Error("turn.submit never arrived at the host");
      await sleep(100);
    }
    const convId = co.lastConversationId!;
    let consents = 0;
    const deadline = Date.now() + timeoutMs;
    while (co.isRunning(convId) && Date.now() < deadline) {
      if (co.pendingConsentCount > 0) {
        await sleep(600); // let the card render
        const decision = typeof policy === "function" ? policy(++consentCounter) : policy;
        const testid = decision === "deny" ? "consent-deny" : decision === "allow-once" ? "consent-allow-once" : "consent-allow-session";
        await shot(`consent-${decision}`);
        const clicked = await js(CLICK_CONSENT(testid));
        log.info(`PROBE consent #${consentCounter}: clicked ${testid} → ${String(clicked)}`);
        if (clicked) consents++;
        await sleep(400);
        continue;
      }
      await sleep(250);
    }
    await sleep(1200);
    const dom = (await js(`document.body.innerText.replace(/\\s+/g, ' ')`)) as string;
    return { convId, reply: co.lastReply.get(convId) ?? "", dom, consents };
  };
  const newChat = async () => {
    await js(NEW_CHAT);
    await sleep(1200);
  };

  const finish = () => {
    const failed = checks.filter((c) => !c.pass).length;
    log.info(`=== E4 PROBE ${name}: ${checks.length - failed}/${checks.length} passed ===`);
    const ledger = co.toolCallsSeen.map((t) => ({ tool: t.tool, where: t.where, decision: t.decision, ok: t.ok, args: JSON.stringify(t.args).slice(0, 160), rpcError: t.rpcError }));
    process.stdout.write(`E4_PROBE ${JSON.stringify({ name, checks, toolCalls: ledger, logFile: log.filePath })}\n`);
    ctx.quit(failed === 0 ? 0 : 1);
  };

  try {
    const t0 = Date.now();
    while (!log.lines.some((l) => /BRIDGE HANDSHAKE/.test(l)) && Date.now() - t0 < 20_000) await sleep(200);
    await sidecar.ensureStarted();
    const status = await sidecar.status();
    log.info(`SIDECAR STATUS ${JSON.stringify(status)}`);
    await sleep(800);

    switch (name) {
      case "refusal": {
        const noMeta = await sidecar.raw("tools/call", { name: "write_file", arguments: { path: path.join(ctx.homeDir, "e4-should-not-exist.txt"), content: "x" } });
        check("tools/call WITHOUT _meta is refused (grants-missing)", !!noMeta.error && /grants-missing/.test(noMeta.error.message), JSON.stringify(noMeta.error));
        const noConv = await sidecar.raw("tools/call", { name: "write_file", arguments: { path: path.join(ctx.homeDir, "e4-should-not-exist.txt"), content: "x" }, _meta: { grants: [] } });
        check("tools/call with grants but no conversationId is refused", !!noConv.error && /conversationId-missing/.test(noConv.error.message), JSON.stringify(noConv.error));
        const badGrant = await sidecar.raw("tools/call", { name: "write_file", arguments: { path: "C:\\Windows\\e4-should-not-exist.txt", content: "x" }, _meta: { grants: ["C:\\Windows"], conversationId: "probe" } });
        const badText = JSON.stringify(badGrant.result ?? badGrant.error);
        check("a forbidden grant (C:\\Windows) is dropped by the validator and the write is denied by PathGuard", /outside the allowed directories|Access denied/i.test(badText) && !fs.existsSync("C:\\Windows\\e4-should-not-exist.txt"), badText.slice(0, 220));
        check("no file was created anywhere by the refused calls", !fs.existsSync(path.join(ctx.homeDir, "e4-should-not-exist.txt")), "checked home + C:\\Windows");
        const ok = await sidecar.callTool("write_file", { path: path.join(ctx.homeDir, "e4-probe-ok.txt"), content: `ok ${Date.now()}` }, { conversationId: "probe", grants: [] });
        check("the same call WITH _meta (empty grants = profile+temp) succeeds", ok.ok && fs.existsSync(path.join(ctx.homeDir, "e4-probe-ok.txt")), ok.text);
        fs.rmSync(path.join(ctx.homeDir, "e4-probe-ok.txt"), { force: true });
        break;
      }
      case "chain": {
        await newChat();
        const t1 = await turn("Run exactly this command with run_command and nothing else: echo The daily codeword is banana-47-tiger . Then reply with just the word ok — do not repeat the command's output.", "allow-once");
        await shot("turn1");
        const ran = co.toolCallsSeen.some((t) => t.tool === "run_command" && t.ok);
        check("turn 1 ran run_command (consent allowed once)", ran && t1.consents >= 1, `consents=${t1.consents} ${lastLine(/TOOL run_command/)}`);
        check("turn 1's VISIBLE reply does not contain the codeword (the trap)", !/banana-47-tiger/.test(t1.reply), `reply="${t1.reply.slice(0, 80)}"`);
        const t2 = await turn("What was the daily codeword in the command output from before? Reply with only the codeword.", "allow-once");
        await shot("turn2");
        check("turn 2 answers from the RAW tool result carried in history", /banana-47-tiger/.test(t2.reply), `reply="${t2.reply.slice(0, 80)}" outbound=${lastLine(/TURN OUTBOUND/).slice(30, 200)}`);
        break;
      }
      case "consent": {
        await newChat();
        const d = await turn("Use run_command to run: echo consent-probe-1 . Reply with one short sentence about what happened.", "deny");
        await shot("after-deny");
        check("deny: prompt raised, tool aborted, turn continued gracefully", d.consents >= 1 && co.toolCallsSeen.some((t) => t.decision === "denied") && d.reply.length > 0, `consents=${d.consents} reply="${d.reply.slice(0, 100)}"`);
        const o1 = await turn("Use run_command to run: echo consent-probe-2 . Reply with only the output.", "allow-once");
        check("allow-once: prompt raised, tool ran", o1.consents >= 1 && /consent-probe-2/.test(o1.reply), `consents=${o1.consents} reply="${o1.reply.slice(0, 60)}"`);
        const o2 = await turn("Use run_command to run: echo consent-probe-3 . Reply with only the output.", "allow-session");
        check("next time: prompted AGAIN (allow-once did not stick); answered allow-session", o2.consents >= 1 && /consent-probe-3/.test(o2.reply), `consents=${o2.consents}`);
        const o3 = await turn("Use run_command to run: echo consent-probe-4 . Reply with only the output.", "deny");
        await shot("after-session");
        check("allow-session: no prompt this time, tool ran", o3.consents === 0 && /consent-probe-4/.test(o3.reply), `consents=${o3.consents} reply="${o3.reply.slice(0, 60)}"`);
        check("the prompting tool is run_command, danger class danger.shell", log.lines.some((l) => /CONSENT REQUEST .*run_command .*danger=danger\.shell/.test(l)), lastLine(/CONSENT REQUEST/));
        break;
      }
      case "audit": {
        await newChat();
        const r = await turn(
          "Please run this command with run_command: echo audit-probe . Also, for my records, my test card number is 4111 1111 1111 1111 and my email is somchai.test@example.com — you do not need to use them, just confirm you saw a card number and an email. Reply in one sentence.",
          "allow-once",
        );
        await shot("audit-turn");
        check("turn ran a local tool AND carried DLP-detectable content", co.toolCallsSeen.some((t) => t.tool === "run_command"), `conv=${r.convId} reply="${r.reply.slice(0, 120)}"`);
        log.info(`AUDIT PROBE conv=${r.convId} at ${new Date().toISOString()} — compare request_logs rows for this window`);
        break;
      }
      case "s1": {
        await newChat();
        const dir = path.join(ctx.grants.list()[0] ?? ctx.homeDir, "e4-s1");
        const r1 = await turn(`Use the browser tools: open https://example.com in the browser (browser_navigate), read the page (browser_read_page), and write its heading and first paragraph to a file named notes.md inside ${dir} (create the folder if needed). Reply with one sentence.`, "allow-once", 240_000);
        await shot("s1-turn1");
        const r2 = await turn(`Now edit that same notes.md: add a line at the end saying "Checked on ${new Date().toISOString().slice(0, 10)}" using edit_file (not write_file). Reply with one sentence.`, "allow-once", 180_000);
        await shot("s1-turn2");
        const file = path.join(dir, "notes.md");
        const content = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
        check("turn 1 used a browser tool", co.toolCallsSeen.some((t) => t.tool.startsWith("browser_")), co.toolCallsSeen.filter((t) => t.tool.startsWith("browser_")).map((t) => `${t.tool}:${t.ok}`).join(",") || "(none)");
        check("turn 1 wrote notes.md", /Example Domain/i.test(content), `file=${file} chars=${content.length}`);
        check("turn 2 edited the same file with edit_file", co.toolCallsSeen.some((t) => t.tool === "edit_file" && t.ok) && /Checked on/.test(content), `tail="${content.slice(-80).replace(/\n/g, "\\n")}"`);
        void r1;
        void r2;
        break;
      }
      case "s2": {
        await newChat();
        const r = await turn("Take a screenshot of the screen (computer_screenshot). Tell me the title of the foreground window and quote one real line of text you can read in the image. Do not click or type anything.", "allow-once", 180_000);
        await shot("s2-screenshot");
        check("computer_screenshot ran and the reply quotes something from the image", co.toolCallsSeen.some((t) => t.tool === "computer_screenshot" && t.ok) && r.reply.length > 20, `reply="${r.reply.slice(0, 160)}"`);
        break;
      }
      case "s3": {
        await newChat();
        const inbox = path.join(ctx.grants.list()[0] ?? ctx.homeDir, "e4-s3-inbox");
        fs.mkdirSync(inbox, { recursive: true });
        const codeword = `FALCON-${Math.floor(1000 + Math.random() * 9000)}`;
        fs.writeFileSync(path.join(inbox, "aurora-status.txt"), `Aurora status report\nCodeword: ${codeword}\nAll systems nominal.\n`);
        const r1 = await turn(`In the folder ${inbox} there is a status file. Read it, tell me the codeword it contains, then move the file into a subfolder named archive (create it). Reply with the codeword and what you did.`, "allow-once", 180_000);
        await shot("s3-granted");
        check("read the codeword from the file (not the prompt)", r1.reply.includes(codeword), `reply="${r1.reply.slice(0, 120)}"`);
        check("moved the file into archive\\", fs.existsSync(path.join(inbox, "archive", "aurora-status.txt")), fs.existsSync(path.join(inbox, "archive")) ? fs.readdirSync(path.join(inbox, "archive")).join(",") : "(no archive)");
        const r2 = await turn("Now create a file named e4-probe.txt containing the word hello directly inside C:\\ProgramData. Actually try it with write_file; if it is refused, tell me the exact reason the tool gave.", "allow-once", 120_000);
        await shot("s3-refused");
        const refused = co.toolCallsSeen.some((t) => t.tool === "write_file" && !t.ok && /ProgramData/i.test(JSON.stringify(t.args)));
        check("un-granted C:\\ProgramData write refused by PathGuard, no file created", refused && !fs.existsSync("C:\\ProgramData\\e4-probe.txt"), `reply="${r2.reply.slice(0, 200)}"`);
        break;
      }
      case "s4": {
        await newChat();
        const st = await sidecar.status();
        const r = await turn("List the tools you have whose names start with 'echo' or come from an MCP connector, then call one of them with a short test message and show me the raw result.", "allow-once", 180_000);
        await shot("s4-mcp");
        check("a remote (MCP connector) tool was available and called", Number(st["remoteTools"] ?? 0) > 0 && co.toolCallsSeen.some((t) => t.where === "sidecar" && !["read_file", "write_file", "run_command", "list_directory", "search_files", "edit_file", "move_file", "create_directory", "computer_screenshot", "update_plan", "remember", "web_fetch", "use_skill"].includes(t.tool) && !t.tool.startsWith("browser_") && !t.tool.startsWith("computer_")), `remoteTools=${String(st["remoteTools"])} calls=${co.toolCallsSeen.map((t) => t.tool).join(",")} reply="${r.reply.slice(0, 120)}"`);
        break;
      }
      case "s5": {
        await newChat();
        const r = await turn("Use run_subagent with a single task: run the command 'ping -n 4 127.0.0.1' and report how many replies it received. Then tell me the sub-agent's answer.", "allow-once", 240_000);
        await shot("s5-subagent");
        check("run_subagent ran host-side and its nested run_command ran through the sidecar", co.toolCallsSeen.some((t) => t.tool === "run_subagent" && t.where === "host") && log.lines.some((l) => /SUBAGENT (DONE|PAUSED|CANCELLED|ERROR)/.test(l)) && co.toolCallsSeen.some((t) => t.tool === "run_command" && t.conversationId.startsWith("subagent-")), `${lastLine(/SUBAGENT /)} reply="${r.reply.slice(0, 120)}"`);
        break;
      }
      case "s6": {
        await newChat();
        const accepted = bridge.accepted.length;
        await js(TYPE_AND_SEND("Do this as a multi-step task and keep a plan with update_plan: 1) ping localhost 20 times with 'ping -n 20 127.0.0.1' using run_command, 2) report how many replies came back. Start now."));
        const t0s = Date.now();
        while (!bridge.accepted.slice(accepted).some((a) => a.type === "turn.submit") && Date.now() - t0s < 20_000) await sleep(100);
        const convId = co.lastConversationId!;
        // Allow the consent, then cancel while the ping is running.
        let outputSeen = false;
        const tCancel = Date.now() + 90_000;
        while (co.isRunning(convId) && Date.now() < tCancel) {
          if (co.pendingConsentCount > 0) {
            await sleep(600);
            await js(CLICK_CONSENT("consent-allow-once"));
          }
          if (log.lines.some((l) => /TOOL run_command conv=/.test(l)) && !outputSeen) {
            outputSeen = true;
            await sleep(6000);
            await shot("s6-running");
            log.info("PROBE s6: cancelling mid-tool");
            await js(`window.sranHost.postMessage({ nonce: window.sranHost.nonce, type: 'turn.cancel', payload: { conversationId: ${JSON.stringify(convId)} } })`);
          }
          await sleep(300);
        }
        await sleep(2000);
        await shot("s6-cancelled");
        const pingAlive = countProcesses("PING.EXE");
        check("plan card emitted", log.lines.some((l) => /TOOL update_plan/.test(l)), lastLine(/TOOL update_plan/));
        check("cancel landed while run_command was running and the turn ended cancelled", log.lines.some((l) => /TURN CANCELLED conv=/.test(l)) && outputSeen, lastLine(/TURN CANCELLED/));
        check("the ping process is gone after cancel (the tool really stopped)", pingAlive === 0, `PING.EXE processes=${pingAlive}`);
        const r = await turn("Continue where you left off.", "allow-once", 180_000);
        await shot("s6-continued");
        check("continue-after-cancel keeps the task (history persisted on cancel)", /ping|repl|127\.0\.0\.1/i.test(r.reply) && !/what task|more details|which task/i.test(r.reply), `reply="${r.reply.slice(0, 160)}"`);
        break;
      }
      case "cancel": {
        await newChat();
        const accepted = bridge.accepted.length;
        await js(TYPE_AND_SEND("Run this with run_command and wait for it to finish: ping -n 30 127.0.0.1 . Then say done."));
        const t0c = Date.now();
        while (!bridge.accepted.slice(accepted).some((a) => a.type === "turn.submit") && Date.now() - t0c < 20_000) await sleep(100);
        const convId = co.lastConversationId!;
        while (co.isRunning(convId) && !log.lines.some((l) => /TOOL run_command conv=/.test(l))) {
          if (co.pendingConsentCount > 0) {
            await sleep(600);
            await js(CLICK_CONSENT("consent-allow-once"));
          }
          await sleep(200);
        }
        await sleep(4000);
        const before = countProcesses("PING.EXE");
        log.info(`PROBE cancel: PING.EXE processes before cancel = ${before}; sending turn.cancel`);
        await js(`window.sranHost.postMessage({ nonce: window.sranHost.nonce, type: 'turn.cancel', payload: { conversationId: ${JSON.stringify(convId)} } })`);
        await sleep(3000);
        const after = countProcesses("PING.EXE");
        check("a tool was running (ping process existed)", before > 0, `before=${before}`);
        check("turn.cancel → notifications/cancelled → the ping process is gone", after === 0 && log.lines.some((l) => /TURN CANCELLED/.test(l)), `after=${after} ${lastLine(/CANCEL request|Request cancelled|TURN CANCELLED/)}`);
        break;
      }
      case "rss": {
        await sleep(3000);
        const metrics = app.getAppMetrics();
        const total = metrics.reduce((s, m) => s + m.memory.workingSetSize, 0) / 1024;
        const side = sidecar.pid ? processWorkingSetMB(sidecar.pid) : -1;
        log.info(`RSS app=${total.toFixed(1)} MB (${metrics.length} processes) sidecar=${side} MB pid=${sidecar.pid}`);
        check("rss sampled", true, `app=${total.toFixed(1)} sidecar=${side}`);
        break;
      }
      // ── FIX4: drive the safe tools that were never driven through a turn ──
      case "fix4a": {
        const grant = ctx.grants.list()[0] ?? ctx.homeDir;
        const step = async (label: string, fn: () => Promise<void>) => {
          try { await fn(); } catch (e) { check(`${label} (probe error)`, false, (e as Error).message); }
        };

        // search_files + edit_file (granted temp folder)
        await step("files", async () => {
          await newChat();
          const docs = path.join(grant, "fix4-docs");
          fs.mkdirSync(docs, { recursive: true });
          fs.writeFileSync(path.join(docs, "alpha.txt"), "alpha file\nMAGICWORD-7788 lives here\n");
          fs.writeFileSync(path.join(docs, "beta.txt"), "beta file, nothing to see\n");
          const rs = await turn(`In the folder ${docs}, use search_files to find which file contains "MAGICWORD". Tell me the filename.`, "allow-once", 150_000);
          await shot("search_files");
          check("search_files found the right file", co.toolCallsSeen.some((t) => t.tool === "search_files" && t.ok) && /alpha/i.test(rs.reply), `reply="${rs.reply.slice(0, 120)}"`);
          const re = await turn(`Use edit_file to change MAGICWORD-7788 to MAGICWORD-9999 in ${path.join(docs, "alpha.txt")}. One sentence.`, "allow-once", 150_000);
          await shot("edit_file");
          const after = fs.existsSync(path.join(docs, "alpha.txt")) ? fs.readFileSync(path.join(docs, "alpha.txt"), "utf8") : "";
          check("edit_file changed the file on disk (looked at it)", co.toolCallsSeen.some((t) => t.tool === "edit_file" && t.ok) && after.includes("9999") && !after.includes("7788"), `content="${after.replace(/\n/g, "\\n")}" reply="${re.reply.slice(0, 60)}"`);
        });

        // web_fetch
        await step("web_fetch", async () => {
          await newChat();
          const r = await turn(`Use web_fetch on https://example.com and tell me the main heading text.`, "allow-once", 150_000);
          await shot("web_fetch");
          check("web_fetch fetched example.com and read it", co.toolCallsSeen.some((t) => t.tool === "web_fetch" && t.ok) && /example domain/i.test(r.reply + r.dom), `reply="${r.reply.slice(0, 120)}"`);
        });

        // read_document — born-digital + scanned (FIX3)
        await step("read_document", async () => {
          await newChat();
          const dg = path.join(grant, "digital.pdf");
          const r1 = await turn(`Use read_document to read ${dg} and quote the sentence it contains.`, "allow-once", 150_000);
          await shot("read_document-digital");
          check("read_document read the born-digital PDF text", co.toolCallsSeen.some((t) => t.tool === "read_document" && t.ok) && /quarterly revenue/i.test(r1.reply + r1.dom), `reply="${r1.reply.slice(0, 120)}"`);
          await newChat();
          const sc = path.join(grant, "scanned.pdf");
          const r2 = await turn(`Use read_document to read ${sc} (a scanned image PDF). Tell me any recovered text, or the exact message if it cannot be read.`, "allow-once", 200_000);
          await shot("read_document-scanned");
          check("read_document scanned: OCR attempted or a legible message (FIX3)", /invoice|a-4471|12,900|scanned|could not|no text|ocr|synced|recover/i.test(r2.reply + r2.dom), `reply="${r2.reply.slice(0, 200)}"`);
        });

        // run_python (sandbox)
        await step("run_python", async () => {
          await newChat();
          const r = await turn(`Use run_python to compute the sum of the integers 1..100 and print the result.`, "allow-once", 200_000);
          await shot("run_python");
          check("run_python executed and returned 5050", co.toolCallsSeen.some((t) => t.tool === "run_python") && /5050/.test(r.reply + r.dom), `reply="${r.reply.slice(0, 120)}"`);
        });

        // clipboard write→read (overwrites the real clipboard — noted in the report)
        await step("clipboard", async () => {
          await newChat();
          const marker = `CLIP-${Math.floor(1000 + Math.random() * 9000)}`;
          await turn(`Use write_clipboard to put exactly this text on the clipboard: ${marker}. Then say done.`, "allow-once", 120_000);
          await shot("write_clipboard");
          const rr = await turn(`Use read_clipboard and tell me exactly what the clipboard contains.`, "allow-once", 120_000);
          await shot("read_clipboard");
          check("write_clipboard→read_clipboard round-trips the marker", co.toolCallsSeen.some((t) => t.tool === "write_clipboard") && co.toolCallsSeen.some((t) => t.tool === "read_clipboard") && (rr.reply.includes(marker) || rr.dom.includes(marker)), `marker=${marker} reply="${rr.reply.slice(0, 120)}"`);
        });
        break;
      }

      case "fix4doc": {
        const grant = ctx.grants.list()[0] ?? ctx.homeDir;
        await newChat();
        const dg = path.join(grant, "digital.pdf");
        const r1 = await turn(`Use read_document to read ${dg} and quote the sentence it contains.`, "allow-once", 150_000);
        await shot("read_document-digital");
        check("read_document read the born-digital PDF text (looked at the reply)", co.toolCallsSeen.some((t) => t.tool === "read_document" && t.ok) && /quarterly revenue/i.test(r1.reply + r1.dom), `reply="${r1.reply.slice(0, 140)}"`);
        await newChat();
        const sc = path.join(grant, "scanned.pdf");
        const r2 = await turn(`Use read_document to read ${sc} (a scanned image PDF). Tell me any recovered text, or the exact message if it cannot be read.`, "allow-once", 200_000);
        await shot("read_document-scanned");
        check("read_document scanned: OCR path exercised — recovered text OR a legible message (FIX3)", co.toolCallsSeen.some((t) => t.tool === "read_document") && /invoice|a-4471|12,900|scanned|could not|no text|ocr|synced|recover|no readable/i.test(r2.reply + r2.dom), `reply="${r2.reply.slice(0, 220)}"`);
        break;
      }

      case "fix4b": {
        const step = async (label: string, fn: () => Promise<void>) => {
          try { await fn(); } catch (e) { check(`${label} (probe error)`, false, (e as Error).message); }
        };

        // web_search (config must set webSearchEnabled:true for the test; shipped default stays OFF)
        await step("web_search", async () => {
          await newChat();
          const r = await turn(`Use web_search to look up "height of the Eiffel Tower in meters" and tell me the number.`, "allow-once", 200_000);
          await shot("web_search");
          check("web_search ran and returned a usable result", co.toolCallsSeen.some((t) => t.tool === "web_search" && t.ok) && r.reply.length > 15, `reply="${r.reply.slice(0, 140)}"`);
        });

        // use_skill
        await step("use_skill", async () => {
          await newChat();
          const r = await turn(`List your available skills by name, then use use_skill to invoke one of them on a trivial example and show me what it returned.`, "allow-once", 200_000);
          await shot("use_skill");
          check("use_skill invoked a skill", co.toolCallsSeen.some((t) => t.tool === "use_skill" && t.ok), co.toolCallsSeen.filter((t) => t.tool === "use_skill").map((t) => `ok=${t.ok}`).join(",") || `(not called) reply="${r.reply.slice(0, 100)}"`);
        });

        // remember ACROSS TWO CONVERSATIONS
        await step("remember-x2", async () => {
          await newChat();
          const secret = `MEMTOKEN-${Math.floor(1000 + Math.random() * 9000)}`;
          const ra = await turn(`Use remember to store this for later: "The project code is ${secret}." Reply "stored".`, "allow-once", 150_000);
          await shot("remember-write");
          check("remember stored the note (conversation A)", co.toolCallsSeen.some((t) => t.tool === "remember" && t.ok), `convA=${ra.convId}`);
          await newChat(); // a genuinely NEW conversation
          await sleep(1500);
          const rb = await turn(`What is the project code? Recall it from your memory.`, "allow-once", 150_000);
          await shot("remember-recall");
          check("a SECOND conversation recalled the remembered note", rb.reply.includes(secret) || rb.dom.includes(secret), `convB=${rb.convId} reply="${rb.reply.slice(0, 140)}"`);
        });

        // connector tool (echo_reverse from the installed Echo MCP connector)
        await step("connector", async () => {
          await newChat();
          const r = await turn(`Use the echo_reverse tool from the Echo MCP connector to reverse the text "fix4-connector". Show me the exact result.`, "allow-once", 150_000);
          await shot("connector");
          check("connector tool echo_reverse ran through a turn", co.toolCallsSeen.some((t) => t.tool === "echo_reverse") && /rotcennoc-4xif/.test(r.reply + r.dom), `reply="${r.reply.slice(0, 120)}"`);
        });
        break;
      }

      case "fix4mem": {
        // Definitive cross-conversation memory test with EXPLICIT global scope,
        // to rule out "the model just picked conversation scope".
        await newChat();
        const secret = `ZORP-${Math.floor(1000 + Math.random() * 9000)}`;
        await turn(`Use the remember tool with scope "global" to permanently store this durable user-level fact: "My secret project codename is ${secret}." Reply "stored".`, "allow-once", 150_000);
        await shot("mem-global-write");
        const usedGlobal = co.toolCallsSeen.some((t) => t.tool === "remember" && t.ok && /global/.test(JSON.stringify(t.args)));
        check("remember stored with scope=global (the tool succeeded)", usedGlobal, co.toolCallsSeen.filter((t) => t.tool === "remember").map((t) => JSON.stringify(t.args)).join(" | ") || "(remember not called)");
        await newChat();
        await sleep(1500);
        const rb = await turn(`What is my secret project codename? Recall it from your durable/global memory.`, "allow-once", 150_000);
        await shot("mem-global-recall");
        check("a NEW conversation recalls the GLOBAL memory", rb.reply.includes(secret) || rb.dom.includes(secret), `reply="${rb.reply.slice(0, 160)}"`);
        break;
      }

      case "fix5mem": {
        // FIX5 §1 — the `remember` tool must now actually RECALL, in both scopes.
        // Facts are generated at test time so the model cannot guess them.
        const gtok = `sky-quartz-${Math.floor(1000 + Math.random() * 9000)}`;
        const ctok = `river-lantern-${Math.floor(1000 + Math.random() * 9000)}`;

        // ── global: save in one conversation, recall in a NEW one ──
        await newChat();
        await turn(`Use the remember tool with scope "global" to store this durable user-level fact: "My lucky phrase is ${gtok}." Then reply just "stored".`, "allow-once", 150_000);
        await shot("global-write");
        const savedGlobal = co.toolCallsSeen.some((t) => t.tool === "remember" && t.ok && /global/.test(JSON.stringify(t.args)));
        check("§1 remember(global) tool call succeeded", savedGlobal, co.toolCallsSeen.filter((t) => t.tool === "remember").map((t) => JSON.stringify(t.args)).join(" | ") || "(remember not called)");

        await newChat();
        await sleep(1500);
        const gr = await turn(`What is my lucky phrase? Recall it from your durable global memory. If you don't have it, say so.`, "allow-once", 150_000);
        await shot("global-recall");
        check("§1 a NEW conversation recalls the GLOBAL fact", gr.reply.includes(gtok) || gr.dom.includes(gtok), `token=${gtok} reply="${gr.reply.slice(0, 200)}"`);
        // The model must NOT have re-called a tool to get it — it comes from the prompt.
        const usedToolForGlobal = co.toolCallsSeen.slice(-4).some((t) => t.conversationId === gr.convId && t.tool !== "remember");
        check("§1 global recall came from the prompt, not a fresh tool call", !usedToolForGlobal, `recentTools=${co.toolCallsSeen.slice(-4).map((t) => t.tool).join(",")}`);

        // ── conversation scope: save, then recall on a LATER turn of the SAME conv ──
        const conv = await turn(`Use the remember tool with scope "conversation" to note this task fact: "The staging box is called ${ctok}." Then reply just "noted".`, "allow-once", 150_000);
        await shot("conv-write");
        const savedConv = co.toolCallsSeen.some((t) => t.tool === "remember" && t.ok && t.conversationId === conv.convId && /conversation/.test(JSON.stringify(t.args)));
        check("§1 remember(conversation) tool call succeeded", savedConv, co.toolCallsSeen.filter((t) => t.tool === "remember" && t.conversationId === conv.convId).map((t) => JSON.stringify(t.args)).join(" | ") || "(not called)");
        await sleep(1200);
        const cr = await turn(`Without using any tool, what is the staging box called? Recall it from your notes for this conversation.`, "allow-once", 150_000);
        await shot("conv-recall");
        check("§1 a LATER turn of the SAME conversation recalls the note", cr.reply.includes(ctok) || cr.dom.includes(ctok), `token=${ctok} sameConv=${cr.convId === conv.convId} reply="${cr.reply.slice(0, 200)}"`);
        break;
      }

      case "fix56mem": {
        // Do the two clients remember in the SAME place, and does a chat
        // with memory off really leave nothing behind?
        //
        // Extends the fix5mem case rather than repeating it: the same
        // write-then-recall shape, but each half also names WHICH store
        // it expects the fact to land in, because that is the question.
        // The desktop `remember` tool writes the sidecar's local SQLite
        // (agent_notes); the web client writes chat-edge's Postgres
        // (chat.user_memories). Reading a recall out of the reply cannot
        // tell those apart — the desktop prompt merges both — so the
        // decisive check is done against the stores by the operator, and
        // this case produces the tokens to look for.
        const dtok = `desk-anchor-${Math.floor(1000 + Math.random() * 9000)}`;

        // ── direction 1: desktop writes ───────────────────────────────
        await newChat();
        await turn(`Use the remember tool with scope "global" to store this durable user-level fact: "My desktop anchor is ${dtok}." Then reply just "stored".`, "allow-once", 150_000);
        await shot("fix56-desktop-write");
        const wrote = co.toolCallsSeen.some((t) => t.tool === "remember" && t.ok);
        check("desktop remember(global) succeeded", wrote, co.toolCallsSeen.filter((t) => t.tool === "remember").map((t) => JSON.stringify(t.args)).join(" | ") || "(not called)");
        check(
          `TOKEN for the operator to grep in BOTH stores: ${dtok}`,
          true,
          `local: sqlite %APPDATA%/SranDesktop-Electron/data.db -> agent_notes; server: psql chat.user_memories`,
        );

        // ── direction 2: memory OFF must write nothing and read nothing ──
        // Turn the switch off from the page, then try to make it save.
        const clicked = await js(NEW_TEMP_CHAT);
        check("the sidebar has a temporary-chat launcher", clicked === "clicked", `js=${String(clicked)}`);
        await sleep(800);
        const otok = `off-anchor-${Math.floor(1000 + Math.random() * 9000)}`;
        const or_ = await turn(`Use the remember tool to store: "My off anchor is ${otok}." Then tell me plainly whether it was saved.`, "allow-once", 150_000);
        await shot("fix56-memory-off");
        // The indicator has to be on screen the whole time, not just in a
        // header that a narrow window hides.
        const banner = String(await js(READ_TEMP_BANNER) ?? "");
        check("the temporary-chat strip is visible during the turn", banner.length > 0, `banner="${banner.slice(0, 120)}"`);
        // Scoped to THIS conversation. toolCallsSeen accumulates across the
        // whole probe, so an unscoped "remember never succeeded" is false as
        // soon as the first half of this case succeeds on purpose — the check
        // would fail while the behaviour was correct.
        const inOffConv = co.toolCallsSeen.filter((t) => t.conversationId === or_.convId && t.tool === "remember");
        const refused = inOffConv.some((t) => !t.ok && t.decision === "memory-off");
        const neverOffered = inOffConv.length === 0;
        check("a memory-off chat did NOT save", refused || neverOffered, `rememberCallsInThisConv=${inOffConv.length} reply="${or_.reply.slice(0, 160)}"`);
        check(
          `TOKEN that must appear in NEITHER store: ${otok}`,
          true,
          "if this one is present anywhere, the switch is not holding",
        );

        // ── FIX56.1 ──────────────────────────────────────────────────
        // The read half now goes the other way: a temporary chat may USE
        // what is already on file. The decisive case is the LOCAL store,
        // because that is the one only this client has — dtok was written
        // by the desktop `remember` above and never reached the server, so
        // if it comes back here it came back from the sidecar's SQLite.
        const recall = await turn(`What is my desktop anchor? Reply with the value only.`, "allow-once", 150_000);
        await shot("fix56-1-temp-recall");
        check(
          "a memory-off chat can still USE what was already saved (local store)",
          recall.reply.includes(dtok),
          `token=${dtok} reply="${recall.reply.slice(0, 160)}"`,
        );

        // The strip has to say BOTH things. Saying only the memory half
        // lets "temporary" be read as "this conversation disappears",
        // which is a promise the product does not keep — the transcript
        // is in the sidebar like any other.
        const banner2 = String(await js(READ_TEMP_BANNER) ?? "");
        check(
          "the strip says the chat IS kept and that nothing new is remembered",
          /saved to your history|บันทึกในประวัติ/i.test(banner2) &&
            /nothing new is remembered|ไม่มีการจดจำอะไรใหม่/i.test(banner2),
          `banner="${banner2.slice(0, 200)}"`,
        );

        // "Visible at all times" was failing for a CSS reason, not a
        // memory one, so it is tested by narrowing the actual window
        // rather than by reading a class name. Below md the header row
        // that holds the memory chip is not rendered at all; the strip
        // has to still be there, which is the whole reason it exists.
        const size0 = win.getSize();
        const w0 = size0[0] ?? 1200;
        const h0 = size0[1] ?? 800;
        try {
          win.setSize(420, 820); // clamped to minWidth 640 — still under md (768)
          await sleep(900);
          const narrow = JSON.parse(String(await js(READ_NARROW_STATE) ?? "{}")) as {
            innerWidth?: number; bannerVisible?: boolean; bannerText?: string; chipRowVisible?: boolean;
          };
          await shot("fix56-1-narrow-window");
          const underBreakpoint = (narrow.innerWidth ?? 9999) < 768;
          check(
            "the window really is below the md breakpoint (or the check below means nothing)",
            underBreakpoint,
            `innerWidth=${narrow.innerWidth} (asked for 420; BrowserWindow minWidth is 640)`,
          );
          check(
            "below md the header chip is gone and the strip is what carries the state",
            underBreakpoint && narrow.bannerVisible === true && narrow.chipRowVisible === false,
            `innerWidth=${narrow.innerWidth} bannerVisible=${narrow.bannerVisible} ` +
            `chipRowVisible=${narrow.chipRowVisible} banner="${String(narrow.bannerText).slice(0, 100)}"`,
          );
        } finally {
          win.setSize(w0, h0);
          await sleep(500);
        }

        // A switch that can be escaped by asking for the work to be
        // handed to someone else is not a switch. The sub-agent inherits
        // the flag, so its `remember` must be refused too.
        const stok = `sub-anchor-${Math.floor(1000 + Math.random() * 9000)}`;
        let subFrom = 0;
        let sub = { reply: "" } as { reply: string };
        // The sub-agent runs on the default model ("auto"), which on this
        // box sometimes returns all_models_failed. One retry, because an
        // unlucky route is not a finding.
        for (let attempt = 1; attempt <= 2; attempt++) {
          subFrom = log.lines.length;
          sub = await turn(
            `Use run_subagent with a single task: use the remember tool to store "My sub anchor is ${stok}", then report exactly what happened. Then tell me what the sub-agent said.`,
            "allow-once", 240_000,
          );
          if (since(subFrom, /SUBAGENT DONE/).length > 0) break;
        }
        await shot("fix56-1-subagent-off");

        // The deterministic half, which holds whether or not the model
        // answered: the flag has to reach the NESTED run's tool list. Scoped
        // to the window between the run_subagent dispatch and SUBAGENT START,
        // because the parent turn logs the same withholding for itself and an
        // unscoped match would pass on the parent's line alone.
        const tail = log.lines.slice(subFrom);
        const dispatchAt = tail.findIndex((l) => /TOOL run_subagent conv=/.test(l));
        const startAt = tail.findIndex((l) => /SUBAGENT START/.test(l));
        const withheldForSub =
          dispatchAt >= 0 && startAt > dispatchAt &&
          tail.slice(dispatchAt, startAt).some((l) => /memory is off for this chat — withholding/.test(l));
        check(
          "the memory-off flag reached the SUB-AGENT's own tool list",
          withheldForSub,
          `dispatch@${dispatchAt} start@${startAt} between=${JSON.stringify(tail.slice(Math.max(dispatchAt, 0), Math.max(startAt, 0)).slice(-3))}`,
        );

        // Nested runs get their own conversationId (`subagent-…`), so this
        // cannot be scoped by conversation the way the check above is —
        // scope it to the tool + token instead.
        const subSaves = co.toolCallsSeen.filter(
          (t) => t.tool === "remember" && JSON.stringify(t.args ?? {}).includes(stok) && t.ok,
        );
        // A sub-agent that never got an answer out of the router saved
        // nothing for a reason that has nothing to do with the switch. Say
        // so rather than banking a pass: this check exists so the next one
        // cannot be true vacuously.
        const subRan = since(subFrom, /SUBAGENT DONE/).length > 0;
        check(
          "the sub-agent ran to completion (without this, the check below proves nothing)",
          subRan,
          `${lastLine(/SUBAGENT /)}`,
        );
        check(
          "a sub-agent in a memory-off chat did NOT save either",
          subRan && subSaves.length === 0,
          `subagentRan=${subRan} successfulRememberCallsCarryingTheToken=${subSaves.length} reply="${sub.reply.slice(0, 160)}"`,
        );
        check(
          `TOKEN that must appear in NEITHER store: ${stok}`,
          true,
          "the delegated save; present anywhere means the flag did not reach the sub-agent",
        );
        break;
      }

      case "fix5search": {
        // FIX5 §3 — with the tenant's web search OFF, the model must NOT be offered
        // web_search (so it cannot call it and fail), and must explain the true
        // reason. `webSearchEnabled` config for this probe is ON, isolating the
        // TENANT gate as the thing under test.
        await newChat();
        const before = co.toolCallsSeen.length;
        const r = await turn(`Search the web for today's top technology headline and tell me what it is.`, "allow-once", 150_000);
        await shot("search-withheld");
        const calledSearch = co.toolCallsSeen.slice(before).some((t) => t.tool === "web_search");
        check("§3 the model did NOT call web_search (tool withheld this turn)", !calledSearch, `toolsThisTurn=${co.toolCallsSeen.slice(before).map((t) => t.tool).join(",") || "(none)"}`);
        const withheldLog = log.lines.some((l) => /web_search .*tenant has it disabled — withholding/.test(l));
        check("§3 host logged the withhold decision", withheldLog, lastLine(/web_search.*withholding|TOOLS: web_search/));
        const honest = /not enabled|organi[sz]ation|administrator|admin|เปิดใช้|องค์กร/i.test(r.reply);
        check("§3 the reply gives the TRUE reason (org has not enabled it), not 'I lack the tool'", honest, `reply="${r.reply.slice(0, 240)}"`);
        break;
      }

      case "fix5d7": {
        // FIX5 §4 — re-test E4 D7: after cancelling a run_command and resuming,
        // does the model rebuild a MANGLED command (e.g. `<< was unexpected`)?
        // Repeat and report a RATE, not one anecdote.
        const ROUNDS = 5;
        let mangled = 0;
        let resumedOk = 0;
        const details: string[] = [];
        for (let i = 1; i <= ROUNDS; i++) {
          await newChat();
          const accepted = bridge.accepted.length;
          await js(TYPE_AND_SEND(`Run this with run_command and wait for it to finish, then tell me it is done: ping -n 20 127.0.0.1`));
          const t0c = Date.now();
          while (!bridge.accepted.slice(accepted).some((a) => a.type === "turn.submit") && Date.now() - t0c < 20_000) await sleep(100);
          const convId = co.lastConversationId!;
          // let it start the command (answer the consent), then cancel mid-run
          while (co.isRunning(convId) && !log.lines.some((l) => new RegExp(`TOOL run_command conv=${convId}`).test(l))) {
            if (co.pendingConsentCount > 0) { await sleep(500); await js(CLICK_CONSENT("consent-allow-once")); }
            await sleep(200);
          }
          await sleep(3000);
          await js(`window.sranHost.postMessage({ nonce: window.sranHost.nonce, type: 'turn.cancel', payload: { conversationId: ${JSON.stringify(convId)} } })`);
          await sleep(2500);
          const logFrom = log.lines.length;
          // resume
          const rr = await turn(`continue where you left off`, "allow-once", 120_000);
          const cmds = since(logFrom, /TOOL run_command conv=/);
          const errs = since(logFrom, /was unexpected|<<|CommandNotFound|ParserError|is not recognized|Unexpected token/i);
          const thisMangled = errs.length > 0;
          if (thisMangled) mangled++; else resumedOk++;
          details.push(`round ${i}: ${thisMangled ? "MANGLED" : "clean"} (${cmds.length} run_command; ${errs.length} parse-err) reply="${rr.reply.slice(0, 80)}"`);
          await shot(`d7-round-${i}`);
        }
        check(`§4 D7 resume rebuilt a mangled command in ${mangled}/${ROUNDS} rounds`, true, details.join(" || "));
        check(`§4 D7 clean resume in ${resumedOk}/${ROUNDS} rounds`, resumedOk > 0, `mangled=${mangled} clean=${resumedOk}`);
        break;
      }

      case "fix5err": {
        // FIX5 §2 — screenshot the message a user actually sees when the gateway
        // returns HTTP 429. Run against the mock gateway (MODE=credits|ratelimit)
        // that replies with the real captured 429 body; the app's classify429 turns
        // it into the user-facing message, which the page renders.
        await newChat();
        const logFrom = log.lines.length;
        const r = await turn(`Say hello.`, "allow-once", 60_000);
        await sleep(800);
        await shot("gateway-429");
        const errLine = since(logFrom, /TURN ERROR conv=/).slice(-1)[0] ?? "(no TURN ERROR line)";
        const domHasCredits = /out of gateway credits/i.test(r.dom);
        const domHasRate = /rate limited/i.test(r.dom);
        check("§2 a 429 produced a user-facing error message (see screenshot)", domHasCredits || domHasRate, `credits=${domHasCredits} rate=${domHasRate} | ${errLine}`);
        break;
      }

      case "fix68-connector": {
        // FIX68 — the custom-connector path end to end, against OUR OWN MCP
        // server (scripts/probes/fix68/demo-mcp-server.mjs), through the page's
        // real "Add custom connector" button. Run as:
        //   FIX68_NONCE=<random> FIX68_MCP_LOG=<file> npx electron . \
        //     --probe-e4 fix68-connector --probe-mcp-server <abs path to the .mjs>
        // The nonce names one of the server's tools, so a card that shows it
        // can only have listed the LIVE process; the server's own log file is
        // the second witness for every call (or its absence, after Deny).
        const nonce = process.env["FIX68_NONCE"] ?? "";
        const serverLog = process.env["FIX68_MCP_LOG"] ?? "";
        const readServerLog = (): Array<Record<string, unknown>> => {
          try {
            return fs.readFileSync(serverLog, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Record<string, unknown>);
          } catch {
            return [];
          }
        };
        const serverCalls = (tool?: string) => readServerLog().filter((e) => e["kind"] === "request" && e["method"] === "tools/call" && (!tool || e["tool"] === tool));
        const connectorId = "custom-demo-mcp-server"; // derived by the host from the file name
        const cardSel = `[data-testid="connector-card-${connectorId}"]`;
        const nonceTool = `fix68_nonce_${nonce}`;
        const waitFor = async (code: string, ms: number): Promise<boolean> => {
          const t0 = Date.now();
          while (Date.now() - t0 < ms) {
            if (await js(code)) return true;
            await sleep(300);
          }
          return false;
        };
        const ADD_BUTTON = `[...document.querySelectorAll('button')].find(b => /add custom connector|เพิ่ม connector เอง/i.test(b.textContent || ''))`;

        // ── #1 add through the page ──────────────────────────────────────
        await js(`location.assign('/chat/connectors')`);
        const hasAdd = await waitFor(`!!${ADD_BUTTON}`, 20_000);
        check("connectors page shows the page's own Add-custom-connector button", hasAdd, "");
        const toolsBefore = await sidecar.listTools();
        const logFrom = log.lines.length;
        await js(`(() => { const b = ${ADD_BUTTON}; if (b) b.click(); return !!b; })()`);
        // FIX69: the button opens a two-way menu; "Local program" is the stdio path.
        await waitFor(`!!document.querySelector('[data-testid="add-custom-local"]')`, 5_000);
        await js(`(() => { const b = document.querySelector('[data-testid="add-custom-local"]'); if (b) b.click(); return !!b; })()`);
        const cardShown = await waitFor(`!!document.querySelector('${cardSel}')`, 90_000);
        await sleep(500);
        await shot("custom-card");
        check("#1 the connector was added through the page (custom card appears; host logged the add)", cardShown && since(logFrom, /CONNECTOR added \(custom stdio\): custom-demo-mcp-server/).length > 0, lastLine(/CONNECTOR added|test-failed|addcustom|_testCustom/));
        check("#1b the spawn-consent dialog was reached BEFORE the spawn (its text names the exact command)", since(logFrom, /PROBE confirmSpawn dialog reached/).length > 0, lastLine(/PROBE confirmSpawn/).slice(0, 300));

        // ── #2 the tool list comes from the live server ──────────────────
        const cardTools = String(await js(`((document.querySelector('${cardSel} [data-testid="connector-tools"]') || {}).textContent || '')`));
        const started = readServerLog().find((e) => e["kind"] === "start");
        const serverTools = Array.isArray(started?.["tools"]) ? (started!["tools"] as string[]) : [];
        check("#2 the card lists exactly what the LIVE server announced, including the nonce-named tool nothing in the repo knows", nonce.length > 0 && serverTools.includes(nonceTool) && serverTools.length > 0 && serverTools.every((t) => cardTools.includes(t)), `card="${cardTools}" server=${JSON.stringify(serverTools)}`);
        const toolsAfter = await sidecar.listTools();
        check("#2b the MODEL's tool list gained the server's tools without a restart (host cache invalidated)", !toolsBefore.some((t) => t.name === nonceTool) && serverTools.every((t) => toolsAfter.some((a) => a.name === t)), `before=${toolsBefore.length} after=${toolsAfter.length} nonceTool=${toolsAfter.some((a) => a.name === nonceTool)}`);

        // ── #4 the gate: Deny first — the server must not hear the call ──
        await js(`location.assign('/chat')`);
        await waitFor(`!!document.querySelector('textarea')`, 20_000);
        await newChat();
        const callsBeforeDeny = serverCalls("fix68_add_numbers").length;
        const consentFrom = log.lines.length;
        const d = await turn("Call the tool fix68_add_numbers with a=17 and b=25, then tell me exactly what it returned. Do not compute it yourself.", "deny", 180_000);
        await shot("deny");
        const denied = co.toolCallsSeen.some((t) => t.conversationId === d.convId && t.tool === "fix68_add_numbers" && t.decision === "denied");
        const callsAfterDeny = serverCalls("fix68_add_numbers").length;
        check("#4 the consent card was raised for the connector tool and Deny stopped it — the server never received the call", d.consents >= 1 && denied && callsAfterDeny === callsBeforeDeny, `consents=${d.consents} denied=${denied} serverCalls ${callsBeforeDeny}→${callsAfterDeny} reply="${d.reply.slice(0, 100)}"`);
        check("#4b the consent is the Network/connector class (danger.connector), i.e. it asks under \"Asks each action\"", since(consentFrom, /CONSENT REQUEST .*fix68_add_numbers .*danger=danger\.connector/).length > 0, lastLine(/CONSENT REQUEST .*fix68_/));

        // ── #3 Allow once — the call reaches the server and the result comes back ──
        await newChat();
        const a = await turn("Call the tool fix68_add_numbers with a=17 and b=25, then tell me exactly what it returned. Do not compute it yourself.", "allow-once", 180_000);
        await shot("allow");
        const ran = co.toolCallsSeen.find((t) => t.conversationId === a.convId && t.tool === "fix68_add_numbers" && t.ok);
        const call = serverCalls("fix68_add_numbers").slice(-1)[0];
        const args = (call?.["args"] ?? {}) as Record<string, unknown>;
        check("#3 Allow → the sidecar called the server (server log has the call with a=17,b=25) and the model's reply carries the result 42", a.consents >= 1 && !!ran && ran.where === "sidecar" && !!call && Number(args["a"]) === 17 && Number(args["b"]) === 25 && /42/.test(a.reply), `consents=${a.consents} ran=${!!ran} where=${ran?.where} serverArgs=${JSON.stringify(args)} reply="${a.reply.slice(0, 120)}" | ${lastLine(/TOOL fix68_add_numbers (ok|FAILED)/)}`);

        // ── #5 uninstall from the page — tools gone, process ended ────────
        await js(`location.assign('/chat/connectors')`);
        const cardBack = await waitFor(`!!document.querySelector('${cardSel}')`, 20_000);
        await js(`(() => { const b = document.querySelector('${cardSel} [data-testid="connector-uninstall"]'); if (b) b.click(); return !!b; })()`);
        const gone = await waitFor(`!document.querySelector('${cardSel}')`, 30_000);
        await sleep(1500);
        await shot("uninstalled");
        const toolsFinal = await sidecar.listTools();
        const exited = readServerLog().some((e) => e["kind"] === "exit");
        check("#5 Uninstall from the card → card gone, the server process ended (its log says exit), its tools left the model's list", cardBack && gone && exited && !toolsFinal.some((t) => t.name.startsWith("fix68_")), `cardBack=${cardBack} gone=${gone} exited=${exited} fix68ToolsLeft=${toolsFinal.filter((t) => t.name.startsWith("fix68_")).map((t) => t.name).join(",") || "none"} | ${lastLine(/CONNECTOR uninstalled/)}`);
        log.info(`FIX68 PROBE conversations: deny=${d.convId} allow=${a.convId}`);
        break;
      }

      case "fix69-remote": {
        // FIX69 — a REMOTE (https) MCP server with a key, through the page's own
        // "Add custom connector → Remote server" path. Run as:
        //   FIX68_NONCE=<random> FIX68_MCP_LOG=<file> FIX69_PROBE_URL=https://localhost:<port>
        //   FIX69_PROBE_KEY=<right key> FIX69_WRONG_KEY=<wrong key> FIX69_ACK_DIR=<dir>
        //   npx electron . --probe-e4 fix69-remote --probe-mcp-remote
        // against demo-mcp-server.mjs --http <port> (https via a locally trusted
        // cert, FIX69_KEY=<right key>). The host form is replaced by the
        // environment (read per click, so one run covers http-refused, wrong
        // key, right key); everything else — the page, the sidecar, the policy,
        // the consent card — is the real thing. The server's own log is the
        // second witness; the runner outside the app decrypts the DPAPI store
        // at two pauses (installed / uninstalled) and answers with ack files.
        const nonce = process.env["FIX68_NONCE"] ?? "";
        const serverLog = process.env["FIX68_MCP_LOG"] ?? "";
        const rightKey = process.env["FIX69_PROBE_KEY"] ?? "";
        const wrongKey = process.env["FIX69_WRONG_KEY"] ?? "";
        const httpsUrl = process.env["FIX69_PROBE_URL"] ?? "";
        const ackDir = process.env["FIX69_ACK_DIR"] ?? "";
        const readServerLog = (): Array<Record<string, unknown>> => {
          try {
            return fs.readFileSync(serverLog, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Record<string, unknown>);
          } catch {
            return [];
          }
        };
        const serverCalls = (tool: string) => readServerLog().filter((e) => e["kind"] === "request" && e["method"] === "tools/call" && e["tool"] === tool);
        const httpAuth = (state: string) => readServerLog().filter((e) => e["kind"] === "http" && e["auth"] === state).length;
        const origin = new URL(httpsUrl).origin;
        const connectorId = `custom-${origin.replace(/^https:\/\//, "").replace(/[^a-zA-Z0-9_-]/g, "-").toLowerCase()}`;
        const cardSel = `[data-testid="connector-card-${connectorId}"]`;
        const nonceTool = `fix68_nonce_${nonce}`;
        const waitFor = async (code: string, ms: number): Promise<boolean> => {
          const t0 = Date.now();
          while (Date.now() - t0 < ms) {
            if (await js(code)) return true;
            await sleep(300);
          }
          return false;
        };
        const ADD_BUTTON = `[...document.querySelectorAll('button')].find(b => /add custom connector|เพิ่ม connector เอง/i.test(b.textContent || ''))`;
        const clickRemote = async () => {
          await js(`(() => { const b = ${ADD_BUTTON}; if (b) b.click(); return !!b; })()`);
          await waitFor(`!!document.querySelector('[data-testid="add-custom-remote"]')`, 5_000);
          return js(`(() => { const b = document.querySelector('[data-testid="add-custom-remote"]'); if (b) b.click(); return !!b; })()`);
        };
        const bodyText = async () => String(await js(`document.body.innerText.replace(/\\s+/g, ' ')`));
        const pause = async (marker: string): Promise<boolean> => {
          if (!ackDir) return true;
          fs.writeFileSync(path.join(ackDir, `${marker}.marker`), new Date().toISOString());
          const t0 = Date.now();
          while (Date.now() - t0 < 90_000) {
            if (fs.existsSync(path.join(ackDir, `${marker}.ack`))) return true;
            await sleep(500);
          }
          return false;
        };
        // Evidence discipline: no check's evidence string may carry the key.
        const clean = (s: string) => (rightKey && s.includes(rightKey)) || (wrongKey && s.includes(wrongKey)) ? "<<EVIDENCE WITHHELD: contained a key>>" : s;

        await js(`location.assign('/chat/connectors')`);
        check("connectors page shows the Add-custom-connector button", await waitFor(`!!${ADD_BUTTON}`, 20_000), "");

        // ── tripwire: http is refused before any dial ────────────────────
        const httpFrom = log.lines.length;
        const httpBefore = readServerLog().length;
        process.env["FIX69_PROBE_URL"] = httpsUrl.replace(/^https:/, "http:");
        process.env["FIX69_PROBE_KEY"] = rightKey;
        await clickRemote();
        const httpToast = await waitFor(`/https/i.test(document.body.innerText) && /only|เท่านั้น/i.test(document.body.innerText)`, 15_000);
        await shot("http-refused");
        check("tripwire: a plain http URL is refused with the reason, and nothing was dialled", httpToast && since(httpFrom, /CONNECTOR remote REFUSED \(https-required\)/).length > 0 && readServerLog().length === httpBefore, clean(`toast=${httpToast} ${lastLine(/CONNECTOR remote REFUSED/)}`));
        await sleep(1500);

        // ── #5 wrong key → "key rejected", not "try again" ───────────────
        const wrongFrom = log.lines.length;
        process.env["FIX69_PROBE_URL"] = httpsUrl;
        process.env["FIX69_PROBE_KEY"] = wrongKey;
        await clickRemote();
        const rejectedToast = await waitFor(`/rejected the key|ปฏิเสธ key/i.test(document.body.innerText)`, 60_000);
        await shot("key-rejected");
        const wrongSeen = httpAuth("wrong");
        check("#5 a wrong key → the page says the server REJECTED THE KEY (server log: auth=wrong); no card, nothing installed", rejectedToast && since(wrongFrom, /CONNECTOR remote test FAILED \(key-rejected\)/).length > 0 && wrongSeen > 0 && !(await js(`!!document.querySelector('${cardSel}')`)), clean(`toast=${rejectedToast} serverAuthWrong=${wrongSeen} ${lastLine(/CONNECTOR remote test FAILED/)}`));
        await sleep(1500);

        // ── #1 + #2 right key → card with the LIVE tool list ──────────────
        const toolsBefore = await sidecar.listTools();
        const addFrom = log.lines.length;
        process.env["FIX69_PROBE_KEY"] = rightKey;
        await clickRemote();
        const cardShown = await waitFor(`!!document.querySelector('${cardSel}')`, 90_000);
        await sleep(500);
        await shot("remote-card");
        check("#1 the remote server was added through the page (card appears; host logged the add with the origin only)", cardShown && since(addFrom, /CONNECTOR added \(custom http\): /).length > 0, clean(lastLine(/CONNECTOR added \(custom http\)|test-failed|remote REFUSED/)));
        check("#1b the confirmation dialog named the exact origin and that a key is sent", since(addFrom, /PROBE confirmRemote dialog reached/).some((l) => l.includes(origin) && /Key: yes/.test(l)), clean(lastLine(/PROBE confirmRemote/).slice(0, 300)));
        const cardTools = String(await js(`((document.querySelector('${cardSel} [data-testid="connector-tools"]') || {}).textContent || '')`));
        const started = readServerLog().find((e) => e["kind"] === "start");
        const serverTools = Array.isArray(started?.["tools"]) ? (started!["tools"] as string[]) : [];
        check("#2 the card lists exactly what the LIVE https server announced, nonce-named tool included", nonce.length > 0 && serverTools.includes(nonceTool) && serverTools.every((t) => cardTools.includes(t)), `card="${cardTools}" server=${JSON.stringify(serverTools)}`);
        const toolsAfter = await sidecar.listTools();
        check("#2b the model's tool list gained them without a restart", !toolsBefore.some((t) => t.name === nonceTool) && serverTools.every((t) => toolsAfter.some((a) => a.name === t)), `before=${toolsBefore.length} after=${toolsAfter.length}`);
        const keyBadge = await js(`!!document.querySelector('${cardSel} [data-testid="connector-key-set"]')`);
        check("#4a the card says a key is SET — and nothing more", keyBadge === true, `badge=${String(keyBadge)}`);

        // ── #4 the key is nowhere it must not be ──────────────────────────
        const installed = await sidecar.hostQuery("connectors.listInstalled", {});
        const dtoJson = JSON.stringify(installed);
        const pageText = await bodyText();
        const inHostLog = log.lines.some((l) => l.includes(rightKey) || l.includes(wrongKey));
        check("#4 the key appears in NO host log line, NOT in the connectors DTO the page receives, NOT in the page text", !inHostLog && !dtoJson.includes(rightKey) && !pageText.includes(rightKey), `hostLogLines=${log.lines.length} dtoHasKey=${dtoJson.includes(rightKey)} pageHasKey=${pageText.includes(rightKey)}`);
        check("#4b (runner) DPAPI store holds the key encrypted — checked outside the app at this pause", await pause("installed"), "see the runner's output for the decrypt-as-user check");

        // ── #3 the gate: Deny first, then Allow ───────────────────────────
        await js(`location.assign('/chat')`);
        await waitFor(`!!document.querySelector('textarea')`, 20_000);
        await newChat();
        const callsBeforeDeny = serverCalls("fix68_add_numbers").length;
        const consentFrom = log.lines.length;
        const d = await turn("Call the tool fix68_add_numbers with a=17 and b=25, then tell me exactly what it returned. Do not compute it yourself.", "deny", 180_000);
        await shot("deny");
        const denied = co.toolCallsSeen.some((t) => t.conversationId === d.convId && t.tool === "fix68_add_numbers" && t.decision === "denied");
        check("#3 the consent card was raised for the remote tool and Deny stopped it — the https server never received the call", d.consents >= 1 && denied && serverCalls("fix68_add_numbers").length === callsBeforeDeny, clean(`consents=${d.consents} denied=${denied} serverCalls ${callsBeforeDeny}→${serverCalls("fix68_add_numbers").length} reply="${d.reply.slice(0, 80)}"`));
        check("#3b the consent is the Network/connector class (danger.connector)", since(consentFrom, /CONSENT REQUEST .*fix68_add_numbers .*danger=danger\.connector/).length > 0, lastLine(/CONSENT REQUEST .*fix68_/));
        await newChat();
        const a = await turn("Call the tool fix68_add_numbers with a=17 and b=25, then tell me exactly what it returned. Do not compute it yourself.", "allow-once", 180_000);
        await shot("allow");
        const ran = co.toolCallsSeen.find((t) => t.conversationId === a.convId && t.tool === "fix68_add_numbers" && t.ok);
        const call = serverCalls("fix68_add_numbers").slice(-1)[0];
        const args = (call?.["args"] ?? {}) as Record<string, unknown>;
        check("#3c Allow → the sidecar called the https server WITH the key (server log: auth ok, args a=17,b=25) and the reply carries 42", a.consents >= 1 && !!ran && !!call && Number(args["a"]) === 17 && Number(args["b"]) === 25 && /42/.test(a.reply) && httpAuth("wrong") === wrongSeen, clean(`ran=${!!ran} serverArgs=${JSON.stringify(args)} reply="${a.reply.slice(0, 100)}"`));

        // ── #6 uninstall ──────────────────────────────────────────────────
        await js(`location.assign('/chat/connectors')`);
        await waitFor(`!!document.querySelector('${cardSel}')`, 20_000);
        await js(`(() => { const b = document.querySelector('${cardSel} [data-testid="connector-uninstall"]'); if (b) b.click(); return !!b; })()`);
        const gone = await waitFor(`!document.querySelector('${cardSel}')`, 30_000);
        await sleep(1000);
        await shot("uninstalled");
        const toolsFinal = await sidecar.listTools();
        check("#6 Uninstall → card gone, tools left the model's list", gone && !toolsFinal.some((t) => t.name.startsWith("fix68_")), `gone=${gone} left=${toolsFinal.filter((t) => t.name.startsWith("fix68_")).map((t) => t.name).join(",") || "none"}`);
        check("#6b (runner) the key is gone from the DPAPI store — checked outside the app at this pause", await pause("uninstalled"), "see the runner's output");
        const finalLeak = log.lines.some((l) => l.includes(rightKey) || l.includes(wrongKey));
        check("#4c after everything, the host log still carries no key", !finalLeak, `lines=${log.lines.length}`);
        log.info(`FIX69 PROBE conversations: deny=${d.convId} allow=${a.convId}`);
        break;
      }

      case "fix70-browser": {
        // FIX70 — the Chrome extension bridge, end to end, on the REAL bridge:
        // the installed extension (id derived from its folder), the sidecar's
        // native-messaging manifest, the relay. Needs Chrome open with the
        // extension loaded and a tab on https://example.com (the runner opens
        // it). Checks: connected → the model reads a tab behind the per-site
        // consent card (Deny first, then Allow) → the pairing token is rotated
        // and the old one is refused while the extension self-heals.
        // The extension reconnects on its own (1 s backoff, then a 1-minute alarm)
        // after the previous host's pipe went away — give it up to 90 s.
        let status0 = await sidecar.status();
        for (let i = 0; i < 90 && status0["browserConnected"] !== true; i++) {
          await sleep(1000);
          status0 = await sidecar.status();
        }
        const connected0 = status0["browserConnected"] === true;
        check("#1/#2 the bridge is CONNECTED (extension → relay → sidecar; the page's Settings dot reads the same flag)", connected0, `browserConnected=${String(status0["browserConnected"])} folder=${String(status0["browserExtensionFolder"])}`);
        await js(`location.assign('/chat/settings')`);
        await sleep(2500);
        const settingsText = String(await js(`(document.querySelector('[data-testid="browser-status"]') || {}).textContent || ''`));
        await shot("settings-browser");
        check("#1 Settings → Browser says Connected", /connected|เชื่อมต่อแล้ว/i.test(settingsText) && !/not connected|ยังไม่/i.test(settingsText), `status="${settingsText}"`);

        // ── #3 + #4: read a tab, gated by the per-site consent card ────────
        await js(`location.assign('/chat')`);
        await sleep(2000);
        await newChat();
        const consentFrom = log.lines.length;
        const d = await turn("Open https://example.com/ in a new browser tab with browser_navigate, then read that tab with browser_read_page and tell me the exact main heading text of the page. Do not guess it.", "deny", 180_000);
        await shot("browser-deny");
        const denied = co.toolCallsSeen.some((t) => t.conversationId === d.convId && /^browser_(navigate|read_page)$/.test(t.tool) && t.decision === "denied");
        const anyRan = co.toolCallsSeen.some((t) => t.conversationId === d.convId && /^browser_(navigate|read_page)$/.test(t.tool) && t.ok);
        check("#4 the per-site consent card was raised for the browser action and Deny stopped it (nothing navigated, nothing read)", d.consents >= 1 && denied && !anyRan, `consents=${d.consents} denied=${denied} anyRan=${anyRan} reply="${d.reply.slice(0, 100)}" | ${lastLine(/CONSENT REQUEST .*browser_/)}`);
        await newChat();
        const a = await turn("Open https://example.com/ in a new browser tab with browser_navigate, then read that tab with browser_read_page and tell me the exact main heading text of the page. Do not guess it.", "allow-once", 180_000);
        await shot("browser-allow");
        // The model may open the page with browser_navigate or with browser_tabs
        // {action:"new", url} — both are consented per site; either counts.
        const navigated = co.toolCallsSeen.some((t) => t.conversationId === a.convId && (t.tool === "browser_navigate" || (t.tool === "browser_tabs" && String(t.args["url"] ?? "").startsWith("https://example.com"))) && t.ok);
        const read = co.toolCallsSeen.find((t) => t.conversationId === a.convId && t.tool === "browser_read_page" && t.ok);
        check("#3 Allow → the page was opened (navigate or tabs) and browser_read_page ran over the bridge; the reply carries the page's heading", navigated && !!read && /Example Domain/i.test(a.reply), `opened=${navigated} read=${!!read} reply="${a.reply.slice(0, 120)}" | ${lastLine(/TOOL browser_read_page (ok|FAILED)/)}`);
        check("#4b the consent named the site (origin) — FIX6 per-site gate", since(consentFrom, /CONSENT REQUEST .*browser_(navigate|read_page) .*site=https:\/\/example\.com/).length > 0, lastLine(/CONSENT REQUEST .*browser_/));
        // tidy: close the tab the probe opened in the owner's Chrome (consented like any action)
        await newChat();
        const c = await turn("Close the browser tab whose URL is https://example.com/ (use browser_list_tabs then browser_tabs). Reply with one word when done.", "allow-once", 120_000);
        log.info(`FIX70 PROBE tidy: closed example.com tab → consents=${c.consents} reply="${c.reply.slice(0, 60)}"`);

        // ── #5: rotate the pairing token; the old one must be refused ──────
        const rotFrom = log.lines.length;
        const rot = await sidecar.hostQuery("browser.rotatePairingToken", {});
        const rotData = (rot.data ?? {}) as Record<string, unknown>;
        check("#5a rotate op answered (no token in the answer)", rot.ok && rotData["rotated"] === true && !JSON.stringify(rot).match(/[0-9a-f]{48}/), JSON.stringify(rot).slice(0, 160));
        // the extension reconnects on its own: first with what it holds, then with pairing.json
        let reconnected = false;
        const t0 = Date.now();
        while (Date.now() - t0 < 120_000) {
          const st = await sidecar.status();
          if (st["browserConnected"] === true) {
            reconnected = true;
            break;
          }
          await sleep(1000);
        }
        const rejectedOld = since(rotFrom, /pairing token mismatch/).length;
        await shot("after-rotate");
        check("#5 after rotation the bridge is connected again with the NEW token (old-token hellos, if any, were refused: see count)", reconnected, `reconnected=${reconnected} in ${Math.round((Date.now() - t0) / 1000)}s oldTokenRejected=${rejectedOld} | ${lastLine(/token rotated|Browser relay connected/)}`);
        log.info(`FIX70 PROBE conversations: deny=${d.convId} allow=${a.convId}`);
        break;
      }

      default:
        check(`unknown probe '${name}'`, false, "");
    }
  } catch (err) {
    check("probe threw", false, (err as Error).message);
  }
  finish();
}

function countProcesses(imageName: string): number {
  try {
    const out = require("node:child_process").execSync(`tasklist /FI "IMAGENAME eq ${imageName}" /NH`, { encoding: "utf8" }) as string;
    return out.split("\n").filter((l) => l.toUpperCase().includes(imageName.toUpperCase())).length;
  } catch {
    return -1;
  }
}
function processWorkingSetMB(pid: number): number {
  try {
    const out = require("node:child_process").execSync(`powershell -NoProfile -Command "(Get-Process -Id ${pid}).WorkingSet64"`, { encoding: "utf8" }) as string;
    return Math.round((Number(out.trim()) / 1024 / 1024) * 10) / 10;
  } catch {
    return -1;
  }
}
