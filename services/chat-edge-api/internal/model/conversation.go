package model

import (
	"time"

	"github.com/google/uuid"
)

// Conversation maps to “chat.conversations“.
//
// “Title“ is the DLP-sanitized conversation label. The title
// service hits Gateway with the first user message; Gateway returns
// either the raw value (when no PII detected) or the pseudonymized
// form. Either way, the chat-edge-api persists that result and
// never sees / stores raw PII in this field.
type Conversation struct {
	ID           uuid.UUID
	TenantID     uuid.UUID
	UserID       uuid.UUID
	Title        string
	ModelID      string
	MessageCount int
	IsDeleted    bool
	DeletedAt    *time.Time
	// IsArchived (migration 032) hides the conversation from the SIDEBAR
	// LIST only. Unlike IsDeleted it does not affect Get / Search / send /
	// export — an archived chat is fully readable, it is just filed away.
	IsArchived bool
	CreatedAt  time.Time
	UpdatedAt  time.Time
	// KnowledgeBaseID is the per-conversation RAG binding. Nullable
	// because most conversations have no KB attached. When set, the
	// message flow injects retrieved chunks into the system prompt.
	KnowledgeBaseID *uuid.UUID

	// MemoryEnabled toggles the cross-conversation memory layer for
	// THIS conversation (S1 — migration 014). Default true; set to
	// false for "temporary chats" the user doesn't want feeding into
	// their long-term memory.
	//
	// When false the message flow:
	//   - skips injecting the <saved_user_memory> system-prompt block
	//   - omits the save_memory / list_memories tools from the
	//     gateway request's tool list
	MemoryEnabled bool

	// IsTemporary (FIX127 B1 — migration 048) marks a chat the user
	// explicitly started as temporary (sidebar "temporary chat", the quick
	// window, the memory page). It is the ONLY thing that makes a chat
	// temporary: a user who turns memory off on an ordinary chat still gets
	// a title. Until FIX127 the title service treated memory-off as
	// temporary, so that user's chat stayed "New conversation" for good.
	IsTemporary bool

	// T4 — Thinking Mode (migration 019). ReasoningEnabled is the
	// master toggle; when true the message-build path adds the
	// ``reasoning`` block to the Gateway request and the SSE pump
	// forwards reasoning_summary_delta + analysis_status frames to
	// the client. Default false so existing conversations are
	// byte-identical to their pre-T4 behaviour.
	//
	// ReasoningMode mirrors the public ReasoningConfig.mode values
	// (off / auto / low / medium / high). Persisted even when
	// ReasoningEnabled is false so the previously-chosen depth
	// restores when the user re-enables.
	//
	// ShowReasoningSummary gates wire emission of the reasoning
	// channel. When false the Gateway still asks the model to
	// think (better answers) but the chat-edge SSE pump drops the
	// reasoning frames before they reach the browser.
	ReasoningEnabled     bool
	ReasoningMode        string
	ShowReasoningSummary bool

	// LockedModel pins the concrete model an "auto" conversation
	// resolved to on its first turn (Fix 5 — migration 020). Once set,
	// resolveAutoIfNeeded reuses it instead of re-running the per-turn
	// heuristic, so the model can't swap mid-conversation. Empty = not
	// locked yet (or the conversation uses an explicit model_id, which
	// is inherently stable). Cleared when the user changes the picker.
	LockedModel string
	// RoutingTier (FIX35 Part C, migration 039) — the cost-tier ceiling
	// the Auto router may spend up to for this conversation:
	// "" (tenant / deployment default) | low | medium | high | xhigh | max.
	// Forwarded as routing_hints.tier on every "auto" turn; ignored when
	// the user picked a concrete model.
	RoutingTier string

	// IsPinned floats the conversation to the sidebar's "PINNED"
	// section (migration 021), independent of the time buckets. Default
	// false. ListByUser orders is_pinned-first so pinned rows always
	// appear on the first page regardless of their updated_at.
	IsPinned bool

	// FolderID is the sidebar-folder membership (migration 022). Nullable —
	// NULL means "unfiled" (the conversation shows in the PINNED / time
	// buckets). The frontend groups conversations under folders by this id.
	FolderID *uuid.UUID

	// ProjectID is the project membership (migration 029). Nullable — NULL
	// means "no project". A project-assigned conversation inherits the
	// project's instructions + knowledge base at prompt-compile time.
	ProjectID *uuid.UUID
}
