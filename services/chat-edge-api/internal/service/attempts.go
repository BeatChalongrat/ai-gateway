package service

// FIX121 — the per-model attempt log of a failed turn is a fact about the
// row, not only about the live frame.
//
// The gateway ends a turn whose fallback walk was exhausted with an error
// frame carrying ``attempts``: one entry per model it tried. chat-edge
// forwarded that to the client and kept nothing, so "Connection attempts
// (3)" was on screen until the first reload and the desktop never had it.
// Now that the streaming path walks the Auto router's alternatives, one
// failed turn routinely names several models, and the question "which ones,
// and why did each fail" is asked the next day from a run id.
//
// What is stored is a bounded copy, never the frame verbatim: the list is an
// open JSON value that arrives from another service (and, on the import
// path, from a desktop client), so its size and its keys are fixed here.

import (
	"context"
	"encoding/json"
	"strings"

	"github.com/google/uuid"
)

const (
	// maxStoredAttempts bounds one row's list. The gateway's streaming
	// ceiling is a handful of candidates; twelve leaves room for a policy
	// override without letting a client park an unbounded array on a row.
	maxStoredAttempts = 12
	// maxAttemptText bounds each free-text value of an entry.
	maxAttemptText = 300
)

// attemptKeys is the closed set of keys kept from an attempt entry. Strings
// are trimmed and cut to maxAttemptText; latency_ms keeps a number.
var attemptStringKeys = []string{"provider", "model", "error_code", "error_message", "status", "attempted_at"}

// NormalizeAttempts turns a frame's (or an import item's) attempts value
// into the JSON stored on the row. Returns nil when there is nothing worth
// storing: not a list, an empty list, or no entry that names a model or a
// provider.
func NormalizeAttempts(v any) json.RawMessage {
	list, ok := v.([]any)
	if !ok || len(list) == 0 {
		return nil
	}
	out := make([]map[string]any, 0, len(list))
	for _, raw := range list {
		if len(out) >= maxStoredAttempts {
			break
		}
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		kept := map[string]any{}
		for _, k := range attemptStringKeys {
			s, ok := entry[k].(string)
			if !ok {
				continue
			}
			s = strings.TrimSpace(s)
			if s == "" {
				continue
			}
			if r := []rune(s); len(r) > maxAttemptText {
				s = string(r[:maxAttemptText])
			}
			kept[k] = s
		}
		if _, hasModel := kept["model"]; !hasModel {
			if _, hasProvider := kept["provider"]; !hasProvider {
				continue
			}
		}
		if n, ok := entry["latency_ms"].(float64); ok && n >= 0 {
			kept["latency_ms"] = n
		}
		out = append(out, kept)
	}
	if len(out) == 0 {
		return nil
	}
	b, err := json.Marshal(out)
	if err != nil {
		return nil
	}
	return b
}

// SetAttempts records a failed turn's attempt log on its assistant row.
// Best-effort like SetServedModel and SetFallbackFrom: nothing to store and
// a nil id write nothing.
func (s *MessageService) SetAttempts(ctx context.Context, msgID uuid.UUID, attempts any) error {
	if msgID == uuid.Nil {
		return nil
	}
	raw := NormalizeAttempts(attempts)
	if raw == nil {
		return nil
	}
	return s.messages.SetAttempts(ctx, msgID, raw)
}
