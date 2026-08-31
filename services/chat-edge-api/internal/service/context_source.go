// ContextSource — the runtime carrier the service layer uses to
// track which sources contributed to an assistant response.
// Mirrors the wire shape of model.ResponseContextSource but lives
// in-memory only; the message flow drops these into a
// SourcesAccumulator during compile + tool execution and the
// finish callback flushes the snapshot to chat.response_context
// _sources in one go.
//
// Two reasons to keep this separate from the model type:
//  1. The model has a fixed uuid.UUID SourceID — runtime sources
//     from compaction don't have one, so the accumulator type
//     uses a *string that the persist step converts on the way out.
//  2. The runtime path doesn't carry tenant / user / response_id
//     until persist time — the accumulator stays tidy by only
//     holding the descriptive fields.
package service

import (
	"encoding/json"
	"sync"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// ContextSource is the in-memory descriptor of one source.
// SourceID is a string so the search tool can emit a
// conversation-id without round-tripping through uuid.UUID parsing.
// Metadata is already-marshalled JSON so the persist step can pass
// it straight to the JSONB column.
type ContextSource struct {
	SourceType   string
	SourceID     string
	DisplayLabel string
	Metadata     json.RawMessage
}

// SourcesAccumulator is the per-turn shared buffer the compile
// step + every tool execution writes into. Drained in the
// finish() callback after the assistant message is persisted, so
// chat.response_context_sources rows always carry the freshly-
// minted message ID.
//
// Designed for the message-flow's concurrency pattern:
//   - the compile happens on the request goroutine before the
//     stream opens
//   - each tool execution runs sequentially inside the pump
//     goroutine
//   - finish() runs on whichever goroutine drained the stream
//
// Mutex covers the rare-but-real case where a sync state
// extraction (compile) overlaps with a separately-scheduled tool
// execution; the cost is negligible.
type SourcesAccumulator struct {
	mu      sync.Mutex
	sources []ContextSource
}

// NewSourcesAccumulator returns a fresh empty accumulator. Always
// non-nil — callers that need a no-op accumulator (legacy paths
// where the source repo isn't wired) can still use one.
func NewSourcesAccumulator() *SourcesAccumulator {
	return &SourcesAccumulator{}
}

// Add appends one or more sources. Variadic so tool handlers can
// flush multiple search hits in one call.
func (a *SourcesAccumulator) Add(srcs ...ContextSource) {
	if a == nil || len(srcs) == 0 {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.sources = append(a.sources, srcs...)
}

// Snapshot returns a copy of the current source list for
// persistence. Cheap O(n) copy — n is bounded by realistic source
// counts (3-20 per response).
func (a *SourcesAccumulator) Snapshot() []ContextSource {
	if a == nil {
		return nil
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	out := make([]ContextSource, len(a.sources))
	copy(out, a.sources)
	return out
}

// ContextSourceLabelMaxRunes caps each display_label length. Keeps
// the wire payload tight even on very long memory entries / past-
// conversation snippets.
const ContextSourceLabelMaxRunes = 120

// TruncateLabel applies the per-label cap with a … marker, UTF-8
// safe via rune-walk so Thai / CJK glyphs stay intact.
func TruncateLabel(s string) string {
	if s == "" {
		return ""
	}
	runes := []rune(s)
	if len(runes) <= ContextSourceLabelMaxRunes {
		return s
	}
	return string(runes[:ContextSourceLabelMaxRunes]) + "…"
}

// MarshalMeta is the small helper service code uses to build the
// JSONB metadata blob without sprinkling json.Marshal everywhere.
// Returns nil on any error so the source still persists, just
// without metadata.
func MarshalMeta(v map[string]any) json.RawMessage {
	if len(v) == 0 {
		return nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		return nil
	}
	return b
}

// parseUUIDOrNil tolerates malformed input — returns uuid.Nil so
// the persist step at least has a zero value rather than crashing
// on a typo. Callers that need strict parsing handle it themselves.
func parseUUIDOrNil(s string) uuid.UUID {
	if s == "" {
		return uuid.Nil
	}
	u, err := uuid.Parse(s)
	if err != nil {
		return uuid.Nil
	}
	return u
}

// parseUUIDPtr is the pointer-returning variant for nullable
// columns (model.ResponseContextSource.SourceID). Empty / invalid
// → nil so the JSONB row records NULL rather than uuid.Nil.
func parseUUIDPtr(s string) *uuid.UUID {
	if s == "" {
		return nil
	}
	u, err := uuid.Parse(s)
	if err != nil {
		return nil
	}
	return &u
}

// toModelSources converts the accumulator's runtime snapshot to
// the model-layer shape for batch persistence. Caller supplies
// the identity + message scope.
//
// Lives here (not the repo) so the model package stays free of
// service-layer types.
func toModelSources(
	snapshot []ContextSource,
	messageID, userID, tenantID, convID string,
) []model.ResponseContextSource {
	if len(snapshot) == 0 {
		return nil
	}
	mid := parseUUIDOrNil(messageID)
	uid := parseUUIDOrNil(userID)
	tid := parseUUIDOrNil(tenantID)
	cid := parseUUIDOrNil(convID)
	out := make([]model.ResponseContextSource, 0, len(snapshot))
	for i := range snapshot {
		s := &snapshot[i]
		row := model.ResponseContextSource{
			ResponseMessageID: mid,
			TenantID:          tid,
			UserID:            uid,
			ConversationID:    cid,
			SourceType:        s.SourceType,
			DisplayLabel:      s.DisplayLabel,
			Metadata:          s.Metadata,
		}
		if sid := parseUUIDPtr(s.SourceID); sid != nil {
			row.SourceID = sid
		}
		out = append(out, row)
	}
	return out
}
