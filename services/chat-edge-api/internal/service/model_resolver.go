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

// isReasoningCapableID reports whether ``id`` is in the static
// reasoning-capable set the model handler maintains (T4 — same
// list as ``reasoningCapableModels`` in handler/model.go, plus
// the Gemini 2.5+ / 3 prefix rule). Kept as a free function in
// the resolver package so the auto-routing path can prefer
// reasoning models without dragging the handler layer in.
func isReasoningCapableID(id string) bool {
	switch id {
	case "o1", "o3", "o3-mini", "o3-2025-04-16",
		"o3-mini-2025-01-31", "o4-mini", "o3-pro",
		"claude-sonnet-4-20250514", "claude-opus-4-20250514",
		"claude-sonnet-4.5-20250514",
		"deepseek-reasoner", "deepseek-r1-distill-llama-70b":
		return true
	}
	if strings.HasPrefix(id, "gemini-2.5") || strings.HasPrefix(id, "gemini-3") {
		return true
	}
	return false
}

// complexSignals are phrases that imply multi-step reasoning, math,
// analysis, or code work — a hit pushes an Auto turn toward a
// reasoning-capable model. Mirrors the gateway-side ``_COMPLEX_SIGNALS``
// in backend/app/services/query_classifier.py; keep them roughly in
// sync when tuning. Lower-cased for case-insensitive Contains checks.
var complexSignals = []string{
	"analyze", "analyse", "compare and contrast", "evaluate",
	"pros and cons", "trade-off", "tradeoff", "step by step",
	"think through", "reason about", "prove", "derive",
	"debug", "review this code", "find the bug", "optimize",
	"what would happen if", "design a system", "architect",
	"calculate", "solve", "equation",
	// Thai
	"วิเคราะห์", "เปรียบเทียบ", "ข้อดีข้อเสีย", "อธิบายละเอียด",
	"คิดทีละขั้น", "หาจุดบกพร่อง", "ออกแบบระบบ", "คำนวณ",
}

// isComplexQuery is the Auto Thinking Mode gate: it decides whether
// THIS turn warrants a reasoning model. The user no longer flips a
// Thinking toggle — the system infers it from the prompt. Signals:
//
//   - attached images / files → likely analysis
//   - long message (>500 chars) or long conversation (>30 turns)
//   - code blocks or 3+ questions
//   - any complexSignals phrase (analyze / debug / calculate / Thai …)
//
// Deliberately a heuristic (not ML) so the slog decision line stays
// reconstructable. When true AND the catalog has a reasoning model,
// ResolveAutoModelWithReasoning routes there; otherwise it falls back
// to the fast/flagship tiers and the turn simply doesn't think.
func isComplexQuery(
	message string,
	hasFiles, hasImages bool,
	conversationMessageCount int,
) bool {
	if hasImages || hasFiles {
		return true
	}
	if len(message) > 500 || conversationMessageCount > 30 {
		return true
	}
	lower := strings.ToLower(message)
	if strings.Contains(lower, "```") {
		return true
	}
	if strings.Count(lower, "?")+strings.Count(message, "？") >= 3 {
		return true
	}
	for _, s := range complexSignals {
		if strings.Contains(lower, s) {
			return true
		}
	}
	return false
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
	return ResolveAutoModelWithReasoning(
		message, hasFiles, hasImages, conversationMessageCount,
		availableIDs, false,
	)
}

// ResolveAutoModelWithReasoning extends ResolveAutoModel with the
// T4 reasoning preference: when ``reasoningEnabled`` is true and
// the catalog has at least one reasoning-capable id, that id wins
// over the heuristic tier choice. Falls through to the standard
// fast/flagship rules when no reasoning model exists (so a
// catalog without R1 / o3 / Claude 4 / Gemini 2.5 still works).
//
// Kept as a separate entry-point so the legacy ResolveAutoModel
// signature stays a one-arg-list call site for tests + callers
// that don't have a conversation handy.
func ResolveAutoModelWithReasoning(
	message string,
	hasFiles, hasImages bool,
	conversationMessageCount int,
	availableIDs []string,
	reasoningEnabled bool,
) string {
	if len(availableIDs) == 0 {
		return "gpt-4o-mini"
	}

	// T4: prefer a reasoning model when the user has reasoning ON.
	// Iterate availableIDs in catalog order so the operator's
	// preferred reasoning model (the first one listed) wins
	// deterministically.
	if reasoningEnabled {
		for _, id := range availableIDs {
			if isReasoningCapableID(id) {
				return id
			}
		}
		// No reasoning model in the catalog — fall through to the
		// regular heuristic. The Gateway silently ignores the
		// reasoning param on a non-capable model so the request
		// still succeeds, just without thinking output.
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
