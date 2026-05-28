// Context budget allocator for S4 — Memory System.
//
// Given a model id + the measured size of every system-prompt
// block we want to ship, returns how many tokens are left for raw
// conversation messages. Used by ContextCompiler.Compile to decide
// whether the loaded history fits or has to be trimmed.
//
// Numbers are intentionally conservative:
//   - the model "context window" we record is below the provider's
//     advertised hard cap so prompt-tuning headroom + a slightly
//     pessimistic estimator can't push us over
//   - the output reserve scales with the model so a 16K-context
//     legacy model still leaves room for an answer, while a 200K
//     model doesn't waste 50K of useable budget on reserve
package service

import "strings"

// ContextBudget is the per-request allocation snapshot. The
// ContextCompiler returns this verbatim in CompiledContext so
// observability log lines + the /context-info endpoint can render
// the full breakdown without re-deriving it.
type ContextBudget struct {
	// TotalBudget is the conservative context-window cap for the
	// model (NOT the provider's advertised limit — we leave
	// ~5–10 % headroom inside the limits map).
	TotalBudget int

	// Sizes of each block that ships in the request's system
	// message. The compiler measures these with EstimateTokens
	// (heuristic) before doing budget math.
	SystemPromptTokens int // base persona + custom instructions
	MemoryTokens       int // <saved_user_memory>
	StateTokens        int // <conversation_state>
	FileContextTokens  int // RAG + file + excel + doc + pptx blocks
	ToolReserve        int // tool definitions cost ~ a few hundred each

	// OutputReserve is the working budget held back for the
	// assistant reply + any tool-call envelopes the model wants
	// to emit during this turn.
	OutputReserve int

	// MessagesRemaining is the leftover budget for raw conversation
	// history. ContextCompiler.selectRecentMessages walks the
	// history from newest to oldest until this many tokens are
	// consumed. Floor of 2000 so even a heavily-instrumented turn
	// can carry a few messages.
	MessagesRemaining int
}

// ModelContextLimits records the working context-window size we
// budget against for each provider/model. These are deliberately
// BELOW the provider's documented hard cap so:
//   - prompt-tuning churn doesn't accidentally overflow
//   - the conservative estimator's overshoot stays inside budget
//
// Add a new model: pick the documented cap, knock 5–10 % off,
// round to a clean number, drop it in.
var ModelContextLimits = map[string]int{
	// OpenAI
	"gpt-4o":         120000,
	"gpt-4o-mini":    120000,
	"gpt-4-turbo":    120000,
	"gpt-4":          7500,
	"gpt-3.5-turbo":  15000,

	// Anthropic — Claude 3.5 / 4 family share the 200K hard cap.
	"claude-sonnet-4":   190000,
	"claude-opus-4":     190000,
	"claude-3-5-sonnet": 190000,
	"claude-3-5-haiku":  190000,
	"claude-3-opus":     190000,
	"claude-3-sonnet":   190000,
	"claude-3-haiku":    190000,

	// Google — Gemini 1.5 Pro is 1M but bill model rate-limits + our
	// extractor cost balance favours a saner cap.
	"gemini-1.5-pro":   190000,
	"gemini-1.5-flash": 190000,
	"gemini-2.0-flash": 190000,
}

// defaultContextLimit is the fallback for models we haven't pinned
// in the map. Mirrors a modern flagship's working size; if the
// real model is smaller, the estimator's overshoot keeps the
// request from blowing past the hard cap.
const defaultContextLimit = 120000

// GetModelContextLimit looks up the working limit, falling back to
// prefix match (so "claude-sonnet-4-20250514" inherits the
// "claude-sonnet-4" entry) and finally to defaultContextLimit.
func GetModelContextLimit(modelID string) int {
	if v, ok := ModelContextLimits[modelID]; ok {
		return v
	}
	// Prefix match — longer prefixes win so "claude-3-5-sonnet"
	// beats "claude-3" when both would match.
	bestLen, best := 0, defaultContextLimit
	for prefix, limit := range ModelContextLimits {
		if strings.HasPrefix(modelID, prefix) && len(prefix) > bestLen {
			bestLen = len(prefix)
			best = limit
		}
	}
	return best
}

// outputReserveFor picks how much budget to hold back for the
// model's reply. 25 % of total, capped at 16K (we don't need 50K
// of reserve on a 200K Claude). Floor of 2K so a tiny-context
// model still leaves room for a meaningful reply.
func outputReserveFor(totalBudget int) int {
	reserve := totalBudget / 4
	if reserve > 16000 {
		reserve = 16000
	}
	if reserve < 2000 {
		reserve = 2000
	}
	return reserve
}

// CalculateBudget assembles a ContextBudget for one request.
// Caller measures each system-prompt block with EstimateTokens
// first; this function does the subtraction + floor logic.
//
// MessagesRemaining is floor-guarded at 2000 so a model with a
// huge system prompt still has SOME budget for messages — better
// to overflow slightly than to send zero history. The compiler's
// fall-back path on overflow is "trust the provider's error", not
// "silently send nothing".
func CalculateBudget(
	modelID string,
	systemPromptTokens, memoryTokens, stateTokens, fileTokens, toolReserve int,
) ContextBudget {
	total := GetModelContextLimit(modelID)
	output := outputReserveFor(total)

	b := ContextBudget{
		TotalBudget:        total,
		SystemPromptTokens: systemPromptTokens,
		MemoryTokens:       memoryTokens,
		StateTokens:        stateTokens,
		FileContextTokens:  fileTokens,
		ToolReserve:        toolReserve,
		OutputReserve:      output,
	}
	used := systemPromptTokens + memoryTokens + stateTokens + fileTokens + toolReserve + output
	remaining := total - used
	if remaining < 2000 {
		remaining = 2000
	}
	b.MessagesRemaining = remaining
	return b
}
