// Wire-format types — match the Chat Edge API contract from
// services/chat-edge-api. Do NOT add or remove fields without
// reflecting them on the server side; the client trusts these
// shapes to be exact.

export type UUID = string;

export interface AuthSuccess {
  user_id: UUID;
  tenant_id: UUID;
  role: string;
  access_token: string;
}

// Category buckets exposed by the chat-edge model metadata enricher.
// Front-end groups the dropdown by these; unknown values land in
// "other".
export type ModelCategory =
  | "flagship"
  | "fast"
  | "reasoning"
  | "specialized"
  | "other";

/** T4 — Thinking Mode capability block. Present only on models
 *  whose chat-edge entry advertises reasoning support. The frontend
 *  uses ``supported`` to show the reasoning badge in the model picker.
 *  Thinking itself is automatic (Auto Thinking Mode — no user toggle);
 *  ``modes`` is retained for the API contract. Absent (undefined) for
 *  non-reasoning models. */
export interface ReasoningCapability {
  supported: boolean;
  modes: string[];
  default_mode: string;
  summary_supported: boolean;
}

export interface ModelEntry {
  id: string;
  name: string;
  provider: string;
  description: string;
  context_window: number;
  capabilities: string[];
  icon: string;
  category: ModelCategory;
  max_output_tokens?: number;
  input_price_per_1m?: number;
  output_price_per_1m?: number;
  allowed: boolean;
  /** T4 — reasoning capability. Undefined for non-reasoning models. */
  reasoning?: ReasoningCapability;
  /** Sprint 2A.5 — single source of truth for which UI to render:
   *  "chat" → chat composer + tool buttons; "video"/"audio"/"tts" →
   *  direct-dispatch media form. Missing/undefined is treated as "chat"
   *  so older chat-edge payloads degrade cleanly. */
  model_kind?: "chat" | "video" | "audio" | "tts";
  /** FIX47 Part C — an admin's per-model choice of what the picker offers
   *  (model_configs.show_in_chat). Absent on older chat-edge builds =
   *  shown. A hidden model is still allowed: a conversation bound to it
   *  keeps working, it just is not offered for new picks. */
  show_in_chat?: boolean;
  /** FIX65 — the operator's own box (gateway SELF_HOSTED_PROVIDERS).
   *  Vendor models in the picker are curated to a floor (vision + tools +
   *  streaming) so nothing there needs a capability icon; a self-hosted
   *  model is offered as it is, under its own group, with what it can do
   *  spelled out. Absent = vendor. */
  self_hosted?: boolean;
  /** Sprint 3 — coarse cost tier for a selector badge. Optional: dormant
   *  until the gateway/chat-edge serialize it; the badge renders only when
   *  present so older payloads degrade cleanly. */
  cost_tier?: RoutingTier | "free";
}

export interface ModelListResponse {
  models: ModelEntry[];
}

export interface Conversation {
  id: UUID;
  title: string;
  model_id: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  /** When set, the message flow retrieves context from this KB and
   *  injects it into the system prompt with [Source: ...] citations. */
  knowledge_base_id?: UUID | null;
  /** S1 (Personal Memory). When false this conversation is a
   *  "temporary chat": no <saved_user_memory> block is injected and
   *  the save_memory / list_memories tools are hidden from the
   *  model. Defaults to true server-side; older payloads that
   *  predate the field flow through as `undefined` which the UI
   *  treats as "memory on". */
  memory_enabled?: boolean;
  /** T4 — Thinking Mode. ``reasoning_enabled`` is the master
   *  toggle; ``reasoning_mode`` mirrors the public ReasoningConfig
   *  modes (off/auto/low/medium/high); ``show_reasoning_summary``
   *  gates whether the SSE stream surfaces the model's thinking
   *  text to the client. All three default server-side (false /
   *  "auto" / true) so older payloads degrade cleanly. */
  reasoning_enabled?: boolean;
  reasoning_mode?: string;
  show_reasoning_summary?: boolean;
  /** FIX35 Part C — Auto router cost ceiling for this conversation:
   *  "" (tenant / deployment default) | low | medium | high | xhigh | max.
   *  Only meaningful while the conversation is on Auto. */
  routing_tier?: RoutingTier | "";
  /** Sidebar pin (migration 021). When true the conversation appears
   *  in the dedicated "PINNED" section at the top of the sidebar,
   *  above the time buckets. Defaults false server-side; older
   *  payloads flow through as `undefined` (treated as unpinned). */
  is_pinned?: boolean;
  /** Archived (migration 032). An archived chat leaves the sidebar list
   *  but is NOT deleted: it stays openable, searchable and sendable, and
   *  the archive view lists it. Older payloads flow through as
   *  `undefined`, treated as not archived. */
  is_archived?: boolean;
  /** Legacy folder membership (migration 022). RETIRED in Phase 9:
   *  folders became projects (migration 031) and nothing in the UI reads
   *  this any more. The server still returns it while the column exists
   *  for rollback, so the field stays declared — do not group by it. */
  folder_id?: UUID | null;
  /** Project membership — the ONE organiser since Phase 9.
   *  `null`/`undefined` means the conversation is unassigned and shows in
   *  the PINNED / time buckets; a UUID groups it under that project in
   *  the sidebar (and the server injects the project's instructions + KB
   *  into the prompt when the project carries them). */
  project_id?: UUID | null;
}

/** A project — the single sidebar organiser (Phase 9). Groups
 *  conversations, and OPTIONALLY carries shared instructions + a
 *  knowledge base the server injects into every chat inside it. A
 *  project with empty instructions and no KB is pure grouping: exactly
 *  what a folder was before migration 031 merged the two. */
export interface Project {
  id: UUID;
  name: string;
  instructions: string;
  knowledge_base_id: UUID | null;
  /** Live count of non-deleted conversations assigned to this project. */
  conversation_count: number;
  created_at: string;
  updated_at: string;
}

/** One saved version of a canvas artifact (migration 030). Rows sharing
 *  (conversation_id, artifact_key) form the lineage; the highest
 *  ``version`` is current. A revert appends a new version rather than
 *  mutating history — same model as the Phase-4 file workspace. */
export interface CanvasArtifactRow {
  id: UUID;
  conversation_id: UUID;
  message_id: UUID | null;
  artifact_key: string;
  type: "html" | "svg" | "markdown" | "mermaid" | "react";
  title: string | null;
  content: string;
  version: number;
  created_at: string;
}

export interface ConversationListResponse {
  conversations: Conversation[];
  has_more: boolean;
  next_cursor: string | null;
}

/** One hit from GET /search — the S6 full-text engine (ts_rank over
 *  message bodies / titles / memories). Mirrors the Go
 *  PastContextResult wire shape. */
export interface PastContextSearchResult {
  type: string; // "message" | "conversation" | "memory"
  conversation_id?: string;
  conversation_title?: string;
  content: string;
  role?: string;
  date: string;
  relevance: string;
}

export interface SearchResponse {
  query: string;
  scope: string;
  results: PastContextSearchResult[];
  total: number;
}

/** Sidebar-friendly content match: one row per conversation whose
 *  MESSAGE BODIES matched the search (title matches render as normal
 *  conversation rows instead). */
export interface ConversationSearchHit {
  conversation_id: string;
  conversation_title: string;
  snippet: string;
  date: string;
}

export type MessageRole = "user" | "assistant" | "system";
export type MessageStatus =
  | "pending"
  | "streaming"
  | "completed"
  | "cancelled"
  | "error"
  | "regenerated"
  // FIX74 — the turn is parked on a question card (web path); the row
  // carries ``pending_question`` and streams again when it is answered.
  | "waiting_user";

export interface TokenUsage {
  input: number;
  output: number;
}

export interface FileAttachment {
  id: UUID;
  conversation_id?: UUID;
  message_id?: UUID;
  filename: string;
  content_type: string;
  file_size_bytes: number;
  download_url?: string;
  extraction_status:
    | "pending"
    | "processing"
    | "completed"
    | "failed"
    | "unsupported";
  created_at: string;
  /** Phase 4 — Excel Smart Workspace. True for files produced by an
   *  AI edit plan (via POST /excel/execute) so the UI can flip them
   *  into the result-card treatment with version + change-log. */
  is_generated?: boolean;
  /** FIX67 — the model that produced a generated image/video ("gpt-image-1",
   *  or "mock-image-1" after a fallback). Shown as "Made by …" under it. */
  generated_by?: string;
  parent_file_id?: UUID;
  version?: number;
  change_log?: string[];
}

/** Validation report shape returned by the chat-edge Excel
 *  validator. Mirrors excel_validator.py output. */
export interface ExcelValidationReport {
  valid: boolean;
  issues: string[];
  warnings: string[];
  stats: { sheets?: number; charts?: number; broken_formulas?: number };
}

/** Response from POST /conversations/{id}/excel/execute. */
export interface ExcelExecuteResponse {
  file: FileAttachment;
  validation: ExcelValidationReport;
  changes: string[];
}

/** Phase 4 Session 2 — Word/PDF report generation.
 *  Mirrors doc_validator.py output (docx + pdf use the same envelope). */
export interface DocValidationReport {
  valid: boolean;
  file_type: "docx" | "pdf" | string;
  file_size_bytes: number;
  errors: string[];
  warnings: string[];
  stats: {
    page_count?: number;
    paragraph_count?: number;
    table_count?: number;
    section_count?: number;
    word_count?: number;
  };
}

/** Per-action change_log row from docx_generator.py / pdf_generator.py. */
export interface DocChangeLogEntry {
  action: number;
  type: string;
  status: "ok" | "error" | "skipped";
  detail?: string;
}

export interface DocGenerationReport {
  success: boolean;
  mode?: "create" | "edit";
  output_path: string;
  file_size_bytes: number;
  page_count?: number;
  font_used?: string;
  actions_executed: number;
  actions_failed: number;
  change_log: DocChangeLogEntry[];
}

/** Response from POST /conversations/{id}/document/execute. */
export interface DocExecuteResponse {
  file: FileAttachment;
  validation: DocValidationReport;
  generation: DocGenerationReport;
  changes: string[];
}

/** Phase 4 Session 5 — Artifact Workspace types. */

/** One entry in the workspace panel's generated-files list. The
 *  shell extends FileAttachment with the chain rollup (how many
 *  versions exist and what the original upload was called). */
export interface GeneratedFileEntry extends FileAttachment {
  version_count: number;
  parent_filename?: string;
}

/** One row in the version timeline. */
export interface FileVersion extends FileAttachment {
  is_current: boolean;
}

export interface ComparisonChange {
  type: string;
  detail: string;
  sheet?: string;
}

export interface ComparisonReport {
  v1_size_bytes: number;
  v2_size_bytes: number;
  size_change: string;
  file_type: string;
  structural_changes: ComparisonChange[];
  content_diff_summary: string;
}

export interface ComparisonResponse {
  v1: {
    id: string;
    filename: string;
    version: number;
    file_size_bytes: number;
    created_at: string;
  };
  v2: {
    id: string;
    filename: string;
    version: number;
    file_size_bytes: number;
    created_at: string;
  };
  comparison: ComparisonReport;
}

/** Phase 4 Session 3 — PowerPoint generation.
 *  Per-slide entry in pptx_generator.py's change_log. */
export interface PptxChangeLogEntry {
  slide: number;
  type: string;
  status: "ok" | "error" | "skipped";
  detail?: string;
}

/** Output envelope from pptx_generator.py. The Go DocService
 *  marshals into DocGenerationReport, but the change_log entries
 *  use slide+type rather than action+type — so we typedef a
 *  pptx-specific subtype the result card renders against. */
export interface PptxGenerationReport extends Omit<DocGenerationReport, "change_log"> {
  slide_count?: number;
  slides_created?: number;
  slides_failed?: number;
  change_log: PptxChangeLogEntry[];
}

export interface FileListResponse {
  files: FileAttachment[];
}

export interface Message {
  id: UUID;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  model_id?: string | null;
  token_usage?: TokenUsage | null;
  created_at: string;
  files?: FileAttachment[];
  /** Branching — server-computed; absent for legacy messages. */
  branch_index?: number;
  branch_count?: number;
  has_branches?: boolean;
  /** S7 — context attribution for assistant messages. Populated on
   *  the conversation-list response when the AI's reply drew on
   *  saved memories, conversation state, a past-conversation
   *  search, or a compaction event. Absent / empty for user
   *  messages and assistant messages with no special context. */
  context_sources?: ContextSource[];
  /** local-only — true while content is being streamed in. */
  isStreaming?: boolean;
  /** local-only — the key the thread renders this row under. An
   *  optimistic row is born with a temporary ``id`` that the stream
   *  later replaces with the persisted one; React keys on THIS value so
   *  the bubble is never unmounted and re-created by that swap
   *  (``id`` is for the server; ``client_key`` is for the DOM). Absent
   *  for rows loaded from the server, whose ``id`` never changes. */
  client_key?: string;
  /** local-only — current feedback rating for this message. */
  feedback?: "thumbs_up" | "thumbs_down" | null;
  /** T4 — Thinking Mode. ``reasoning_content`` is the accumulated
   *  thinking text (persisted on the server); ``is_thinking`` is
   *  the local-only flag the SSE handler flips while reasoning
   *  fragments are arriving; ``thinking_duration_ms`` is the
   *  measured analysis duration captured between
   *  ``analysis_status: started`` and ``analysis_status: completed``. */
  reasoning_content?: string | null;
  is_thinking?: boolean;
  thinking_duration_ms?: number;
  /** local-only — flipped on by the ``image_generating`` SSE event
   *  while the backend is generating an image for this turn.
   *  MessageBubble renders the ImageGeneratingPlaceholder until the
   *  ``message_files`` event (or a terminal frame) clears it. */
  is_generating_image?: boolean;
  /** local-only — flipped on by the ``video_generating`` SSE event while
   *  the backend is generating a video for this turn. MessageBubble shows
   *  the VideoGeneratingPlaceholder until ``message_files`` (or a terminal
   *  frame) clears it. */
  is_generating_video?: boolean;
  /** Web-search citations — the sources the assistant cited via the
   *  web_search tool. Persisted server-side (search_sources column) so
   *  they hydrate on reload, and delivered live via the
   *  ``message_sources`` SSE event. Drives the citation chips. */
  search_sources?: SearchSource[];
  /** local-only — flipped on by the ``search_status`` SSE event while
   *  the backend is searching the web. MessageBubble shows the
   *  SearchingIndicator until content streams / sources land. */
  is_searching?: boolean;
  /** FIX33 A-W9 — the server (or the desktop host) is summarising older
   *  messages before answering (`compaction_status: "summarizing"`); the
   *  thread shows "Organizing the conversation…" until content streams. */
  is_compacting?: boolean;
  /** local-only (Phase 8) — the sandboxed run_python calls this turn
   *  made, delivered by the ``code_execution`` SSE event and keyed by
   *  tool-call id. Renders the code + stdout card. NOT persisted: a
   *  reload after the turn keeps the produced files but drops the
   *  cards (see the SSE handler comment in chat-edge). */
  code_runs?: CodeRun[];
  /** local-only (A2) — generic activity rows for tools WITHOUT a
   *  dedicated indicator (past-conversation search, canvas update —
   *  plus any unknown/MCP tool). Delivered by the ``tool_activity``
   *  SSE event, keyed by tool-call id. FIX50 P1-D: kept after
   *  message_complete in BOTH modes (finished cards fold to their
   *  header) so a tool-computed answer keeps showing its work;
   *  still ephemeral like code_runs — a reload drops them. */
  tool_activities?: ToolActivity[];
  /** local-only (desktop shell, Phase 4) — the agent's live plan
   *  checklist from ``plan_update`` frames. Ephemeral. */
  plan?: PlanStep[];
  /** local-only (desktop shell, Phase 4) — the turn paused at a budget
   *  checkpoint (``paused`` frame); drives the Continue affordance. */
  paused?: { reason: string } | null;
  /** local-only (desktop shell, Phase 4) — an in-thread tool-consent
   *  prompt awaiting the user. Set from the bridge's consent.request;
   *  cleared when answered. */
  pending_consent?: PendingConsent | null;
  /** FIX73 (desktop shell) — the model's question card (ask_user_question)
   *  awaiting the user. Set from the bridge's question.request; cleared when
   *  answered, skipped, or the turn ends. */
  pending_question?: PendingQuestion | null;
  /** local-only (desktop shell, Phase 4) — the phase marker text
   *  ("Working… (step n/max)"); shown while content is still empty. */
  agent_status?: string | null;
  /** local-only (desktop shell, FIX30 §3.2) — what the turn is WAITING on,
   *  from the host's ``status`` frames: the consent card ("consent") or a
   *  long local tool ("tool", re-sent every heartbeat with its elapsed
   *  time). Null when the model is the one working. */
  agent_wait?: { kind: "consent" | "tool" | "question"; tool: string; promptId?: string; elapsedMs: number } | null;
  /** local-only — wall-clock duration of the streaming turn (send →
   *  message_complete). Shown in the per-message metadata line; absent
   *  after a page reload (not persisted). */
  response_time_ms?: number;
  /** B1 — local-only. Set when the stream failed; drives the error
   *  bubble styling + localized message in MessageBubble. */
  error_code?: string;
  error_message?: string;
  error_attempts?: FallbackAttempt[];
  /** B2 — local-only. Set when a fallback model answered, so the UI
   *  can show a small "answered by X" notice under the message. */
  fallback_info?: {
    requested_model: string;
    actual_model: string;
    reason?: string;
  };
  /** FIX50 P1-C — local-only. Set when input DLP masked part of the
   *  QUESTION before the model read it. Counts per detector only,
   *  never values or spans. FIX51 Part 1 (OPEN_DECISIONS #62): no
   *  longer RENDERED — the under-answer line was removed by owner
   *  decision. The field stays because the frame still flows and the
   *  admin masking counter depends on the same upstream signal; a
   *  future quieter surface (small icon, only when the mask hit the
   *  question body) would read it from here. */
  dlp_notice?: {
    findings: number;
    by_detector: Record<string, number>;
  };
  /** local-only — set when the gateway downgraded a reasoning request
   *  (the resolved model can't reason). FIX51 Part 1: no longer
   *  rendered — capability is told at selection time instead (the
   *  thinking toggle disables with the reason). The field stays
   *  because the frame still arrives and is stashed. */
  reasoning_notice?: {
    model?: string;
    reason?: string;
  };
  /** local-only — set when one or more attached images couldn't be
   *  read into the vision request (unreachable, resize failure, or an
   *  unsupported format). Drives a small informational line under the
   *  reply naming the files, same quiet styling as reasoning_notice —
   *  this is not an error; the turn still proceeds for any images that
   *  did resolve. */
  /** FIX32 §1.2 (desktop shell) — the host compacted the SENT context this
   *  turn: `dropped` older messages were not sent to the model. The user is
   *  told, the way web-chat's compacted_history source marker tells them. */
  compaction_notice?: { dropped: number; sent?: number; total?: number } | null;
  image_notice?: {
    filenames: string[];
  };
}

/** FIX33 A-W6 — the context meter's numbers. Web: GET
 *  /conversations/{id}/context-info (a dry run of the compiler). Desktop:
 *  the host's `context_budget` turn event / `context.info` op, same fields.
 *  The meter renders ONLY from `meter_show_ratio` (0.70) of the budget up;
 *  below that the header shows nothing (spec addendum #1). */
export interface ContextInfo {
  used_ratio: number;
  history_tokens: number;
  messages_budget: number;
  threshold_tokens: number;
  messages_total: number;
  messages_sent?: number;
  messages_would_send?: number;
  state_present: boolean;
  compacted: boolean;
  dropped?: number;
  omitted_older?: number;
  est_messages_remaining: number;
  /** Optional on the web wire; the client falls back to CONTEXT_METER_SHOW_RATIO. */
  show_meter?: boolean;
  meter_show_ratio?: number;
}

/** S7 — one source the AI drew on while producing an assistant
 *  message. Mirrors chat.response_context_sources on the wire. */
export type ContextSourceType =
  | "saved_memory"
  | "conversation_state"
  | "past_conversation"
  | "file_context"
  | "compacted_history";

export interface ContextSource {
  id: UUID;
  source_type: ContextSourceType;
  source_id?: string | null;
  display_label: string;
  /** Per-type metadata. For saved_memory: { memory_type, category }.
   *  For past_conversation: { conversation_title, hit_type,
   *  relevance, date }. For file_context: { filename, file_type }. */
  metadata?: Record<string, unknown> | null;
  created_at: string;
}

export interface UserPreferences {
  custom_instructions_about: string;
  custom_instructions_respond: string;
  updated_at?: string;
}

export interface ShareLink {
  share_token: string;
  share_url: string;
  expires_at?: string;
  created_at: string;
}

export type ShareExpiry = "1h" | "24h" | "7d" | "never";

export interface MessageListResponse {
  messages: Message[];
  has_more: boolean;
}

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
  };
}

export interface FeedbackResponse {
  feedback: {
    id: UUID;
    message_id: UUID;
    rating: "thumbs_up" | "thumbs_down";
    created_at: string;
  };
}

// ── Knowledge base (RAG) ────────────────────────────────────────

export type KnowledgeDocumentStatus =
  | "pending"
  | "processing"
  | "indexed"
  | "failed";

export interface KnowledgeBase {
  id: UUID;
  name: string;
  description: string;
  embedding_model: string;
  chunk_size: number;
  chunk_overlap: number;
  is_active: boolean;
  document_count: number;
  total_chunks: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocument {
  id: UUID;
  knowledge_base_id: UUID;
  filename: string;
  content_type: string;
  file_size_bytes: number;
  status: KnowledgeDocumentStatus;
  chunk_count: number;
  char_count: number;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeBaseListResponse {
  knowledge_bases: KnowledgeBase[];
}

export interface KnowledgeBaseDetailResponse {
  knowledge_base: KnowledgeBase;
  documents: KnowledgeDocument[];
}

export interface KnowledgeDocumentListResponse {
  documents: KnowledgeDocument[];
}

export interface KnowledgeSearchHit {
  content: string;
  score: number;
  document_id: UUID;
  filename: string;
  chunk_index: number;
}

export interface KnowledgeSearchResponse {
  results: KnowledgeSearchHit[];
}

// ── Memory system (S1) ─────────────────────────────────────────

/** One row from chat.user_memories. Mirrors the JSON shape the
 *  MemoryHandler serialises — every field optional / null-safe so
 *  the frontend can render rows with missing metadata without
 *  defensive checks at every site. */
export interface Memory {
  id: UUID;
  scope_type: string;
  scope_id?: UUID | null;
  memory_type: string;
  category?: string;
  content: string;
  source_type: string;
  source_conversation_id?: UUID | null;
  source_message_id?: UUID | null;
  evidence_excerpt?: string;
  status: string;
  confidence_score: number;
  importance_score: number;
  is_user_confirmed: boolean;
  is_pinned: boolean;
  sensitivity_level: string;
  use_count: number;
  last_used_at?: string | null;
  valid_until?: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryListResponse {
  memories: Memory[];
  total: number;
}

export interface MemoryEvent {
  id: UUID;
  memory_id?: UUID | null;
  action: string;
  actor_type: string;
  reason?: string;
  metadata?: Record<string, unknown> | null;
  created_at: string;
}

export interface MemoryEventListResponse {
  events: MemoryEvent[];
}

// ── SSE event shapes ────────────────────────────────────────────

export interface SSEMessageStart {
  message_id: UUID;
  conversation_id: UUID;
}

export interface SSEContentDelta {
  delta: string;
}

/** T4 — one fragment of reasoning / thinking text from the model.
 *  Emitted between ``analysis_status: started`` and
 *  ``analysis_status: completed``. */
export interface SSEReasoningDelta {
  delta: string;
}

/** T4 — phase marker bracketing the reasoning stream. The
 *  ChatStore flips Message.is_thinking on `started` and back off
 *  on `completed`, computing the duration in between. */
export interface SSEAnalysisStatus {
  status: "started" | "completed";
}

/** Image-generation phase marker. The backend emits `started` just
 *  before the image provider call begins; the ChatStore flips
 *  Message.is_generating_image on, and the `message_files` event
 *  (or a terminal frame) flips it back off. */
export interface SSEImageGenerating {
  status: "started";
}

export interface SSEMessageComplete {
  message_id: UUID;
  token_usage?: TokenUsage & { reasoning?: number };
  /** FIX35 Part B — the model that actually answered (an "auto"
   *  conversation's reply names the model the router picked). */
  model_id?: string;
}

/** FIX35 Part B — emitted as soon as the gateway names the model that is
 *  answering (before content on an auto / openrouter/auto route). The
 *  message's ``model_id`` is set from it so the meta line under the reply
 *  can say which model an Auto turn got, without waiting for the end. */
export interface SSEModelResolved {
  model: string;
}

// ── Usage & credits (credit badge + popover) ─────────────────
// Served by chat-edge GET /usage/me, which proxies the gateway's
// portal API (same numbers the Portal Dashboard shows).
// 1 credit = 1 token; the pool is a lifetime allotment.

export interface ModelUsage {
  model: string;
  /** Gateway provider id (openai / anthropic / openrouter / …);
   *  may be empty on legacy rows. */
  provider: string;
  tokens: number;
  /** Display credits — real logged cost × the gateway's credit rate,
   *  so expensive models burn proportionally more than cheap ones. */
  credits_used: number;
  request_count: number;
  /** Share of total credit usage, 0–100 with one decimal. */
  percentage: number;
}

export interface UsageResponse {
  credits: {
    total: number;
    used: number;
    remaining: number;
    reset_date: string | null;
  };
  usage_by_model: ModelUsage[];
  /** Display credits across ALL models (not just the top rows). */
  total_credits_used: number;
  period: string;
}

export interface SSEMessageFiles {
  message_id: UUID;
  files: FileAttachment[];
}

/** One web-search citation (rendered as a chip under the message). */
export interface SearchSource {
  title: string;
  url: string;
  snippet: string;
  domain: string;
  published_date?: string | null;
}

/** Web-search phase marker. The backend emits `searching` just before
 *  the web_search tool runs; the ChatStore flips Message.is_searching
 *  on, and content_delta / message_sources / message_complete clear it. */
export interface SSESearchStatus {
  status: "searching";
}

/** Web-search citations delivered live after the assistant turn. The
 *  ChatStore stores them on the message; they also hydrate on reload
 *  from the message-list API (search_sources). */
export interface SSEMessageSources {
  message_id: UUID;
  sources: SearchSource[];
}

/** One sandboxed python run (Phase 8 Task 2). The backend emits this
 *  twice per call — `running` when the sandbox job starts, then the
 *  terminal frame — both under the same `id`, so the card updates in
 *  place instead of stacking. */
export interface CodeRun {
  id: string;
  status: "running" | "completed" | "error";
  code: string;
  stdout?: string;
  stderr?: string;
  exit_code?: number;
  timed_out?: boolean;
  duration_ms?: number;
  /** Files the run wrote to /job/out and that were attached to the
   *  message (they also arrive as normal attachments). */
  artifacts?: string[];
  /** Output was clipped for display; the model still saw all of it. */
  truncated?: boolean;
}

/** SSE payload for a run_python progress frame — identical shape to
 *  CodeRun (the backend serialises CodeExecutionUpdate directly). */
export type SSECodeExecution = CodeRun;

/** One generic tool-activity frame (the ``tool_activity`` SSE event).
 *  Two frames per call share an id: "started" then "finished" — the
 *  client upserts by id so they collapse into one card.
 *
 *  Phase 4 (desktop shell): the host emits RICH payloads — arg summary on
 *  ``started``; success/result/code/screenshot on ``finished`` — so the
 *  shell renders full tool cards. The web path still emits only
 *  {id,tool,phase}; every extra field is optional so ToolCard degrades to
 *  the thin running/done line for web. ``parentId`` nests a sub-agent's
 *  child cards under the parent run_subagent card. ``output`` accumulates
 *  live ``tool_output`` chunks (run_command mini-terminal). */
export interface ToolActivity {
  id: string;
  tool: string;
  phase: "started" | "finished";
  argsSummary?: string;
  /** FIX77 ② — the ONE human-useful argument (filename, host, query) the
   *  row shows beside its friendly label; the raw argsSummary moved into
   *  the card's expanded detail. */
  subject?: string;
  parentId?: string | null;
  success?: boolean;
  durationMs?: number;
  input?: string;
  resultPreview?: string;
  isCodeRun?: boolean;
  codeText?: string | null;
  imageDataUri?: string | null;
  /** FIX79 — every image ONE call produced (run_python alternatives saved
   *  under distinct names in a single run). All render; a later run of the
   *  same tool supersedes the whole set together. */
  imageDataUris?: string[] | null;
  /** Accumulated live stdout/stderr from ``tool_output`` frames. */
  output?: string;
}

/** One step of the agent's live plan checklist (``plan_update`` SSE). */
export interface PlanStep {
  text: string;
  status: "pending" | "in_progress" | "done";
}

/** An in-thread tool-consent request (desktop shell only), rendered as a
 *  card in the streaming assistant message until answered. */
export interface PendingConsent {
  promptId: string;
  tool: string;
  description?: string;
  category?: string;
  argumentSummary: string;
  argumentDetail?: string;
  dangerClass: string;
  site?: string;
  grantOptions?: ("once" | "session" | "always")[];
  alwaysPromptReason?: "download" | "sensitive-info" | "authorization" | null;
  /** FIX31 §1.1: set when the request comes from a sub-agent the assistant
   *  started on its own — the card says who asked and with what task. */
  requestedBy?: { kind: "subagent"; subagentId: string; label: string; task: string };
}

/** FIX73 (desktop shell) — one question of the model's question card. */
export interface PendingQuestionItem {
  question: string;
  header: string;
  multiSelect: boolean;
  options: { label: string; description: string }[];
}
/** FIX73 — the model asked the user something (ask_user_question). Rendered
 *  as a card in the streaming assistant row until answered or skipped. The
 *  "Something else" row and the free reply box are the CLIENT's additions:
 *  the user's own text goes back as the answer, never the word "Other". */
export interface PendingQuestion {
  promptId: string;
  toolCallId?: string;
  questions: PendingQuestionItem[];
}
/** FIX73 — what the card posts back (question.respond). */
export interface QuestionAnswerPayload {
  promptId: string;
  outcome: "answered" | "skipped";
  answers: Record<string, string[]>;
  response: string | null;
}

/** SSE payload — the backend serialises ToolActivityUpdate directly. */
export type SSEToolActivity = ToolActivity;

/** One attempted model in a fallback chain (B2/B3). */
export interface FallbackAttempt {
  model?: string;
  provider?: string;
  error?: string;
  error_code?: string;
  status?: string;
}

export interface SSEError {
  code: string;
  message: string;
  /** Structured payload (provider / upstream status). */
  details?: Record<string, unknown>;
  /** Per-model failure log when the whole chain failed (B3). */
  attempts?: FallbackAttempt[];
  /** FIX47 — the gateway's verdict: can the same request succeed on a
   *  retry? Absent on frames from older builds. */
  retryable?: boolean;
}

/** B2 — emitted before content when a fallback model answered. */
export interface SSEFallbackInfo {
  requested_model: string;
  actual_model: string;
  reason?: string;
  attempts?: FallbackAttempt[];
}

/** Emitted before content when the gateway downgraded a reasoning
 *  request because the resolved model can't reason. Informational —
 *  never rendered as an error. */
export interface SSEReasoningNotice {
  model?: string;
  reason?: string;
}

/** ``provider_stall`` SSE frame — chat-edge reporting that the upstream
 *  model has produced nothing for its threshold (stalled=true), or that
 *  output resumed (stalled=false). Informational: the turn is not
 *  cancelled and may still finish on its own. */
export interface SSEProviderStall {
  stalled: boolean;
  waiting_seconds?: number;
}

/** ``image_notice`` SSE frame — one or more attached images couldn't be
 *  turned into vision content this turn (unreachable, resize failure, or
 *  unsupported format). Informational: the turn still runs, minus those
 *  images. Emitted once, before content. */
export interface SSEImageNotice {
  failures: { filename: string; reason: string }[];
}

// ── Direct-dispatch media generation (Sprint 2A.5) ──────────────
// Wire shapes for the BACKEND public endpoints POST /v1/videos|audio/
// generations + GET /v1/jobs/{id}. Every credit field carries a sibling
// *_usd display string.

export interface VideoGenInput {
  prompt: string;
  model: string;
  duration_sec: number;
  resolution: string;
  has_audio: boolean;
  conversation_id?: string | null;
  idempotency_key?: string;
}

export interface AudioGenInput {
  prompt: string;
  model: string;
  mode: "tts" | "music";
  voice_id?: string;
  duration_sec?: number;
  conversation_id?: string | null;
  idempotency_key?: string;
}

export interface MediaGenSubmitResponse {
  job_id: string;
  status: string;
  estimated_cost_credits: number;
  estimated_cost_usd: string;
  balance_after_credits: number;
  balance_after_usd: string;
  requires_confirmation: boolean;
  poll_url: string;
}

export interface MediaProvenance {
  provider: string;
  model_id: string;
  c2pa_embedded?: boolean;
  synthid_embedded?: boolean;
  visible_watermark?: boolean;
  generation_seed?: number;
  provider_disclosed_at?: string;
}

export interface MediaJobStatus {
  job_id: string;
  status: string;
  progress_pct?: number | null;
  output_url?: string | null;
  provenance?: MediaProvenance | null;
  actual_cost_credits?: number | null;
  actual_cost_usd?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

// ─── Cloud scheduled tasks ───────────────────────────────────
// SERVER-SIDE scheduled prompts (chat-edge /cloud-tasks). Unlike the
// desktop-only LOCAL tasks (host in-process timer, hostApi), these fire
// even with the computer off, but cannot touch local files and run with
// a read-only tool set only. Managed over plain authenticated HTTP and
// available in BOTH the browser and the desktop shell.
export interface CloudTask {
  id: string;
  conversation_id: string;
  name: string;
  prompt: string;
  schedule_kind: string;
  interval_seconds: number;
  next_run_at: string;
  enabled: boolean;
  max_runs?: number | null;
  run_count: number;
  last_run_at?: string | null;
  paused_reason?: string | null; // "revoked" | "max_runs" | "no_balance"
  created_at: string;
  updated_at: string;
}
export interface CloudTaskRun {
  id: string;
  task_id: string;
  conversation_id?: string | null;
  fired_at: string;
  outcome: string; // "ok" | "refused_revoked" | "refused_no_balance" | "error"
  skip_reason?: string | null; // "revoked" | "no_balance" | "tool_withheld" | ...
  assistant_message_id?: string | null;
  policy_action?: string | null; // DLP verdict: "allow" | "mask" | "block" | ...
  tokens?: number | null;
  error_detail?: string | null;
}

// ── FIX35 Part C — Auto router cost tiers ─────────────────────
/** The gateway's five price bands (services/auto_router/tiers.py). A
 *  conversation on Auto may set a ceiling; the picker shows a model's band. */
export type RoutingTier = "low" | "medium" | "high" | "xhigh" | "max";
export const ROUTING_TIERS: readonly RoutingTier[] = ["low", "medium", "high", "xhigh", "max"];
