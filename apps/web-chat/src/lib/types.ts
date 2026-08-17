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
  /** Sprint 3 — coarse cost tier for a selector badge. Optional: dormant
   *  until the gateway/chat-edge serialize it; the badge renders only when
   *  present so older payloads degrade cleanly. */
  cost_tier?: "free" | "low" | "medium" | "high";
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
  | "regenerated";

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
  /** local-only (Phase 8) — the sandboxed run_python calls this turn
   *  made, delivered by the ``code_execution`` SSE event and keyed by
   *  tool-call id. Renders the code + stdout card. NOT persisted: a
   *  reload after the turn keeps the produced files but drops the
   *  cards (see the SSE handler comment in chat-edge). */
  code_runs?: CodeRun[];
  /** local-only (A2) — generic activity rows for tools WITHOUT a
   *  dedicated indicator (save_memory, list_memories, conversation
   *  state, past-conversation search, canvas update — plus any
   *  unknown/MCP tool). Delivered by the ``tool_activity`` SSE event,
   *  keyed by tool-call id. Ephemeral like code_runs and additionally
   *  cleared at message_complete. */
  tool_activities?: ToolActivity[];
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
 *  client upserts by id so they collapse into one indicator row. */
export interface ToolActivity {
  id: string;
  tool: string;
  phase: "started" | "finished";
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
}

/** B2 — emitted before content when a fallback model answered. */
export interface SSEFallbackInfo {
  requested_model: string;
  actual_model: string;
  reason?: string;
  attempts?: FallbackAttempt[];
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
