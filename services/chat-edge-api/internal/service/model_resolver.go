// Auto-model resolver.
//
// The web client persists "auto" as the model id when the user
// flips the model picker to its Auto row. The backend special-
// cases that sentinel: it stays "auto" in the conversation row so
// the user's preference persists across turns, but at request
// time it resolves to a concrete model id chosen by the heuristic
// below. Resolution is per-request so the same conversation can
// land on a fast model for short turns and a flagship model for
// long / image / file / code turns.
//
// Heuristic, NOT ML — good enough for MVP. The decision rules are
// deliberately simple so an operator reading the slog line can
// always reconstruct WHY a turn was upgraded.
package service

import (
	"strings"
)

// AutoModelID is the sentinel the frontend persists when the user
// flips the model picker to Auto. The handler / config layer
// accepts this verbatim; ResolveAutoModel turns it into a concrete
// id at gateway-call time.
const AutoModelID = "auto"

// IsAutoModel reports whether the supplied id is the Auto sentinel
// (or empty, which we treat as auto for safety so an unconfigured
// migration row doesn't crash the send path).
func IsAutoModel(id string) bool {
	s := strings.TrimSpace(id)
	return s == "" || s == AutoModelID
}

// modelTier classifies a model id into "fast" or "flagship".
// Keyword-based so a new entry the admin adds (e.g. gpt-5-mini)
// lands in the right bucket without a config change. Everything
// without a fast marker is treated as flagship — safer to over-
// classify as flagship than miss a complex turn.
func modelTier(id string) string {
	lower := strings.ToLower(id)
	for _, marker := range []string{"mini", "haiku", "flash", "nano", "small", "8b"} {
		if strings.Contains(lower, marker) {
			return "fast"
		}
	}
	return "flagship"
}

// ResolveAutoModel picks the concrete model id Auto should serve
// this turn. Decision factors (in order; first match wins):
//
//  1. Images attached    → flagship (vision)
//  2. Files attached     → flagship (likely analysis)
//  3. Long message (>500 chars) → flagship
//  4. Long conversation (>30 turns) → flagship (complex context)
//  5. Code indicators in the message → flagship (better at code)
//  6. Default → fast
//
// Falls back to the first available model when neither tier exists
// in the catalog, and to "gpt-4o-mini" as the absolute last resort
// so a misconfigured catalog never wedges the send path.
func ResolveAutoModel(
	message string,
	hasFiles, hasImages bool,
	conversationMessageCount int,
	availableIDs []string,
) string {
	if len(availableIDs) == 0 {
		return "gpt-4o-mini"
	}

	var fast, flagship string
	for _, id := range availableIDs {
		switch modelTier(id) {
		case "fast":
			if fast == "" {
				fast = id
			}
		case "flagship":
			if flagship == "" {
				flagship = id
			}
		}
	}
	// Cover the catalogues that only carry one tier — collapse both
	// names onto whatever exists so the decision rules below always
	// have a defined target.
	if flagship == "" {
		flagship = fast
	}
	if fast == "" {
		fast = flagship
	}
	if flagship == "" {
		return availableIDs[0]
	}

	if hasImages {
		return flagship
	}
	if hasFiles {
		return flagship
	}
	if len(message) > 500 {
		return flagship
	}
	if conversationMessageCount > 30 {
		return flagship
	}

	lower := strings.ToLower(message)
	for _, marker := range []string{"```", "func ", "def ", "class ", "import ", "select ", "create table"} {
		if strings.Contains(lower, marker) {
			return flagship
		}
	}

	return fast
}
