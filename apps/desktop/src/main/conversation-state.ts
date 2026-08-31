// FIX33 A-D1 / A-D2 — the desktop conversation state: store, extractor,
// renderer. Closes FIX32 divergence #3 (the desktop trimmed but never
// summarised).
//
// STORE (A-D1). One JSON file per conversation under
// <userData>/conversation-state/, holding the SAME fields as
// chat.conversation_states (migration 015 + 037) so the day desktop turns
// route through chat-edge the rows move across unchanged. Chosen over a
// SQLite table in the sidecar because the extraction call must be made by
// the Electron host — it holds the session and the gateway key; the sidecar
// deliberately holds neither (FIX3 §4) — and the state is consumed at
// message-build time in the host. A sidecar store would add a host↔sidecar
// round trip per turn and put summary text in the process that runs tools.
//
// EXTRACTOR (A-D2). Behind an interface (StateExtractor) so the
// implementation can become "ask chat-edge" without touching a call site
// (§3.6). The gateway implementation makes ONE non-streaming-shaped call
// with the shared prompt asset as the system message and NO tools.
//
// DLP (A-D6, read this before touching the injection): the desktop talks to
// the gateway on the PUBLIC path, where the default policy RESTORES
// pii/identity/financial/network/thai_name placeholders in responses
// (backend/app/services/policy/profiles.py, category_actions_output). So the
// summary that comes back — and therefore the stored state — can contain
// raw values, exactly like the local chain already does. It is therefore
// injected as a USER-role message pair (renderStateMessages), never into the
// system message: user/assistant roles are scanned and masked on egress like
// every other message, while role=system is skipped by the default profile
// (scan_system_messages=False — FIX32 §1.3 C3). No DLP code is touched; the
// desktop simply never puts summary text in the one unscanned channel.
//
// No 'electron' import: unit-tested in plain Node.

import fs from "node:fs";
import path from "node:path";

import { COMPACTION_EXTRACTION_PROMPT } from "./compaction-prompt";
import type { CompletionRequest, GatewayClient, GatewayMessage } from "./gateway-client";
import type { Logger } from "./logger";

// ── the numbers both surfaces share (web: chat-edge config + web-chat UI) ──
/** Auto-compact fires when history ≥ this × the messages budget (CONTEXT_COMPACT_TRIGGER_RATIO). */
export const COMPACT_TRIGGER_RATIO = 0.85;
/** …and trims the middle down to this × the budget (CONTEXT_COMPACT_TARGET_RATIO). */
export const COMPACT_TARGET_RATIO = 0.6;
/** UI: the context meter + "summarise and continue" appear from here; below it, nothing (spec addendum #1). */
export const CONTEXT_METER_SHOW_RATIO = 0.7;
/** One extraction call folds at most this many chain messages (chat-edge: extractionBatchLimit). */
export const EXTRACTION_BATCH_LIMIT = 120;
/** At most this many batches per turn (chat-edge: extractionMaxBatches). */
export const EXTRACTION_MAX_BATCHES = 8;

/** Same sentence chat-edge appends inside every rendered block (service.StateHonestyNote). */
export const STATE_HONESTY_NOTE =
  "This summary stands in for older messages that are no longer sent verbatim. If the user refers to something that is in neither this summary nor the visible messages, say you may have lost that detail and ask them to restate it — do not guess.";

// ── the record: chat.conversation_states, field for field ──
export interface ConversationStateRecord {
  conversation_id: string;
  current_goal: string | null;
  structured_summary: Record<string, unknown>;
  confirmed_decisions: Record<string, unknown>[];
  active_artifacts: Record<string, unknown>[];
  open_tasks: Record<string, unknown>[];
  rejected_approaches: Record<string, unknown>[];
  unresolved_questions: Record<string, unknown>[];
  /** The desktop chain has no message ids; stays null until the turn path merges. */
  summarized_up_to_message_id: string | null;
  /** The anchor on the desktop: how many chain messages (after the system
   *  message) have been folded. Extraction resumes from this index. */
  message_count_at_summary: number;
  summary_token_count: number;
  manual_compact_boundary_message_id: string | null;
  compaction_instructions: string;
  /** Desktop-only companion to the id column above: the boundary as a chain
   *  index (everything at or before it is dropped, head excepted). */
  manual_compact_boundary_index: number | null;
  updated_at: string;
}

export function emptyState(conversationId: string): ConversationStateRecord {
  return {
    conversation_id: conversationId,
    current_goal: null,
    structured_summary: {},
    confirmed_decisions: [],
    active_artifacts: [],
    open_tasks: [],
    rejected_approaches: [],
    unresolved_questions: [],
    summarized_up_to_message_id: null,
    message_count_at_summary: 0,
    summary_token_count: 0,
    manual_compact_boundary_message_id: null,
    compaction_instructions: "",
    manual_compact_boundary_index: null,
    updated_at: new Date(0).toISOString(),
  };
}

export function isStateEmpty(s: ConversationStateRecord | null | undefined): boolean {
  if (!s) return true;
  if (s.current_goal && s.current_goal.trim()) return false;
  if (Object.values(s.structured_summary ?? {}).some((v) => typeof v === "string" && v.trim())) return false;
  return [s.confirmed_decisions, s.active_artifacts, s.open_tasks, s.rejected_approaches, s.unresolved_questions].every((a) => !a || a.length === 0);
}

// ── store ──
export interface ConversationStateStore {
  get(conversationId: string): ConversationStateRecord | null;
  put(state: ConversationStateRecord): void;
  delete(conversationId: string): void;
}

export class JsonConversationStateStore implements ConversationStateStore {
  constructor(private readonly dir: string) {}
  private file(id: string): string {
    return path.join(this.dir, `${id.replace(/[^A-Za-z0-9_-]/g, "_")}.json`);
  }
  get(conversationId: string): ConversationStateRecord | null {
    try {
      const p = this.file(conversationId);
      if (!fs.existsSync(p)) return null;
      const parsed = JSON.parse(fs.readFileSync(p, "utf8")) as Partial<ConversationStateRecord>;
      return { ...emptyState(conversationId), ...parsed, conversation_id: conversationId };
    } catch {
      return null;
    }
  }
  put(state: ConversationStateRecord): void {
    fs.mkdirSync(this.dir, { recursive: true });
    const p = this.file(state.conversation_id);
    const tmp = `${p}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ ...state, updated_at: new Date().toISOString() }, null, 2));
    fs.renameSync(tmp, p);
  }
  delete(conversationId: string): void {
    fs.rmSync(this.file(conversationId), { force: true });
  }
}

export class MemoryConversationStateStore implements ConversationStateStore {
  private readonly m = new Map<string, ConversationStateRecord>();
  get(id: string): ConversationStateRecord | null {
    const v = this.m.get(id);
    return v ? { ...v } : null;
  }
  put(s: ConversationStateRecord): void {
    this.m.set(s.conversation_id, { ...s, updated_at: new Date().toISOString() });
  }
  delete(id: string): void {
    this.m.delete(id);
  }
}

// ── extractor ──
export interface ExtractionInput {
  conversationId: string;
  model: string;
  existing: ConversationStateRecord | null;
  /** The chain slice to fold (chronological). */
  messages: readonly GatewayMessage[];
  instructions: string;
  signal: AbortSignal;
}
export type ExtractionResult =
  | { ok: true; state: ExtractedState; usage?: { promptTokens: number; outputTokens: number } | null }
  | { ok: false; error: string };

export interface ExtractedState {
  current_goal?: string;
  structured_summary?: Record<string, unknown>;
  confirmed_decisions?: Record<string, unknown>[];
  active_artifacts?: Record<string, unknown>[];
  open_tasks?: Record<string, unknown>[];
  rejected_approaches?: Record<string, unknown>[];
  unresolved_questions?: Record<string, unknown>[];
}

export interface StateExtractor {
  extract(input: ExtractionInput): Promise<ExtractionResult>;
}

/** The prompt body — mirrors chat-edge's buildExtractionPrompt. */
export function buildExtractionUserBody(existing: ConversationStateRecord | null, messages: readonly GatewayMessage[], instructions: string): string {
  const parts: string[] = ["Analyse the following conversation messages and produce the structured state summary described in your instructions.\n"];
  const instr = instructions.trim();
  if (instr) parts.push(`\n--- USER INSTRUCTIONS FOR THIS SUMMARY (take precedence over the defaults) ---\n${instr}\n`);
  if (existing && !isStateEmpty(existing)) {
    parts.push("\n--- EXISTING STATE (update/merge with new information) ---\n");
    parts.push(
      JSON.stringify({
        current_goal: existing.current_goal ?? "",
        structured_summary: existing.structured_summary ?? {},
        confirmed_decisions: existing.confirmed_decisions ?? [],
        active_artifacts: existing.active_artifacts ?? [],
        open_tasks: existing.open_tasks ?? [],
        rejected_approaches: existing.rejected_approaches ?? [],
        unresolved_questions: existing.unresolved_questions ?? [],
      }) + "\n",
    );
  }
  parts.push("\n--- NEW MESSAGES TO ANALYSE ---\n");
  for (const m of messages) {
    let text = "";
    const c = m.content as unknown;
    if (typeof c === "string") text = c;
    else if (Array.isArray(c)) text = (c as { type?: string; text?: string }[]).map((b) => (b?.type === "text" ? b.text ?? "" : "[image]")).join(" ");
    if (m.role === "assistant" && m.tool_calls && m.tool_calls.length > 0) {
      text = `${text} (called tool${m.tool_calls.length > 1 ? "s" : ""}: ${m.tool_calls.map((t) => t.function?.name ?? "?").join(", ")})`.trim();
    }
    if (text.length > 500) text = text.slice(0, 500) + "... [truncated]";
    parts.push(`[${m.role}] ${text}\n`);
  }
  return parts.join("");
}

/** Tolerant of the fences some models add even when told not to. */
export function parseExtraction(raw: string): ExtractedState | null {
  let s = raw.trim();
  if (s.startsWith("```json")) s = s.slice(7);
  else if (s.startsWith("```")) s = s.slice(3);
  if (s.endsWith("```")) s = s.slice(0, -3);
  s = s.trim();
  try {
    const v = JSON.parse(s) as unknown;
    if (!v || typeof v !== "object" || Array.isArray(v)) return null;
    return v as ExtractedState;
  } catch {
    return null;
  }
}

/** chat-edge's mergeExtractionIntoState: keep what exists, take what is new. */
export function mergeExtraction(existing: ConversationStateRecord | null, fresh: ExtractedState, conversationId: string): ConversationStateRecord {
  const out: ConversationStateRecord = { ...(existing ?? emptyState(conversationId)), conversation_id: conversationId };
  if (typeof fresh.current_goal === "string" && fresh.current_goal.trim()) out.current_goal = fresh.current_goal.trim();
  if (fresh.structured_summary && typeof fresh.structured_summary === "object" && Object.keys(fresh.structured_summary).length > 0) out.structured_summary = fresh.structured_summary;
  const arr = (v: unknown): Record<string, unknown>[] | null => (Array.isArray(v) && v.length > 0 ? (v as Record<string, unknown>[]) : null);
  const d = arr(fresh.confirmed_decisions);
  if (d) out.confirmed_decisions = d;
  const a = arr(fresh.active_artifacts);
  if (a) out.active_artifacts = a;
  const t = arr(fresh.open_tasks);
  if (t) out.open_tasks = t;
  const r = arr(fresh.rejected_approaches);
  if (r) out.rejected_approaches = r;
  const q = arr(fresh.unresolved_questions);
  if (q) out.unresolved_questions = q;
  out.summary_token_count = Math.ceil(JSON.stringify(out.structured_summary).length / 4);
  return out;
}

/**
 * The gateway extractor: one call, the shared prompt as system, NO tools.
 * Consumes the streaming endpoint (the host's only completion path) and
 * concatenates the deltas — the model never sees a tool definition here, so
 * Claude's "called a tool instead of writing the summary" failure cannot
 * happen; the prompt forbids it in words as well (A-W3).
 */
export class GatewayStateExtractor implements StateExtractor {
  constructor(
    private readonly gateway: Pick<GatewayClient, "streamCompletion">,
    private readonly log: Logger,
  ) {}
  async extract(input: ExtractionInput): Promise<ExtractionResult> {
    const req: CompletionRequest = {
      model: input.model,
      messages: [
        { role: "system", content: COMPACTION_EXTRACTION_PROMPT },
        { role: "user", content: buildExtractionUserBody(input.existing, input.messages, input.instructions) },
      ],
      // A-W3 / A-D2: no tools on the extraction call — ever. (The host's
      // CompletionRequest carries no temperature; chat-edge pins 0.1 on its
      // side. The gateway default is fine for a JSON envelope.)
      max_tokens: 1500,
    };
    let text = "";
    try {
      for await (const ev of this.gateway.streamCompletion(req, input.signal)) {
        if (ev.kind === "content_delta") text += ev.delta;
        else if (ev.kind === "error") return { ok: false, error: `${ev.code}: ${ev.message}` };
        else if (ev.kind === "done") break;
      }
    } catch (err) {
      return { ok: false, error: (err as Error).message };
    }
    const parsed = parseExtraction(text);
    if (!parsed) {
      this.log.warn(`COMPACTION extraction for ${input.conversationId} returned non-JSON (${text.length} chars) — state kept, anchor not advanced`);
      return { ok: false, error: "non-json" };
    }
    return { ok: true, state: parsed };
  }
}

// ── renderer: chat-edge's BuildStateContext, byte-compatible where it matters ──
function titleCase(s: string): string {
  return s.length ? s[0]!.toUpperCase() + s.slice(1) : s;
}
function list(label: string, items: Record<string, unknown>[] | undefined, field: string): string {
  if (!items || items.length === 0) return "";
  const lines = items.map((it) => (typeof it[field] === "string" ? (it[field] as string).trim() : "")).filter(Boolean);
  if (lines.length === 0) return "";
  return `\n${label}:\n${lines.map((l) => `- ${l}`).join("\n")}\n`;
}

/** The <conversation_state> block, exactly the shape chat-edge renders. */
export function renderStateBlock(state: ConversationStateRecord): string {
  let out = "<conversation_state>\nSummary of this conversation so far (older messages have been compacted):\n";
  if (state.current_goal && state.current_goal.trim()) out += `\nGoal: ${state.current_goal.trim()}\n`;
  for (const key of ["key_context", "progress", "technical_details"]) {
    const v = state.structured_summary?.[key];
    if (typeof v === "string" && v.trim()) out += `\n${titleCase(key.replace(/_/g, " "))}: ${v.trim()}\n`;
  }
  out += list("Confirmed decisions", state.confirmed_decisions, "decision");
  if (state.active_artifacts && state.active_artifacts.length > 0) {
    const lines = state.active_artifacts
      .map((it) => {
        const name = typeof it["name"] === "string" ? (it["name"] as string).trim() : "";
        if (!name) return "";
        const status = typeof it["status"] === "string" ? (it["status"] as string) : "";
        return status ? `- ${name} (${status})` : `- ${name}`;
      })
      .filter(Boolean);
    if (lines.length) out += `\nActive artifacts:\n${lines.join("\n")}\n`;
  }
  out += list("Open tasks", state.open_tasks, "task");
  out += list("Rejected approaches", state.rejected_approaches, "approach");
  out += list("Unresolved questions", state.unresolved_questions, "question");
  out += `\n${STATE_HONESTY_NOTE}\n</conversation_state>\n`;
  return out;
}

/**
 * The state as a USER/assistant pair (see the DLP note at the top). Placed
 * right after the system message and before the preserved first exchange.
 */
export function renderStateMessages(state: ConversationStateRecord): GatewayMessage[] {
  return [
    {
      role: "user",
      content: `[Context from earlier in this conversation — reference only, not a new request]\n${renderStateBlock(state)}`,
    },
    { role: "assistant", content: "Understood — I'll keep that summary as background and continue from where we left off." },
  ];
}
