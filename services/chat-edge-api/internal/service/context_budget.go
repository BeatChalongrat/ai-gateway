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

import (
	"context"
	"strings"
)

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

// ── Two different numbers, and the difference is the whole bug ────────
//
// An ADVERTISED window is what the provider says the model holds
// (Typhoon: 16,384). A WORKING limit is what we budget against, which is
// deliberately smaller so prompt-tuning churn and the estimator's
// overshoot stay inside the real cap. Everything in ModelContextLimits
// below is a WORKING limit — somebody already subtracted the headroom
// by hand. Everything the catalogue carries is ADVERTISED.
//
// FIX101: mixing the two is how this went wrong. Use workingLimitFrom()
// on anything that arrives from data; never on a value out of the map.
//
// ModelContextLimits is now a SEED, not the authority. The authority is
// the catalogue (model_configs.max_context_tokens, operator-editable in
// the admin console, refreshed on chat-edge's five-minute TTL). The map
// answers only for ids the catalogue does not carry — a gateway that is
// down, or a model discovered after this binary was built.
//
// Add a new model here: pick the documented cap, knock 5–10 % off,
// round to a clean number, drop it in. Better: put it in the catalogue.
var ModelContextLimits = map[string]int{
	// OpenAI
	"gpt-4o":        120000,
	"gpt-4o-mini":   120000,
	"gpt-4-turbo":   120000,
	"gpt-4":         7500,
	"gpt-3.5-turbo": 15000,

	// Anthropic — Claude 3.5 / 4 family share the 200K hard cap.
	"claude-sonnet-4":   190000,
	"claude-opus-4":     190000,
	"claude-3-5-sonnet": 190000,
	"claude-3-5-haiku":  190000,
	"claude-3-opus":     190000,
	"claude-3-sonnet":   190000,
	"claude-3-haiku":    190000,

	// Google — Gemini 1.5/2.0 advertise a 1M window. We budget against
	// 500K: half the hard cap leaves generous headroom for the
	// conservative estimator's overshoot while still letting long
	// documents ride along (far above the OpenAI/Anthropic tiers).
	"gemini-1.5-pro":   500000,
	"gemini-1.5-flash": 500000,
	"gemini-2.0-flash": 500000,

	// FIX101 Part B — the self-hosted box, MEASURED rather than assumed.
	// Every number below is workingLimitFrom(the window the provider
	// stated in its own refusal), recorded so that a gateway-down window
	// still budgets these correctly instead of falling to the default.
	// The catalogue carries the same measurements and still wins; see
	// (FIX101 Part B measured each against the running deployment on 2026-09-10.)
	"Typhoon-S-ThaiLLM-8B-Instruct":         14500,  // 16,384
	"OpenThaiGPT-ThaiLLM-8B-Instruct-v7.2":  14500,  // 16,384
	"Pathumma-ThaiLLM-qwen3-8b-think-3.0.0": 14500,  // 16,384
	"THaLLE-0.2-ThaiLLM-8B-fa":              14500,  // 16,384
	"qwen3.5-9b":                            29000,  // 32,768
	"qwen3.6-35b-a3b":                       29000,  // 32,768
	"qwen3.8-27b-fp8":                       117500, // 131,072
}

// headroomFraction is the 5–10 % convention the map documents, applied
// as an actual step to an actual number instead of being typed in once
// per model and then trusted forever. 10 %: the conservative end,
// because the estimator that has to fit inside it runs high, and because
// the cost of being 10 % under is some lost history while the cost of
// being over is a refused turn.
const headroomFraction = 0.90

// headroomStep rounds the result DOWN to a clean number — the second
// half of the map's own instruction, "round to a clean number".
const headroomStep = 500

// smallestObservedWindow is 16,384: the smallest context window measured
// on a model actually serving this deployment. Four of the seven
// self-hosted models refuse there — Typhoon-S-ThaiLLM-8B-Instruct (the
// model that broke the owner's conversation on 2026-09-10),
// OpenThaiGPT-ThaiLLM-8B-Instruct-v7.2,
// Pathumma-ThaiLLM-qwen3-8b-think-3.0.0 and THaLLE-0.2-ThaiLLM-8B-fa —
// each verified by the provider's own refusal sentence. It is also
// gpt-3.5-turbo's cap.
const smallestObservedWindow = 16384

// workingLimitFrom turns an ADVERTISED window into the working limit we
// budget against. This is the only place the headroom convention lives.
func workingLimitFrom(advertised int) int {
	if advertised <= 0 {
		// Not "unknown model" — "caller passed nothing". The unknown-model
		// answer is defaultContextLimit, chosen by GetModelContextLimitWith;
		// resolving it here would make the two definitions circular.
		advertised = smallestObservedWindow
	}
	w := int(float64(advertised) * headroomFraction)
	if w >= headroomStep {
		w -= w % headroomStep
	}
	return w
}

// defaultContextLimit is the fallback for a model neither the catalogue
// nor the seed map knows.
//
// It used to be 120,000, justified in a comment that read: "Mirrors a
// modern flagship's working size; if the real model is smaller, the
// estimator's overshoot keeps the request from blowing past the hard
// cap." That sentence was false, and it is worth saying why rather than
// deleting it quietly, because otherwise the next person writes it
// again. The estimator runs a few per cent high. Nothing about a few per
// cent saves a request when the assumed window is seven times the real
// one — which is exactly what happened: Typhoon inherited 120,000
// against a real 16,384, so the app reported the conversation 11 % full
// while the provider refused it at 16,413 tokens. A comment that claims
// a safety property the code does not have is worse than no comment.
//
// The replacement is conservative on purpose. An unknown model that is
// genuinely larger loses some history until somebody catalogues it,
// which is one admin edit and is recoverable. An unknown model that is
// genuinely smaller refuses the turn, which is not.
var defaultContextLimit = workingLimitFrom(smallestObservedWindow)

// ContextWindowLookup answers "what does the CATALOGUE say this model's
// advertised window is", in tokens; 0 means it does not know. Backed by
// handler.ModelHandler's cached gateway catalogue, which is
// model_configs.max_context_tokens — operator-editable in the admin
// console, refreshed on the five-minute TTL, invalidatable. Optional:
// nil falls through to the seed map, the pre-FIX101 behaviour.
type ContextWindowLookup func(ctx context.Context, modelID string) int

// GetModelContextLimit resolves the WORKING limit from the seed map
// alone. Kept for callers with no catalogue to hand;
// GetModelContextLimitWith is the one production uses.
func GetModelContextLimit(modelID string) int {
	return GetModelContextLimitWith(context.Background(), nil, modelID)
}

// GetModelContextLimitWith resolves the WORKING limit in priority order:
//
//  1. the catalogue's advertised window, with headroom applied
//  2. an exact seed-map entry (already a working limit)
//  3. the longest matching seed-map prefix, so
//     "claude-sonnet-4-20250514" inherits "claude-sonnet-4"
//  4. defaultContextLimit
//
// The catalogue wins outright rather than being combined with the map,
// because an operator who edits the number in the admin console has to
// see it take effect — a "whichever is smaller" rule would silently
// ignore them raising a limit the map understates.
func GetModelContextLimitWith(ctx context.Context, lookup ContextWindowLookup, modelID string) int {
	if lookup != nil {
		if advertised := lookup(ctx, modelID); advertised > 0 {
			return workingLimitFrom(advertised)
		}
	}
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
	return CalculateBudgetWith(
		context.Background(), nil, modelID,
		systemPromptTokens, memoryTokens, stateTokens, fileTokens, toolReserve,
	)
}

// CalculateBudgetWith is CalculateBudget with the catalogue in hand.
func CalculateBudgetWith(
	ctx context.Context,
	lookup ContextWindowLookup,
	modelID string,
	systemPromptTokens, memoryTokens, stateTokens, fileTokens, toolReserve int,
) ContextBudget {
	total := GetModelContextLimitWith(ctx, lookup, modelID)
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
