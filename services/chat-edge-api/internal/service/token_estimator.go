// Approximate token counter for S4 context budget management.
//
// We deliberately avoid tiktoken / a provider-specific tokeniser:
//   - tiktoken's Go bindings drag in a sizeable model file + cgo
//   - the budget math is a guardrail, not billing — a 5–10 %
//     overestimate is fine and actually safer than a precise
//     count (precise tokenisers vary per model anyway)
//
// The heuristic:
//   - ASCII / Latin: ~4 chars per token (matches OpenAI's GPT-2/4
//     tokenisation closely enough)
//   - Thai / CJK   : ~1.5 chars per token (each rune is usually
//     1-2 tokens in BPE schemes)
//   - +4 token overhead per message for role / separator framing
//
// The estimator is intentionally conservative: when in doubt, count
// MORE tokens so the budget logic triggers compaction sooner. The
// alternative (underestimating) would let conversations overflow
// the provider's hard limit silently.
package service

import "strings"

// EstimateTokens returns an approximate token count for `text`.
// Returns 0 on empty input — the per-message overhead is added by
// EstimateGatewayMessagesTokens at the call site, not here.
func EstimateTokens(text string) int {
	if text == "" {
		return 0
	}
	asciiRunes := 0
	cjkRunes := 0
	for _, r := range text {
		if isCJKOrThai(r) {
			cjkRunes++
		} else {
			asciiRunes++
		}
	}
	// ASCII: floor(n/4). CJK/Thai: ceil(n / 1.5) ≈ ceil(2n/3).
	tokens := asciiRunes/4 + (cjkRunes*2+2)/3
	if tokens < 1 {
		tokens = 1
	}
	return tokens
}

// EstimateGatewayMessagesTokens sums the per-message token cost
// including framing overhead. Handles both plain-string content
// and the multimodal content-block array used by vision turns:
// each image_url block bills as a flat ~500 tokens (rough OpenAI
// figure for "auto" detail), each text block uses the same
// estimator as plain strings.
func EstimateGatewayMessagesTokens(messages []GatewayMessage) int {
	total := 0
	for i := range messages {
		total += estimateMessageTokens(&messages[i])
	}
	return total
}

// estimateMessageTokens is the per-message helper used by
// EstimateGatewayMessagesTokens AND by ContextCompiler.
// selectRecentMessages so the two stay in lockstep when we tune
// the framing overhead.
func estimateMessageTokens(m *GatewayMessage) int {
	contentTokens := 0
	switch c := m.Content.(type) {
	case string:
		contentTokens = EstimateTokens(c)
	case []GatewayContentBlock:
		for _, b := range c {
			switch b.Type {
			case "image_url":
				contentTokens += 500
			default:
				contentTokens += EstimateTokens(b.Text)
			}
		}
	default:
		// Unknown shape — assume small overhead, don't crash.
		contentTokens = 1
	}
	// Tool-call envelopes add per-call overhead; the function name
	// + arguments string contribute most of the cost. Cheap rough
	// estimate so we don't undercount on tool-heavy turns.
	for _, tc := range m.ToolCalls {
		contentTokens += EstimateTokens(tc.Function.Name) + EstimateTokens(tc.Function.Arguments) + 8
	}
	if m.Name != "" {
		contentTokens += EstimateTokens(m.Name)
	}
	// +4 per-message framing (role, content separator, tool_call_id
	// if any) — matches OpenAI's documented overhead.
	return contentTokens + 4
}

// isCJKOrThai reports whether `r` lives in a script where BPE
// tokenisation typically yields ~1.5 chars/token instead of the
// ~4 chars/token Latin pattern. Covers the main scripts SRAN Chat
// users speak today (Thai + CJK + Hangul).
func isCJKOrThai(r rune) bool {
	switch {
	case r >= 0x0E00 && r <= 0x0E7F: // Thai
		return true
	case r >= 0x4E00 && r <= 0x9FFF: // CJK Unified Ideographs
		return true
	case r >= 0x3040 && r <= 0x30FF: // Hiragana + Katakana
		return true
	case r >= 0xAC00 && r <= 0xD7AF: // Hangul Syllables
		return true
	case r >= 0xFF00 && r <= 0xFFEF: // Halfwidth/Fullwidth forms
		return true
	}
	return false
}

// containsCJKOrThai is exported for the rare caller (tests today)
// that wants to know whether a string is dominantly Asian-script
// without re-walking it. Reads as runes so multibyte safe.
func containsCJKOrThai(s string) bool {
	for _, r := range s {
		if isCJKOrThai(r) {
			return true
		}
	}
	return false
}

// trimToTokens returns the longest prefix of `s` whose estimated
// token count is at most `maxTokens`. Used by future block-level
// truncation logic (memory / state injection); currently exposed
// so callers can keep the heuristic in one place.
func trimToTokens(s string, maxTokens int) string {
	if maxTokens <= 0 || s == "" {
		return ""
	}
	if EstimateTokens(s) <= maxTokens {
		return s
	}
	// Rough proportional cut. Since the heuristic is monotonic and
	// conservative, scaling the byte length by tokens / EstimateTokens
	// gives a string slightly UNDER the budget; cheaper than binary
	// search and good enough for guardrail use.
	cur := EstimateTokens(s)
	targetBytes := len(s) * maxTokens / cur
	if targetBytes <= 0 {
		return ""
	}
	if targetBytes > len(s) {
		targetBytes = len(s)
	}
	// Avoid slicing mid-rune by walking back to the previous rune
	// boundary.
	cut := strings.LastIndex(s[:targetBytes], " ")
	if cut < 0 {
		cut = targetBytes
	}
	for cut > 0 && cut < len(s) && (s[cut]&0xC0) == 0x80 {
		cut--
	}
	return s[:cut]
}
