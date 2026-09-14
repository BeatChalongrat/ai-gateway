package model

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// Message maps to “chat.messages_masked“.
//
// CRITICAL: “ContentMasked“ is the ONLY content field on this
// struct because the database table only stores masked content.
// Raw user prompts (pre-mask) and restored AI replies (post-restore)
// are transient — they cross the wire to the LLM via Gateway, get
// streamed back to the browser, and then disappear. The chat-edge-
// api stores the masked snapshot from Gateway's response. The
// “SecureTraceID“ points at the Gateway's secure-trace row for
// admin audits.
type Message struct {
	ID               uuid.UUID
	ConversationID   uuid.UUID
	TenantID         uuid.UUID
	UserID           uuid.UUID
	Role             string // "user" | "assistant" | "system"
	ContentMasked    string
	Status           string // "pending" | "streaming" | "completed" | "cancelled" | "error"
	ModelID          *string
	TokenUsageInput  *int
	TokenUsageOutput *int
	SecureTraceID    *uuid.UUID
	CreatedAt        time.Time
	// Branching — see migration 009. ParentID is NULL for messages
	// that haven't been edited and aren't part of an edit's
	// downstream chain; when set, points at the fork-root user
	// message id and is shared by all siblings + descendants in
	// that fork group.
	ParentID    *uuid.UUID
	BranchIndex int
	IsActive    bool

	// T4 — Thinking Mode (migration 019). Reasoning / thinking
	// summary the model emitted for this assistant turn. Stored
	// masked; raw reasoning text is filtered by the Gateway's
	// outbound DLP scan on the reasoning channel before the
	// chat-edge ever sees it. Nullable — non-reasoning turns and
	// pre-T4 rows return ``nil``.
	ReasoningContent *string

	// Web search (migration 023). JSONB array of cited sources
	// ({title,url,snippet,domain,published_date}) for an assistant
	// turn that answered with the web_search tool. nil for non-search
	// turns. Drives the citation chips on reload.
	SearchSources json.RawMessage

	// DLP canonical artifact (migration 024 / Phase 4A.2.2). The
	// canonical, tokenized, PRE-restore assistant content that is safe
	// to replay to the provider as history. ``ContentMasked`` holds the
	// restored DISPLAY text shown to the user; ``ContentCanonical`` holds
	// the provider-safe history text. ``CanonicalStatus`` is the
	// lifecycle verdict (legacy|complete|unsupported|missing|unavailable|
	// invalid|cancelled|error). Provider history uses ContentCanonical
	// ONLY when status==complete; all other states are EXCLUDED (never a
	// display fallback). Nullable for non-complete / pre-feature rows.
	ContentCanonical       *string
	CanonicalStatus        string // default 'legacy' (migration)
	CanonicalSchemaVersion *int
	CanonicalGenerationID  *string
	CanonicalSHA256        *string
	CanonicalLength        *int
	CanonicalErrorCode     *string

	// DLP display lifecycle (migration 025 / Phase 4A.2.4). Classifies what
	// ``ContentMasked`` actually holds: ``legacy_display`` (restored RAW
	// display at rest — raw-sensitive; pre-feature + flag-off writes),
	// ``canonical_safe`` (tokenized canonical), or ``redacted`` (safe
	// placeholder). Drives the read-path representation policy. Empty string
	// from a pre-migration row is treated as ``legacy_display``.
	DisplayState string

	// Desktop import identity (migration 026 / FIX 9). The desktop's own
	// message id for rows that arrived via the import endpoints; nil for
	// native chat-edge rows. Surfaced on the list wire so the desktop can
	// correlate a server row back to its local copy (dedup + tool-chain
	// re-attachment) when reading the server-authoritative transcript.
	ClientID *string

	// FIX118 (migration 043) — how the turn ENDED, next to its status.
	// TerminalCause is one of the closed set in
	// docs/contracts/terminal_cause.json (completed | stopped_by_user |
	// error | timeout | disconnected | eof_without_terminal | interrupted);
	// nil for user rows, pre-FIX118 rows and a parked row. ErrorCode is the
	// catalogue code the terminal error frame carried; nil when the turn
	// did not fail. Both ride the list wire so a reload can say WHY a turn
	// failed instead of only that it did.
	TerminalCause *string
	ErrorCode     *string
}
