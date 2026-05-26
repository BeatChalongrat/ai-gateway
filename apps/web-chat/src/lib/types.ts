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
}

export interface ConversationListResponse {
  conversations: Conversation[];
  has_more: boolean;
  next_cursor: string | null;
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
  /** local-only — true while content is being streamed in. */
  isStreaming?: boolean;
  /** local-only — current feedback rating for this message. */
  feedback?: "thumbs_up" | "thumbs_down" | null;
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

// ── SSE event shapes ────────────────────────────────────────────

export interface SSEMessageStart {
  message_id: UUID;
  conversation_id: UUID;
}

export interface SSEContentDelta {
  delta: string;
}

export interface SSEMessageComplete {
  message_id: UUID;
  token_usage?: TokenUsage;
}

export interface SSEError {
  code: string;
  message: string;
}
