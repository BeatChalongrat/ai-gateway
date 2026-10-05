// The turn (E3 chat-only → E4 the tool loop). Ported from
// SranDesktop.Application/Services/ChatOrchestrator.cs and the WebView2
// shell's ShellTurnCoordinator, with the host/sidecar boundary of
// docs/E4_REPORT.md §1:
//
//   turn.submit → [system, …history (with tool chains)…, user] → gateway SSE
//   → content deltas | tool_calls → per call: DLP marker → permission
//   (PermissionStore + ApprovalMode + SensitiveReadGuard) → consent via the
//   bridge when needed → execute in the sidecar (with _meta grants +
//   conversation id, live output via notifications/progress) or in the host
//   (run_subagent, web_search, run_python, clipboard) → tool result message
//   → loop, until the model answers without tools, the 100-step cap, the
//   20-minute limit, an error, or turn.cancel.
//
// turn.event vocabulary to the page (the Phase 3/4 payloads web-chat renders):
//   message_start, content_delta, reasoning_delta, analysis_status, status,
//   tool_activity {id, tool, phase, argsSummary, subject?, parentId?, success?, durationMs?,
//   resultPreview?, imageDataUri?, imageDataUris?, isCodeRun?, codeText?}, tool_output {id, chunk},
//   plan_update {steps}, message_files {message_id, files} (FIX80 — files a
//   tool persisted into the conversation), fallback_info, reasoning_notice,
//   error, message_complete.
//
// History: userData/history/<conversation>.json keeps the FULL gateway
// message list including assistant tool_calls and tool results (chat-edge's
// transcript import carries only visible text — §3.1 needs the raw results
// on the next turn). Visible user/assistant text is still imported to
// chat-edge so a reload renders it.

import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { BridgeChannel } from "./bridge";
import type { ChatEdgeClient, ConversationDto, ImportMessageItem, MessageDto } from "./chat-edge-client";
import type { ConnectorOAuth } from "./connector-oauth";
import type { ConnectorTools } from "./connector-mcp";
import type { GatewayClient, GatewayContentBlock, GatewayMessage, GatewayToolCall, GatewayToolDefinition } from "./gateway-client";
import type { GrantStore } from "./grants";
import { HostTools, NESTED_MAX_ITERATIONS, NESTED_MAX_MINUTES, SUBAGENT_FAILED_RE, type NestedRunRequest, type NestedRunResult } from "./host-tools";
import type { Logger } from "./logger";
import { LiveApprovalMode, parseStoredApprovalMode } from "./approval-mode";
import { composeUserContent, pruneAttachedImages, resolveAttachments, stripImageBlocks, type ImageEncoder, type ResolvedAttachments } from "./attachments";
import { appendAssistantReply, roleSequence } from "./chain";
import { type ChainFreshness, chainFreshness, type LocalChain, nextMarker, parseHistoryFile, serializeHistoryFile, unmarkedFreshness } from "./chain-freshness";
import {
  applyApprovalMode,
  BROWSER_SITE_PERM,
  BROWSER_SITE_TOOLS,
  classifyDanger,
  dlpBlocked,
  evaluateBrowserSite,
  listApprovedSites,
  parseApprovalMode,
  readRequiresConsent,
  resolveScope,
  siteScope,
  summarizeArgs,
  toolMeta,
  toolSubject,
  type PermissionDecision,
  type PermissionStore,
} from "./permissions";
import type { BudgetBasis } from "./context-budget";
import { CONTEXT_NOTE_RESERVE, compileChain } from "./context-compiler";
import { ModelWindows } from "./model-windows";
import type { RunLedgerStore } from "./run-ledger";
import type { ContextManager } from "./context-manager";
import { memoryActive, subAgentSystemPrompt, type MemoryUsedItem, type PromptAssembler } from "./prompt-assembler";
import { remoteConsentMessage, spawnConsentMessage } from "./native-dialogs";
import { PROMPT_PART, PromptGuard } from "./prompt-guard";
import { checkRemoteTarget } from "./remote-target";
// AM10 F — the sentences the host writes for the page, in the current language
// (aliased: `t` is a common local name in this file).
import { t as hostText } from "./strings";
import { endingForOutcome, normalizeHostError, stopReasonFor, type HostErrorFrame, type StopReason, type TerminalCause, type TurnEnding } from "./terminal-cause";

/** The hostname of a user-typed URL for a log line — never the query. */
function hostnameOf(raw: string): string {
  try {
    return new URL(raw.trim()).hostname || "?";
  } catch {
    return "?";
  }
}
import type { SidecarClient } from "./sidecar-client";
import type { PromptPart } from "../shared/guardrail-mirror";
import { BROWSER_BRIDGE_TOOLS, withholdMemoryTools, type ConsentRespondPayload, type HostQueryPayload, type QuestionRespondPayload, type TurnCancelPayload, type TurnSubmitPayload, type UiReadyPayload } from "../shared/bridge-protocol";
import { ASK_USER_TOOL, ASK_USER_TOOL_DEFINITION, CANCELLED_WHILE_ASKING, NOT_AVAILABLE_IN_THIS_RUN, formatAnswerForModel, validateAskArgs, type AskQuestion } from "./ask-user";

interface BufferedEvent {
  seq: number;
  event: string;
  data: unknown;
}
interface ConversationLog {
  seq: number;
  events: BufferedEvent[];
  /** FIX41 — the id of the generation the buffered events belong to.
   *  A conversation has many runs; a cursor that only knows `seq` cannot
   *  tell one from the next, and once the ledger can come back from disk
   *  (Part F) `seq` no longer even continues from the previous process. */
  runId: string;
  /** The terminal event this run already emitted, or null. */
  terminal: string | null;
}

/** FIX41 — the three events that END a run (docs/STREAM_CONTRACT.md §3).
 *  Exactly one of them is sent, and it is always the last frame. */
const TERMINAL_TURN_EVENTS = new Set(["message_complete", "error", "cancelled"]);
/** FIX124 — a turn that answers the last user message AGAIN (Regenerate, or
 *  Retry of a failed turn): it imports no user row, and names the assistant
 *  row it replaces when the server knows that row by a client id. */
interface RegenerateTarget {
  replacesClientId?: string;
}
interface RunningTurn {
  controller: AbortController;
  done: Promise<void>;
  /** FIX80 — ConversationFiles the turn's tools persisted server-side
   *  (run_python artifacts). Shown live via a message_files frame and
   *  linked to the assistant row at import (file_ids). */
  attachedFiles?: { id: string; filename: string; content_type: string; file_size_bytes: number }[];
  messageId: string;
  /** FIX119 — the run id of the turn's run (beginRun), for the import. */
  runId?: string;
  assistantText: string;
  /** FIX35 Part B — the model the gateway said actually answered (an
   *  "auto" turn names the router's pick). Persisted as the assistant
   *  row's model_id and repeated on message_complete. */
  servedModel?: string;
  /** FIX120 — the model the turn ASKED for when the gateway's fallback chain
   *  answered with a different one (servedModel). Persisted on the imported
   *  row as fallback_from and repeated on message_complete, so the page's
   *  "answered by X" notice survives a reload. Before this the desktop kept
   *  attributing a rescued turn to the requested model. */
  fallbackFrom?: string;
  /** FIX118 — what the run's terminal error frame said (cause + code), so
   *  the imported assistant row records the same ending the page saw. */
  errored?: { cause?: TerminalCause; errorCode?: string; attempts?: unknown[] };
  /** FIX119 — why the model stopped on the last round (closed set), from
   *  the gateway's final chunk. "" when nothing was said. */
  stopReason?: StopReason | "";
}
interface PendingConsent {
  conversationId: string;
  payload: Record<string, unknown>;
  resolve: (r: ConsentRespondPayload) => void;
}
/** FIX73: a question card the model raised and the user has not answered. */
interface PendingQuestion {
  conversationId: string;
  payload: Record<string, unknown>;
  resolve: (r: QuestionRespondPayload | { cancelled: true }) => void;
}

/** FIX31 §1.1: who is asking, when it is not the assistant the user is talking
 *  to. Carried on the consent card so the user approves knowingly: the request
 *  comes from a sub-agent the assistant started on its own, with THIS task. */
export interface ConsentRequester {
  kind: "subagent";
  subagentId: string;
  /** The fan-out label ("date"), or "" for a single unlabelled sub-agent. */
  label: string;
  /** The task text the parent model wrote for it (truncated) — the model's
   *  words, not the user's; the card says so. */
  task: string;
}

/** FIX31 §1.1/§1.2: per-run wait bookkeeping (consent waits are excluded from a
 *  run's time budget; a denied tool ends a nested run). */
interface RunWaitState {
  /** Total ms spent waiting for the user's consent (excluded from deadlines). */
  waitedMs?: number;
  /** Set while a consent card is open for this run; null otherwise. */
  waitingSince?: number | null;
  /** Nested runs: the tool the user just refused — the run ends FAILED. */
  deniedTool?: string | null;
  requester?: ConsentRequester;
}

export interface TurnCoordinatorOptions {
  /** FIX30 §3.2: how often the host re-says what a turn is waiting on (a
   *  consent decision, a long local tool) — a `status` turn event the page
   *  shows AND a byte for its idle watchdog, so a legitimate wait is never
   *  mistaken for a dead stream. Default 15 s. */
  waitKeepaliveMs?: number;
  homeDirectory: string;
  dataDir: string;
  defaultModel: string;
  /** dev only: pin every turn to one model (index.ts gates it on !isRelease) */
  forceModel?: string;
  approvalMode: string;
  maxIterations?: number;
  maxTurnMinutes?: number;
  signOut: () => Promise<void>;
  /** host.query folders.* — the native picker lives in index.ts */
  pickFolder: () => Promise<string | null>;
  /** FIX2 host-side native dialogs (a headless sidecar cannot show a window). */
  pickSkillFile?: () => Promise<string | null>;
  /** Native file picker for a custom stdio MCP server's program (the user
   * chooses it — the page names no process). */
  pickExecutableFile?: () => Promise<string | null>;
  /** Native yes/no confirmation (spawn consent). Returns true only on an
   * explicit approval — never driven without the user. */
  confirmSpawn?: (message: string) => Promise<boolean>;
  /** FIX69: the HOST-owned form for a remote MCP server — url + optional key.
   * Resolves null when the user cancels. The key is handed to the sidecar over
   * the private pipe and is never logged, echoed to the page, or kept here. */
  promptRemoteConnector?: () => Promise<{ url: string; authHeader: string; key: string } | null>;
  /** FIX69: native confirmation naming the exact origin about to be dialled. */
  confirmRemote?: (message: string) => Promise<boolean>;
  /** FIX69: the catalogue "Install" path's key, from the same host-owned
   * window (key field only). Null when cancelled. */
  promptConnectorKey?: (connectorName: string) => Promise<string | null>;
  /** FIX69: may a remote connector target an internal address? From the
   * shipped config (connectorPrivateNetwork); default false. */
  allowPrivateNetwork?: boolean;
  /** FIX2 §1.2: apply the OS/Electron side-effect of a host-owned setting
   * (startWithWindows → login item). The persisted VALUE lives only in the
   * sidecar; this derives the effect from it, so there is no second copy. */
  applyHostSetting?: (key: string, value: unknown) => void;
  /** FIX22 §1a: the write-time prompt guard (gateway check, mirror fallback).
   * Optional so the pure tests can build a coordinator without it. */
  promptGuard?: PromptGuard;
  /** FIX22 §2: the update notifier's page-ready hook (repost a pending notice
   * after a reload; first check once the page is up). */
  onPageReady?: () => void;
  /** FIX27 Part 1: bytes → data URI for an attached image (Electron nativeImage
   * in index.ts; the pure tests pass a stub). Absent → images are reported as
   * unreadable, never silently dropped. */
  encodeImage?: ImageEncoder;
  /** FIX31 Part 2: the host-side connector OAuth flow + the host-side MCP
   * client that carries its Bearer. Absent (tests, probes) → the page's
   * oauth ops answer oauth-unsupported exactly as before. */
  connectorOAuth?: ConnectorOAuth;
  connectorTools?: ConnectorTools;
  /** FIX33 A-D2/A-D5: the context manager (state store + summariser + meter).
   * Absent (pure tests, probes) → FIX32's compileChain: trim + honest note, no
   * summary. The summarising logic lives THERE, never in this file (§3.6). */
  contextManager?: ContextManager;
  /** FIX104: the catalogue's context windows, last known (index.ts keeps them
   * under userData). Absent (tests, probes) → an in-memory copy that starts
   * empty: the conservative default until the model list has been read. */
  modelWindows?: ModelWindows;
  /** FIX34 Part A: the quick-ask window's shell hooks (index.ts owns the
   * windows and the host prefs file; the coordinator only relays the page's
   * control-plane ops). Absent (tests, probes) → the ops answer
   * quick-unsupported / prefs-unsupported. */
  shell?: {
    prefsGet: () => { quickHotkey: string; trayClick: string };
    /** Applies + persists; a hotkey the OS refuses answers { ok:false, error }. */
    prefsSet: (patch: Record<string, unknown>) => { ok: boolean; prefs: { quickHotkey: string; trayClick: string }; error?: string };
    quickClose: () => void;
    quickOpenInMain: (conversationId: string) => void;
  };
}

// FIX3 §4: the sentinel the sidecar's read_document returns for a scanned PDF it
// could not OCR (it holds no JWT). The bytes ride with it; the host OCRs them.
const OCR_RELAY_MARKER = "__SRAN_OCR_NEEDED__";

// FIX8 A1.6: the self-reschedule tool, offered only while a scheduled task is
// firing. It changes THAT task's own cron and/or prompt for future runs.
const UPDATE_TASK_SCHEDULE_TOOL: GatewayToolDefinition = {
  type: "function",
  function: {
    name: "update_task_schedule",
    description:
      "Change THIS scheduled task's own cron schedule and/or prompt for future runs, from inside its run. " +
      "Use it when the task should next run at a different time, or with a revised instruction. Only available while a scheduled task is running.",
    parameters: {
      type: "object",
      properties: {
        cron: { type: "string", description: "New 5-field cron (e.g. '0 8 * * *'). Omit to keep the current schedule." },
        prompt: { type: "string", description: "New prompt for future runs. Omit to keep the current prompt." },
      },
    },
  },
};

/** FIX34 Part A — the quick-ask window's system hint (a tool-less turn). Exported so the test pins it. */
export const QUICK_ASK_HINT =
  "This reply is for the quick-ask window: a small always-on-top box for short questions. In this window you have NO tools — no file access, no browser, no commands, no code execution, no image generation. Answer directly and concisely. If the request genuinely needs one of those, say so in one sentence and tell the user to open this chat in the main window (there is an \"Open in main window\" button), instead of guessing or describing a result you did not produce.";
const MAX_OUTPUT_TOKENS = 8192;
/** FIX105 Part B — chat-edge serves at most 100 transcript rows a page. */
const HISTORY_PAGE_SIZE = 100;
/** FIX105 Part B — how far back the host reads a transcript it has no local chain for. chat-edge's own load
 *  ceiling (MAX_CONTEXT_MESSAGES) is 1,000 by default; rows beyond it are counted and said, never dropped quietly. */
const HISTORY_LOAD_CEILING = 1000;
/** FIX109: where a turn's history came from — the local chain or the server transcript. */
export type HistorySource = "local" | "server";
/** FIX105 — chat-edge's CodeMessageTooLong and its sentence (message_too_long.go; pinned by context-budget-pin.test.ts). */
const MESSAGE_TOO_LONG_CODE = "message_too_long";
const MESSAGE_TOO_LONG_TEXT = "This message alone is too long for this model. Shorten it or split it into parts; the rest of the conversation is unaffected.";
/** chat-edge's IsAutoModel: the router sentinel (or no model at all). */
function isAutoModel(id: string): boolean {
  const s = id.trim();
  return s === "" || s === "auto";
}
const KEEP_SCREENSHOTS = 3;
// FIX27: attached images kept in the chain (chat-edge replays at most 5 history images).
const KEEP_ATTACHED_IMAGES = 5;
const SCREENSHOT_MARKER = "[Screenshot from";

/** chat-edge accepts at most this many items per import request. */
const IMPORT_BATCH_MAX = 200;

export class TurnCoordinator {
  private readonly logs = new Map<string, ConversationLog>();
  /** FIX41 Part F — the on-disk run ledger (optional; see run-ledger.ts). */
  private runLedger: RunLedgerStore | null = null;
  private readonly turns = new Map<string, RunningTurn>();
  private readonly pendingConsents = new Map<string, PendingConsent>();
  /** FIX73: open question cards, by promptId. Same lifecycle as
   *  pendingConsents: answered by the page, closed-as-cancelled on Stop,
   *  re-posted on ui.ready. Never answered by the app itself. */
  private readonly pendingQuestions = new Map<string, PendingQuestion>();
  /** FIX31 §1.1: ONE consent card at a time per page conversation. Parallel
   *  sub-agents can each need permission at once; the page holds a single card
   *  per row, so a second request would overwrite the first and leave a prompt
   *  nobody can reach. Requests queue here and are posted one after another. */
  private readonly consentQueues = new Map<string, Promise<void>>();
  /** FIX31 §1.2: the tools a firing scheduled task needed permission for and
   *  could not get (nobody there) — recorded on the task's history line. */
  private taskNeededPermission: { tool: string; scope: string }[] = [];
  private readonly cwdByConversation = new Map<string, string>();
  readonly lastReply = new Map<string, string>();
  /** FIX22 §1b: the labelled user-data parts of the last assembled prompt per
   * conversation, kept ONLY to explain a guardrail 403 after the fact. */
  private readonly promptPartsByConversation = new Map<string, PromptPart[]>();
  /** Probe seams. */
  lastConversationId: string | null = null;
  readonly toolCallsSeen: { conversationId: string; tool: string; args: Record<string, unknown>; decision: string; ok: boolean; where: "host" | "sidecar"; rpcError?: string }[] = [];
  private turnCounter = 0;
  // FIX1 §3.3: snapshot the browser-bridge connection ONCE per top-level turn so
  // the tool list can drop the 8 bridge tools when it is down — and cannot flap
  // mid-turn (a sub-agent in the same turn reads the same snapshot). Default
  // false: a tool the model cannot use is not offered until the bridge is proven up.
  private browserConnected = false;
  // FIX5 §3: the tenant's web-search capability, cached so we do not ask the
  // gateway on every turn. Learned from GET /v1/search/config (cheap, unbilled).
  // Invalidation is purely the TTL — an admin flipping the tenant flag is picked
  // up within SEARCH_CAP_TTL_MS (≤5 min, well under the "wrong for an hour" bar)
  // — plus process restart (cold cache). null = never learned yet.
  // FIX8 A1.3: while a scheduled task is firing, its own permission mode overrides
  // the app-wide approvalMode for that run. Null outside a task run. Safe as a
  // single field because the scheduler fires tasks one at a time.
  private activeTaskApprovalMode: ReturnType<typeof parseApprovalMode> | null = null;
  // FIX25 §1b: the interactive mode is read LIVE from the sidecar's settings
  // (the store of record, written by the composer through settings.set) on
  // every tool call — not frozen at construction. opts.approvalMode (the
  // config file) is only the fallback when the sidecar cannot answer.
  private readonly liveMode: LiveApprovalMode;
  /** FIX8 A1.6: the scheduled task currently firing — lets update_task_schedule
   * reschedule THIS task from inside its own run. Null outside a task run. */
  private activeTaskId: string | null = null;
  private searchTenantEnabled: boolean | null = null;
  private searchTenantEnabledAt = 0;
  private static readonly SEARCH_CAP_TTL_MS = 5 * 60_000;
  // FIX9 §1.1: the models the gateway accepts, cached, for validating the
  // page-supplied model. A page can name any model string; an unknown one is
  // refused rather than passed to the gateway.
  private acceptedModels: Map<string, { tools: boolean; vision: boolean; contextWindow?: number }> | null = null;
  private acceptedModelsAt = 0;
  private static readonly MODELS_TTL_MS = 5 * 60_000;
  /** FIX104 — the windows from the same list, kept past a failed read and a restart. */
  private readonly modelWindows: ModelWindows;

  constructor(
    private readonly bridge: BridgeChannel,
    private readonly gateway: GatewayClient,
    private readonly chatEdge: ChatEdgeClient,
    private readonly assembler: PromptAssembler,
    private readonly sidecar: SidecarClient,
    private readonly hostTools: HostTools,
    private readonly permissions: PermissionStore,
    private readonly grants: GrantStore,
    private readonly opts: TurnCoordinatorOptions,
    private readonly log: Logger,
  ) {
    this.liveMode = new LiveApprovalMode(sidecar, parseApprovalMode(opts.approvalMode), log);
    this.modelWindows = opts.modelWindows ?? new ModelWindows(null);
  }

  waitForTurn(conversationId: string): Promise<void> {
    return this.turns.get(conversationId)?.done ?? Promise.resolve();
  }
  isRunning(conversationId: string): boolean {
    return this.turns.has(conversationId);
  }
  currentAssistantText(conversationId: string): string | null {
    return this.turns.get(conversationId)?.assistantText ?? null;
  }
  get activeTurns(): number {
    return this.turns.size;
  }
  get pendingConsentCount(): number {
    return this.pendingConsents.size;
  }

  // ── inbound ────────────────────────────────────────────────────────────────
  onTurnSubmit(payload: TurnSubmitPayload): void {
    this.lastConversationId = payload.conversationId;
    const existing = this.turns.get(payload.conversationId);
    if (existing) {
      this.log.info(`TURN SUBMIT while a turn is running for conv=${payload.conversationId}: cancelling the old one`);
      existing.controller.abort();
    }
    const controller = new AbortController();
    const messageId = "asst_" + randomUUID().replace(/-/g, "");
    const turn: RunningTurn = { controller, messageId, assistantText: "", done: Promise.resolve() };
    turn.done = this.runTurn(payload, turn)
      .catch((err: unknown) => {
        // FIX41 — runTurn was not wrapped. Anything that THREW rather than
        // yielding an `error` frame (a gateway client throw, a tool
        // executor bug, a rejected persist) left the page streaming
        // forever: the promise rejected, the turn was deleted, and no
        // terminal event was ever sent. However it ends, it says so.
        this.log.error(`TURN THREW conv=${payload.conversationId}: ${(err as Error)?.message ?? String(err)}`);
        if (this.turns.get(payload.conversationId) === turn) {
          this.emitTerminalIfMissing(payload.conversationId, {
            code: "host_exception",
            message: hostText("error.hostException"),
          });
        }
      })
      .finally(() => {
        if (this.turns.get(payload.conversationId) === turn) {
          this.emitTerminalIfMissing(payload.conversationId, {
            code: "no_result",
            message: hostText("error.noResult"),
          });
          this.turns.delete(payload.conversationId);
        }
      });
    this.turns.set(payload.conversationId, turn);
  }

  onTurnCancel(payload: TurnCancelPayload): void {
    const turn = this.turns.get(payload.conversationId);
    if (!turn) return;
    this.log.info(`TURN CANCEL requested conv=${payload.conversationId}`);
    turn.controller.abort();
    // A consent prompt still open for this turn is answered "deny" by the cancel.
    for (const [id, p] of this.pendingConsents) {
      if (p.conversationId === payload.conversationId) {
        this.pendingConsents.delete(id);
        p.resolve({ promptId: id, decision: "deny", scope: "once" });
      }
    }
    // FIX73: an open question card is closed by the cancel — as cancelled,
    // not as an answer.
    for (const [id, p] of this.pendingQuestions) {
      if (p.conversationId === payload.conversationId) {
        this.pendingQuestions.delete(id);
        p.resolve({ cancelled: true });
      }
    }
  }

  /** FIX73: the user's answer to a question card. Unknown/expired ids are
   *  ignored, like consent — a stale card after a reload cannot answer a
   *  newer question. */
  onQuestionRespond(payload: QuestionRespondPayload): void {
    const pending = this.pendingQuestions.get(payload.promptId);
    if (!pending) {
      this.log.info(`question.respond for unknown/expired prompt ${payload.promptId} — ignored`);
      return;
    }
    this.pendingQuestions.delete(payload.promptId);
    this.log.info(`QUESTION ${payload.outcome} for prompt ${payload.promptId}${payload.response ? " (free text)" : ""}`);
    pending.resolve(payload);
  }

  onConsentRespond(payload: ConsentRespondPayload): void {
    const pending = this.pendingConsents.get(payload.promptId);
    if (!pending) {
      this.log.info(`consent.respond for unknown/expired prompt ${payload.promptId} — ignored`);
      return;
    }
    this.pendingConsents.delete(payload.promptId);
    this.log.info(`CONSENT ${payload.decision}/${payload.scope} for prompt ${payload.promptId}`);
    pending.resolve(payload);
  }

  onUiReady(payload: UiReadyPayload): void {
    // FIX22 §2: the page is up (again) — let the update notifier re-post a
    // pending notice / run its first check. Independent of any conversation.
    try {
      this.opts.onPageReady?.();
    } catch (err) {
      this.log.warn(`onPageReady hook failed: ${(err as Error).message}`);
    }
    if (!payload.conversationId) return;
    const log = this.logs.get(payload.conversationId);
    if (log) {
      // FIX41 — the page's cursor is (runId, seq). A cursor from a
      // DIFFERENT run says nothing about this one, so it is treated as 0
      // and the whole run replays; anything else would silently withhold
      // the beginning of the current answer.
      const sameRun = !payload.runId || payload.runId === log.runId;
      const from = sameRun ? payload.lastSeq : 0;
      const replay = log.events.filter((e) => e.seq > from);
      // FIX127 C2 — a run that has already ENDED is not replayed to a page
      // that was not following it (a fresh page after a hard navigation,
      // F5, re-auth, a deep link: no cursor, or another run's). The import
      // ran before the terminal, so that page reads the finished answer
      // from the server transcript; replaying it as well painted the same
      // answer twice — the second copy built from frames alone, without
      // the row's files (the C1 "Generate Document is back" card). A page
      // that WAS following this run (same run id) still gets the tail it
      // missed, terminal included.
      const followingThisRun = !!payload.runId && payload.runId === log.runId;
      if (log.terminal && !followingThisRun) {
        this.log.info(`RESUME conv=${payload.conversationId} run=${log.runId}: run already ended (${log.terminal}); the page reads the server transcript — nothing replayed`);
      } else if (replay.length === 0) this.log.info(`RESUME conv=${payload.conversationId} run=${log.runId}: page at seq ${payload.lastSeq}, nothing to replay`);
      else {
        this.log.info(`RESUME conv=${payload.conversationId} run=${log.runId}: replaying ${replay.length} event(s) from seq ${from + 1} (page was at ${payload.lastSeq}${sameRun ? "" : " of another run"})`);
        for (const e of replay) this.bridge.postTurnEvent(payload.conversationId, e.seq, e.event, e.data, log.runId);
      }
    }
    for (const [id, p] of this.pendingConsents) {
      if (p.conversationId === payload.conversationId) {
        this.log.info(`RESUME conv=${payload.conversationId}: re-posting pending consent ${id}`);
        this.bridge.post("consent.request", p.payload);
      }
    }
    for (const [id, p] of this.pendingQuestions) {
      if (p.conversationId === payload.conversationId) {
        this.log.info(`RESUME conv=${payload.conversationId}: re-posting pending question ${id}`);
        this.bridge.post("question.request", p.payload);
      }
    }
  }

  async onHostQuery(payload: HostQueryPayload): Promise<void> {
    switch (payload.op) {
      case "session.logout":
        this.log.info("HOST QUERY session.logout: the page asked to sign out — host revokes, clears, blanks");
        this.bridge.postHostResult(payload.requestId, true, { signedOut: true });
        await this.opts.signOut();
        return;
      case "diag.reportRendererError":
        this.log.warn(`RENDERER ERROR (${String(payload.args["kind"] ?? "error")}): ${String(payload.args["message"] ?? "").slice(0, 300)}`);
        this.bridge.postHostResult(payload.requestId, true);
        return;
      case "folders.list":
        this.bridge.postHostResult(payload.requestId, true, { folders: this.grants.list() });
        return;
      // ── FIX34 Part A: shell prefs (quick-ask hotkey, tray click) + quick window ──
      case "shell.prefs.get": {
        if (!this.opts.shell) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "prefs-unsupported");
          return;
        }
        this.bridge.postHostResult(payload.requestId, true, this.opts.shell.prefsGet());
        return;
      }
      case "shell.prefs.set": {
        if (!this.opts.shell) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "prefs-unsupported");
          return;
        }
        const r = this.opts.shell.prefsSet(payload.args ?? {});
        this.log.info(`SHELL PREFS set → ${JSON.stringify(r.prefs)}${r.ok ? "" : ` (refused: ${r.error})`}`);
        this.bridge.postHostResult(payload.requestId, r.ok, r.prefs, r.error);
        return;
      }
      // ── FIX130 A4b: the Memory page changed the saved memories ──
      // (edit, delete, accept, reject, add, clear). The prompt assembler keeps
      // chat-edge's memories for 60 s; without this an edit or a delete took up
      // to a minute to reach the desktop's answers. Nothing to validate: the
      // op carries no arguments and only makes the next turn read again.
      case "memory.changed": {
        this.assembler.invalidateServerMemories();
        this.log.info("HOST QUERY memory.changed: the page changed the saved memories — the next turn reads them again");
        this.bridge.postHostResult(payload.requestId, true, { invalidated: true });
        return;
      }
      // ── FIX127 C3: the new-chat page's abandoned draft conversation ──
      case "conversation.discardEmpty": {
        const conversationId = payload.args?.["conversationId"];
        if (typeof conversationId !== "string" || !conversationId) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "conversationId-missing");
          return;
        }
        // Never delete a chat that has a message, whatever the page says:
        // the server's count is the truth, and a turn in flight counts too.
        if (this.turns.has(conversationId)) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "turn-in-flight");
          return;
        }
        const conversation = await this.chatEdge.getConversation(conversationId).catch(() => null);
        if (!conversation) {
          this.bridge.postHostResult(payload.requestId, true, { deleted: false, reason: "not-found" });
          return;
        }
        if ((conversation.message_count ?? 0) > 0) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "conversation-has-messages");
          return;
        }
        const deleted = await this.chatEdge.deleteConversation(conversationId);
        this.log.info(`DISCARD EMPTY conv=${conversationId} deleted=${deleted}`);
        this.bridge.postHostResult(payload.requestId, deleted, { deleted });
        return;
      }
      case "quick.close": {
        if (!this.opts.shell) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "quick-unsupported");
          return;
        }
        this.bridge.postHostResult(payload.requestId, true, { closed: true });
        this.opts.shell.quickClose();
        return;
      }
      case "quick.openInMain": {
        const conversationId = payload.args?.["conversationId"];
        if (!this.opts.shell) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "quick-unsupported");
          return;
        }
        if (typeof conversationId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(conversationId)) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "conversationId-missing");
          return;
        }
        this.bridge.postHostResult(payload.requestId, true, { opened: true });
        this.opts.shell.quickOpenInMain(conversationId);
        return;
      }
      // ── FIX33 A-D5: the context meter (read) and "summarise and continue" ──
      case "context.info": {
        const conversationId = payload.args?.["conversationId"];
        if (typeof conversationId !== "string" || !conversationId) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "conversationId-missing");
          return;
        }
        if (!this.opts.contextManager) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "context-unsupported");
          return;
        }
        try {
          const conversation = await this.chatEdge.getConversation(conversationId).catch(() => null);
          const model = this.opts.forceModel ?? conversation?.model_id ?? this.opts.defaultModel;
          // FIX104 — a placeholder turn, not "": an empty text is buildMessages'
          // REGENERATE shape, which cuts the history at the last user message, so
          // the meter the page reads after every turn left out the newest
          // exchange (and failed outright on a chat with no user message yet).
          const built = await this.buildMessages(conversationId, conversation, " ");
          // buildMessages appends the placeholder user turn; the meter counts history only.
          const messages = built.messages.slice(0, -1);
          const signal = new AbortController().signal;
          // The same tool count and budget the next turn will use: a
          // tools-blind model is sent no tool definitions, so its meter must
          // not reserve room for them.
          // FIX130 A2 — "memory active": on AND not a temporary chat.
          const memoryEnabled = memoryActive(conversation);
          const tools = (await this.modelSupportsTools(model, signal)) ? await this.toolDefinitions(null, false, false, memoryEnabled) : [];
          const basis = await this.budgetBasis(conversationId, model, signal);
          this.bridge.postHostResult(payload.requestId, true, this.opts.contextManager.info(conversationId, messages, { model, basis, numTools: tools.length, memoryEnabled, omittedOlder: built.omittedOlder }));
        } catch (err) {
          this.bridge.postHostResult(payload.requestId, false, undefined, `context-info-failed:${(err as Error).message.slice(0, 80)}`);
        }
        return;
      }
      case "context.compact": {
        const conversationId = payload.args?.["conversationId"];
        const instructionsRaw = payload.args?.["instructions"];
        const instructions = typeof instructionsRaw === "string" ? instructionsRaw.slice(0, 2000) : "";
        if (typeof conversationId !== "string" || !conversationId) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "conversationId-missing");
          return;
        }
        if (!this.opts.contextManager) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "context-unsupported");
          return;
        }
        if (this.turns.has(conversationId)) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "turn-in-progress");
          return;
        }
        try {
          const conversation = await this.chatEdge.getConversation(conversationId).catch(() => null);
          const model = this.opts.forceModel ?? conversation?.model_id ?? this.opts.defaultModel;
          const built = await this.buildMessages(conversationId, conversation, "");
          const history = built.messages.slice(1, -1); // no system, no empty user turn
          const res = await this.opts.contextManager.manualCompact(conversationId, history, model, instructions, new AbortController().signal);
          if (res.ok) this.bridge.postHostResult(payload.requestId, true, { ok: true });
          else this.bridge.postHostResult(payload.requestId, false, undefined, `compact-${res.error}`);
        } catch (err) {
          this.bridge.postHostResult(payload.requestId, false, undefined, `compact-failed:${(err as Error).message.slice(0, 80)}`);
        }
        return;
      }
      case "folders.pickAndGrant": {
        // The page never names a path: the folder comes from a native dialog.
        const picked = await this.opts.pickFolder();
        if (!picked) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "cancelled");
          return;
        }
        const v = this.grants.add(picked);
        this.log.info(`GRANT ${v.verdict}: ${picked}`);
        if (v.verdict === "Ok") this.bridge.postHostResult(payload.requestId, true, { folder: v.canonical, folders: this.grants.list() });
        else this.bridge.postHostResult(payload.requestId, false, undefined, `rejected:${v.verdict}`);
        return;
      }
      case "folders.revoke": {
        const folder = payload.args["folder"] ?? payload.args["path"];
        const removed = typeof folder === "string" ? this.grants.remove(folder) : false;
        this.log.info(`GRANT revoke ${removed ? "ok" : "no-op"}: ${String(folder)}`);
        this.bridge.postHostResult(payload.requestId, removed, { folders: this.grants.list() }, removed ? undefined : "not-found");
        return;
      }
      // ── FIX6: approved sites (control-plane). The page may LIST and REVOKE;
      //    there is NO "sites.add" — a site is approved ONLY via the consent
      //    prompt, so neither the page nor the model can name a site to approve. ──
      case "sites.list": {
        this.bridge.postHostResult(payload.requestId, true, { sites: listApprovedSites(this.permissions) });
        return;
      }
      case "sites.revoke": {
        const origin = payload.args?.["origin"];
        const removed = typeof origin === "string" ? this.permissions.revoke(BROWSER_SITE_PERM, origin) : false;
        this.log.info(`SITE revoke ${removed ? "ok" : "no-op"}: ${String(origin)}`);
        this.bridge.postHostResult(payload.requestId, removed, { sites: listApprovedSites(this.permissions) }, removed ? undefined : "not-found");
        return;
      }
      // ── settings.get — forward to the sidecar (single source for settings) ──
      case "settings.get": {
        const r = await this.sidecar.hostQuery("settings.get", payload.args ?? {});
        // FIX25 §3: `grantedFolders` in the sidecar's snapshot is the WPF-era
        // list the host never writes; the folder grants live in the host's
        // GrantStore (the one folders.list / the permission gate read). Overlay
        // it so every reader of the snapshot sees the real list — this was why
        // Settings showed "No folders granted yet" beside two live grants.
        const data = r.ok && r.data && typeof r.data === "object" ? { ...(r.data as Record<string, unknown>), grantedFolders: this.grants.list() } : r.data;
        this.bridge.postHostResult(payload.requestId, r.ok, data, r.error);
        return;
      }
      // ── settings.set — sidecar persists the value; host applies OS effects ──
      case "settings.set": {
        // FIX22 §1a: the writing-style profile lands verbatim in every turn's
        // system prompt. Check it against the gateway's guardrail BEFORE the
        // sidecar persists it; a refusal names the phrase. The sidecar never
        // sees a refused value.
        if (payload.args?.["key"] === "writingStyleProfile" && this.opts.promptGuard) {
          const value = payload.args?.["value"];
          const verdict = typeof value === "string" ? await this.opts.promptGuard.checkForWrite(value, "the writing-style profile") : { blocked: false as const };
          if (verdict.blocked) {
            this.bridge.postHostResult(payload.requestId, false, undefined, PromptGuard.refusalMessage(verdict, "the writing-style profile"));
            return;
          }
        }
        // FIX25 §1c: the approval mode's vocabulary is closed on BOTH sides —
        // refuse here too so a bad value never reaches the store, and log every
        // accepted change (the page — never the model — is the only writer:
        // settings.set is a host.query op, not a tool).
        if (payload.args?.["key"] === "approvalMode" && parseStoredApprovalMode(payload.args?.["value"]) === null) {
          this.log.warn(`SETTINGS approvalMode REFUSED: not manual|auto|skip (${String(payload.args?.["value"]).slice(0, 40)})`);
          this.bridge.postHostResult(payload.requestId, false, undefined, "invalid");
          return;
        }
        const r = await this.sidecar.hostQuery("settings.set", payload.args ?? {});
        // §1.2: for host-owned keys, derive the OS effect from the SAME stored
        // value (no second copy). Only after the sidecar accepted+persisted it.
        if (r.ok) {
          const key = payload.args?.["key"];
          if (key === "approvalMode") this.log.info(`MODE approvalMode → ${String(payload.args?.["value"])} (set by the page; applies from the next tool call)`);
          if ((key === "startWithWindows" || key === "minimizeToTray") && this.opts.applyHostSetting) {
            this.opts.applyHostSetting(String(key), payload.args?.["value"]);
          }
        }
        this.bridge.postHostResult(payload.requestId, r.ok, r.data, r.error);
        return;
      }
      // ── skills.installPicker — native file dialog → sidecar install ──
      case "skills.installPicker": {
        const path = this.opts.pickSkillFile ? await this.opts.pickSkillFile() : null;
        if (!path) {
          this.bridge.postHostResult(payload.requestId, true, { installed: false, cancelled: true });
          return;
        }
        const r = await this.sidecar.hostQuery("skills._install", { path });
        this.bridge.postHostResult(payload.requestId, r.ok, r.data, r.error);
        return;
      }
      // ── connectors.install — none installs; api_key/oauth2 stated (no secure
      //    text input this round; those secrets must not route through the page) ──
      case "connectors.install": {
        const r = await this.sidecar.hostQuery("connectors.install", payload.args ?? {});
        if (r.error === "needs-api-key") {
          // FIX69: the key is collected in the HOST-owned window (never the
          // page) and handed to the sidecar over the private pipe. Only a
          // real catalogue reaches here — placeholder cards are not shown.
          const connectorId = typeof payload.args?.["connectorId"] === "string" ? (payload.args["connectorId"] as string) : "";
          const name = typeof (r.data as { name?: unknown } | undefined)?.name === "string" ? ((r.data as { name: string }).name) : connectorId;
          if (!this.opts.promptConnectorKey || !connectorId) {
            this.log.info("HOST QUERY connectors.install: api_key connector needs a secure key input (no host window in this build)");
            this.bridge.postHostResult(payload.requestId, false, undefined, "api-key-unsupported");
            return;
          }
          const key = await this.opts.promptConnectorKey(name);
          if (!key) {
            this.bridge.postHostResult(payload.requestId, true, { installed: false, cancelled: true });
            return;
          }
          const k = await this.sidecar.hostQuery("connectors._installApiKey", { connectorId, apiToken: key });
          if (k.ok) {
            this.sidecar.invalidateTools();
            this.log.info(`CONNECTOR installed (catalogue, key set): ${connectorId}`);
          }
          this.bridge.postHostResult(payload.requestId, k.ok, k.data, k.error);
          return;
        }
        if (r.ok) this.sidecar.invalidateTools(); // FIX68: the tool set changed
        this.bridge.postHostResult(payload.requestId, r.ok, r.data, r.error);
        return;
      }
      // ── connectors.uninstall — forwarded as before, plus FIX68: the host's
      //    cached tool list must forget the connector's tools, or the model is
      //    offered tools the sidecar no longer has. ──
      case "connectors.uninstall": {
        const r = await this.sidecar.hostQuery("connectors.uninstall", payload.args ?? {});
        if (r.ok) {
          this.sidecar.invalidateTools();
          this.log.info(`CONNECTOR uninstalled: ${String(payload.args?.["connectorId"] ?? "?")} (tool list invalidated)`);
        }
        this.bridge.postHostResult(payload.requestId, r.ok, r.data, r.error);
        return;
      }
      // ── OAuth connectors — FIX31 Part 2: HOST-side (RFC 8252: system
      //    browser, PKCE, loopback; token in the host's encrypted store; the
      //    page learns THAT a connector is connected, never the token). With
      //    no owner registration file every connector answers
      //    oauth-not-configured; without the module at all, oauth-unsupported. ──
      case "connectors.oauthConnect":
      case "connectors.oauthDisconnect":
      case "connectors.oauthStatus": {
        const oauth = this.opts.connectorOAuth;
        if (!oauth) {
          this.log.info(`HOST QUERY ${payload.op}: oauth unsupported (no host flow wired in this build)`);
          this.bridge.postHostResult(payload.requestId, false, undefined, "oauth-unsupported");
          return;
        }
        if (payload.op === "connectors.oauthStatus") {
          const ids = Array.isArray(payload.args?.["connectorIds"]) ? (payload.args["connectorIds"] as unknown[]).filter((x): x is string => typeof x === "string") : [];
          this.bridge.postHostResult(payload.requestId, true, { statuses: oauth.statuses(ids) });
          return;
        }
        const connectorId = typeof payload.args?.["connectorId"] === "string" ? (payload.args["connectorId"] as string) : "";
        if (!connectorId) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "invalid");
          return;
        }
        if (payload.op === "connectors.oauthConnect") {
          const r = await oauth.connect(connectorId);
          this.log.info(`HOST QUERY connectors.oauthConnect ${connectorId}: ${r.ok ? "connected" : r.error}`);
          if (r.ok) this.bridge.postHostResult(payload.requestId, true, { connectorId, connected: true, expiresAt: r.expiresAt });
          else this.bridge.postHostResult(payload.requestId, false, undefined, r.error);
          return;
        }
        const r = await oauth.disconnect(connectorId);
        this.opts.connectorTools?.forget(connectorId);
        this.log.info(`HOST QUERY connectors.oauthDisconnect ${connectorId}: ${r.ok ? `disconnected revoked=${r.revoked}` : r.error}`);
        if (r.ok) this.bridge.postHostResult(payload.requestId, true, { connectorId, connected: false, revoked: r.revoked });
        else this.bridge.postHostResult(payload.requestId, false, undefined, r.error);
        return;
      }
      // ── addCustomDialog — a custom stdio MCP server, added NATIVELY: the user
      //    picks the program (no page-named process), approves the spawn in a
      //    native dialog, and only then is it test-spawned + installed. ──
      case "connectors.addCustomDialog": {
        const result = await this.addCustomConnector();
        if (result.error) this.bridge.postHostResult(payload.requestId, false, undefined, result.error);
        else this.bridge.postHostResult(payload.requestId, true, { added: result.added });
        return;
      }
      // ── addRemoteDialog — FIX69: a remote (https) MCP server. The url and the
      //    key are collected in a HOST-owned form, checked against the target
      //    policy, confirmed natively, then test-dialled + installed by the
      //    sidecar. The page learns only added / cancelled / a reason code. ──
      case "connectors.addRemoteDialog": {
        const result = await this.addRemoteConnector();
        if (result.error) this.bridge.postHostResult(payload.requestId, false, undefined, result.error);
        else this.bridge.postHostResult(payload.requestId, true, { added: result.added });
        return;
      }
      // ── tasks.runNow — a task's run is a HOST turn (§1.1) ──
      case "tasks.runNow": {
        const id = payload.args?.["taskId"];
        if (typeof id !== "string") {
          this.bridge.postHostResult(payload.requestId, false, undefined, "invalid");
          return;
        }
        // Answer the page immediately; the turn runs in the background.
        this.bridge.postHostResult(payload.requestId, true, { taskId: id, started: true });
        void this.fireTaskById(id);
        return;
      }
      // ── FIX1 §1.2: the one new page op — the browser-folder escape hatch. The
      //    page names NO path; the host opens a native dialog and forwards the
      //    folder the USER chose to the sidecar (browser.applyExtensionFolder). ──
      case "browser.pickExtensionFolder": {
        const picked = await this.opts.pickFolder();
        if (!picked) {
          this.bridge.postHostResult(payload.requestId, false, undefined, "cancelled");
          return;
        }
        this.log.info(`BROWSER pick extension folder: ${picked}`);
        const r = await this.sidecar.hostQuery("browser.applyExtensionFolder", { path: picked });
        this.bridge.postHostResult(payload.requestId, r.ok, r.data, r.error);
        return;
      }
      // ── everything else: forward to the sidecar's host-only surface ──
      // (skills.list/setEnabled/delete, tasks.list/create/update/setEnabled/delete,
      //  connectors.listCatalog/listInstalled/uninstall, globalNotes.list/delete).
      default: {
        const r = await this.sidecar.hostQuery(payload.op, payload.args ?? {});
        this.bridge.postHostResult(payload.requestId, r.ok, r.data, r.error);
        return;
      }
    }
  }

  /** FIX2: add a custom stdio MCP connector, entirely through NATIVE host
   * dialogs. The user picks the server's program (the page names no process),
   * the command is derived (node/python script → the interpreter; else the file
   * itself), the SPAWN is consented in a native dialog, and only then is the
   * server test-spawned + installed via the sidecar. Nothing runs without that
   * explicit native approval — the §6 stop-condition gate. */
  private async addCustomConnector(): Promise<{ added: boolean; error?: string }> {
    if (!this.opts.pickExecutableFile || !this.opts.confirmSpawn) return { added: false, error: "addcustom-unsupported" };
    const file = await this.opts.pickExecutableFile();
    if (!file) return { added: false }; // cancelled
    const lower = file.toLowerCase();
    const base = file.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
    let command = file;
    let args: string[] = [];
    if (/\.(mjs|cjs|js)$/.test(lower)) { command = "node"; args = [file]; }
    else if (/\.py$/.test(lower)) { command = "python"; args = [file]; }
    const id = `custom-${base.replace(/[^a-zA-Z0-9_-]/g, "-").toLowerCase()}`;
    const name = base || "Custom connector";
    const approved = await this.opts.confirmSpawn(spawnConsentMessage(name, `${command} ${args.join(" ")}`));
    if (!approved) return { added: false }; // spawn consent declined
    const connector = { id, name, command, args };
    const test = await this.sidecar.hostQuery("connectors._testCustom", { connector });
    const t = test.data as { ok?: boolean; toolCount?: number; error?: string } | undefined;
    if (!test.ok || !t?.ok) return { added: false, error: `test-failed:${t?.error ?? "no tools"}` };
    const install = await this.sidecar.hostQuery("connectors._installCustom", { connector });
    if (!install.ok) return { added: false, error: install.error ?? "install-failed" };
    // FIX68: the sidecar dialled the server and cached its tools during
    // _installCustom; the HOST's tools/list cache must be dropped too, or the
    // new tools reach the model only after a restart.
    this.sidecar.invalidateTools();
    this.log.info(`CONNECTOR added (custom stdio): ${id} (${t.toolCount} tool(s); tool list invalidated)`);
    return { added: true };
  }

  /** FIX69: add a remote (https) MCP connector. The url and the optional key
   * come from the HOST-owned form (never the page); the target is checked
   * against the policy (https only; internal addresses only when the shipped
   * config allows; metadata never); the exact origin is confirmed natively;
   * then the sidecar test-dials with the key over the private pipe and, on
   * success, installs (the key goes to the DPAPI store there). Nothing here
   * logs, keeps or returns the key. */
  private async addRemoteConnector(): Promise<{ added: boolean; error?: string }> {
    if (!this.opts.promptRemoteConnector || !this.opts.confirmRemote) return { added: false, error: "addremote-unsupported" };
    const form = await this.opts.promptRemoteConnector();
    if (!form) return { added: false }; // cancelled
    const check = await checkRemoteTarget(form.url, { allowPrivate: this.opts.allowPrivateNetwork === true });
    if (!check.ok) {
      this.log.info(`CONNECTOR remote REFUSED (${check.reason}) host=${check.host ?? hostnameOf(form.url)}`);
      return { added: false, error: `remote-refused:${check.reason}` };
    }
    const origin = check.origin!;
    const host = check.host!;
    const id = `custom-${origin.replace(/^https:\/\//, "").replace(/[^a-zA-Z0-9_-]/g, "-").toLowerCase()}`;
    const keyed = form.key.length > 0;
    const header = form.authHeader.trim();
    const approved = await this.opts.confirmRemote(remoteConsentMessage(host, origin, keyed, header));
    if (!approved) return { added: false }; // declined
    const connector = { id, name: host, transport: "http", serverUrl: form.url.trim(), ...(header ? { authHeader: header } : {}) };
    // The key rides ONLY in these two host→sidecar calls (private stdio pipe,
    // never logged by either side) and lands in the DPAPI store.
    const args: Record<string, unknown> = keyed ? { connector, apiToken: form.key } : { connector };
    const test = await this.sidecar.hostQuery("connectors._testCustom", args);
    if (!test.ok) return { added: false, error: test.error ?? "test-failed" };
    const t = test.data as { ok?: boolean; toolCount?: number; error?: string; code?: string } | undefined;
    if (!t?.ok) {
      this.log.info(`CONNECTOR remote test FAILED (${t?.code ?? "?"}) host=${host}`);
      return { added: false, error: `test-failed:${t?.code ?? "failed"}` };
    }
    const install = await this.sidecar.hostQuery("connectors._installCustom", args);
    if (!install.ok) return { added: false, error: install.error ?? "install-failed" };
    this.sidecar.invalidateTools();
    this.log.info(`CONNECTOR added (custom http): ${id} → ${origin} key=${keyed ? "set" : "none"} (${t.toolCount} tool(s); tool list invalidated)`);
    return { added: true };
  }

  /** FIX2 §1.1: fire a scheduled task NOW as a host turn. Only the host has the
   * turn engine + session; the sidecar holds the store. Used by tasks.runNow and
   * the 60 s scheduler. Creates/reuses a conversation, runs the prompt, records
   * the result back to the sidecar's store. */
  async fireTaskById(taskId: string): Promise<void> {
    try {
      const listed = await this.sidecar.hostQuery("tasks.list", {});
      const tasks = (listed.data as { tasks?: Array<{ id: string; name: string; prompt: string; conversationId?: string | null }> } | undefined)?.tasks ?? [];
      const task = tasks.find((t) => t.id === taskId);
      if (!task) {
        this.log.warn(`TASK fire: ${taskId} not found`);
        return;
      }
      await this.fireTask(task);
    } catch (err) {
      this.log.warn(`TASK fire ${taskId} failed: ${(err as Error).message}`);
    }
  }

  async fireTask(task: { id: string; name?: string; prompt: string; conversationId?: string | null; permissionMode?: string }, opts?: { outcome?: "ok" | "catchup"; scheduledFor?: string | null }): Promise<void> {
    let conversationId = task.conversationId ?? null;
    if (!conversationId) {
      conversationId = await this.chatEdge.createConversation(this.opts.forceModel ?? this.opts.defaultModel);
      if (!conversationId) {
        this.log.warn(`TASK ${task.id}: could not create a conversation; recording an error`);
        await this.sidecar.hostQuery("tasks._recordRun", { taskId: task.id, result: "Error: could not create a conversation.", outcome: "error" });
        return;
      }
      try {
        await this.chatEdge.renameConversation(conversationId, `⏰ ${task.name ?? "Task"}`);
      } catch {
        /* cosmetic */
      }
    }
    this.log.info(`TASK FIRE ${task.id} (${task.name ?? "?"}) → conv=${conversationId} mode=${task.permissionMode ?? "(default)"}${opts?.outcome === "catchup" ? " [catch-up]" : ""}`);
    const controller = new AbortController();
    let text = "";
    let errored = false;
    let taskMessages: GatewayMessage[] = [];
    let conversation: ConversationDto | null = null;
    const model = this.opts.forceModel ?? this.opts.defaultModel;
    // FIX8 A1.3: the task's own permission mode governs its run's approvals (a
    // manual-mode task stalls on a tool it lacks permission for). Sequential runs
    // (the scheduler fires one at a time), so a single active-mode field is safe.
    this.activeTaskApprovalMode = parseApprovalMode(task.permissionMode);
    this.activeTaskId = task.id;
    this.taskNeededPermission = [];
    try {
      await this.refreshBrowserConnected();
      conversation = await this.chatEdge.getConversation(conversationId).catch(() => null);
      const built = await this.buildMessages(conversationId, conversation, task.prompt);
      taskMessages = built.messages;
      // FIX32 §1.2: the task path gets the same token budget as an interactive
      // turn (an old alarm conversation grows too). The FULL chain persists.
      // FIX130 A2 — a task's conversation follows the same memory rule as any
      // chat: no memory-write tool unless its memory is active.
      const taskMemory = memoryActive(conversation);
      const taskTools = await this.toolDefinitions(null, false, false, taskMemory);
      const taskBasis = await this.budgetBasis(conversationId, model, controller.signal);
      const tcc = this.opts.contextManager
        ? await this.opts.contextManager.compile(conversationId, built.messages, { model, basis: taskBasis, numTools: taskTools.length, memoryEnabled: taskMemory, omittedOlder: built.omittedOlder }, controller.signal)
        : compileChain(built.messages, taskBasis, taskTools.length);
      if (tcc.compacted) this.log.info(`CONTEXT COMPILED conv=${conversationId} (task ${task.id}) status=compacted total=${tcc.totalMessages} sent=${tcc.sentMessages} dropped=${tcc.dropped}`);
      const taskChainStart = tcc.messages.length;
      await this.runLoop({
        conversationId,
        model,
        messages: tcc.messages,
        signal: controller.signal,
        maxIterations: this.opts.maxIterations ?? 100,
        maxMs: (this.opts.maxTurnMinutes ?? 20) * 60_000,
        tools: taskTools,
        memoryEnabled: taskMemory,
        // The task's prompt is the user's own words (they wrote the task).
        userMessage: task.prompt,
        parentToolCallId: null,
        onText: (t) => {
          text += t;
        },
      });
      if (tcc.altered) taskMessages.push(...tcc.messages.slice(taskChainStart));
    } catch (err) {
      text = `Error: ${(err as Error).message}`;
      errored = true;
    } finally {
      this.activeTaskApprovalMode = null;
      this.activeTaskId = null;
    }
    // FIX28 §3 (G4): a task run is a turn like any other — persist its chain
    // locally and its visible transcript to chat-edge, so the alarm-clock
    // conversation shows the prompt and the answer instead of staying empty.
    try {
      // FIX109 Part A: the same two writes as an interactive turn — the marker
      // first as if the import fails, then advanced by the rows it inserted.
      if (taskMessages.length > 0) {
        appendAssistantReply(taskMessages, text);
        this.saveLocalHistory(conversationId, taskMessages.slice(1), nextMarker(conversation?.message_count, 0));
      }
      const taskTurn = { messageId: "asst_" + randomUUID().replace(/-/g, ""), assistantText: text } as RunningTurn;
      const imported = await this.importVisible(conversationId, task.prompt, taskTurn, model, []);
      if (taskMessages.length > 0) this.saveLocalHistory(conversationId, taskMessages.slice(1), nextMarker(conversation?.message_count, imported));
      this.log.info(`TASK TRANSCRIPT ${task.id} conv=${conversationId}: roles=[${roleSequence(taskMessages).join(",")}]`);
    } catch (err) {
      this.log.warn(`TASK ${task.id}: transcript persist failed: ${(err as Error).message}`);
    }
    // FIX31 §1.2: a run that hit a consent request while nobody was there ends
    // as "needs_permission" with the tool(s) named — History says so instead of
    // showing a green "Ran" over an action that never happened.
    const needed = this.taskNeededPermission;
    this.taskNeededPermission = [];
    if (needed.length > 0) this.log.info(`TASK ${task.id} NEEDED PERMISSION (unattended): ${needed.map((n) => `${n.tool}@${n.scope}`).join(", ")}`);
    await this.sidecar.hostQuery("tasks._recordRun", {
      taskId: task.id,
      result: text.slice(0, 500),
      conversationId,
      outcome: errored ? "error" : needed.length > 0 ? "needs_permission" : (opts?.outcome ?? "ok"),
      reason: needed.length > 0 ? [...new Set(needed.map((n) => n.tool))].join(", ") : null,
      scheduledFor: opts?.scheduledFor ?? null,
    });
  }

  /** FIX31 §1.2: a scheduled task runs unattended. A tool call that would need
   *  the user's permission is NOT performed and NOT prompted for — a card nobody
   *  can reach is the FIX30 bug again, and the app never answers for the user.
   *  The model is told plainly; the run's history line records the tool. To run
   *  such a task unattended the owner sets its permission mode accordingly
   *  (see the report — it is the owner's call, not a default). */
  private refuseUnattended(call: GatewayToolCall, run: { messages: GatewayMessage[]; conversationId: string; parentToolCallId: string | null; consentConversationId?: string }, name: string, scope: string, args: Record<string, unknown>): void {
    const eventConv = run.consentConversationId ?? run.conversationId;
    this.taskNeededPermission.push({ tool: name, scope });
    run.messages.push(toolResult(call, `Error: '${name}' needs the user's permission (scope: ${scope}), and this is a scheduled task running unattended — nobody is there to answer, and the app never answers on the user's behalf. The action was NOT performed. In your final answer say exactly what you needed permission for, then stop; do not look for another route around it.`));
    this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Needs permission — nobody there to answer (scheduled task)", parentId: run.parentToolCallId, durationMs: 0 });
    this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "needs-permission-unattended", ok: false, where: "host" });
    this.log.info(`TOOL ${name} NEEDS PERMISSION — scheduled task ${this.activeTaskId} runs unattended; not performed (scope=${scope})`);
  }

  /** FIX8 A1.6: apply an update_task_schedule call to the task currently firing. */
  private async rescheduleActiveTask(args: Record<string, unknown>): Promise<string> {
    if (!this.activeTaskId) return "Error: update_task_schedule is only available while a scheduled task is running.";
    const cron = typeof args["cron"] === "string" && (args["cron"] as string).trim() ? (args["cron"] as string).trim() : undefined;
    const prompt = typeof args["prompt"] === "string" && (args["prompt"] as string).trim() ? (args["prompt"] as string).trim() : undefined;
    if (!cron && !prompt) return "Nothing to change — provide a new cron and/or prompt.";
    const r = await this.sidecar.hostQuery("tasks._reschedule", { taskId: this.activeTaskId, cron, prompt });
    if (!r.ok) return `Error: could not reschedule this task (${r.error === "invalid" ? "invalid cron" : r.error ?? "unknown"}).`;
    const next = (r.data as { nextRunAt?: string } | undefined)?.nextRunAt;
    return `Rescheduled this task${cron ? ` to '${cron}'` : ""}${prompt ? (cron ? " and updated its prompt" : " with a new prompt") : ""}.${next ? ` Next run: ${next}.` : ""}`;
  }

  // ── events ─────────────────────────────────────────────────────────────────
  /** FIX33 — the context manager's events (compaction_status, context_budget)
   *  travel on the SAME turn-event channel as compaction_notice. */
  emitTurnEvent(conversationId: string, event: string, data: unknown): void {
    this.emit(conversationId, event, data);
  }

  private emit(conversationId: string, event: string, data: unknown): number {
    let log = this.logs.get(conversationId);
    if (!log) {
      log = { seq: 0, events: [], runId: randomUUID(), terminal: null };
      this.logs.set(conversationId, log);
    }
    const seq = ++log.seq;
    log.events.push({ seq, event, data });
    this.bridge.postTurnEvent(conversationId, seq, event, data, log.runId);
    // FIX41 Part F — the run's frames go to disk as they are emitted, so a
    // turn that the app is killed in the middle of leaves a record. Purely
    // best-effort inside the store: bookkeeping never breaks a turn.
    this.runLedger?.append(log.runId, { seq, event, data });
    if (TERMINAL_TURN_EVENTS.has(event)) {
      log.terminal = event;
      this.runLedger?.end(log.runId);
    }
    return seq;
  }

  /** FIX41 — the backstop for "however it ends, it says so": if the run
   *  for this conversation never emitted one of the three terminal events,
   *  emit `error` now. Called from the turn's catch AND its finally, so a
   *  throw, an early return, and a path nobody has written yet all end the
   *  same way instead of leaving the page streaming forever.
   *
   *  No-op when the conversation has no run, when the run already ended,
   *  or when it never emitted anything (nothing was ever opened). */
  private emitTerminalIfMissing(conversationId: string, err: { code: string; message: string }): void {
    const log = this.logs.get(conversationId);
    if (!log || log.terminal || log.events.length === 0) return;
    this.log.warn(`TURN had no terminal event conv=${conversationId} run=${log.runId} — emitting ${err.code}`);
    this.emitError(conversationId, err);
  }

  /** FIX118 — every terminal `error` frame leaves through here. A host
   *  code that has a catalogue equivalent is rewritten to it (the original
   *  rides details.host_code); a code nothing maps is passed through AND
   *  logged, so a missing code is visible in the log rather than invented
   *  on the wire. The page maps by code; both surfaces then show the same
   *  sentence for the same cause. */
  private emitError(conversationId: string, frame: HostErrorFrame): number {
    const { frame: out, mapped } = normalizeHostError(frame);
    if (mapped) this.log.info(`TURN ERROR conv=${conversationId}: host code ${frame.code} → ${out.code} (cause ${out.cause})`);
    else if (!/^[a-z_]+$/.test(frame.code) || frame.code.startsWith("edit_") || frame.code === "model_not_accepted") this.log.warn(`TURN ERROR conv=${conversationId}: code ${frame.code} is not in the shared catalogue — passed through unmapped`);
    return this.emit(conversationId, "error", out);
  }

  /** FIX41 — start a new run for this conversation.
   *
   *  `seq` is deliberately carried FORWARD rather than reset. Two guards
   *  keep a second turn's frames from being dropped by a page cursor left
   *  over from the first (this one, and bridge.ts clearing the cursor on
   *  submit); FIX41 adds the run id so correctness stops depending on
   *  either of them holding. Only the replay buffer is cleared — it exists
   *  to serve THIS run. */
  private beginRun(conversationId: string): string {
    const runId = randomUUID();
    this.logs.set(conversationId, { seq: this.logs.get(conversationId)?.seq ?? 0, events: [], runId, terminal: null });
    this.runLedger?.begin(conversationId, runId);
    return runId;
  }

  /** FIX41 Part F — attach the on-disk run ledger. Optional: without it
   *  the host behaves exactly as chunk 1 did (a turn interrupted by the
   *  app closing leaves no trace). */
  setRunLedger(store: RunLedgerStore): void {
    this.runLedger = store;
  }

  /**
   * FIX41 Part F2 — settle runs the previous process was in the middle of.
   *
   * Called once at start-up. For each run whose file has no terminal line,
   * the frames are restored into memory so the page can still be handed
   * the answer that was produced, and then the run is CLOSED with an
   * `error`. Before this, closing the app mid-turn left the page with
   * nothing at all and the conversation with no record that the turn had
   * ever happened — neither the question nor the partial answer, because
   * both are persisted only after runLoop returns.
   *
   * Returns how many runs were settled (for the log and for tests).
   */
  settleInterruptedRuns(): number {
    if (!this.runLedger) return 0;
    let settled = 0;
    for (const run of this.runLedger.pending()) {
      // Never step on a live turn: if this process has already started one
      // for the conversation, the stale file is simply reaped.
      if (this.turns.has(run.conversationId) || this.logs.has(run.conversationId)) {
        this.runLedger.forget(run.runId);
        continue;
      }
      this.logs.set(run.conversationId, {
        seq: run.lastSeq,
        events: run.events.map((e) => ({ seq: e.seq, event: e.event, data: e.data })),
        runId: run.runId,
        terminal: null,
      });
      this.log.warn(`RUN INTERRUPTED conv=${run.conversationId} run=${run.runId} — ${run.events.length} frame(s) recovered, closing it`);
      // FIX118 — the same code chat-edge's resume replay ends on, so a
      // page reading either surface shows the same sentence.
      this.emitError(run.conversationId, {
        code: "generation_interrupted",
        message: hostText("error.interrupted"),
        cause: "interrupted",
      });
      settled++;
    }
    this.runLedger.sweep();
    return settled;
  }

  // ── the turn ───────────────────────────────────────────────────────────────
  private async runTurn(payload: TurnSubmitPayload, turn: RunningTurn): Promise<void> {
    const { conversationId } = payload;
    const signal = turn.controller.signal;
    // FIX11 §3: an EDIT forks the conversation server-authoritatively via chat-edge
    // (not a host gateway turn). Relay its stream; the page re-fetches and the
    // ◀ n/m ▶ navigator reflects the new branch.
    if (payload.editMessageId) {
      await this.runEditFork(payload, turn);
      return;
    }
    const turnNo = ++this.turnCounter;
    const runId = this.beginRun(conversationId);
    this.log.info(`TURN RUN conv=${conversationId} run=${runId}`);
    turn.runId = runId;
    this.emit(conversationId, "message_start", { message_id: turn.messageId, conversation_id: conversationId });

    let conversation: ConversationDto | null = null;
    try {
      conversation = await this.chatEdge.getConversation(conversationId);
    } catch {
      /* logged */
    }
    const model = this.opts.forceModel ?? payload.modelId ?? conversation?.model_id ?? this.opts.defaultModel;
    // FIX9 §1.1: the model here can be page-supplied (payload.modelId or the
    // conversation's model, both set by the renderer). Validate it against the
    // models the gateway actually accepts; an unknown/unexpected value is REFUSED,
    // not passed through — a page that could name any model string could steer a
    // turn somewhere nobody reviewed.
    if (!(await this.isModelAccepted(model, signal))) {
      this.log.warn(`TURN #${turnNo} conv=${conversationId}: REFUSED unknown model "${model}"`);
      this.emitError(conversationId, { code: "model_not_accepted", message: hostText("error.modelNotAccepted", { model }) });
      return;
    }

    let messages: GatewayMessage[];
    let userText = payload.text;
    let omittedOlder = 0;
    let attached: ResolvedAttachments | null = null;
    let memoryUsed: MemoryUsedItem[] = [];
    // FIX124 — the page sends Regenerate, and Retry of a failed turn, as a
    // submit with EMPTY text and nothing attached. buildMessages recovers the
    // question from the history so the model is asked it again; the flag is
    // decided HERE, from what the page sent, because after that line
    // `userText` is non-empty and nothing downstream can tell a regenerate
    // from a person typing the same words. It was imported as a second user
    // row (U, A1, U', A2) on every Regenerate.
    const isRegenerate = payload.text.length === 0 && payload.attachmentHandles.length === 0;
    let regenerate: RegenerateTarget | null = null;
    // FIX29 §3.2: know whether the browser bridge is up BEFORE the prompt is
    // built, so the prompt can say "browser access is unavailable" instead of
    // the model improvising a tab list from a screenshot.
    await this.refreshBrowserConnected();
    try {
      // FIX27 Part 1: attachments are content on THIS user message — resolved by
      // opaque chat-edge id (never a path), images as image_url blocks,
      // documents as the untrusted-observation envelope chat-edge uses.
      attached = await this.resolveTurnAttachments(conversationId, payload.attachmentHandles, signal);
      const built = await this.buildMessages(conversationId, conversation, payload.text, attached);
      messages = built.messages;
      userText = built.userText;
      omittedOlder = built.omittedOlder;
      memoryUsed = built.memoryUsed;
      if (isRegenerate) regenerate = await this.regenerateTarget(conversationId);
    } catch (err) {
      this.log.error(`TURN #${turnNo} conv=${conversationId}: could not build messages: ${(err as Error).message}`);
      this.emitError(conversationId, { code: "host_exception", message: hostText("error.notPrepared") });
      return;
    }
    // FIX3 §3: if the sidecar is still starting (first launch pays ~6 s of native
    // extraction + connector refresh + browser provisioning), show its REAL phase
    // instead of a silent hang — and end legibly if it never comes up.
    await this.awaitSidecarReady(conversationId);

    // Snapshot the browser bridge once for the whole turn (§3.3): the tool list
    // is fixed for the turn, so it cannot flap if the bridge drops mid-turn.
    await this.refreshBrowserConnected();

    const historyStart = 1; // everything after the system message is persisted
    this.log.info(`TURN START #${turnNo} conv=${conversationId} model=${model} msgId=${turn.messageId} textLen=${payload.text.length} attachments=${payload.attachmentHandles.length}${attached && payload.attachmentHandles.length ? ` (${attached.imageBlocks.length} image, ${attached.attached.length - attached.imageBlocks.length} document, ${attached.failures.length} failed → on the user message)` : ""}`);
    if (attached && attached.failures.length > 0) {
      // Never drop a file silently: the page shows the names (same event web-chat uses).
      this.emit(conversationId, "image_notice", { filenames: attached.failures.map((f) => f.filename) });
    }
    // FIX130 A3d — the prompt carried saved memories: the page shows
    // "ใช้ความจำ N รายการ" on this answer. The same frame chat-edge sends the
    // web (before the answer, keyed to the assistant bubble); it rides the run
    // log, so a page that reattaches mid-turn is replayed it. Desktop turns
    // bypass chat-edge's context_sources, so after a reload the marker is not
    // there for a desktop turn — live only on this surface.
    if (memoryUsed.length > 0) {
      this.emit(conversationId, "memory_used", { message_id: turn.messageId, count: memoryUsed.length, items: memoryUsed });
      this.log.info(`MEMORY: ${memoryUsed.length} memory item(s) in the prompt conv=${conversationId}`);
    }
    // FIX32 §1.2 — token-budgeted context (context-compiler.ts, chat-edge's
    // shape). The FULL chain still persists locally; only what is SENT is
    // trimmed, the first exchange survives, tool chains stay atomic, and both
    // the model (context note) and the user (compaction_notice → the page's
    // marker) are told. Silently forgetting is the failure mode this exists
    // to prevent.
    // FIX34 Part A: a quick-ask turn carries NO tools — nothing to consent to,
    // so the small window never shows a consent card (Claude's quick entry is
    // answer-only too). The rest of the turn is identical.
    // FIX47.6 Part C: a tools-blind model gets no tools either — offering
    // them made every turn die on model_no_tool_support (the mid-turn tool
    // wall this round removes). The picker's guidance line tells the user
    // such a model answers without tools.
    const modelTakesTools = payload.quick ? false : await this.modelSupportsTools(model, signal);
    // The chat's memory switch (chat-edge's conversations.memory_enabled — the
    // same row the web client toggles). Absent field means on, so a client or
    // a server build that never sends it cannot silently turn memory off.
    //
    // FIX130 A2 — what the turn uses is "memory active": the switch on AND
    // not a temporary chat. A memory-off or temporary chat reads no memory
    // (the assembler) and writes none (no memory-write tool, `remember`
    // refused at dispatch — the run carries this value), and keeps no summary
    // state (the context manager is told the same), mirroring chat-edge.
    const memoryEnabled = memoryActive(conversation);
    if (!memoryEnabled) {
      this.log.info(`MEMORY: not active for conv=${conversationId} (${conversation?.is_temporary === true ? "temporary chat" : "memory off"}) — reads none, writes none, no memory-write tools`);
    }
    const tools = payload.quick || !modelTakesTools ? [] : await this.toolDefinitions(null, false, payload.imageMode, memoryEnabled);
    if (payload.quick) this.log.info(`TOOLS: quick-ask turn conv=${conversationId} — no tools offered`);
    else if (!modelTakesTools) this.log.info(`TOOLS: skipped for tools-blind model conv=${conversationId} model=${model}`);
    // FIX33 A-D2: with a context manager the middle is SUMMARISED before it
    // goes (state store + extraction, the state riding as a user-role pair);
    // without one, FIX32's trim + honest note.
    // FIX104: measured against the catalogue's window for the model that answers.
    const basis = await this.budgetBasis(conversationId, model, signal);
    const cc = this.opts.contextManager
      ? await this.opts.contextManager.compile(conversationId, messages, { model, basis, numTools: tools.length, memoryEnabled, omittedOlder }, signal)
      : compileChain(messages, basis, tools.length, { omittedOlder });
    const sendMessages = cc.messages;
    if (cc.compacted) {
      // FIX104 (the web's FIX102 Part C): with a context manager the page is
      // told only when no summary stands in for everything left out; the
      // meter says "sees N of M" on every trimmed turn. Without one (pure
      // tests, probes) FIX32's plain notice stays.
      const notice = "lossNotice" in cc ? cc.lossNotice : { dropped: cc.dropped, sent: cc.sentMessages, total: cc.totalMessages };
      if (notice) this.emit(conversationId, "compaction_notice", notice);
      this.log.info(`CONTEXT COMPILED conv=${conversationId} status=compacted total=${cc.totalMessages} sent=${cc.sentMessages} dropped=${cc.dropped} hist_tokens=${cc.historyTokens} budget=${cc.budget.messagesRemaining}/${cc.budget.total} window=${cc.budget.window || "default"}${cc.budget.model ? ` of ${cc.budget.model}` : ""}${"summaryStatus" in cc ? ` summary=${cc.summaryStatus}` : ""} model=${model}`);
    }
    // FIX104 — chat-edge's applyContextHints: on auto, the router is told what
    // the compiled request needs, so it cannot pick a model that would refuse it.
    const routingContext = isAutoModel(model)
      ? { minContextTokens: cc.budget.system + cc.budget.toolReserve + cc.sentTokens + cc.budget.outputReserve + (cc.compacted ? CONTEXT_NOTE_RESERVE : 0), preferredContextTokens: cc.budget.window }
      : undefined;
    // FIX9 Part B: image mode (the 🎨 button) — bias the model to call generate_image
    // and offer it this turn. A system hint on the existing message, not a new type.
    //
    // FIX61: the hint is FOLDED INTO the leading system message, never spliced
    // in as a second one. Some OpenAI-compatible servers accept a system
    // message only at index 0 — the self-hosted qwen3.8-27b-fp8 answers
    // `[system, system, user]` with 400 "System message must be at the
    // beginning", and because the toggle stays on, every later turn of that
    // chat failed the same way while a new chat worked. chat-edge folds its
    // own hints the same way (appendSystemHint).
    if (payload.imageMode && !payload.quick) {
      foldSystemHint(sendMessages, "The user has turned on image mode. If they are asking for a picture, drawing, logo, or any visual, call the generate_image tool with a detailed prompt rather than describing the image in words.");
    }
    // FIX34 Part A: the quick-ask hint — same mechanism as image mode. The model
    // is told it has no tools HERE and where they are, so a request that needs
    // files / the browser / a command gets "open the main window" instead of a
    // made-up result.
    if (payload.quick) {
      foldSystemHint(sendMessages, QUICK_ASK_HINT);
    }
    const chainStart = sendMessages.length; // where this turn's chain begins
    this.log.info(`TURN OUTBOUND conv=${conversationId}: ${sendMessages.length} msg → [${describe(sendMessages)}]`);
    const startedAt = Date.now();
    const outcome = await this.runLoop({
      conversationId,
      model,
      messages: sendMessages,
      signal,
      maxIterations: this.opts.maxIterations ?? 100,
      maxMs: (this.opts.maxTurnMinutes ?? 20) * 60_000,
      tools,
      memoryEnabled,
      userMessage: userText,
      routingContext,
      newestOverBudget: cc.newestOverBudget,
      parentToolCallId: null,
      onText: (t) => {
        turn.assistantText += t;
      },
      onServedModel: (m) => {
        turn.servedModel = m;
      },
      onFallback: (from, actual) => {
        // FIX120 — the answering model is the row's model; the requested
        // one is kept beside it. A later model_resolved may still refine
        // servedModel (a router id → the concrete model).
        turn.fallbackFrom = from;
        turn.servedModel = actual;
      },
      onEnding: (e) => {
        turn.errored = e;
      },
      onStopReason: (r) => {
        turn.stopReason = r;
      },
      runId,
    });
    const elapsed = Date.now() - startedAt;
    // FIX118 — how this turn ended, as the imported assistant row will say
    // it. Before, a failed or stopped turn was imported as a plain row and
    // became `completed` on the server.
    const ending = endingForOutcome(outcome, turn.errored ?? null);
    this.lastReply.set(conversationId, turn.assistantText);

    // FIX28 §5.1: the assistant's final answer joins the chain — this chain is
    // exactly what the next turn sends to the provider, and until now it held
    // only the user's side. Logged so the sent role sequence is visible.
    appendAssistantReply(sendMessages, turn.assistantText);
    this.log.info(`TURN CHAIN conv=${conversationId}: roles=[${roleSequence(sendMessages).join(",")}]`);
    // Persist EVERYTHING after the system message locally (tool chains
    // included), and the visible text to chat-edge. On cancel too (Phase 6a
    // P1: a cancelled turn must be resumable). When the SENT chain was
    // compacted, what persists is the FULL untrimmed history plus this turn's
    // new tail — compaction affects one request, never the stored past.
    const appendedChain = sendMessages.slice(chainStart);
    // `altered` (FIX33) covers both a trim and an injected state pair: neither
    // may ever enter the stored chain.
    const fullChain = cc.altered ? [...messages, ...appendedChain] : sendMessages;
    // FIX109 Part A: the chain is written with the server marker it is current
    // with — twice. First as if the import fails (the count read at the start
    // of the turn), then with the rows the import inserted. A crash between
    // the two leaves a marker the next load reads as "server ahead" — the
    // direction that re-reads the transcript rather than trusting the file.
    const chain = fullChain.slice(historyStart);
    this.saveLocalHistory(conversationId, chain, nextMarker(conversation?.message_count, 0));
    const imported = await this.importVisible(conversationId, userText, turn, model, appendedChain, attached?.fileIds ?? [], ending, runId, regenerate);
    this.saveLocalHistory(conversationId, chain, nextMarker(conversation?.message_count, imported));
    // FIX28 §2 (F3): the page renders both rows under client ids; hand it the
    // persisted chat-edge ids so Edit (fork), regenerate and feedback can name
    // a real message without leaving and re-entering the conversation.
    const ids = await this.persistedIds(conversationId, turn, regenerate !== null);
    // FIX35 Part B — message_complete repeats the served model (an auto
    // turn's page-side meta line names the router's pick).
    // FIX119 — why the model stopped rides message_complete, so the page can
    // say "the answer was cut off" instead of presenting a max_tokens cut as
    // finished.
    const completeData = { message_id: turn.messageId, ...ids, ...(turn.servedModel ? { model_id: turn.servedModel } : {}), ...(turn.stopReason ? { stop_reason: turn.stopReason } : {}), ...(turn.fallbackFrom ? { fallback_from: turn.fallbackFrom } : {}) };

    // A turn that has been SUPERSEDED (the user sent again, which aborts the
    // old one) must not write a terminal: the page has already moved on to
    // the new run, and closing its sink would strand it. Found while adding
    // `cancelled` — the same race existed before with message_complete, it
    // was just less visible because the page treated it as a normal finish.
    if (this.turns.get(conversationId) !== turn) {
      this.log.info(`TURN SUPERSEDED conv=${conversationId} — no terminal from the old run`);
      return;
    }
    if (outcome === "error") return; // runLoop already emitted the terminal `error`
    if (outcome === "cancelled") {
      // FIX41 — a person pressed Stop. Saying `message_complete` here made
      // a stopped turn indistinguishable from a finished one for anything
      // reading the stream rather than the button.
      this.emit(conversationId, "cancelled", { ...completeData, partial: true, cause: "stopped_by_user" });
      this.log.info(`TURN CANCELLED conv=${conversationId} run=${runId} after ${elapsed} ms, ${turn.assistantText.length} chars kept`);
      return;
    }
    if (outcome === "paused") {
      // A limit, not a person and not a failure: the answer stands and can
      // be continued. Still `message_complete`, but it says why it stopped
      // instead of leaving that only in the prose.
      this.emit(conversationId, "message_complete", { ...completeData, paused: true });
      this.log.info(`TURN PAUSED conv=${conversationId} after ${elapsed} ms`);
      return;
    }
    // FIX122 — the host no longer names the chat. It asked `defaultModel`
    // (not the model that answered) and renamed unconditionally, over a
    // title the person had typed; and the page was ALSO asking chat-edge, so
    // every first turn was titled twice. chat-edge's generate-title is the
    // one place now: the page calls it when this terminal arrives, after the
    // import above has put the rows (and the served model) on the server.
    this.emit(conversationId, "message_complete", completeData);
    this.log.info(`TURN DONE conv=${conversationId} run=${runId} in ${elapsed} ms, ${turn.assistantText.length} chars, stop_reason=${turn.stopReason || "-"}`);
  }

  /** FIX11 §3: a hosted EDIT is a server-authoritative fork. Rather than run the
   * host's own gateway turn, call chat-edge's /edit — which forks the branch,
   * streams the assistant reply, AND persists it (parent_id/branch_index/
   * is_active) — and relay the SSE to the page through the same turn.event
   * vocabulary a normal turn uses. No local persist: chat-edge is authoritative
   * for the fork, and the page re-fetches to update the ◀ n/m ▶ navigator. */
  private async runEditFork(payload: TurnSubmitPayload, turn: RunningTurn): Promise<void> {
    const { conversationId } = payload;
    const signal = turn.controller.signal;
    const editId = payload.editMessageId as string;
    this.beginRun(conversationId);
    this.emit(conversationId, "message_start", { message_id: turn.messageId, conversation_id: conversationId });
    this.log.info(`TURN EDIT-FORK conv=${conversationId} editOf=${editId} textLen=${payload.text.length}`);
    const res = await this.chatEdge.editStream(conversationId, editId, payload.text, signal);
    if (!res || !res.body) {
      this.emit(conversationId, "error", { code: "edit_failed", message: hostText("error.editFailed") });
      return;
    }
    const reader = (res.body as ReadableStream<Uint8Array>).getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          this.relayEditFrame(conversationId, frame, turn);
        }
      }
    } catch (err) {
      if (!signal.aborted) this.emit(conversationId, "error", { code: "edit_stream_error", message: hostText("error.editStream", { reason: (err as Error).message }) });
    }
    this.lastReply.set(conversationId, turn.assistantText);
    this.emit(conversationId, "message_complete", { message_id: turn.messageId });
    this.log.info(`TURN EDIT-FORK DONE conv=${conversationId}, ${turn.assistantText.length} chars`);
  }

  /** Translate one chat-edge SSE frame (event:/data:) into the host's turn.event
   * vocabulary. message_start/complete are host-driven (see runEditFork); only the
   * content/reasoning/error deltas are relayed. */
  private relayEditFrame(conversationId: string, frame: string, turn: RunningTurn): void {
    let event = "";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!event || event === "message_start" || event === "message_complete") return;
    const dataStr = dataLines.join("\n");
    let data: Record<string, unknown> = {};
    try {
      data = dataStr ? (JSON.parse(dataStr) as Record<string, unknown>) : {};
    } catch {
      return;
    }
    switch (event) {
      case "content_delta":
        if (typeof data["delta"] === "string") {
          turn.assistantText += data["delta"];
          this.emit(conversationId, "content_delta", { delta: data["delta"] });
        }
        break;
      case "reasoning_delta":
        if (typeof data["delta"] === "string") this.emit(conversationId, "reasoning_delta", { delta: data["delta"] });
        break;
      case "error":
        this.emit(conversationId, "error", { code: String(data["code"] ?? "edit_error"), message: String(data["message"] ?? "The edit failed.") });
        break;
      default:
        break;
    }
  }

  /** ChatOrchestrator.RunAsync — shared by the top-level turn and nested runs. */
  private async runLoop(run: {
    conversationId: string;
    model: string;
    messages: GatewayMessage[];
    signal: AbortSignal;
    maxIterations: number;
    maxMs: number;
    tools: GatewayToolDefinition[];
    /** The chat's memory switch. Absent means on. Carried on the run so the
     *  dispatch guard can refuse a memory write on every path, including a
     *  sub-agent that inherits it from the chat that spawned it. */
    memoryEnabled?: boolean;
    /** FIX130 — the text the USER sent this turn (a sub-agent inherits its
     *  parent turn's). `remember` saves an active memory only when it asks to
     *  remember; anything else becomes a pending suggestion. */
    userMessage?: string;
    /** FIX104 — auto only: what the compiled request needs (routing_hints). */
    routingContext?: { minContextTokens: number; preferredContextTokens: number };
    /** FIX105 — the compiler saw the newest message alone over the budget. */
    newestOverBudget?: boolean;
    parentToolCallId: string | null;
    onText: (t: string) => void;
    /** FIX35 Part B — the gateway named the model that is answering. */
    onServedModel?: (model: string) => void;
    /** FIX120 — the gateway's fallback chain answered: (requested, actual). */
    onFallback?: (requestedModel: string, actualModel: string) => void;
    /** FIX118 — called once when the run ends on an error frame, with the
     *  cause and code the frame carried. */
    onEnding?: (e: { cause?: TerminalCause; errorCode: string; attempts?: unknown[] }) => void;
    /** FIX119 — called on every `done` with the closed-set stop reason of
     *  that round ("" when nothing was said); the last one is the turn's. */
    onStopReason?: (r: StopReason | "") => void;
    /** FIX119 — the turn's run id, sent to the gateway as X-Request-Id. */
    runId?: string;
    onTrace?: (entry: string) => void;
    /** nested: consent prompts are attributed to this conversation on the page */
    consentConversationId?: string;
  } & RunWaitState): Promise<"done" | "cancelled" | "error" | "paused" | "denied"> {
    const { conversationId, signal } = run;
    const eventConv = run.consentConversationId ?? conversationId;
    // FIX31: time spent waiting for the user's consent is NOT run time — a
    // deadline that counted it would end (or, nested, abort) a run for the
    // crime of asking. The hard-stop timer in runNested applies the same rule.
    const deadline = Date.now() + run.maxMs;
    const overdue = () => Date.now() >= deadline + (run.waitedMs ?? 0);
    let iteration = 0;
    // FIX64 — the stale-catalog repair, at most once per run (see the
    // error case below).
    let toolsRetried = false;
    // FIX72 — a reasoning model whose thinking overran the output budget
    // (output_limit_reached: the round ended on finish_reason=length with no
    // answer and no tool call — measured on qwen3.8-27b-fp8 for ~20% of
    // ambiguous single-round prompts) is retried ONCE with thinking off.
    // Measured: with thinking off the same ambiguous prompt called the tool
    // 5/5 in ~40 completion tokens. Once set, thinking stays off for the rest
    // of THIS run (the model has shown it over-thinks this task); it is a
    // no-op at the gateway for any model whose box has no off-switch, so a
    // model that cannot be quieted simply surfaces the error on the retry.
    let thinkingOff = false;
    while (iteration < run.maxIterations && !overdue()) {
      if (signal.aborted) return "cancelled";
      iteration++;
      if (!run.parentToolCallId) this.emit(eventConv, "status", { status: iteration === 1 ? "thinking" : `continuing (step ${iteration})`, iteration, max: run.maxIterations });
      pruneScreenshots(run.messages, KEEP_SCREENSHOTS);
      pruneAttachedImages(run.messages, KEEP_ATTACHED_IMAGES, isScreenshotMessage);
      // FIX65 — a model the catalogue says cannot read images gets none:
      // the blocks become a note the model can see, and the page is told.
      if (!(await this.modelSupportsVision(run.model, signal))) {
        const removed = stripImageBlocks(run.messages);
        if (removed > 0) {
          this.log.info(`IMAGES: ${run.model} cannot read images per the catalogue — ${removed} image block(s) replaced by a note conv=${conversationId}`);
          if (!run.parentToolCallId) this.emit(eventConv, "image_notice", { failures: [{ filename: `${removed} image(s)`, reason: "this model cannot read images; the turn was sent without them" }] });
        }
      }

      const calls = new Map<number, { id: string; name: string; args: string }>();
      let errored = false;
      let sawDone = false;
      let sawContent = false;
      let resendWithoutTools = false;
      for await (const ev of this.gateway.streamCompletion(
        { model: run.model, messages: run.messages, max_tokens: MAX_OUTPUT_TOKENS, tools: run.tools.length ? run.tools : undefined, tool_choice: run.tools.length ? "auto" : undefined, reasoning: { mode: thinkingOff ? "off" : "auto", show_summary: !thinkingOff }, routingContext: run.routingContext },
        signal,
        { requestId: run.runId },
      )) {
        if (signal.aborted) return "cancelled";
        switch (ev.kind) {
          case "content_delta":
            sawContent = true;
            run.onText(ev.delta);
            if (!run.parentToolCallId) this.emit(eventConv, "content_delta", { delta: ev.delta });
            break;
          case "tool_call_delta": {
            const cur = calls.get(ev.index) ?? { id: "", name: "", args: "" };
            if (ev.id) cur.id = ev.id;
            if (ev.name) cur.name += ev.name;
            cur.args += ev.argumentsDelta;
            calls.set(ev.index, cur);
            break;
          }
          case "reasoning_delta":
            if (!run.parentToolCallId) this.emit(eventConv, "reasoning_delta", { delta: ev.delta });
            break;
          case "analysis_status":
            if (!run.parentToolCallId) this.emit(eventConv, "analysis_status", { status: ev.status });
            break;
          case "queued":
            // FIX133 A2 — the page shows "waiting in queue · position N" the
            // same way the web page does; position 0 clears it.
            if (!run.parentToolCallId) {
              this.emit(eventConv, "queued", {
                position: ev.position,
                ahead: ev.ahead,
                active: ev.active,
                waiting: ev.waiting,
                max_active: ev.max_active,
                estimate_s: ev.estimate_s,
              });
            }
            break;
          case "fallback_info":
            // FIX120 — attribute the turn to the model that answered.
            if (ev.requested_model && ev.actual_model && ev.requested_model !== ev.actual_model) run.onFallback?.(ev.requested_model, ev.actual_model);
            if (!run.parentToolCallId) this.emit(eventConv, "fallback_info", { requested_model: ev.requested_model, actual_model: ev.actual_model, reason: ev.reason });
            break;
          case "dlp_notice":
            // FIX50 P1-C — same quiet-notice surface as fallback_info.
            if (!run.parentToolCallId) this.emit(eventConv, "dlp_notice", { findings: ev.findings, by_detector: ev.by_detector });
            break;
          case "model_resolved":
            // FIX35 Part B — attribute the turn to the model that answered and
            // tell the page now, not only at message_complete.
            run.onServedModel?.(ev.model);
            if (!run.parentToolCallId) this.emit(eventConv, "model_resolved", { model: ev.model });
            break;
          case "reasoning_notice":
            if (!run.parentToolCallId) this.emit(eventConv, "reasoning_notice", { model: ev.model, reason: ev.reason });
            break;
          case "error": {
            // FIX72 — the round ran out of output budget before answering or
            // calling a tool (a reasoning model spending the whole budget
            // thinking). Retry the run ONCE with thinking off rather than
            // killing it. Only before anything of this round was shown and no
            // tool call landed; guarded to once by `thinkingOff`.
            if (
              ev.code === "output_limit_reached" && !thinkingOff && !sawContent && calls.size === 0
            ) {
              thinkingOff = true;
              this.log.info(`REASONING: ${run.model} hit its output limit before answering — retrying conv=${conversationId} with thinking off`);
              resendWithoutTools = true; // reuse: abandon this stream, re-enter the same step
              break;
            }
            // FIX64 — the one error this host causes itself: it offered
            // tools from a model list the admin has since changed (the
            // list is cached for MODELS_TTL_MS; the gateway refuses at
            // once). Re-ask against a fresh list and, if the model is now
            // tools-blind, resend the same turn without tools instead of
            // showing a dead turn. Once per run, before anything of the
            // reply has been shown, never mid-chain.
            if (
              ev.code === "model_no_tool_support" && run.tools.length > 0 && !toolsRetried &&
              iteration === 1 && !sawContent && calls.size === 0
            ) {
              toolsRetried = true;
              this.acceptedModels = null;
              this.acceptedModelsAt = 0;
              const stillTakesTools = await this.modelSupportsTools(run.model, signal);
              if (!stillTakesTools) {
                this.log.info(`TOOLS: gateway refused tools for ${run.model} and the fresh model list agrees — resending conv=${conversationId} without tools`);
                run.tools = [];
                resendWithoutTools = true;
                break;
              }
              this.log.warn(`TOOLS: gateway refused tools for ${run.model} but the fresh model list still says it takes tools — surfacing the error`);
            }
            // FIX65 — the same repair for images: the list said the model
            // sees, the gateway says it does not. Re-fetch; if the model is
            // now known to be blind, replace the image blocks with a note,
            // tell the page, and resend once.
            if (ev.code === "model_no_vision_support" && !toolsRetried && iteration === 1 && !sawContent && calls.size === 0) {
              toolsRetried = true;
              this.acceptedModels = null;
              this.acceptedModelsAt = 0;
              const sees = await this.modelSupportsVision(run.model, signal);
              const removed = sees ? 0 : stripImageBlocks(run.messages);
              if (removed > 0) {
                this.log.info(`IMAGES: gateway refused images for ${run.model} and the fresh model list agrees — resending conv=${conversationId} without ${removed} image block(s)`);
                if (!run.parentToolCallId) this.emit(eventConv, "image_notice", { failures: [{ filename: `${removed} image(s)`, reason: "this model cannot read images; the turn was sent without them" }] });
                resendWithoutTools = true;
                break;
              }
              this.log.warn(`IMAGES: gateway refused images for ${run.model} but the fresh model list still says it sees (or nothing to strip) — surfacing the error`);
            }
            errored = true;
            this.log.error(`TURN ERROR conv=${conversationId} run=${run.runId ?? "-"}: ${ev.code} ${ev.message}${ev.details && typeof ev.details["gateway_request_id"] === "string" ? ` gateway_request_id=${ev.details["gateway_request_id"]}` : ""}`);
            // FIX22 §1b: a guardrail 403 names no cause. Explain it AFTER the
            // fact from the labelled parts of the prompt we sent — diagnostic
            // only (the turn is already refused), appended to the error the
            // page shows, never stored in a message that gets re-sent.
            // FIX105 — a refusal of a request whose newest message alone was over
            // the budget: no trim could have fixed it, and "this conversation is
            // too long" would send the person to shorten the wrong thing.
            // chat-edge renames the same case (message_too_long.go).
            const tooLong = ev.code === "context_length_exceeded" && run.newestOverBudget === true;
            const code = tooLong ? MESSAGE_TOO_LONG_CODE : ev.code;
            let message = tooLong ? MESSAGE_TOO_LONG_TEXT : ev.message;
            if (ev.code === "gateway_forbidden" && /guardrail/i.test(ev.message)) {
              message += PromptGuard.explain403(this.promptPartsByConversation.get(run.conversationId) ?? []);
            }
            // FIX118 — the frame carries the gateway's own verdict and the
            // terminal cause; the row imported for this turn records both.
            const cause: TerminalCause = ev.cause ?? "error";
            // FIX121 — and the per-model attempt log: on the frame for the
            // page's "Connection attempts", on the row for the day after.
            run.onEnding?.({ cause, errorCode: code, ...(ev.attempts ? { attempts: ev.attempts } : {}) });
            if (!run.parentToolCallId) this.emitError(eventConv, { code, message, ...(ev.retryable === undefined ? {} : { retryable: ev.retryable }), cause, ...(ev.details ? { details: ev.details } : {}), ...(ev.attempts ? { attempts: ev.attempts } : {}) });
            else run.onText(`\n[error: ${ev.message}]`);
            break;
          }
          case "done": {
            sawDone = true;
            // FIX119 — why the model stopped. Read here rather than thrown
            // away: a max_tokens cut used to look exactly like a clean finish.
            const reason = stopReasonFor(ev.stop_reason, ev.finish_reason);
            run.onStopReason?.(reason);
            if (reason === "max_tokens") this.log.warn(`TURN OUTPUT LIMIT conv=${conversationId} run=${run.runId ?? "-"}: the answer was cut at max_tokens (${ev.provider_request_id ? `provider_request_id=${ev.provider_request_id}` : "no provider request id"})`);
            break;
          }
        }
        if (errored || resendWithoutTools) break;
      }
      if (resendWithoutTools) {
        // Same step, same messages, no tools: the gateway's stream is
        // abandoned (nothing of it was shown) and the loop re-enters.
        iteration--;
        continue;
      }
      if (errored) return "error";
      if (signal.aborted) return "cancelled";
      void sawDone;

      const pending: GatewayToolCall[] = [...calls.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([i, c]) => ({ id: c.id || `call_${i}_${randomUUID().slice(0, 8)}`, type: "function" as const, function: { name: c.name, arguments: c.args || "{}" } }));
      if (pending.length === 0) return "done";

      // The assistant message WITH tool_calls goes into history first.
      const assistantText = lastAssistantDelta(run.messages);
      run.messages.push({ role: "assistant", content: assistantText, tool_calls: pending });
      // FIX72 — a round may carry MORE THAN ONE tool_use block (the browser
      // batch contract; measured 1/round for qwen and deepseek today, so this
      // is forward-correctness). Run them in the order they appear and
      // STOP at the first failure: the later calls were planned assuming the
      // earlier ones succeeded, so each is answered with the exact contract
      // text rather than executed against a state that never happened. Only
      // engages for a real batch (pending.length > 1); a single-call round is
      // byte-identical to before.
      let batchFailed = false;
      for (const call of pending) {
        if (signal.aborted) {
          run.messages.push(toolResult(call, "Not executed: the turn was cancelled."));
          continue;
        }
        if (overdue()) {
          run.messages.push(toolResult(call, "Not executed: the turn was paused at its time limit. It can be resumed to continue."));
          continue;
        }
        if (batchFailed) {
          run.messages.push(toolResult(call, NOT_EXECUTED_AFTER_FAILURE));
          this.activity(eventConv, call.id, call.function.name, "finished", { success: false, resultPreview: "Not executed (earlier action failed)", parentId: run.parentToolCallId, durationMs: 0 });
          continue;
        }
        await this.executeToolCall(call, run);
        // FIX31 §1.1: the user refused one of this sub-agent's tools — the run
        // ends here, FAILED, instead of asking again (or trying another route).
        if (run.deniedTool && run.parentToolCallId) return "denied";
        if (pending.length > 1 && toolResultErrored(run.messages, call.id)) batchFailed = true;
      }
      if (overdue()) {
        run.onText("\n\nPaused: reached the 20-minute limit. The task can be continued.");
        if (!run.parentToolCallId) this.emit(eventConv, "content_delta", { delta: "\n\nPaused: reached the time limit. The task can be continued." });
        return "paused";
      }
    }
    if (iteration >= run.maxIterations) {
      const note = `\n\nPaused: reached the ${run.maxIterations}-step limit. The task can be continued.`;
      run.onText(note);
      if (!run.parentToolCallId) this.emit(eventConv, "content_delta", { delta: note });
    }
    return "paused";
  }

  /** ChatOrchestrator.ExecuteToolCallAsync — DLP marker → permission → consent → run → result. */
  private async executeToolCall(
    call: GatewayToolCall,
    run: { conversationId: string; messages: GatewayMessage[]; signal: AbortSignal; parentToolCallId: string | null; onTrace?: (entry: string) => void; consentConversationId?: string; tools: GatewayToolDefinition[]; memoryEnabled?: boolean; userMessage?: string } & RunWaitState,
  ): Promise<void> {
    const name = call.function.name;
    const eventConv = run.consentConversationId ?? run.conversationId;
    let args: Record<string, unknown> = {};
    try {
      const parsed = JSON.parse(call.function.arguments || "{}") as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) args = parsed as Record<string, unknown>;
    } catch (err) {
      run.messages.push(toolResult(call, `Error: invalid tool arguments JSON: ${(err as Error).message}`));
      this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Invalid arguments JSON", parentId: run.parentToolCallId });
      return;
    }
    const meta = toolMeta(name);
    // FIX77 ② — the row label is client-side (i18n); the host adds the one
    // human-useful argument (subject) and keeps the full dump (argsSummary)
    // for the card's expanded detail — the consent card's argumentSummary
    // (requestConsent below) is a separate, unchanged string.
    const subject = toolSubject(name, args);
    this.activity(eventConv, call.id, name, "started", { argsSummary: summarizeArgs(args), ...(subject ? { subject } : {}), parentId: run.parentToolCallId, ...(name === "run_python" ? { isCodeRun: true, codeText: String(args["code"] ?? "") } : {}) });

    const dlp = dlpBlocked(args);
    if (dlp !== null) {
      run.messages.push(toolResult(call, "Error: the gateway's DLP policy blocked this tool call's arguments (sensitive content was masked). Explain this to the user and try a different approach if possible."));
      this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: `Blocked by DLP (${dlp})`, parentId: run.parentToolCallId, durationMs: 0 });
      this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "dlp-blocked", ok: false, where: "host" });
      return;
    }
    const known = run.tools.some((t) => t.function.name === name);
    if (!known) {
      run.messages.push(toolResult(call, `Error: unknown tool '${name}'.`));
      this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Unknown tool", parentId: run.parentToolCallId, durationMs: 0 });
      return;
    }

    // FIX8 A1.6: a task reschedules ITSELF. Host-managed, no consent (it edits only
    // this task's own cron/prompt) and only valid while THIS task is firing.
    if (name === "update_task_schedule") {
      const outcome = await this.rescheduleActiveTask(args);
      run.messages.push(toolResult(call, outcome));
      this.activity(eventConv, call.id, name, "finished", { success: !outcome.startsWith("Error"), resultPreview: outcome.slice(0, 80), parentId: run.parentToolCallId, durationMs: 0 });
      this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "auto", ok: !outcome.startsWith("Error"), where: "host" });
      return;
    }

    // FIX73: ask_user_question never enters the permission path below — a
    // stored approval or the Skip mode must never "answer" a question for the
    // user (the rule: it always falls through to a person). It is also
    // withheld from nested and scheduled runs at the offer site; this guard is
    // the same fact at the only point every path passes through.
    if (name === ASK_USER_TOOL) {
      if (run.parentToolCallId || this.activeTaskId !== null) {
        run.messages.push(toolResult(call, NOT_AVAILABLE_IN_THIS_RUN));
        this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Not available in this run", parentId: run.parentToolCallId, durationMs: 0 });
        this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "ask-unavailable", ok: false, where: "host" });
        this.log.info(`TOOL ${name} REFUSED — ${run.parentToolCallId ? "sub-agent" : "scheduled task"} runs cannot ask the user`);
        return;
      }
      const v = validateAskArgs(args);
      if (!v.ok) {
        run.messages.push(toolResult(call, `Error: ${name}: ${v.reason}`));
        this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: `Invalid question: ${v.reason}`.slice(0, 200), parentId: run.parentToolCallId, durationMs: 0 });
        this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "ask-invalid", ok: false, where: "host" });
        this.log.info(`TOOL ${name} REFUSED — invalid arguments: ${v.reason}`);
        return;
      }
      const t0 = Date.now();
      const answer = await this.requestQuestion(eventConv, run.conversationId, call.id, v.questions, run.signal, run);
      const durationMs = Date.now() - t0;
      if ("cancelled" in answer) {
        run.messages.push(toolResult(call, CANCELLED_WHILE_ASKING));
        this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Cancelled", parentId: run.parentToolCallId, durationMs });
        this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "ask-cancelled", ok: false, where: "host" });
        return;
      }
      const content = formatAnswerForModel(v.questions, answer);
      run.messages.push(toolResult(call, content));
      this.activity(eventConv, call.id, name, "finished", { success: true, resultPreview: content.slice(0, 200), parentId: run.parentToolCallId, durationMs });
      this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: `ask-${answer.outcome}`, ok: true, where: "host" });
      this.log.info(`TOOL ${name} ${answer.outcome} after ${durationMs} ms — ${content.slice(0, 120).replace(/\n/g, " ")}`);
      return;
    }

    // Permission (PermissionService + ApprovalMode + SensitiveReadGuard).
    const grants = this.grants.list();
    // FIX8 A1.3: a firing task's own mode wins during its run; otherwise the
    // interactive mode, read live (FIX25 §1b) so a composer change applies to
    // THIS call onward — never to a consent already decided (§1c.4).
    const mode = this.activeTaskApprovalMode ?? (await this.liveMode.current());
    let scope: string;
    let decision: PermissionDecision;
    let decisionLabel: string;
    let authorizedSite: string | null = null;

    if (meta.category === "Browser" && BROWSER_SITE_TOOLS.has(name)) {
      // ── FIX6: the per-SITE gate (Claude-in-Chrome model). ──────────────────
      // The site is resolved from the TAB / destination, never from a model-named
      // site (§1.2): a tab-acting tool reads its target tab's LIVE origin from the
      // bridge; navigate/open_url use their destination url (which can only PROMPT
      // for an unapproved origin — naming it cannot self-approve it).
      const resolved = await this.resolveBrowserSite(name, args, grants, run.conversationId, run.signal);
      if (!resolved.ok) {
        run.messages.push(toolResult(call, `Error: could not determine which website this action targets (${resolved.error}); it cannot be authorized. Ask the user to open or name the page first.`));
        this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Site unresolved", parentId: run.parentToolCallId, durationMs: 0 });
        this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "site-unresolved", ok: false, where: "host" });
        this.log.warn(`TOOL ${name} REFUSED — site unresolved (${resolved.error})`);
        return;
      }
      scope = resolved.site;
      authorizedSite = resolved.site;
      const ev = evaluateBrowserSite(name, args, resolved.site, this.permissions, mode, resolved.url, resolved.elementLabel);
      if (ev.refused) {
        run.messages.push(toolResult(call, `Error: not allowed — ${ev.refused.reason} is refused in every mode (including Skip). Tell the user you cannot do this and why; do not try another route.`));
        this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: `Refused: ${ev.refused.reason}`, parentId: run.parentToolCallId, durationMs: 0 });
        this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: `refused:${ev.refused.reason}`, ok: false, where: "host" });
        this.log.warn(`TOOL ${name} REFUSED (always-refused: ${ev.refused.reason}) site=${resolved.site}`);
        return;
      }
      decision = ev.decision;
      decisionLabel = decision === "Auto" ? "auto" : "allowed";
      if (decision === "NeedsConsent") {
        if (this.activeTaskId !== null) {
          this.refuseUnattended(call, run, name, resolved.site, args);
          return;
        }
        const danger = classifyDanger(name, meta.category);
        const response = await this.requestConsent(eventConv, run.conversationId, call.id, name, meta, args, resolved.site, danger.reasonKey || "danger.browser", run.signal, { site: resolved.site, alwaysPromptReason: ev.alwaysPrompt }, run.requester, run);
        if (response.decision === "deny") {
          run.messages.push(toolResult(call, "Error: the user declined permission for this action on this site."));
          this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Declined by user", parentId: run.parentToolCallId, durationMs: 0 });
          this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "denied", ok: false, where: "host" });
          this.log.info(`TOOL ${name} DECLINED by the user (site=${resolved.site})${run.requester ? ` [sub-agent ${run.requester.label || run.requester.subagentId}]` : ""}`);
          if (run.parentToolCallId) run.deniedTool = name;
          return;
        }
        // "always" → persist the SITE grant (covers every browser tool on it);
        // "once" → nothing recorded, so the next action on this site asks again.
        if (response.scope === "always") this.permissions.persist(BROWSER_SITE_PERM, resolved.site, "allow_always");
        decisionLabel = `allowed-${response.scope}`;
      }
    } else {
      scope = resolveScope(meta, args, grants, this.opts.homeDirectory, this.cwdByConversation.get(run.conversationId) ?? null);
      decision = applyApprovalMode(this.permissions.check(meta, scope), meta, mode);
      if (decision === "Auto" && mode !== "skip" && readRequiresConsent(meta, args, grants, this.opts.homeDirectory) && !this.permissions.hasStoredDecision(name, scope)) {
        decision = "NeedsConsent";
      }
      decisionLabel = decision === "Auto" ? "auto" : "allowed";
      if (decision === "NeedsConsent") {
        if (this.activeTaskId !== null) {
          this.refuseUnattended(call, run, name, scope, args);
          return;
        }
        const danger = classifyDanger(name, meta.category);
        const response = await this.requestConsent(eventConv, run.conversationId, call.id, name, meta, args, scope, danger.reasonKey || "danger.generic", run.signal, undefined, run.requester, run);
        if (response.decision === "deny") {
          run.messages.push(toolResult(call, "Error: the user denied permission for this tool call."));
          this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Permission denied by user", parentId: run.parentToolCallId, durationMs: 0 });
          this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "denied", ok: false, where: "host" });
          this.log.info(`TOOL ${name} DENIED by the user (scope=${scope})${run.requester ? ` [sub-agent ${run.requester.label || run.requester.subagentId}]` : ""}`);
          if (run.parentToolCallId) run.deniedTool = name;
          return;
        }
        // once → run, ask again next time; session → no re-prompt this session.
        if (response.scope === "session") this.permissions.recordSession(name, scope, "AllowAlways");
        decisionLabel = `allowed-${response.scope}`;
      } else if (decision === "Denied") {
        run.messages.push(toolResult(call, "Error: this tool is blocked by user policy."));
        this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Blocked by policy", parentId: run.parentToolCallId, durationMs: 0 });
        this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "policy-denied", ok: false, where: "host" });
        return;
      }
    }

    if (name === "run_command" && typeof args["working_directory"] === "string" && args["working_directory"]) {
      this.cwdByConversation.set(run.conversationId, args["working_directory"] as string);
    }

    // A chat with memory turned off must not write one. The tool is already
    // withheld from such a turn, so reaching here means the model asked for a
    // tool it was not offered — refuse rather than save. The refusal is an
    // Error for the same reason the guardrail one below is: the model cannot
    // then tell the user it remembered something it did not. Checked here as
    // well as at the offer site because this is the only point every path
    // (main turn, sub-agent, scheduled task) passes through.
    // FIX130 A2 — run.memoryEnabled is "memory active", so a temporary chat
    // (whatever its switch says) is refused here too.
    if (name === "remember" && run.memoryEnabled === false) {
      run.messages.push(toolResult(call, "Error: memory is off for this chat (or this is a temporary chat), so nothing can be saved. Tell the user plainly that you did not save it."));
      this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Not saved — memory is off for this chat", parentId: run.parentToolCallId, durationMs: 0 });
      this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: "memory-off", ok: false, where: "host" });
      this.log.info(`TOOL remember REFUSED (memory is off for conversation ${run.conversationId})`);
      return;
    }

    // FIX22 §1a: `remember` writes the sidecar's durable note store, and that
    // note is folded into every later prompt. A note that matches a guardrail
    // rule would 403 every future turn — and `remember` is a tool the model can
    // call on its own, so a poisoned page can plant one. Ask the gateway FIRST
    // (the sidecar holds no key; the host is the choke point). The tool result
    // is an Error, so the model cannot truthfully report a save that did not
    // happen. The refused note is never dispatched to the sidecar.
    if (name === "remember" && this.opts.promptGuard) {
      const note = typeof args["note"] === "string" ? (args["note"] as string) : "";
      const verdict = await this.opts.promptGuard.checkForWrite(note, "this note");
      if (verdict.blocked) {
        const msg = PromptGuard.refusalMessage(verdict, "this note");
        run.messages.push(toolResult(call, msg));
        this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: `Refused: would trip guardrail ${verdict.rule}`, parentId: run.parentToolCallId, durationMs: 0 });
        this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: `guardrail-refused:${verdict.decidedBy}`, ok: false, where: "host" });
        this.log.warn(`TOOL remember REFUSED (would poison the prompt; rule ${verdict.rule}, decided by ${verdict.decidedBy})`);
        return;
      }
    }

    // Execute — host or sidecar.
    const t0 = Date.now();
    let ok = false;
    let text = "";
    let image: string | null = null;
    // FIX79 — every image ONE call produced (run_python alternatives);
    // rides the finished frame so the card renders them all.
    let imagesAll: string[] | null = null;
    let where: "host" | "sidecar" = "sidecar";
    let rpcError: string | undefined;
    const connectorTool = this.opts.connectorTools?.has(name) === true;
    // FIX58: `remember` is split by scope. A global note is a host tool now
    // (it writes chat-edge's store, the same one the web client writes, so
    // the two clients finally remember in one place); a conversation-scoped
    // note stays with the sidecar, because the prompt assembler reads those
    // back from the local store and the server's injection query never
    // returns them. Routing both to the host would save half of them where
    // nothing reads them.
    const hostHandled = this.hostTools.has(name) && !(name === "remember" && rememberScopeOf(args) === "conversation");
    this.log.info(`TOOL ${name} conv=${run.conversationId} decision=${decisionLabel} scope=${scope} → ${connectorTool ? "host (connector)" : hostHandled ? "host" : "sidecar"}`);
    // FIX30 §3.2: a local tool has NO timeout (legitimate ones run long — a
    // 30-ping took 29 s, a home-wide search 25 s). What it gets instead is a
    // heartbeat: every keepalive tick the page is told which tool is running
    // and for how long, so "Working…" never means "unknown", and the page's
    // idle watchdog does not mistake a long tool for a dead stream.
    const heartbeat = setInterval(() => this.emit(eventConv, "status", { status: "tool_running", waiting: "tool", tool: name, toolCallId: call.id, elapsedMs: Date.now() - t0 }), this.opts.waitKeepaliveMs ?? 15_000);
    try {
      if (connectorTool && this.opts.connectorTools) {
        // FIX31 Part 2: the connector call is made HERE with the host's Bearer;
        // the sidecar never sees the tool, the args or the token.
        where = "host";
        const r = await this.opts.connectorTools.execute(name, args, run.signal);
        ok = r.ok;
        text = r.text;
        image = r.imageDataUri;
      } else if (hostHandled) {
        where = "host";
        try {
          // FIX130 — a sub-agent's run id is not a conversation; `remember`
          // names the chat the user is in (eventConv) as a suggestion's source.
          const toolConv = name === "remember" ? eventConv : run.conversationId;
          const r = await this.hostTools.execute(name, args, { conversationId: toolConv, toolCallId: call.id, signal: run.signal, memoryEnabled: run.memoryEnabled, userMessage: run.userMessage });
          ok = r.ok;
          text = r.text;
          image = r.imageDataUri;
          // FIX130 A3b/A4b — a memory was saved (active) or suggested
          // (pending): the next turn must read it (drop the cache), and the
          // page shows "บันทึกความจำแล้ว" / "เสนอความจำใหม่ — รอยืนยัน" on
          // this answer (the frame chat-edge sends the web).
          if (ok && r.memorySaved) {
            this.assembler.invalidateServerMemories();
            const turn = this.turns.get(eventConv);
            if (turn) this.emit(eventConv, "memory_saved", { message_id: turn.messageId, items: [r.memorySaved] });
          }
          imagesAll = r.imageDataUris && r.imageDataUris.length > 1 ? r.imageDataUris : null;
          // FIX80 — the tool persisted files into the conversation
          // (run_python artifacts): show them under the assistant message
          // NOW (message_files) and remember them for the import, which
          // links them to the assistant row (file_ids) so they survive a
          // reload and sync to the web view.
          if (ok && r.attachedFiles?.length) {
            const turn = this.turns.get(eventConv);
            if (turn) {
              turn.attachedFiles = [...(turn.attachedFiles ?? []), ...r.attachedFiles];
              const now = new Date().toISOString();
              this.emit(eventConv, "message_files", {
                message_id: turn.messageId,
                files: turn.attachedFiles.map((f) => ({ ...f, extraction_status: "completed", created_at: now, message_id: turn.messageId })),
              });
            }
          }
        } catch (err) {
          ok = false;
          text = `${name} threw: ${(err as Error).message}`;
        }
      } else {
        const r = await this.sidecar.callTool(name, args, {
          conversationId: run.conversationId,
          grants,
          signal: run.signal,
          authorizedSite: authorizedSite ?? undefined,
          onProgress: (chunk) => this.emit(eventConv, "tool_output", { id: call.id, chunk: chunk + "\n" }),
        });
        ok = r.ok;
        text = r.text;
        image = r.imageDataUri;
        if (r.rpcError) {
          rpcError = `${r.rpcError.code} ${r.rpcError.message}`;
          text = `Error: ${r.rpcError.message}`;
          this.log.warn(`TOOL ${name} REFUSED by the sidecar: ${rpcError}`);
        } else if (name === "read_document" && ok && text.startsWith(OCR_RELAY_MARKER)) {
          // FIX3 §4: a scanned PDF the sidecar could not OCR. It handed us the bytes
          // (PathGuard + SensitiveReadGuard already cleared them sidecar-side); OCR
          // them HOST-side, which holds the session JWT. The host has NO path — only
          // these bytes — so it can never read a file the sidecar refused.
          text = await this.ocrScannedPdf(run.conversationId, text.slice(OCR_RELAY_MARKER.length).trim(), run.signal);
        }
      }
    } finally {
      clearInterval(heartbeat);
    }
    const durationMs = Date.now() - t0;
    if (run.signal.aborted) {
      run.messages.push(toolResult(call, "Not executed to completion: the turn was cancelled."));
      this.activity(eventConv, call.id, name, "finished", { success: false, resultPreview: "Cancelled", parentId: run.parentToolCallId, durationMs });
      return;
    }
    const content = ok ? truncate(text, 50_000) : `Error: ${text}`;
    run.messages.push(toolResult(call, content));
    if (ok && image) run.messages.push(screenshotMessage(name, image));
    if (name === "update_plan" && ok) {
      const steps = parsePlan(args);
      if (steps) this.emit(eventConv, "plan_update", { steps });
    }
    this.activity(eventConv, call.id, name, "finished", {
      success: ok,
      durationMs,
      resultPreview: content.length > 200 ? content.slice(0, 200) + "..." : content,
      parentId: run.parentToolCallId,
      ...(image ? { imageDataUri: image } : {}),
      ...(imagesAll ? { imageDataUris: imagesAll } : {}),
    });
    this.toolCallsSeen.push({ conversationId: run.conversationId, tool: name, args, decision: decisionLabel, ok, where, ...(rpcError ? { rpcError } : {}) });
    run.onTrace?.(`${name}${ok ? "" : " (failed)"}`);
    this.log.info(`TOOL ${name} ${ok ? "ok" : "FAILED"} in ${durationMs} ms (${where}) — ${content.slice(0, 120).replace(/\n/g, " ")}`);
  }

  private activity(conversationId: string, id: string, tool: string, phase: "started" | "finished", extra: Record<string, unknown>): void {
    this.emit(conversationId, "tool_activity", { id, tool, phase, ...extra });
  }

  /** FIX3 §4: OCR a scanned PDF host-side. Input is the sidecar's envelope
   * (bytes it already read, path + consent cleared); the host uploads those
   * bytes to chat-edge and polls for the recovered text. No filesystem path is
   * involved — the host cannot reach a file the sidecar did not hand it. */
  private async ocrScannedPdf(conversationId: string, envelopeJson: string, signal: AbortSignal): Promise<string> {
    let env: { mime?: string; name?: string; pages?: number; b64?: string };
    try {
      env = JSON.parse(envelopeJson);
    } catch {
      return "This scanned document could not be prepared for reading.";
    }
    const name = env.name ?? "document.pdf";
    if (!env.b64) return `The scanned document "${name}" could not be read.`;
    const bytes = Buffer.from(env.b64, "base64");
    const fileId = await this.chatEdge.uploadFileBytes(conversationId, bytes, name, env.mime ?? "application/pdf");
    if (!fileId) {
      return `The scanned document "${name}" could not be sent for reading. Reading a scan needs a chat synced with the SRAN server — start the request in a normal chat and try again.`;
    }
    // Chat-edge OCRs on ingest; poll the extracted text until it completes.
    for (let i = 0; i < 40 && !signal.aborted; i++) {
      const t = await this.chatEdge.getFileText(fileId);
      if (t && t.status === "completed") {
        if (!t.text.trim()) return `The scanned document "${name}" was read but no text could be recovered from it.`;
        let head = `[Scanned PDF: ${name} — ${env.pages ?? "?"} page(s) — text recovered by OCR on the SRAN server]`;
        if (t.truncated) head += "\n[TRUNCATED: this is the beginning of the document, not all of it. Say so if you summarize it.]";
        head += "\n[OCR text may contain recognition errors — quote it with care.]";
        return `${head}\n\n${t.text}`;
      }
      if (t && t.status && !["pending", "processing", "running", "queued", ""].includes(t.status)) {
        return `The scanned document "${name}" could not be read (extraction ${t.status}).`;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    return `The scanned document "${name}" is taking longer than expected to read; please try again.`;
  }

  /** FIX6: resolve the ORIGIN a browser action targets — from the TAB or the
   * destination, NEVER from a model-named site. navigate/open_url gate on their
   * destination url; every tab-acting tool asks the sidecar for the target tab's
   * LIVE origin (browser._siteForAction, a control-plane read over tabs.list). */
  private async resolveBrowserSite(
    name: string,
    args: Record<string, unknown>,
    grants: readonly string[],
    conversationId: string,
    signal: AbortSignal,
  ): Promise<{ ok: true; site: string; url: string | null; elementLabel: string | null } | { ok: false; error: string }> {
    if (name === "browser_navigate" || name === "open_url") {
      const url = typeof args["url"] === "string" ? (args["url"] as string) : "";
      const site = siteScope(url);
      return site ? { ok: true, site, url, elementLabel: null } : { ok: false, error: "the url is not a valid http(s) address" };
    }
    void grants;
    void signal;
    void conversationId;
    const tabId = typeof args["tab_id"] === "number" ? (args["tab_id"] as number) : null;
    try {
      const r = await this.sidecar.hostQuery("browser._siteForAction", tabId === null ? {} : { tabId });
      const d = r.data as { url?: string; site?: string } | undefined;
      if (!r.ok || !d?.site) return { ok: false, error: r.error ?? "no active tab, or the browser bridge is not connected" };
      return { ok: true, site: d.site, url: d.url ?? null, elementLabel: null };
    } catch (err) {
      return { ok: false, error: `bridge read failed: ${(err as Error).message}` };
    }
  }

  private requestConsent(
    eventConv: string,
    conversationId: string,
    toolCallId: string,
    tool: string,
    meta: ReturnType<typeof toolMeta>,
    args: Record<string, unknown>,
    scope: string,
    dangerClass: string,
    signal: AbortSignal,
    siteInfo?: { site: string; alwaysPromptReason: "download" | "sensitive-info" | "authorization" | null },
    requester?: ConsentRequester,
    wait?: RunWaitState,
  ): Promise<ConsentRespondPayload> {
    // FIX31 §1.1: one card at a time per page conversation (see consentQueues).
    // A queued request that is cancelled before its turn is denied without
    // ever being posted — the cancel is the user's own Stop, not a timer.
    const prev = this.consentQueues.get(eventConv) ?? Promise.resolve();
    const mine = prev.then(() => (signal.aborted ? { promptId: "", decision: "deny" as const, scope: "once" as const } : this.requestConsentNow(eventConv, conversationId, toolCallId, tool, meta, args, scope, dangerClass, signal, siteInfo, requester, wait)));
    this.consentQueues.set(eventConv, mine.then(() => undefined, () => undefined));
    return mine;
  }

  private requestConsentNow(
    eventConv: string,
    conversationId: string,
    toolCallId: string,
    tool: string,
    meta: ReturnType<typeof toolMeta>,
    args: Record<string, unknown>,
    scope: string,
    dangerClass: string,
    signal: AbortSignal,
    siteInfo?: { site: string; alwaysPromptReason: "download" | "sensitive-info" | "authorization" | null },
    requester?: ConsentRequester,
    wait?: RunWaitState,
  ): Promise<ConsentRespondPayload> {
    const promptId = `consent-${randomUUID().slice(0, 12)}`;
    const payload = {
      promptId,
      conversationId: eventConv,
      tool,
      description: `${tool} (${meta.category})`,
      category: meta.category,
      argumentSummary: summarizeArgs(args, 200),
      argumentDetail: JSON.stringify(args, null, 2).slice(0, 4000),
      dangerClass,
      toolCallId,
      scope,
      // FIX6: a browser-site prompt carries the origin and offers Anthropic's two
      // grants (once + always). alwaysPromptReason explains a forced prompt on an
      // already-approved site (download / sensitive-info / authorization).
      ...(siteInfo ? { site: siteInfo.site, grantOptions: ["once", "always"] as const, alwaysPromptReason: siteInfo.alwaysPromptReason } : {}),
      // FIX31 §1.1: a sub-agent's request says so — who, and with what task.
      // Never optional for a nested run: the card must not read like the
      // assistant's own request when it is not.
      ...(requester ? { requestedBy: requester } : {}),
    };
    this.log.info(`CONSENT REQUEST ${promptId}: ${tool} scope=${scope} danger=${dangerClass}${siteInfo ? ` site=${siteInfo.site}${siteInfo.alwaysPromptReason ? ` always-prompt=${siteInfo.alwaysPromptReason}` : ""}` : ""}${requester ? ` requested-by=subagent:${requester.label || requester.subagentId}` : ""}`);
    // FIX30 §3.2: the wait is unbounded BY DESIGN (only the user can answer),
    // so the turn must SAY what it is waiting on — once now, and again every
    // keepalive tick. The page renders the text; the ticks are also bytes for
    // its idle watchdog, which otherwise cancelled a turn that was merely
    // waiting for the user (the owner's hang: consent + 183 s → turn.cancel).
    // Emitted on eventConv unconditionally: a nested run's consent still blocks
    // the PARENT turn the page is looking at.
    const waitStarted = Date.now();
    if (wait) wait.waitingSince = waitStarted;
    const keepaliveMs = this.opts.waitKeepaliveMs ?? 15_000;
    const sayWaiting = () => this.emit(eventConv, "status", { status: "waiting_consent", waiting: "consent", tool, promptId, elapsedMs: Date.now() - waitStarted, ...(requester ? { requestedBy: requester } : {}) });
    sayWaiting();
    let ticks = 0;
    const logEvery = Math.max(1, Math.round(60_000 / keepaliveMs));
    const keepalive = setInterval(() => {
      sayWaiting();
      ticks++;
      if (ticks % logEvery === 0) this.log.info(`CONSENT WAITING ${promptId}: ${Math.round((Date.now() - waitStarted) / 1000)} s, no answer yet (${tool})`);
    }, keepaliveMs);
    return new Promise<ConsentRespondPayload>((resolve) => {
      const onAbort = () => {
        if (this.pendingConsents.delete(promptId)) settle({ promptId, decision: "deny", scope: "once" });
      };
      const settle = (r: ConsentRespondPayload) => {
        clearInterval(keepalive);
        signal.removeEventListener("abort", onAbort);
        if (wait) {
          wait.waitedMs = (wait.waitedMs ?? 0) + (Date.now() - waitStarted);
          wait.waitingSince = null;
        }
        this.emit(eventConv, "status", { status: "consent_answered", waiting: null, tool, promptId, decision: r.decision, elapsedMs: Date.now() - waitStarted });
        resolve(r);
      };
      this.pendingConsents.set(promptId, { conversationId, payload, resolve: settle });
      signal.addEventListener("abort", onAbort, { once: true });
      this.bridge.post("consent.request", payload);
    });
  }

  /** FIX73: post the question card and wait for the user. Rides the consent
   *  queue (one card at a time per page conversation), excludes the wait from
   *  the run deadline (FIX31), says what it is waiting on every keepalive tick
   *  (FIX30 §3.2), is re-posted on reload and closed by Stop. Nothing here
   *  consults the permission store or the approval mode. */
  private requestQuestion(
    eventConv: string,
    conversationId: string,
    toolCallId: string,
    questions: readonly AskQuestion[],
    signal: AbortSignal,
    wait?: RunWaitState,
  ): Promise<QuestionRespondPayload | { cancelled: true }> {
    const prev = this.consentQueues.get(eventConv) ?? Promise.resolve();
    const mine = prev.then(() => (signal.aborted ? { cancelled: true as const } : this.requestQuestionNow(eventConv, conversationId, toolCallId, questions, signal, wait)));
    this.consentQueues.set(eventConv, mine.then(() => undefined, () => undefined));
    return mine;
  }

  private requestQuestionNow(
    eventConv: string,
    conversationId: string,
    toolCallId: string,
    questions: readonly AskQuestion[],
    signal: AbortSignal,
    wait?: RunWaitState,
  ): Promise<QuestionRespondPayload | { cancelled: true }> {
    const promptId = `question-${randomUUID().slice(0, 12)}`;
    const payload = { promptId, conversationId: eventConv, toolCallId, questions: questions.map((q) => ({ question: q.question, header: q.header, multiSelect: q.multiSelect, options: q.options.map((o) => ({ label: o.label, description: o.description })) })) };
    this.log.info(`QUESTION REQUEST ${promptId}: ${questions.length} question(s) [${questions.map((q) => q.header).join(", ")}]`);
    const waitStarted = Date.now();
    if (wait) wait.waitingSince = waitStarted;
    const keepaliveMs = this.opts.waitKeepaliveMs ?? 15_000;
    const sayWaiting = () => this.emit(eventConv, "status", { status: "waiting_question", waiting: "question", tool: ASK_USER_TOOL, promptId, elapsedMs: Date.now() - waitStarted });
    sayWaiting();
    let ticks = 0;
    const logEvery = Math.max(1, Math.round(60_000 / keepaliveMs));
    const keepalive = setInterval(() => {
      sayWaiting();
      ticks++;
      if (ticks % logEvery === 0) this.log.info(`QUESTION WAITING ${promptId}: ${Math.round((Date.now() - waitStarted) / 1000)} s, no answer yet`);
    }, keepaliveMs);
    return new Promise((resolve) => {
      const onAbort = () => {
        if (this.pendingQuestions.delete(promptId)) settle({ cancelled: true });
      };
      const settle = (r: QuestionRespondPayload | { cancelled: true }) => {
        clearInterval(keepalive);
        signal.removeEventListener("abort", onAbort);
        if (wait) {
          wait.waitedMs = (wait.waitedMs ?? 0) + (Date.now() - waitStarted);
          wait.waitingSince = null;
        }
        this.emit(eventConv, "status", { status: "question_answered", waiting: null, tool: ASK_USER_TOOL, promptId, outcome: "cancelled" in r ? "cancelled" : r.outcome, elapsedMs: Date.now() - waitStarted });
        resolve(r);
      };
      this.pendingQuestions.set(promptId, { conversationId, payload, resolve: settle });
      signal.addEventListener("abort", onAbort, { once: true });
      this.bridge.post("question.request", payload);
    });
  }

  // ── nested sub-agent runs (host capability, E4 §1 #1) ──────────────────────
  async runNested(req: NestedRunRequest, parentSignal: AbortSignal): Promise<NestedRunResult> {
    const conversationId = `subagent-${randomUUID().replace(/-/g, "")}`;
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    parentSignal.addEventListener("abort", onAbort, { once: true });
    // FIX31 §1.1: every tool a single sub-agent has (run_subagent excepted —
    // still no second level). A call that needs permission asks the USER, on
    // the parent's page, attributed; nothing is auto-approved.
    const tools = await this.toolDefinitions(req.tools, true, false, req.memoryEnabled !== false);
    const messages: GatewayMessage[] = [
      { role: "system", content: subAgentSystemPrompt(this.opts.homeDirectory, this.grants.list()) },
      { role: "user", content: req.task },
    ];
    let answer = "";
    const trace: string[] = [];
    const requester: ConsentRequester = { kind: "subagent", subagentId: conversationId, label: (req.label ?? "").trim(), task: req.task.slice(0, 400) };
    const run = {
      conversationId,
      consentConversationId: req.conversationId,
      model: this.opts.forceModel ?? this.opts.defaultModel,
      messages,
      signal: controller.signal,
      maxIterations: NESTED_MAX_ITERATIONS,
      maxMs: NESTED_MAX_MINUTES * 60_000,
      tools,
      memoryEnabled: req.memoryEnabled !== false,
      userMessage: req.userMessage,
      parentToolCallId: conversationId,
      onText: (t: string) => {
        answer += t;
      },
      onTrace: (e: string) => trace.push(e),
      requester,
      waitedMs: 0,
      waitingSince: null as number | null,
      deniedTool: null as string | null,
    };
    // The hard stop (NESTED_MAX_MINUTES + 0.5) never fires while a consent card
    // is open, and time already spent waiting is added back — a run is stopped
    // for running too long, never for waiting on the user (FIX30's lesson).
    let timer: ReturnType<typeof setTimeout> | null = null;
    let accounted = 0;
    const arm = (ms: number) => {
      timer = setTimeout(() => {
        if (run.waitingSince !== null) return arm(30_000);
        if (run.waitedMs > accounted) {
          const extra = run.waitedMs - accounted;
          accounted = run.waitedMs;
          return arm(extra);
        }
        controller.abort();
      }, ms);
    };
    arm((NESTED_MAX_MINUTES + 0.5) * 60_000);
    this.log.info(`SUBAGENT START ${conversationId} parent=${req.parentToolCallId} tools=${tools.length}${requester.label ? ` label=${requester.label}` : ""}`);
    // FIX29 §2.2: one card per sub-agent. The sub-agent is an activity of its
    // own (id = its conversation id, parent = the run_subagent call); its tool
    // calls hang under IT, not under the run_subagent call — so the page can
    // group the steps instead of showing a flat list.
    const startedAt = Date.now();
    this.activity(req.conversationId, conversationId, "subagent", "started", { argsSummary: req.task.slice(0, 160), parentId: req.parentToolCallId });
    let outcome: "done" | "cancelled" | "error" | "paused" | "denied";
    try {
      outcome = await this.runLoop(run);
    } finally {
      if (timer) clearTimeout(timer);
      parentSignal.removeEventListener("abort", onAbort);
    }
    // FIX31 §1.1: the user refused a tool this sub-agent asked for → the
    // sub-agent is FAILED, in the same words the parent already understands.
    if (outcome === "denied") answer = `FAILED: the user declined permission for ${run.deniedTool ?? "a tool"}${requester.label ? ` (sub-agent “${requester.label}”)` : ""}. Do not retry it.`;
    // FIX30 §3.1: a sub-agent that says it could not do the job is a FAILED
    // sub-agent, not a green tick. The prompt asks it to open with `FAILED:`
    // when it cannot complete the task; `ERROR:` (what the model wrote on its
    // own in the owner's run) counts too. Anything else is still trusted as
    // success — a decline in other words is not detected (see the report).
    const failed = outcome === "denied" || (outcome === "done" && SUBAGENT_FAILED_RE.test(answer));
    const completed = outcome === "done" && !failed;
    this.log.info(`SUBAGENT ${outcome === "denied" ? "DENIED" : failed ? "FAILED" : outcome.toUpperCase()} ${conversationId}: ${answer.length} chars, ${trace.length} tool call(s)`);
    this.activity(req.conversationId, conversationId, "subagent", "finished", {
      success: completed,
      resultPreview: (outcome === "done" || outcome === "denied" ? answer.trim() : `Stopped: ${outcome}`).slice(0, 160),
      durationMs: Date.now() - startedAt,
      parentId: req.parentToolCallId,
    });
    return { answer: answer.trim(), completed, stopReason: completed ? null : failed ? "failed" : outcome, trace };
  }

  /** Sidecar tools + host tools; nested runs never see run_subagent.
   *  FIX32 Part 4: the FIX8-era `autoApprovableOnly` parameter is GONE — FIX31
   *  removed the behaviour (a sub-agent asks the user instead of losing the
   *  tool), and a dead parameter with a security-sounding name invites both
   *  false confidence and accidental resurrection. */
  private async toolDefinitions(allow: string[] | null, nested: boolean, imageMode = false, memoryEnabled = true): Promise<GatewayToolDefinition[]> {
    let defs: GatewayToolDefinition[] = [];
    try {
      const sidecarTools = await this.sidecar.listTools();
      defs = sidecarTools.map((t) => ({ type: "function" as const, function: { name: t.name, description: t.description, parameters: t.inputSchema } }));
    } catch (err) {
      this.log.error(`sidecar tools unavailable: ${(err as Error).message}`);
    }
    // Host tools at their WPF positions: clipboard after the filesystem tools,
    // web_search after remember, run_python + run_subagent last.
    const host = this.hostTools.definitions().map((t) => ({ type: "function" as const, function: { name: t.name, description: t.description, parameters: t.inputSchema } }));
    const at = (name: string) => defs.findIndex((d) => d.function.name === name);
    const insertAfter = (after: string, names: string[]) => {
      const i = at(after);
      const items = host.filter((h) => names.includes(h.function.name));
      if (i >= 0) defs.splice(i + 1, 0, ...items);
      else defs.push(...items);
    };
    insertAfter("search_files", ["read_clipboard", "write_clipboard"]);
    insertAfter("remember", ["web_search", "search_past_conversations"]);
    // FIX28 §2 (F2): any host tool not placed by name above is STILL offered
    // (search_past_conversations was registered but never offered, so the
    // desktop model "could not search"; run_python / run_subagent / the
    // image-mode tool keep their explicit positions below).
    const placed = new Set(defs.map((d) => d.function.name));
    const held = new Set(["run_python", "run_subagent", "generate_image"]);
    defs.push(...host.filter((h) => !placed.has(h.function.name) && !held.has(h.function.name)));
    defs.push(...host.filter((h) => h.function.name === "generate_image"));
    defs.push(...host.filter((h) => h.function.name === "run_python"));
    if (!nested) defs.push(...host.filter((h) => h.function.name === "run_subagent"));
    // FIX31 Part 2: an OAuth connector's tools are HOST tools (the host holds
    // the token). Listed only for connectors that are enabled in the owner's
    // file AND connected — with no file, this adds nothing.
    if (this.opts.connectorTools) {
      try {
        const cdefs = await this.opts.connectorTools.definitions();
        defs.push(...cdefs.map((t) => ({ type: "function" as const, function: { name: t.name, description: t.description, parameters: t.inputSchema } })));
      } catch (err) {
        this.log.warn(`connector tools unavailable this turn: ${(err as Error).message}`);
      }
    }
    let out = defs.filter((d) => d.function.name !== "run_subagent" || !nested);
    if (allow) {
      const set = new Set(allow.map((a) => a.toLowerCase()));
      out = out.filter((d) => set.has(d.function.name.toLowerCase()));
    }
    // Memory off for this chat means no memory-WRITE tool, the same rule the
    // two withholdings below follow: a tool that must not run is not offered.
    // The web client omits its save_memory this way, so this is what makes the
    // switch mean one thing in both clients.
    //
    // FIX130 A2: `memoryEnabled` here is "memory active" (switch on AND not
    // a temporary chat), and the READ tool (search_past_conversations) is
    // withheld too — recalling past chats is memory (withholdMemoryTools).
    if (!memoryEnabled) {
      const before = out.length;
      out = withholdMemoryTools(out, false);
      if (before !== out.length) this.log.info(`TOOLS: memory is off for this chat — withholding ${before - out.length} memory tool(s)`);
    }
    // §3.3: when the browser bridge is down, do not offer the 8 bridge tools —
    // the model should have no vocabulary for a tool it cannot use this turn.
    if (!this.browserConnected) {
      const before = out.length;
      out = out.filter((d) => !BROWSER_BRIDGE_TOOLS.has(d.function.name));
      if (before !== out.length) this.log.info(`TOOLS: browser bridge down — withholding ${before - out.length} browser tool(s) this turn`);
    }
    // FIX5 §3: web_search may be enabled locally (the app setting) yet DISABLED
    // for the tenant on the gateway — offering it then means the model calls a
    // tool that 400s (search_not_enabled). Same rule as the browser bridge: a
    // tool that cannot work is not offered this turn. The honest "your org has
    // not enabled it" line is added to the system prompt (see buildMessages), so
    // the model explains the truth instead of silently lacking the tool.
    if (out.some((d) => d.function.name === "web_search") && !(await this.webSearchTenantEnabled())) {
      out = out.filter((d) => d.function.name !== "web_search");
      this.log.info("TOOLS: web_search is on locally but the tenant has it disabled — withholding this turn");
    }
    // FIX8 A1.6: a task can reschedule ITSELF from inside its run — the tool exists
    // only while a scheduled task is firing (activeTaskId set), and acts on that task.
    if (this.activeTaskId !== null && !nested) out.push(UPDATE_TASK_SCHEDULE_TOOL);
    // FIX73: the question tool is offered ONLY to an attended main turn. A
    // sub-agent cannot talk to the user (its prompt says so; this makes it
    // structurally true) and a scheduled task has nobody to answer — the
    // headless rule: remove the tool rather than offer one that cannot work.
    // Not subject to `allow`: a sub-agent allowlist naming it changes nothing.
    if (!nested && this.activeTaskId === null) out.push(ASK_USER_TOOL_DEFINITION);
    // FIX9 Part B: generate_image is offered ONLY when the user turned on image mode
    // (the 🎨 button). Off → the model has no image tool, so it cannot pretend to.
    if (!imageMode) out = out.filter((d) => d.function.name !== "generate_image");
    return out;
  }

  /** FIX5 §3: the tenant's web-search capability, cached (TTL). GET
   * /v1/search/config is the cheap authority (tenant flag AND a provider). On a
   * probe failure keep the last-known answer; if none was ever learned, fail OPEN
   * (assume enabled) — a single search that then 400s is a smaller harm than
   * falsely telling a user with search ON that their org disabled it. */
  private async webSearchTenantEnabled(): Promise<boolean> {
    const now = Date.now();
    if (this.searchTenantEnabled !== null && now - this.searchTenantEnabledAt < TurnCoordinator.SEARCH_CAP_TTL_MS) {
      return this.searchTenantEnabled;
    }
    const cfg = await this.gateway.searchConfig(AbortSignal.timeout(8_000)).catch(() => null);
    if (cfg) {
      this.searchTenantEnabled = cfg.enabled;
      this.searchTenantEnabledAt = now;
      return cfg.enabled;
    }
    return this.searchTenantEnabled ?? true; // probe failed and never learned → fail open
  }

  /** FIX9 §1.1: is `model` one the gateway accepts? Cached (TTL). When the model
   * list cannot be fetched AND none was ever learned, allow (the gateway is the
   * backstop — a bad model still errors there); once a list is known, an unknown
   * model is refused. Trusted config models (default/force) are always in the list. */
  private async isModelAccepted(model: string, signal: AbortSignal): Promise<boolean> {
    await this.refreshAcceptedModels(signal);
    if (this.acceptedModels === null) return true; // never learned + fetch failed → gateway is the backstop
    return this.acceptedModels.has(model);
  }

  private async refreshAcceptedModels(signal: AbortSignal): Promise<void> {
    const now = Date.now();
    if (this.acceptedModels === null || now - this.acceptedModelsAt >= TurnCoordinator.MODELS_TTL_MS) {
      // A gateway that cannot list (network, or a test double without the
      // method) leaves the last-known map in place — never breaks a turn.
      let map: Map<string, { tools: boolean; vision: boolean; contextWindow?: number }> | null = null;
      try {
        map = await this.gateway.listModels(signal);
      } catch {
        map = null;
      }
      if (map) {
        this.acceptedModels = map;
        this.acceptedModelsAt = now;
        this.modelWindows.learn([...map].map(([id, e]) => [id, e.contextWindow ?? 0] as [string, number]));
      }
    }
  }

  /** FIX47.6 Part C — should this turn carry tool definitions at all?
   * A model the catalog marks tools-blind gets NONE: sending tools
   * anyway made EVERY turn on such a model die on the gateway's
   * model_no_tool_support wall (measured live — the desktop was
   * unusable with those models). Mirrors chat-edge attachTools: the
   * Auto sentinel, unknown ids and a failed catalog fetch keep tools
   * (capability unknown → don't degrade). */
  private async modelSupportsTools(model: string, signal: AbortSignal): Promise<boolean> {
    if (!model || model === "auto") return true;
    await this.refreshAcceptedModels(signal);
    const entry = this.acceptedModels?.get(model);
    return entry ? entry.tools : true;
  }

  /** FIX65 — does the catalogue say this model can read images? Same
   * contract as modelSupportsTools: Auto, unknown ids and a failed fetch
   * keep the images (capability unknown → don't degrade). A model KNOWN
   * to be blind gets no image blocks at all: before this, one refused
   * image turn poisoned the chat, because the image stayed in the
   * history and every later turn died on model_no_vision_support. */
  private async modelSupportsVision(model: string, signal: AbortSignal): Promise<boolean> {
    if (!model || model === "auto") return true;
    await this.refreshAcceptedModels(signal);
    const entry = this.acceptedModels?.get(model);
    return entry ? entry.vision : true;
  }

  /** FIX104 — what this turn's context budget is measured against.
   *
   * The window is the gateway catalogue's `context_window`, refreshed with the
   * model list above; the last known survives a failed read and a restart
   * (model-windows.ts). A pinned model budgets against itself. `auto` is
   * chat-edge's budgetModelFor (FIX102 Part A): the router picks the answering
   * model after this budget is fixed, so the budget follows the model that
   * answered this conversation's last completed turn, while the catalogue
   * still lists it, and the router is told what the request needs
   * (routingContext). No evidence, or a model the catalogue does not know,
   * budgets at the conservative default. */
  private async budgetBasis(conversationId: string, model: string, signal: AbortSignal): Promise<BudgetBasis> {
    await this.refreshAcceptedModels(signal);
    let budgetModel = model;
    if (isAutoModel(model)) {
      const served = await this.lastServedModel(conversationId);
      budgetModel = served && this.modelWindows.advertised(served) > 0 ? served : "";
    }
    return { model: budgetModel, window: budgetModel ? this.modelWindows.advertised(budgetModel) : 0, outputTokens: MAX_OUTPUT_TOKENS };
  }

  /** chat-edge's lastServedModel: the concrete model on the newest completed
   * assistant row of the transcript, "" when there is none. */
  private async lastServedModel(conversationId: string): Promise<string> {
    try {
      const rows = await this.chatEdge.listMessages(conversationId);
      for (let i = rows.length - 1; i >= 0; i--) {
        const m = rows[i]!;
        if (m.role !== "assistant" || (m.status && m.status !== "completed")) continue;
        const id = (m.model_id ?? "").trim();
        if (id && !isAutoModel(id)) return id;
      }
    } catch {
      /* no transcript → no evidence → the default */
    }
    return "";
  }

  /** FIX3 §3: the honest first-launch indicator. While the sidecar is not ready,
   * emit its REAL phase (from stderr, not a timer) as a `status` event the page
   * already renders; on a sidecar that never comes up, end in a legible failure
   * status rather than a permanent "starting". Returns once ready or failed — the
   * turn then proceeds (degraded to host-only tools if the sidecar failed). */
  private async awaitSidecarReady(conversationId: string): Promise<void> {
    if (this.sidecar.ready) return;
    void this.sidecar.ensureStarted().catch(() => {
      /* failure is observed below via `ready` staying false / the child dying */
    });
    let last = this.sidecar.phase;
    this.emit(conversationId, "status", { status: last, startup: true });
    const start = Date.now();
    const HARD_MS = 60_000; // the sidecar's own init timeout is 30 s/spawn, ×≤3
    while (!this.sidecar.ready) {
      await new Promise((r) => setTimeout(r, 500));
      if (this.sidecar.ready) break;
      const died = !this.sidecar.isRunning; // child exited / never spawned
      if (died || Date.now() - start > HARD_MS) {
        this.log.error(`SIDECAR not ready after ${Math.round((Date.now() - start) / 1000)}s (${died ? "exited" : "timeout"})`);
        this.emit(conversationId, "status", {
          status: "The tools service did not start. You can keep chatting; restart the app to enable tools.",
          startup: true,
          failed: true,
        });
        return;
      }
      if (this.sidecar.phase !== last) {
        last = this.sidecar.phase;
        this.emit(conversationId, "status", { status: last, startup: true });
      }
    }
  }

  /** Snapshot whether the browser bridge is connected (sran/status). Errors →
   * treated as down: a failed probe must not accidentally offer dead tools. */
  private async refreshBrowserConnected(): Promise<void> {
    try {
      const s = await this.sidecar.status();
      this.browserConnected = s["browserConnected"] === true;
    } catch {
      this.browserConnected = false;
    }
  }

  // ── history ────────────────────────────────────────────────────────────────
  private historyPath(conversationId: string): string {
    return path.join(this.opts.dataDir, "history", `${conversationId.replace(/[^A-Za-z0-9_-]/g, "_")}.json`);
  }
  /** FIX109 Part A: the chain plus the server marker it was written against
   *  (null for a pre-FIX109 bare array — see chain-freshness.ts). */
  private loadLocalHistory(conversationId: string): LocalChain | null {
    try {
      const p = this.historyPath(conversationId);
      if (!fs.existsSync(p)) return null;
      return parseHistoryFile(JSON.parse(fs.readFileSync(p, "utf8")));
    } catch {
      return null;
    }
  }
  /** FIX109 Part A: written with the server's message_count this chain is
   *  current with, so the next load can tell whether the server moved. */
  private saveLocalHistory(conversationId: string, messages: GatewayMessage[], serverMessageCount: number | null): void {
    try {
      const p = this.historyPath(conversationId);
      fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(p, serializeHistoryFile(messages, serverMessageCount));
    } catch (err) {
      this.log.warn(`history save failed for ${conversationId}: ${(err as Error).message}`);
    }
  }

  /** FIX27 Part 1: resolve this turn's attachment handles against chat-edge. A
   * failure to resolve is reported (log + image_notice), never a path lookup. */
  private async resolveTurnAttachments(conversationId: string, handles: readonly string[], signal: AbortSignal): Promise<ResolvedAttachments | null> {
    if (handles.length === 0) return null;
    const encode: ImageEncoder = this.opts.encodeImage ?? (async () => null);
    const resolved = await resolveAttachments(this.chatEdge, encode, conversationId, handles, {
      log: this.log,
      signal,
      onStatus: (text) => this.emit(conversationId, "status", { status: text }),
    });
    for (const a of resolved.attached) this.log.info(`ATTACH conv=${conversationId}: ${a.kind} ${a.filename} (${a.id}) → user message`);
    return resolved;
  }

  private async buildMessages(conversationId: string, conversation: ConversationDto | null, text: string, attached: ResolvedAttachments | null = null): Promise<{ messages: GatewayMessage[]; userText: string; omittedOlder: number; source: HistorySource; memoryUsed: MemoryUsedItem[] }> {
    let project = null;
    if (conversation?.project_id) {
      const projects = await this.chatEdge.listProjects();
      project = projects.find((p) => p.id === conversation.project_id) ?? null;
    }
    // FIX2: the saved writing-style profile (a sidecar setting) reaches the
    // system prompt. Read per turn; the sidecar is the single source.
    let writingStyleProfile: string | undefined;
    try {
      const s = await this.sidecar.hostQuery("settings.get", {});
      const v = (s.data as { writingStyleProfile?: string } | undefined)?.writingStyleProfile;
      if (typeof v === "string" && v.trim()) writingStyleProfile = v;
    } catch {
      /* best-effort — a prompt without the style is still valid */
    }
    // FIX5 §1: the LOCAL durable notes the `remember` tool wrote (sidecar store).
    // Before FIX5 the prompt never read these, so `remember` never recalled. The
    // sidecar keys conversation notes by the SAME _meta.conversationId the host
    // sends on every tools/call, so this id matches what `remember` stored under.
    let localGlobalNotes: { content: string; createdAt: string }[] | undefined;
    let localConversationNotes: { content: string; createdAt: string }[] | undefined;
    try {
      const n = await this.sidecar.hostQuery("notes._forPrompt", { conversationId });
      const d = n.data as
        | { conversationNotes?: { content: string; createdAt: string }[]; globalNotes?: { content: string; createdAt: string }[] }
        | undefined;
      if (d) {
        localGlobalNotes = d.globalNotes;
        localConversationNotes = d.conversationNotes;
      }
    } catch {
      /* best-effort — server memories still render; a note just won't recall this turn */
    }
    // FIX28 §3 (G3): the enabled skills that apply to this message (trigger match,
    // or no triggers = always). The sidecar has stored skills since FIX2; the
    // Electron host never read them, so an installed skill constrained nothing.
    let skills: { name: string; instructions: string }[] | undefined;
    try {
      const r = await this.sidecar.hostQuery("skills._forPrompt", { message: text });
      const list = (r.data as { skills?: { name: string; instructions: string }[] } | undefined)?.skills;
      if (Array.isArray(list) && list.length > 0) {
        skills = list.filter((s) => s && typeof s.instructions === "string" && s.instructions.trim());
        if (skills.length) this.log.info(`SKILLS conv=${conversationId}: ${skills.map((s) => s.name).join(", ")} → system prompt`);
      }
    } catch {
      /* best-effort — no skill this turn */
    }
    // FIX5 §3: if web_search is on in the app but the tenant has it disabled, the
    // tool is withheld this turn (toolDefinitions). Tell the model WHY so it gives
    // the true reason ("your org has not enabled it") rather than implying it just
    // lacks the ability. Only when locally on — if the user turned it off, that is
    // their own choice, not an org restriction, so no note.
    let webSearchUnavailableReason: "tenant-disabled" | undefined;
    if (this.hostTools.has("web_search") && !(await this.webSearchTenantEnabled())) {
      webSearchUnavailableReason = "tenant-disabled";
    }
    const assembled = await this.assembler.buildSystemPromptParts({
      homeDirectory: this.opts.homeDirectory,
      grantedFolders: this.grants.list(), // FIX26 Part 3: name the fence in the prompt
      conversationId,
      activeProject: project,
      memoryEnabled: conversation?.memory_enabled !== false,
      isTemporary: conversation?.is_temporary === true, // FIX130 A2
      toolsAvailable: true,
      writingStyleProfile,
      localGlobalNotes,
      localConversationNotes,
      webSearchUnavailableReason,
      skills,
      browserUnavailable: !this.browserConnected,
    });
    const system = assembled.text;
    const messages: GatewayMessage[] = [{ role: "system", content: system }];
    const local = this.loadLocalHistory(conversationId);
    let history: GatewayMessage[] | null = null;
    let source: HistorySource = "server";
    let omittedOlder = 0;
    // FIX109 Part A — the local chain is a cache of the server transcript and
    // is checked against it on every load (chain-freshness.ts). Before this,
    // the server transcript was read only when no local file existed, so a
    // turn taken on the web after the first desktop turn reached the page
    // and never the model.
    const freshness: ChainFreshness | null = local ? chainFreshness(local, conversation?.message_count) : null;
    if (local && (freshness === "fresh" || freshness === "unknown")) {
      history = local.messages;
      source = "local";
      if (freshness === "unknown") this.log.info(`HISTORY conv=${conversationId}: server count unknown — local chain kept`);
    }
    if (history === null) {
      // The visible transcript from chat-edge: no local chain (a conversation
      // started on the web or before E4), a stale one (the server moved since
      // this host last wrote), or an unmarked one (decided against the rows).
      // FIX105 Part B — all of it, up to a ceiling. This read asked for 200 rows
      // and chat-edge serves at most 100, newest first, so a web conversation
      // opened here silently started from its newest 100: the page showed
      // everything (it pages on its own) while the MODEL answered about part of
      // a conversation. The host now pages back; rows beyond the ceiling are
      // counted (omittedOlder → the context note, the meter, the loss notice).
      try {
        const loaded = await this.loadServerTranscript(conversationId);
        const visible = loaded.rows.filter((m) => (m.role === "user" || m.role === "assistant") && typeof m.content === "string" && m.content.trim().length > 0);
        if (local && freshness === "unmarked" && unmarkedFreshness(local, visible.length + (loaded.hasMore ? 1 : 0)) === "fresh") {
          // A pre-FIX109 file whose chain holds at least what the server
          // shows: kept, and the next save marks it.
          history = local.messages;
          source = "local";
          this.log.info(`HISTORY conv=${conversationId}: unmarked local chain covers the server's ${visible.length} visible rows — kept`);
        } else {
          if (local) this.log.info(`HISTORY conv=${conversationId}: local chain ${freshness} (server count ${conversation?.message_count ?? "?"}, marker ${local.serverMessageCount ?? "none"}) → server transcript, tool chains not carried`);
          if (loaded.hasMore) omittedOlder = Math.max(1, (conversation?.message_count ?? 0) - loaded.rows.length);
          if (loaded.pages > 1 || loaded.hasMore) this.log.info(`HISTORY conv=${conversationId}: ${loaded.rows.length} transcript rows in ${loaded.pages} page(s)${loaded.hasMore ? `, ~${omittedOlder} older not loaded (ceiling ${HISTORY_LOAD_CEILING})` : ""}`);
          history = visible.map((m) => ({ role: m.role as "user" | "assistant", content: m.content as string }));
          // FIX109 Part B — the chain the summary's anchor indexed is gone;
          // rebase the state on this transcript (the server's own state when
          // it has one, translated through the row ids it carries).
          if (this.opts.contextManager) {
            const memoryOn = memoryActive(conversation); // FIX130 A2
            const serverState = memoryOn ? await this.chatEdge.getConversationState(conversationId) : null;
            const outcome = this.opts.contextManager.rebase(conversationId, visible.map((m) => m.id), serverState, memoryOn);
            if (outcome !== "none") this.log.info(`STATE conv=${conversationId}: ${outcome === "adopted" ? "server summary adopted" : "local summary anchor reset"} for the server transcript`);
          }
        }
      } catch (err) {
        // A transcript that cannot be read leaves the local chain (stale or
        // not) as the best copy there is; with no local chain, an empty past.
        this.log.warn(`Could not load history for ${conversationId}: ${(err as Error).message}${local ? " — local chain kept" : ""}`);
        history = local ? local.messages : [];
        source = local ? "local" : "server";
      }
    }
    let userText = text;
    if (text.length === 0) {
      const lastUser = [...history].reverse().findIndex((m) => m.role === "user" && typeof m.content === "string");
      if (lastUser < 0) throw new Error("regenerate with no prior user message");
      const cut = history.length - 1 - lastUser;
      userText = history[cut]!.content as string;
      history = history.slice(0, cut);
    }
    for (const m of history) messages.push(m);
    messages.push({ role: "user", content: attached ? composeUserContent(userText, attached) : userText });
    // FIX22 §1b: remember what user-data went into this prompt, labelled, so a
    // guardrail 403 can be explained. The gateway scans EVERY user+system
    // message, so earlier user turns are parts too. Order = most actionable
    // first (this message, then stored sources, then history).
    const parts: PromptPart[] = [{ ...PROMPT_PART.userMessage, text: userText }, ...assembled.parts];
    // FIX27: a poisoned document is a prompt part too — a guardrail 403 names it.
    if (attached?.observations) {
      const names = attached.attached.filter((a) => a.kind === "document").map((a) => a.filename);
      parts.splice(1, 0, { ...PROMPT_PART.attachedFile(names.join(", ")), text: attached.observations });
    }
    for (const m of history) {
      if (m.role === "user" && typeof m.content === "string") parts.push({ ...PROMPT_PART.earlierMessage, text: m.content });
    }
    this.promptPartsByConversation.set(conversationId, parts);
    // FIX130 A3d — which saved memories the prompt carried (an older stub
    // assembler that does not say is treated as none).
    return { messages, userText, omittedOlder, source, memoryUsed: assembled.memoryUsed ?? [] };
  }

  /** FIX105 Part B — the chat-edge transcript, oldest first, paged back until it
   * ends or HISTORY_LOAD_CEILING rows are loaded. Throws when a page cannot be
   * read, so a caller never mistakes part of a transcript for all of it. */
  private async loadServerTranscript(conversationId: string): Promise<{ rows: MessageDto[]; hasMore: boolean; pages: number }> {
    const pages: MessageDto[][] = [];
    let before: string | undefined;
    let hasMore = true;
    let total = 0;
    while (hasMore && total < HISTORY_LOAD_CEILING) {
      const page = await this.chatEdge.listMessagesPage(conversationId, HISTORY_PAGE_SIZE, before);
      if (!page) throw new Error(`transcript page ${pages.length + 1} could not be read`);
      pages.unshift(page.messages);
      total += page.messages.length;
      hasMore = page.hasMore && page.messages.length > 0;
      before = page.messages[0]?.id;
    }
    return { rows: pages.flat(), hasMore, pages: pages.length };
  }

  /** FIX28 §2: the chat-edge row ids of this turn's user + assistant messages
   * (matched by the client ids importVisible sent). Best-effort: absent ids
   * just mean the page keeps its client ids, as before. */
  private async persistedIds(conversationId: string, turn: RunningTurn, regenerate = false): Promise<{ user_message_id?: string; assistant_message_id?: string }> {
    try {
      const rows = await this.chatEdge.listMessages(conversationId);
      const userClient = "u_" + turn.messageId.slice(5);
      const out: { user_message_id?: string; assistant_message_id?: string } = {};
      for (const r of rows) {
        if (r.client_id === userClient) out.user_message_id = r.id;
        else if (r.client_id === turn.messageId) out.assistant_message_id = r.id;
      }
      // FIX124 — a regenerate imported no user row, so no row carries this
      // turn's user client id: the question it answered is the transcript's
      // last user row, which already has its id.
      if (regenerate && !out.user_message_id) {
        const asked = [...rows].reverse().find((r) => r.role === "user");
        if (asked) out.user_message_id = asked.id;
      }
      return out;
    } catch {
      return {};
    }
  }

  /** FIX124 — which assistant row a regenerate replaces, by the client id the
   *  server knows it under. A row this host wrote carries its `asst_…` id; a
   *  row written on the web has none, and then nothing is named (the server
   *  cannot be told about a row by an id it never had). Rows still owed to
   *  the server are newer than anything it lists, so they are looked at
   *  first. Best-effort: a transcript that cannot be read names nothing. */
  private async regenerateTarget(conversationId: string): Promise<RegenerateTarget> {
    const owed = [...(this.unsavedImports.get(conversationId) ?? [])].reverse().find((i) => i.role === "assistant");
    if (owed) return { replacesClientId: owed.client_id };
    try {
      const rows = await this.chatEdge.listMessages(conversationId);
      const asked = rows.map((r) => r.role).lastIndexOf("user");
      const answer = asked < 0 ? undefined : [...rows.slice(asked + 1)].reverse().find((r) => r.role === "assistant");
      return typeof answer?.client_id === "string" && answer.client_id.length > 0 ? { replacesClientId: answer.client_id } : {};
    } catch {
      return {};
    }
  }

  /** FIX121 — import rows chat-edge has not confirmed, per conversation,
   *  resent ahead of the next turn's rows. In memory only: a restart
   *  forgets them (the local chain file still has the turn). */
  private readonly unsavedImports = new Map<string, ImportMessageItem[]>();

  /** Imports this turn's visible rows to chat-edge; returns how many rows the
   *  server actually inserted (0 on failure or nothing to import) — the
   *  FIX109 marker is advanced by exactly that. */
  private async importVisible(conversationId: string, userText: string, turn: RunningTurn, model: string, chain: GatewayMessage[], fileIds: string[] = [], ending?: TurnEnding, runId?: string, regenerate: RegenerateTarget | null = null): Promise<number> {
    const now = new Date().toISOString();
    const items: ImportMessageItem[] = [];
    // FIX27: file_ids link the uploaded rows to this user message on chat-edge
    // (the web view shows the chips; a web continuation replays the images).
    // FIX124 — not on a regenerate: there `userText` is the question recovered
    // for the model, not something the person sent, and it already is a row.
    // A new client id made it a second one.
    if (!regenerate && (userText.length > 0 || fileIds.length > 0)) items.push({ client_id: "u_" + turn.messageId.slice(5), role: "user" as const, content: userText, model_id: null, created_at: now, ...(fileIds.length ? { file_ids: fileIds } : {}) });
    // FIX80 — files the turn's tools persisted (run_python artifacts) link
    // to the ASSISTANT row, so they render under the reply after a reload
    // and on the web view, exactly like web run_python attachments.
    const asstFileIds = (turn.attachedFiles ?? []).map((f) => f.id);
    // FIX118 — a turn that did not complete says so on its row (status +
    // closed-set cause + the code of the failure); a completed one sends
    // the pre-FIX118 shape. A failed turn with NO text still leaves an
    // (empty) errored assistant row, as a failed web turn does, so the
    // transcript shows the failure on every surface.
    // FIX119 — the ending is ALWAYS said, `completed` included: the server
    // records NULL for an item that says nothing (the e48 wire), so a new
    // desktop must say it. The run id and the stop reason ride along.
    const endingFields = ending
      ? ending.status !== "completed"
        ? { status: ending.status, terminal_cause: ending.cause, ...(ending.errorCode ? { error_code: ending.errorCode } : {}) }
        : { status: "completed" as const, ...(turn.stopReason ? { stop_reason: turn.stopReason } : {}) }
      : {};
    const runFields = runId ? { run_id: runId } : {};
    // FIX120 — the requested model of a rescued turn rides the import.
    const fallbackFields = turn.fallbackFrom ? { fallback_from: turn.fallbackFrom } : {};
    const attemptFields = ending?.status === "error" && ending.attempts?.length ? { error_attempts: ending.attempts } : {};
    // FIX124 — the answer this one takes the place of, so the server can stop
    // showing both as the active answer to one question.
    const replacesFields = regenerate?.replacesClientId ? { replaces_client_id: regenerate.replacesClientId } : {};
    // FIX121 — every turn leaves an assistant row. A Stop before the first
    // character used to leave none (only text, files or an ERROR made one),
    // so the web showed a question with no answer and no "stopped" label —
    // while a web turn stopped at the same moment has always had its row.
    const leavesARow = turn.assistantText.length > 0 || asstFileIds.length > 0 || ending?.status === "error" || ending?.status === "cancelled";
    if (leavesARow) items.push({ client_id: turn.messageId, role: "assistant" as const, content: turn.assistantText, model_id: turn.servedModel ?? model, created_at: new Date(Date.now() + 1).toISOString(), ...(asstFileIds.length ? { file_ids: asstFileIds } : {}), ...endingFields, ...runFields, ...fallbackFields, ...attemptFields, ...replacesFields });
    void chain;
    // FIX124 — nothing to import is decided on the ITEMS. It was decided on
    // the strings (no user text, no answer, no files) before any item was
    // built, which a regenerate that failed before its first character would
    // have matched — and FIX121's errored row would not have been written.
    if (items.length === 0) return 0;
    // FIX121 — rows an earlier import of this conversation could not deliver
    // go first (the endpoint dedupes on client_id, so resending is safe).
    const owed = this.unsavedImports.get(conversationId) ?? [];
    const batch = [...owed, ...items].slice(-IMPORT_BATCH_MAX);
    const result = await this.chatEdge.importMessages(conversationId, batch, runId);
    if (result && !("failed" in result)) {
      this.unsavedImports.delete(conversationId);
      this.log.info(`TURN PERSISTED conv=${conversationId} run=${runId ?? "-"}: ${result.imported} imported, ${result.skipped} skipped${owed.length ? ` (${owed.length} owed from earlier)` : ""} (chat-edge transcript); local chain history saved`);
      if (result.rejected?.length) {
        const why = result.rejected.map((r) => `${r.client_id ?? "#" + r.index}: ${r.reason}`).join("; ").slice(0, 300);
        this.log.warn(`TURN PARTLY PERSISTED conv=${conversationId} run=${runId ?? "-"}: ${result.rejected.length} item(s) rejected — ${why}`);
        this.notifyNotSaved(conversationId, runId, "rejected", { detail: why });
      }
      return result.imported;
    }
    const failure: { status?: number; detail?: string } = result && "failed" in result ? result : {};
    this.unsavedImports.set(conversationId, batch);
    this.log.warn(`TURN PERSIST FAILED conv=${conversationId} run=${runId ?? "-"} (chat-edge import${failure.status ? ` HTTP ${failure.status}` : ""}) — local chain history saved; ${batch.length} row(s) will be resent with the next turn`);
    this.notifyNotSaved(conversationId, runId, "failed", failure);
    return 0;
  }

  /** FIX121 — a turn that did not reach the server is SAID, not only logged.
   *  The person was looking at a normal answer while the web, the other
   *  device and the server-side summary never had the turn. `host.notice`
   *  rather than a turn event: an errored turn has already written its
   *  terminal by the time the import runs, and the page drops turn events
   *  that arrive with no open sink. */
  private notifyNotSaved(conversationId: string, runId: string | undefined, reason: "failed" | "rejected", info: { status?: number; detail?: string }): void {
    this.bridge.post("host.notice", { kind: "turn-not-saved", conversationId, ...(runId ? { runId } : {}), reason, ...(info.status ? { status: info.status } : {}), ...(info.detail ? { detail: info.detail.slice(0, 300) } : {}) });
  }
}

// ── helpers ─────────────────────────────────────────────────────────────────
function toolResult(call: GatewayToolCall, content: string): GatewayMessage {
  return { role: "tool", tool_call_id: call.id, name: call.function.name, content };
}
// FIX72 — the exact text the browser-tool batch contract specifies for a
// later call in a batch that was skipped because an earlier one failed.
export const NOT_EXECUTED_AFTER_FAILURE = "Not executed: an earlier action in this turn failed.";
/** Whether the tool result already pushed for `callId` is an error. Tool
 * results are `Error: …` on failure (executeToolCall) and the raw output on
 * success; a screenshot pushes an extra user message AFTER the result, so we
 * match on tool_call_id rather than "the last message". */
export function toolResultErrored(messages: GatewayMessage[], callId: string): boolean {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!;
    if (m.role === "tool" && m.tool_call_id === callId) {
      return typeof m.content === "string" && m.content.startsWith("Error");
    }
  }
  return false;
}
function screenshotMessage(toolName: string, dataUri: string): GatewayMessage {
  return {
    role: "user",
    content: [
      { type: "text", text: `${SCREENSHOT_MARKER} ${toolName} — analyze it to decide your next action]` },
      { type: "image_url", image_url: { url: dataUri } },
    ],
  };
}
/** A computer-use screenshot message (screenshotMessage above) — as opposed to
 * a user attachment, which FIX27 prunes separately (pruneAttachedImages). */
function isScreenshotMessage(m: { role: string; content: unknown }): boolean {
  if (m.role !== "user" || !Array.isArray(m.content)) return false;
  const text = (m.content as GatewayContentBlock[]).find((b) => b.type === "text");
  return !!text && text.type === "text" && text.text.startsWith(SCREENSHOT_MARKER);
}
/** ScreenshotPruner: keep the last N screenshot images; older ones become a placeholder. */
function pruneScreenshots(messages: GatewayMessage[], keep: number): void {
  const idx: number[] = [];
  messages.forEach((m, i) => {
    if (isScreenshotMessage(m) && Array.isArray(m.content) && m.content.some((b) => b.type === "image_url")) idx.push(i);
  });
  for (const i of idx.slice(0, Math.max(0, idx.length - keep))) {
    const m = messages[i]!;
    const text = Array.isArray(m.content) ? m.content.find((b) => b.type === "text") : null;
    messages[i] = { role: "user", content: `${text && text.type === "text" ? text.text : SCREENSHOT_MARKER + " earlier]"} (image removed to save context)` };
  }
}
function lastAssistantDelta(messages: GatewayMessage[]): string | null {
  void messages;
  return null; // the streamed text of THIS iteration is not part of the chain message (WPF: assistantContent, kept only when non-empty)
}
function parsePlan(args: Record<string, unknown>): { text: string; status: "pending" | "in_progress" | "done" }[] | null {
  const steps = args["steps"];
  if (!Array.isArray(steps)) return null;
  return steps
    .map((s) => (s && typeof s === "object" ? (s as Record<string, unknown>) : null))
    .filter((s): s is Record<string, unknown> => s !== null)
    .map((s) => ({ text: String(s["text"] ?? s["step"] ?? s["title"] ?? ""), status: (["pending", "in_progress", "done"].includes(String(s["status"])) ? String(s["status"]) : "pending") as "pending" | "in_progress" | "done" }));
}
function describe(messages: GatewayMessage[]): string {
  return messages
    .map((m) => {
      const len = typeof m.content === "string" ? `${m.content.length}ch` : m.content === null ? "null" : "blocks";
      return `${m.role}${m.tool_calls ? "(tool_calls)" : ""}:${len}`;
    })
    .join(" , ");
}
function truncate(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max) + `\n\n[...truncated to ${max} chars]`;
}
/** FIX58 — which store a `remember` call belongs to.
 *
 *  Only an explicit "conversation" keeps a note local; anything else,
 *  including a missing or misspelled scope, is a global note and goes to
 *  the server. Defaulting the unknown case to global matches the tool
 *  schema's own default and errs towards the store the user can see and
 *  delete, rather than towards the one that used to be invisible from the
 *  web. */
/**
 * FIX61 — add a per-turn hint to the outbound chain WITHOUT creating a second
 * system message. The hint is appended to the leading system message; only
 * when the chain has no leading system message is one put in front. A hint
 * is never placed anywhere but index 0: providers that enforce "system
 * message must be at the beginning" reject the whole request otherwise, and
 * a sticky toggle (image mode) then fails every later turn of that chat.
 */
export function foldSystemHint(messages: GatewayMessage[], hint: string): void {
  const head = messages[0];
  if (head && head.role === "system" && typeof head.content === "string") {
    messages[0] = { ...head, content: `${head.content}\n\n${hint}` };
    return;
  }
  messages.unshift({ role: "system", content: hint });
}

export function rememberScopeOf(args: Record<string, unknown>): "global" | "conversation" {
  return String(args["scope"] ?? "").toLowerCase() === "conversation" ? "conversation" : "global";
}
