// SearchService — S6 cross-conversation search.
//
// One service, three sources (messages, conversation titles, saved
// memories), one unified result shape (PastContextResult). The AI's
// search_past_conversations tool + the GET /api/v1/search endpoint
// both call SearchPastContext; the only difference is the result
// formatter (the tool stringifies for the LLM, the endpoint serves
// JSON).
//
// Every query is scoped by (userID, tenantID). The repo layer
// enforces this in SQL — see SearchUserMessages's JOIN through
// chat.conversations and the user_id / tenant_id WHERE clauses in
// the conversation + memory repos.
package service

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

// PastContextResult is the unified hit shape returned by
// SearchPastContext. Type discriminates between "message" (a hit
// inside a past conversation's body), "conversation" (a title
// match), and "memory" (a saved fact). Relevance is a coarse
// "high" / "medium" bucket derived from ts_rank so the AI can
// weight results without having to reason about a raw float.
type PastContextResult struct {
	Type           string `json:"type"`
	ConversationID string `json:"conversation_id,omitempty"`
	// MessageID — message hits only (FIX132 F12): the page opens the chat
	// at this message. The AI tool's text formatter does not print it.
	MessageID         string `json:"message_id,omitempty"`
	ConversationTitle string `json:"conversation_title,omitempty"`
	Content           string `json:"content"`
	Role              string `json:"role,omitempty"`        // messages only
	MemoryType        string `json:"memory_type,omitempty"` // memories only
	Date              string `json:"date"`
	Relevance         string `json:"relevance"`
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

type SearchService struct {
	messages      *repository.MessageRepo
	conversations *repository.ConversationRepo
	memories      *repository.MemoryRepo
	// Phase 4A.2.4.1 — when true, message hits are emitted through
	// SearchSafeContent so a legacy raw ``content_masked`` never reaches the
	// search index or the LLM ``search_past_conversations`` tool. Wired from
	// config in main.go; default false (legacy passthrough).
	displayLifecycle bool
}

// SetDisplayLifecycle toggles the non-raw search representation (Phase
// 4A.2.4.1). Set true when DLP_DISPLAY_LIFECYCLE_ENABLED is on.
func (s *SearchService) SetDisplayLifecycle(enabled bool) {
	s.displayLifecycle = enabled
}

func NewSearchService(
	messages *repository.MessageRepo,
	conversations *repository.ConversationRepo,
	memories *repository.MemoryRepo,
) *SearchService {
	return &SearchService{
		messages:      messages,
		conversations: conversations,
		memories:      memories,
	}
}

// ---------------------------------------------------------------------------
// SearchPastContext — unified entry point
// ---------------------------------------------------------------------------

// Scope values accepted by SearchPastContext. "" defaults to
// ScopeAll (same as Claude's conversation_search default).
const (
	ScopeAll           = "all"
	ScopeMessages      = "messages"
	ScopeConversations = "conversations"
	ScopeMemories      = "memories"
)

// contentTruncate is the per-result content cap. Long messages
// can blow out the formatted tool result's token cost; 500 chars
// is enough to identify "yes that's the conversation I meant".
const contentTruncate = 500

// SearchPastContext is the one entry point both the AI tool and
// the HTTP endpoint call. scope filters which sources to query;
// the default ("all" or "") unions messages + conversations +
// memories. maxResults defaults to 5, capped at 20.
//
// Errors from individual repo calls are swallowed (with a nil
// return path) so a single broken source doesn't black out the
// whole search; the user sees the surviving hits.
func (s *SearchService) SearchPastContext(
	ctx context.Context, userID, tenantID uuid.UUID, query, scope string, maxResults int,
) ([]PastContextResult, error) {
	cleaned := SanitizeSearchQuery(query)
	if cleaned == "" {
		return nil, nil
	}
	if maxResults <= 0 {
		maxResults = 5
	}
	if maxResults > 20 {
		maxResults = 20
	}
	if scope == "" {
		scope = ScopeAll
	}

	wantMessages := scope == ScopeAll || scope == ScopeMessages
	wantConvs := scope == ScopeAll || scope == ScopeConversations
	wantMemories := scope == ScopeAll || scope == ScopeMemories

	out := make([]PastContextResult, 0, maxResults)

	// ── Messages ──────────────────────────────────────────
	if wantMessages {
		limit := maxResults
		if scope == ScopeAll {
			// Reserve slots for the other sources when we're scoped
			// to "all" — biased toward messages (most useful) but
			// leaves headroom for conv-title + memory matches.
			limit = maxResults * 6 / 10
			if limit < 1 {
				limit = 1
			}
		}
		hits, err := s.messages.SearchUserMessages(ctx, userID, tenantID, cleaned, limit)
		if err == nil {
			for i := range hits {
				h := &hits[i]
				// Phase 4A.2.4.1 — never feed a raw legacy content_masked to the
				// index/tool/LLM. canonical_safe → tokenized; legacy/redacted →
				// redacted placeholder. No-op when display lifecycle is off.
				safe := SearchSafeContent(s.displayLifecycle, h.DisplayState, h.Content)
				out = append(out, PastContextResult{
					Type:              "message",
					ConversationID:    h.ConversationID.String(),
					MessageID:         h.MessageID.String(),
					ConversationTitle: h.ConversationTitle,
					Role:              h.Role,
					Content:           truncateRunes(safe, contentTruncate),
					Date:              h.CreatedAt.UTC().Format("2006-01-02"),
					Relevance:         relevanceFromRank(h.Rank),
				})
			}
		}
	}

	// ── Conversation titles ───────────────────────────────
	if wantConvs {
		// Dedup: skip conv-title hits whose conversation already
		// surfaced via a message match — avoids the result list
		// repeating itself.
		seen := map[string]bool{}
		for _, r := range out {
			if r.ConversationID != "" {
				seen[r.ConversationID] = true
			}
		}
		limit := maxResults
		if scope == ScopeAll {
			limit = 3
		}
		hits, err := s.conversations.SearchTitlesForUser(ctx, tenantID, userID, cleaned, limit)
		if err == nil {
			for i := range hits {
				h := &hits[i]
				if seen[h.ID.String()] {
					continue
				}
				out = append(out, PastContextResult{
					Type:              "conversation",
					ConversationID:    h.ID.String(),
					ConversationTitle: h.Title,
					Content: fmt.Sprintf(`Conversation "%s" (%d messages)`,
						h.Title, h.MessageCount),
					Date:      h.UpdatedAt.UTC().Format("2006-01-02"),
					Relevance: relevanceFromRank(h.Rank),
				})
			}
		}
	}

	// ── Memories ──────────────────────────────────────────
	if wantMemories {
		limit := maxResults
		if scope == ScopeAll {
			limit = 3
		}
		mems, err := s.memories.SearchByContent(ctx, userID, tenantID, cleaned, limit)
		if err == nil {
			for i := range mems {
				m := &mems[i]
				out = append(out, PastContextResult{
					Type:       "memory",
					Content:    truncateRunes(m.Content, contentTruncate),
					MemoryType: m.MemoryType,
					Date:       m.CreatedAt.UTC().Format("2006-01-02"),
					Relevance:  "high", // confirmed memory = always high signal
				})
			}
		}
	}

	if len(out) > maxResults {
		out = out[:maxResults]
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

// SanitizeSearchQuery strips characters that would break
// plainto_tsquery (control chars + the tsquery operators), trims
// whitespace, and caps the length. Exported so the HTTP endpoint
// can also sanitise inputs without re-implementing the rule set.
//
// plainto_tsquery is more forgiving than to_tsquery (it ignores
// most operators), but ANDing in a literal '&' or '|' still
// produces weird parses on some Postgres versions; better to
// strip them upstream.
func SanitizeSearchQuery(q string) string {
	q = strings.TrimSpace(q)
	if q == "" {
		return ""
	}
	// Strip control + tsquery operator chars. We deliberately keep
	// CJK / Thai code points (the migration uses the 'simple'
	// dictionary which preserves them as-is).
	var b strings.Builder
	b.Grow(len(q))
	for _, r := range q {
		switch r {
		case '!', '@', '#', '$', '%', '^', '&', '*',
			'(', ')', '\'', '"', '\\', '/', ':', ';',
			'<', '>', '|', '`', '~', '?':
			continue
		}
		if r < 0x20 {
			continue
		}
		b.WriteRune(r)
	}
	out := strings.TrimSpace(b.String())
	if len(out) > 200 {
		out = out[:200]
	}
	return out
}

// relevanceFromRank buckets a raw ts_rank float into the two
// labels the tool result + UI consume. Threshold picked
// empirically — Postgres ts_rank with simple dictionary commonly
// lands user/assistant message hits in the 0.06-0.3 range.
func relevanceFromRank(rank float64) string {
	if rank > 0.06 {
		return "high"
	}
	return "medium"
}

// truncateRunes caps a content string at n runes + a … marker.
// UTF-8 safe by walking runes — keeps Thai / CJK characters
// intact (unlike the byte-cap truncate in gateway_client.go which
// would split a multi-byte rune mid-sequence).
func truncateRunes(s string, n int) string {
	if n <= 0 || s == "" {
		return s
	}
	runes := []rune(s)
	if len(runes) <= n {
		return s
	}
	return string(runes[:n]) + "…"
}

// Keep the time import referenced even when no other function in
// the file uses it directly (some hits go through repo-layer
// time.Time values that are formatted via .Format()).
var _ = time.Now
