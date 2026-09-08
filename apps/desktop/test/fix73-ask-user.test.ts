// FIX73 — ask_user_question: the model asks the user, the user answers, the
// answer comes back as the tool result. Pins (each goes red if the behaviour
// is removed):
//   • the card is posted as question.request (its OWN type, never
//     consent.request) with the validated questions, and the answer returns
//     as the tool result of the same call, labels verbatim;
//   • "Something else" text returns as the user's words (never "Other");
//     a free reply returns as "The user responded: …";
//   • Skip = decline: the model is told to proceed on its own; the turn goes on;
//   • Stop while the card is open → cancelled, no answer invented;
//   • waiting is excluded from the run deadline (FIX31 rule);
//   • one card at a time per conversation (a second question is posted only
//     after the first is answered) — sharing the consent queue;
//   • a pending question is re-posted on ui.ready (reload);
//   • a stored allow_always + approval mode "skip" never answer it;
//   • not offered to a nested (sub-agent) run or a scheduled task, and a
//     call from one is refused — no card;
//   • the sub-agent prompt still says it cannot ask;
//   • a model-emitted "Other" option / >4 questions / 1 option is a tool
//     ERROR, not a card;
//   • a quick-ask turn has no tools, so no card can appear there.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { GatewayMessage, GatewayToolDefinition } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { baseSystemPrompt, subAgentSystemPrompt } from "../src/main/prompt-assembler";
import { TurnCoordinator } from "../src/main/turn-coordinator";
import { ASK_LIMITS, ASK_USER_TOOL, ASK_USER_TOOL_DEFINITION, displayLength, formatAnswerForModel, validateAskArgs } from "../src/main/ask-user";
import { validateQuestionRespond, TURN_INBOUND_TYPES, TURN_OUTBOUND_TYPES } from "../src/shared/bridge-protocol";

const lines: string[] = [];
const log: Logger = { info: (m: string) => { lines.push(m); }, warn: (m: string) => { lines.push(m); }, error: (m: string) => { lines.push(m); }, filePath: null, attachFile: () => {}, lines: [] };
type Ev = { kind: string; [k: string]: unknown };

const ONE_Q = { questions: [{ question: "Which format?", header: "Format", options: [{ label: "Summary", description: "Brief" }, { label: "Detailed", description: "Full" }] }] };
const MULTI_Q = { questions: [{ question: "Which sections?", header: "Sections", multiSelect: true, options: [{ label: "Intro" }, { label: "Body" }, { label: "Appendix" }] }] };

/** A model that asks once, then reports the tool result verbatim. */
function askingGateway(args: unknown = ONE_Q, extra?: { secondAsk?: unknown }) {
  const requests: { tools: string[]; messages: GatewayMessage[] }[] = [];
  let asked = 0;
  return {
    requests,
    async listModels() { return new Map([["m", { tools: true }]]); },
    async *streamCompletion(req: { messages: GatewayMessage[]; tools?: GatewayToolDefinition[] }): AsyncGenerator<Ev> {
      requests.push({ tools: (req.tools ?? []).map((t) => t.function.name), messages: req.messages });
      const last = req.messages[req.messages.length - 1]!;
      const sys = String(req.messages[0]?.content ?? "");
      if (sys.startsWith("You are a focused sub-agent")) {
        if (last.role === "user") {
          yield { kind: "tool_call_delta", index: 0, id: "call_sub_ask", name: ASK_USER_TOOL, argumentsDelta: JSON.stringify(ONE_Q) };
          yield { kind: "done", finish_reason: "tool_calls" };
          return;
        }
        yield { kind: "content_delta", delta: `SUB:${String(last.content)}` };
        yield { kind: "done", finish_reason: "stop" };
        return;
      }
      if (last.role === "user" || (extra?.secondAsk && asked === 1 && last.role === "tool")) {
        const a = asked === 0 ? args : extra!.secondAsk;
        asked++;
        yield { kind: "tool_call_delta", index: 0, id: `call_ask_${asked}`, name: ASK_USER_TOOL, argumentsDelta: JSON.stringify(a) };
        yield { kind: "done", finish_reason: "tool_calls" };
        return;
      }
      yield { kind: "content_delta", delta: `Result: ${String(last.content)}` };
      yield { kind: "done", finish_reason: "stop" };
    },
  };
}

function build(gw: ReturnType<typeof askingGateway>, opts: { approvalMode?: "manual" | "auto" | "skip"; keepaliveMs?: number; persisted?: Record<string, string> } = {}) {
  lines.length = 0;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix73-"));
  if (opts.persisted) fs.writeFileSync(path.join(tmp, "permissions.json"), JSON.stringify(opts.persisted));
  const posts: { type: string; payload: Record<string, unknown> }[] = [];
  const events: { conv: string; event: string; data: Record<string, unknown> }[] = [];
  const bridge = {
    postTurnEvent: (conv: string, _seq: number, event: string, data: unknown) => events.push({ conv, event, data: data as Record<string, unknown> }),
    post: (type: string, payload: unknown) => posts.push({ type, payload: payload as Record<string, unknown> }),
    postHostResult: () => {},
    postHostError: () => {},
  };
  const sidecar = {
    listTools: async () => [{ name: "run_command", description: "run a shell command", inputSchema: { type: "object" } }],
    callTool: async () => ({ ok: true, text: "ran", imageDataUri: null }),
    hostQuery: async (op: string) => (op === "settings.get" ? { ok: true, data: { approvalMode: opts.approvalMode ?? "manual" } } : { ok: true, data: {} }),
  };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
  co = new TurnCoordinator(
    bridge as never, gw as never, {} as never, {} as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "m", approvalMode: opts.approvalMode ?? "manual", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: opts.keepaliveMs ?? 20 },
    log,
  );
  return { co, posts, events, tmp };
}

async function startRun(h: ReturnType<typeof build>, o: { maxMs?: number; nested?: boolean; messages?: GatewayMessage[]; tools?: GatewayToolDefinition[] } = {}) {
  const ctrl = new AbortController();
  let text = "";
  const c = h.co as unknown as { toolDefinitions: (a: null, b: boolean) => Promise<GatewayToolDefinition[]>; runLoop: (r: unknown) => Promise<string>; refreshAcceptedModels: (s: AbortSignal) => Promise<void> };
  await c.refreshAcceptedModels(ctrl.signal);
  const tools = o.tools ?? (await c.toolDefinitions(null, o.nested ?? false));
  const messages: GatewayMessage[] = o.messages ?? [{ role: "system", content: "sys" }, { role: "user", content: "write the report" }];
  const outcome = c.runLoop({ conversationId: "conv-1", model: "m", messages, signal: ctrl.signal, maxIterations: 10, maxMs: o.maxMs ?? 60_000, tools, parentToolCallId: null, onText: (t: string) => { text += t; } });
  return { ctrl, outcome, messages, tools, text: () => text };
}

async function until(pred: () => boolean, ms = 5000): Promise<void> {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await new Promise((r) => setTimeout(r, 5));
  }
}
const questionPosts = (h: { posts: { type: string; payload: Record<string, unknown> }[] }) => h.posts.filter((p) => p.type === "question.request").map((p) => p.payload);
const toolResultFor = (messages: GatewayMessage[], id: string) => messages.find((m) => m.role === "tool" && m.tool_call_id === id);

// FIX88 Part E — the header limit counts what the user sees, so a Thai
// header is not punished for its vowel and tone marks.
describe("FIX88 — the header limit is measured as displayed", () => {
  it("หัวข้อเรียงความ is 15 code points and 12 on screen — and validates", () => {
    expect([..."หัวข้อเรียงความ"].length).toBe(15);
    expect(displayLength("หัวข้อเรียงความ")).toBe(12);
    const r = validateAskArgs({ questions: [{ question: "หัวข้อ?", header: "หัวข้อเรียงความ", options: [{ label: "ก" }, { label: "ข" }] }] });
    expect(r.ok).toBe(true);
  });
  it("a header longer than 12 as displayed is still refused, and the schema carries no code-point maxLength", () => {
    const r = validateAskArgs({ questions: [{ question: "Q", header: "หัวข้อเรียงความยาว", options: [{ label: "ก" }, { label: "ข" }] }] });
    expect(r.ok).toBe(false);
    expect((r as { reason: string }).reason).toMatch(/header is longer than 12/);
    const header = (ASK_USER_TOOL_DEFINITION.function.parameters as { properties: { questions: { items: { properties: { header: Record<string, unknown> } } } } }).properties.questions.items.properties.header;
    expect(header.maxLength).toBeUndefined();
    expect(String(header.description)).toMatch(/as displayed/);
  });
});

describe("FIX73 — the card and the answer", () => {
  it("posts question.request (never consent.request) with the validated questions, and the chosen label comes back as the tool result", async () => {
    const gw = askingGateway();
    const h = build(gw);
    const r = await startRun(h);
    await until(() => questionPosts(h).length === 1);
    expect(h.posts.some((p) => p.type === "consent.request")).toBe(false);
    const req = questionPosts(h)[0]!;
    expect(req.conversationId).toBe("conv-1");
    expect(req.toolCallId).toBe("call_ask_1");
    expect(req.questions).toEqual([{ question: "Which format?", header: "Format", multiSelect: false, options: [{ label: "Summary", description: "Brief" }, { label: "Detailed", description: "Full" }] }]);
    // the page says what it is waiting on, as a status frame, not a consent one
    expect(h.events.some((e) => e.event === "status" && e.data.waiting === "question" && e.data.promptId === req.promptId)).toBe(true);
    h.co.onQuestionRespond({ promptId: String(req.promptId), outcome: "answered", answers: { "Which format?": ["Detailed"] }, response: null });
    expect(await r.outcome).toBe("done");
    expect(toolResultFor(r.messages, "call_ask_1")?.content).toBe('"Which format?": Detailed');
    expect(r.text()).toContain("Detailed");
    expect(h.events.some((e) => e.event === "status" && e.data.status === "question_answered" && e.data.outcome === "answered")).toBe(true);
    expect(h.events.some((e) => e.event === "tool_activity" && e.data.tool === ASK_USER_TOOL && e.data.phase === "finished" && e.data.success === true)).toBe(true);
  });

  it("multi-select joins the labels; 'Something else' text returns as the user's words, never the word Other", async () => {
    const gw = askingGateway(MULTI_Q);
    const h = build(gw);
    const r = await startRun(h);
    await until(() => questionPosts(h).length === 1);
    const req = questionPosts(h)[0]!;
    h.co.onQuestionRespond({ promptId: String(req.promptId), outcome: "answered", answers: { "Which sections?": ["Intro", "Appendix", "a glossary too"] }, response: null });
    expect(await r.outcome).toBe("done");
    const content = String(toolResultFor(r.messages, "call_ask_1")?.content);
    expect(content).toBe('"Which sections?": Intro, Appendix, a glossary too');
    expect(content).not.toMatch(/other/i);
  });

  it("a free reply typed instead of answering returns as 'The user responded: …' with no per-question list", async () => {
    const gw = askingGateway();
    const h = build(gw);
    const r = await startRun(h);
    await until(() => questionPosts(h).length === 1);
    const req = questionPosts(h)[0]!;
    h.co.onQuestionRespond({ promptId: String(req.promptId), outcome: "answered", answers: { "Which format?": ["Summary"] }, response: "Actually make it a slide deck" });
    expect(await r.outcome).toBe("done");
    const content = String(toolResultFor(r.messages, "call_ask_1")?.content);
    expect(content).toBe('The user responded: "Actually make it a slide deck"');
    expect(content).not.toContain("Which format?");
  });

  it("Skip = decline: the model is told nobody chose and to proceed; the turn continues (not cancelled)", async () => {
    const gw = askingGateway();
    const h = build(gw);
    const r = await startRun(h);
    await until(() => questionPosts(h).length === 1);
    const req = questionPosts(h)[0]!;
    h.co.onQuestionRespond({ promptId: String(req.promptId), outcome: "skipped", answers: {}, response: null });
    expect(await r.outcome).toBe("done");
    expect(String(toolResultFor(r.messages, "call_ask_1")?.content)).toMatch(/^The user chose not to answer\. Proceed on your own judgment/);
    expect(h.events.some((e) => e.event === "status" && e.data.status === "question_answered" && e.data.outcome === "skipped")).toBe(true);
  });

  it("Stop while the card is open → the question is cancelled, no answer is invented, the run ends cancelled", async () => {
    const gw = askingGateway();
    const h = build(gw);
    const r = await startRun(h);
    await until(() => questionPosts(h).length === 1);
    r.ctrl.abort();
    h.co.onTurnCancel({ conversationId: "conv-1" });
    expect(await r.outcome).toBe("cancelled");
    expect(String(toolResultFor(r.messages, "call_ask_1")?.content)).toBe("Not executed: the turn was cancelled.");
    expect(h.events.some((e) => e.event === "status" && e.data.status === "question_answered" && e.data.outcome === "cancelled")).toBe(true);
    // a late answer to the closed prompt is ignored, not applied
    const req = questionPosts(h)[0]!;
    h.co.onQuestionRespond({ promptId: String(req.promptId), outcome: "answered", answers: { "Which format?": ["Summary"] }, response: null });
    expect(lines.some((l) => /question\.respond for unknown\/expired prompt/.test(l))).toBe(true);
  });

  it("waiting for the answer does not count against the run deadline (FIX31)", async () => {
    const gw = askingGateway();
    const h = build(gw);
    const r = await startRun(h, { maxMs: 150 });
    await until(() => questionPosts(h).length === 1);
    await new Promise((res) => setTimeout(res, 400)); // well past maxMs
    const req = questionPosts(h)[0]!;
    h.co.onQuestionRespond({ promptId: String(req.promptId), outcome: "answered", answers: { "Which format?": ["Summary"] }, response: null });
    expect(await r.outcome).toBe("done");
    expect(r.text()).toContain("Summary");
  });

  it("one card at a time: a second question is posted only after the first is answered", async () => {
    const second = { questions: [{ question: "Which audience?", header: "Audience", options: [{ label: "Execs" }, { label: "Engineers" }] }] };
    const gw = askingGateway(ONE_Q, { secondAsk: second });
    const h = build(gw);
    const r = await startRun(h);
    await until(() => questionPosts(h).length === 1);
    await new Promise((res) => setTimeout(res, 60));
    expect(questionPosts(h)).toHaveLength(1);
    h.co.onQuestionRespond({ promptId: String(questionPosts(h)[0]!.promptId), outcome: "answered", answers: { "Which format?": ["Summary"] }, response: null });
    await until(() => questionPosts(h).length === 2);
    h.co.onQuestionRespond({ promptId: String(questionPosts(h)[1]!.promptId), outcome: "answered", answers: { "Which audience?": ["Execs"] }, response: null });
    expect(await r.outcome).toBe("done");
    expect(toolResultFor(r.messages, "call_ask_2")?.content).toBe('"Which audience?": Execs');
  });

  it("a pending question survives a page reload: ui.ready re-posts it, and the re-posted id still answers", async () => {
    const gw = askingGateway();
    const h = build(gw);
    const r = await startRun(h);
    await until(() => questionPosts(h).length === 1);
    h.co.onUiReady({ conversationId: "conv-1", lastSeq: 0, runId: null });
    expect(questionPosts(h)).toHaveLength(2);
    expect(questionPosts(h)[1]!.promptId).toBe(questionPosts(h)[0]!.promptId);
    expect(lines.some((l) => /re-posting pending question/.test(l))).toBe(true);
    h.co.onQuestionRespond({ promptId: String(questionPosts(h)[1]!.promptId), outcome: "answered", answers: { "Which format?": ["Summary"] }, response: null });
    expect(await r.outcome).toBe("done");
  });
});

describe("FIX73 — what can never answer it", () => {
  it("a stored allow_always and approval mode 'skip' still post the card — the app never answers for the user", async () => {
    const gw = askingGateway();
    const h = build(gw, { approvalMode: "skip", persisted: { [`${ASK_USER_TOOL}::*`]: "allow_always", [`${ASK_USER_TOOL}::`]: "allow_always" } });
    const r = await startRun(h);
    await until(() => questionPosts(h).length === 1);
    // nothing answered it on its own: still waiting after a while
    await new Promise((res) => setTimeout(res, 80));
    expect(r.messages.some((m) => m.role === "tool")).toBe(false);
    h.co.onQuestionRespond({ promptId: String(questionPosts(h)[0]!.promptId), outcome: "answered", answers: { "Which format?": ["Summary"] }, response: null });
    expect(await r.outcome).toBe("done");
  });

  it("is not offered to a nested run, a nested allowlist naming it changes nothing, and a sub-agent's call is refused without a card", async () => {
    const gw = askingGateway();
    const h = build(gw);
    const c = h.co as unknown as { toolDefinitions: (a: string[] | null, b: boolean) => Promise<GatewayToolDefinition[]> };
    const main = (await c.toolDefinitions(null, false)).map((t) => t.function.name);
    const nested = (await c.toolDefinitions(null, true)).map((t) => t.function.name);
    const nestedAllow = (await c.toolDefinitions([ASK_USER_TOOL, "run_command"], true)).map((t) => t.function.name);
    expect(main).toContain(ASK_USER_TOOL);
    expect(nested).not.toContain(ASK_USER_TOOL);
    expect(nestedAllow).not.toContain(ASK_USER_TOOL);
    // a sub-agent that names it anyway is refused — no card, honest error
    const result = await h.co.runNested({ conversationId: "conv-1", parentToolCallId: "call_parent", task: "ask the user which format", allowedTools: null } as never, new AbortController().signal);
    expect(questionPosts(h)).toHaveLength(0);
    // withheld → the sub-agent has no such tool ("unknown tool"); the dispatch
    // guard ("not available in this run") is the second fence for a list that
    // still carried it. Either way: an honest error, never a card.
    expect(result.answer).toMatch(/^SUB:Error: (unknown tool 'ask_user_question'|ask_user_question is not available in this run)/);
  });

  it("is not offered while a scheduled task is firing (nobody is there), and a call then is refused without a card", async () => {
    const gw = askingGateway();
    const h = build(gw);
    const c = h.co as unknown as { toolDefinitions: (a: null, b: boolean) => Promise<GatewayToolDefinition[]> };
    const attended = await c.toolDefinitions(null, false); // built before the task fired: carries the tool
    (h.co as unknown as { activeTaskId: string | null }).activeTaskId = "task-1";
    const names = (await c.toolDefinitions(null, false)).map((t) => t.function.name);
    expect(names).not.toContain(ASK_USER_TOOL);
    // the model names it anyway, from the attended list — the dispatch guard refuses it
    const r = await startRun(h, { tools: attended });
    expect(await r.outcome).toBe("done");
    expect(questionPosts(h)).toHaveLength(0);
    expect(String(toolResultFor(r.messages, "call_ask_1")?.content)).toMatch(/not available in this run/);
    (h.co as unknown as { activeTaskId: string | null }).activeTaskId = null;
  });

  it("the main prompt teaches asking as rare and preference-only, names the tool, and forbids a model 'Other'; a tool-less prompt and the sub-agent prompt carry none of it", () => {
    const withTools = baseSystemPrompt("C:\home", true, []);
    expect(withTools).toContain("## Asking the user (rare)");
    expect(withTools).toContain("Ask ONLY about a matter of the user's preference or intent that you cannot determine yourself");
    expect(withTools).toContain("the scope, the audience, the format, or the destination of the work");
    expect(withTools).toMatch(/NEVER about\s+something you could look up, read from the files or the conversation, or reasonably assume/);
    expect(withTools).toContain("Use the ask_user_question tool for that question");
    expect(withTools).toContain('do not add an "Other" option yourself');
    expect(baseSystemPrompt("C:\home", false, [])).not.toContain("ask_user_question");
    expect(subAgentSystemPrompt("C:\home", [])).not.toContain("ask_user_question");
  });

  it("the sub-agent prompt still says it cannot ask, and a quick-ask turn offers no tools", () => {
    expect(subAgentSystemPrompt("C:\\\\home", [])).toContain("You cannot talk to the user: never ask clarifying questions.");
    // FIX34: a quick turn is answered with NO tools (turn-coordinator: modelTakesTools = payload.quick ? false : …)
    const src = fs.readFileSync(path.resolve(__dirname, "..", "src", "main", "turn-coordinator.ts"), "utf8");
    expect(src).toContain("const modelTakesTools = payload.quick ? false :");
  });
});

describe("FIX73 — the contract is enforced before any card", () => {
  it("rejects a model-emitted 'Other' option, too many questions, too few options, an over-long header, duplicate labels", () => {
    expect(validateAskArgs({ questions: [{ question: "Q", header: "H", options: [{ label: "A" }, { label: "Other" }] }] })).toMatchObject({ ok: false, reason: expect.stringMatching(/do not offer an 'Other' choice/) });
    expect(validateAskArgs({ questions: [{ question: "Q", header: "H", options: [{ label: "A" }, { label: "อื่นๆ" }] }] })).toMatchObject({ ok: false });
    expect(validateAskArgs({ questions: Array.from({ length: 5 }, (_, i) => ({ question: `Q${i}`, header: `H${i}`, options: [{ label: "A" }, { label: "B" }] })) })).toMatchObject({ ok: false, reason: expect.stringMatching(/at most 4 questions/) });
    expect(validateAskArgs({ questions: [{ question: "Q", header: "H", options: [{ label: "A" }] }] })).toMatchObject({ ok: false, reason: expect.stringMatching(/2–4 choices/) });
    expect(validateAskArgs({ questions: [{ question: "Q", header: "A header that is too long", options: [{ label: "A" }, { label: "B" }] }] })).toMatchObject({ ok: false, reason: expect.stringMatching(/header is longer than 12/) });
    expect(validateAskArgs({ questions: [{ question: "Q", header: "H", options: [{ label: "A" }, { label: "a" }] }] })).toMatchObject({ ok: false, reason: expect.stringMatching(/repeats an earlier option/) });
    expect(validateAskArgs({ questions: [{ question: "x".repeat(ASK_LIMITS.MAX_QUESTION + 1), header: "H", options: [{ label: "A" }, { label: "B" }] }] })).toMatchObject({ ok: false, reason: expect.stringMatching(/longer than 500/) });
    expect(validateAskArgs(ONE_Q)).toMatchObject({ ok: true });
  });

  it("an invalid call is a tool ERROR back to the model — no card is posted", async () => {
    const bad = { questions: [{ question: "Which?", header: "Which", options: [{ label: "This" }, { label: "Something else" }] }] };
    const gw = askingGateway(bad);
    const h = build(gw);
    const r = await startRun(h);
    expect(await r.outcome).toBe("done");
    expect(questionPosts(h)).toHaveLength(0);
    expect(String(toolResultFor(r.messages, "call_ask_1")?.content)).toMatch(/^Error: ask_user_question: .*'Other' choice/);
  });

  it("the tool definition carries the documented counts and the header limit, and forbids 'Other' in its description", () => {
    const p = ASK_USER_TOOL_DEFINITION.function.parameters as { properties: { questions: { maxItems: number; items: { properties: { header: { maxLength?: number; description: string }; options: { minItems: number; maxItems: number } } } } } };
    expect(p.properties.questions.maxItems).toBe(4);
    expect(p.properties.questions.items.properties.options.minItems).toBe(2);
    expect(p.properties.questions.items.properties.options.maxItems).toBe(4);
    // FIX88 Part E — the header limit is stated in the description (as
    // displayed) and enforced by validateAskArgs; no code-point maxLength.
    expect(p.properties.questions.items.properties.header.maxLength).toBeUndefined();
    expect(p.properties.questions.items.properties.header.description).toMatch(/at most 12 characters as displayed/);
    expect(ASK_LIMITS.MAX_HEADER).toBe(12);
    expect(ASK_USER_TOOL_DEFINITION.function.description).toMatch(/Do not add an 'Other' option/);
  });

  it("formatAnswerForModel: labels verbatim, (no answer) for a skipped question, response wins over picks", () => {
    const qs = validateAskArgs({ questions: [ONE_Q.questions[0], { question: "Audience?", header: "Audience", options: [{ label: "Execs" }, { label: "Devs" }] }] });
    if (!qs.ok) throw new Error(qs.reason);
    expect(formatAnswerForModel(qs.questions, { promptId: "p", outcome: "answered", answers: { "Which format?": ["Summary"] }, response: null })).toBe('"Which format?": Summary\n"Audience?": (no answer)');
    expect(formatAnswerForModel(qs.questions, { promptId: "p", outcome: "answered", answers: { "Which format?": ["Summary"] }, response: "  just do both  " })).toBe('The user responded: "just do both"');
  });

  it("the bridge validates question.respond: closed outcome, bounded answers, and the pair is in the closed vocabulary", () => {
    expect(validateQuestionRespond({ promptId: "q1", outcome: "answered", answers: { "Which?": ["A"] }, response: null })).toMatchObject({ ok: true });
    expect(validateQuestionRespond({ promptId: "q1", outcome: "maybe", answers: {} })).toMatchObject({ ok: false, reason: "outcome-invalid" });
    expect(validateQuestionRespond({ promptId: "q1", outcome: "answered", answers: { "Which?": "A" } })).toMatchObject({ ok: false, reason: "answer-not-array" });
    expect(validateQuestionRespond({ promptId: "q1", outcome: "answered", answers: {}, response: "x".repeat(2001) })).toMatchObject({ ok: false, reason: "response-too-long" });
    expect(validateQuestionRespond({ promptId: "", outcome: "skipped" })).toMatchObject({ ok: false, reason: "promptId-missing" });
    expect(TURN_INBOUND_TYPES).toContain("question.respond");
    expect(TURN_OUTBOUND_TYPES).toContain("question.request");
  });
});
