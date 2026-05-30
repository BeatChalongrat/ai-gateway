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
 *  uses ``supported`` to gate the ReasoningToggle and ``modes`` to
 *  populate the mode picker. Absent (undefined) for non-reasoning
 *  models — the toggle stays hidden. */
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
  /** B2.2 — pin/favorite. When true the conversation shows in the
   *  sidebar's "Pinned" section; pinned_at drives that section's
   *  recency sort (null when not pinned). */
  is_pinned?: boolean;
  pinned_at?: string | null;
}

export interface ConversationListResponse {
  conversations: Conversation[];
  has_more: boolean;
  next_cursor: string | null;
}

// ── Full-text search (GET /api/v1/search) ──────────────────────
// One raw hit from the cross-conversation search endpoint. `type`
// discriminates a message-body hit from a conversation-title hit from
// a saved-memory hit; the sidebar consumes only message + conversation
// hits (memories have no conversation to open).
export interface SearchResult {
  type: "message" | "conversation" | "memory";
  conversation_id?: string;
  conversation_title?: string;
  content: string;
  role?: string;
  memory_type?: string;
  date: string;
  relevance: string;
}

export interface SearchResponse {
  query: string;
  scope: string;
  results: SearchResult[];
  total: number;
}

// Normalised sidebar search hit — one row per conversation, carrying
// the best matching snippet. Produced by chatStore from either the
// full-text endpoint or the title-only fallback.
export interface SearchHit {
  conversationId: string;
  title: string;
  /** Matched message body snippet; empty when the match was on the
   *  conversation title only (or via the title-only fallback). */
  snippet: string;
  /** "user" | "assistant" for a message-body hit; undefined otherwise. */
  role?: string;
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
  /** The caller's thumbs rating for this message. Set optimistically
   *  by setFeedback and now also hydrated from the message-list wire
   *  response (per-user), so the selected state survives a reload. */
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

export interface SSEMessageComplete {
  message_id: UUID;
  token_usage?: TokenUsage & { reasoning?: number };
}

export interface SSEError {
  code: string;
  message: string;
}
