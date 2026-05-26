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

export interface ModelEntry {
  id: string;
  name: string;
  provider: string;
  capabilities: string[];
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

export interface Message {
  id: UUID;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  model_id?: string | null;
  token_usage?: TokenUsage | null;
  created_at: string;
  /** local-only — true while content is being streamed in. */
  isStreaming?: boolean;
  /** local-only — current feedback rating for this message. */
  feedback?: "thumbs_up" | "thumbs_down" | null;
}

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
