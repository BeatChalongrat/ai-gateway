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
	"unicode"
	"unicode/utf8"
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
	if utf8.RuneCountInString(message) > 500 || conversationMessageCount > 30 {
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

// isTrivialQuery reports whether a turn is light enough that a reasoning model
// should NOT spend a thinking budget on it (P1-4). Trivial = clearly not a
// complex turn, short, and not a question. Used to suppress thinking on a
// PINNED reasoning-capable model for greetings like "ไง" / "hi" so they don't
// burn a full budget. Rune-based (not bytes) so Thai length is counted
// correctly — "ไง" is 2 runes, not 6 bytes.
func isTrivialQuery(message string) bool {
	if isComplexQuery(message, false, false, 0) {
		return false
	}
	trimmed := strings.TrimSpace(message)
	if utf8.RuneCountInString(trimmed) > 30 {
		return false
	}
	// A question deserves a real answer — let the model think.
	if strings.Contains(trimmed, "?") || strings.Contains(trimmed, "？") {
		return false
	}
	return true
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

// defaultModelPreference is the ordered set of models Auto prefers as a
// conversation's non-reasoning workhorse (Fix 4), best-first. The first
// id present in the catalog wins. Claude 3.5 Sonnet leads: 190K context
// + strong instruction-following make it a better default than the
// gpt-4o-mini fast tier the resolver used before. Claude Sonnet 4 and
// Claude 3.5 Haiku follow for catalogs that carry them instead.
//
// Reasoning turns are NOT affected — they resolve to a reasoning-capable
// model earlier in ResolveAutoModelWithReasoning. This preference only
// changes the non-reasoning default/flagship target.
var defaultModelPreference = []string{
	"claude-sonnet-4-20250514",
	"claude-3-5-sonnet-20241022",
	"claude-3-5-haiku-20241022",
}

// preferredDefaultModel returns the first defaultModelPreference entry
// present in availableIDs, or "" when none are in the catalog (so the
// caller keeps the legacy fast/flagship behaviour).
func preferredDefaultModel(availableIDs []string) string {
	for _, want := range defaultModelPreference {
		for _, id := range availableIDs {
			if id == want {
				return want
			}
		}
	}
	return ""
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

	// Fix 4 — prefer Claude Sonnet as the non-reasoning default. When a
	// preferred Claude model is in the catalog it becomes BOTH the
	// simple-turn default and the flagship target (190K context + strong
	// follow-up handling). If the Anthropic key is invalid the gateway
	// falls back to a working provider at dispatch time, so this is safe
	// even without a live Claude key. No-op when no preferred model is
	// present (keeps the gpt fast/flagship tiers).
	if preferred := preferredDefaultModel(availableIDs); preferred != "" {
		fast = preferred
		flagship = preferred
	}

	if hasImages {
		return flagship
	}
	if hasFiles {
		return flagship
	}
	if utf8.RuneCountInString(message) > 500 {
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

// codeMarkers are substrings that strongly imply the turn involves
// code. Shared by hasCodeMarkers (hint building) and the flagship
// upgrade rule above — kept here so both stay in sync.
var codeMarkers = []string{
	"```", "func ", "def ", "class ", "import ", "select ", "create table",
	"function ", "const ", "var ", "public ", "private ", "#include", "</",
}

// hasCodeMarkers reports whether the message looks like it contains or
// asks about code. Pure string match (no parsing) so an operator can
// reconstruct the has_code hint from the prompt.
func hasCodeMarkers(message string) bool {
	lower := strings.ToLower(message)
	for _, marker := range codeMarkers {
		if strings.Contains(lower, marker) {
			return true
		}
	}
	return false
}

// detectLanguage returns a coarse language tag for routing hints based
// on the dominant script in the message. Heuristic, not a full language
// detector — enough for the gateway to prefer a translation/locale-
// strong model. Returns "th" when any Thai script is present, "zh" for
// CJK, "ja" for Hiragana/Katakana, "ko" for Hangul, else "en".
func detectLanguage(message string) string {
	for _, r := range message {
		switch {
		case r >= 0x0E00 && r <= 0x0E7F:
			return "th"
		case unicode.Is(unicode.Hiragana, r) || unicode.Is(unicode.Katakana, r):
			return "ja"
		case unicode.Is(unicode.Hangul, r):
			return "ko"
		case unicode.Is(unicode.Han, r):
			return "zh"
		}
	}
	return "en"
}

// BuildRoutingHints derives the provider-agnostic capability hints
// chat-edge sends to the gateway for an "Auto" turn. It reuses the same
// per-turn signals the legacy ResolveAutoModelWithReasoning heuristic
// used (images / files / length / turn count / code markers / complex-
// signal phrases) — but instead of collapsing them into one concrete
// model id (which bypassed gateway routing), it forwards the raw needs
// so the gateway can pick the best key-valid model.
//
// reasoningEnabled is the conversation's user-facing reasoning toggle;
// it OR's with the inferred complexity so an explicit "think harder"
// request always sets needs_reasoning even on a short prompt.
//
// numTools is how many tool definitions this turn will attach. It is a
// hint the gateway treats as a hard capability filter: a turn carrying
// tools must not be routed to a model that can't call them.
func BuildRoutingHints(
	message string,
	hasFiles, hasImages bool,
	conversationMessageCount int,
	reasoningEnabled bool,
	numTools int,
) RoutingHints {
	hasCode := hasCodeMarkers(message)
	complex := isComplexQuery(message, hasFiles, hasImages, conversationMessageCount)

	complexity := "medium"
	switch {
	case complex || hasCode || hasImages || hasFiles:
		complexity = "high"
	case utf8.RuneCountInString(message) <= 80 && conversationMessageCount <= 4 && !strings.Contains(message, "?"):
		complexity = "low"
	}

	return RoutingHints{
		NeedsVision:    hasImages,
		NeedsReasoning: reasoningEnabled || complex,
		HasTools:       numTools > 0,
		ToolCount:      numTools,
		HasCode:        hasCode,
		HasFiles:       hasFiles,
		Complexity:     complexity,
		ConvTurnCount:  conversationMessageCount,
		Language:       detectLanguage(message),
	}
}
