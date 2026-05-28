// ContextCompiler — S4 token-budgeted context assembly.
//
// The old flow shipped every message MessageService.prepareSend
// loaded (capped at maxContext, default 50) and trusted the
// provider's hard limit not to overflow. That works until the
// system prefix grows (file context, memory, state) or the user
// chats long enough that 50 turns blow past 100K tokens — at which
// point the provider silently drops the head of the conversation.
//
// The compiler runs AFTER prepareSend and BEFORE
// buildGatewayRequestWithImages. It:
//
//   1. Asks the caller for the system prefix (via a closure so the
//      compiler doesn't have to know which builders are wired).
//   2. Measures every block's token cost via the heuristic
//      EstimateTokens / EstimateGatewayMessagesTokens.
//   3. Subtracts from the model's working context window to compute
//      MessagesRemaining (see CalculateBudget).
//   4. If the loaded history fits → returns it untouched.
//   5. If not → triggers a SYNCHRONOUS state-extraction pass when
//      the state row is missing or stale (so the soon-to-be-dropped
//      messages get folded into the <conversation_state> block
//      first), re-asks for the prefix, then trims the history slice
//      from oldest-first until it fits.
//
// Outputs the same shape the legacy path produced
// (string sysPrefix + []model.Message) so the rest of the send
// flow (buildGatewayRequestWithImages, pumpStreamWithTools) is
// unchanged.
package service

import (
	"context"
	"fmt"
	"log/slog"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// perToolTokens is the rough per-tool-definition cost (description +
// JSON-schema parameters). 200 covers the memory + state tools'
// shape; if a future tool's schema balloons we'll revisit.
const perToolTokens = 200

// ContextCompiler holds the dependencies the compile step needs.
// Decoupled from MessageService so unit tests can swap in fakes;
// MessageService wires both itself and the compiler in main.go.
type ContextCompiler struct {
	states *ConversationStateService
}

func NewContextCompiler(states *ConversationStateService) *ContextCompiler {
	return &ContextCompiler{states: states}
}

// CompileInputs bundles everything Compile needs.
//
// BuildPrefix is a closure so the compiler doesn't have to know the
// exact set of system-prompt builders MessageService has wired.
// The compiler may call it MORE THAN ONCE (initial + post-
// extraction rebuild), so it must be cheap and idempotent.
//
// History is the chronological-order slice prepareSend already
// loaded (newest at the tail). The compiler trims from the head
// (oldest) when compaction is needed.
type CompileInputs struct {
	Conv        *model.Conversation
	UserID      uuid.UUID
	TenantID    uuid.UUID
	History     []model.Message
	ImageURLs   []string
	// BuildPrefix is the per-call closure that re-renders the
	// system-prompt prefix AND returns the ContextSource list the
	// blocks contributed (memory entries, state goal). S7 changed
	// the signature from `func() string` so the compiler can
	// surface attribution without re-walking the source data
	// itself.
	BuildPrefix func() (string, []ContextSource)
	// NumTools is the count of GatewayTool entries the caller plans
	// to attach. Used purely for the budget's reserve estimate;
	// the compiler doesn't enumerate tools itself.
	NumTools int
}

// CompiledContext is what Compile returns. Mirrors the shape the
// existing buildGatewayRequestWithImages call site consumes, plus
// observability fields for the slog.Info line and the
// /context-info endpoint.
type CompiledContext struct {
	SystemPrefix  string
	History       []model.Message
	Budget        ContextBudget
	Compacted     bool
	MessagesTotal int
	MessagesSent  int
	// ExtractionTriggered is true iff Compile ran a synchronous
	// state-extraction call before trimming. Surfaces in the
	// observability log so we can see when the slow-path fired.
	ExtractionTriggered bool
	// Sources is the S7 attribution list — every context block
	// that contributed to the system prompt (memory entries, state
	// goal, compaction marker). The send paths flush these into
	// the per-turn SourcesAccumulator so tool execution can add
	// more (search hits), and finish() persists the union.
	Sources []ContextSource
}

// Compile is the single entry point for the message-service send
// paths. Safe to call with a nil compiler (caller checks); see
// MessageService.compileOrFallback for the integration shim.
func (c *ContextCompiler) Compile(
	ctx context.Context, in CompileInputs,
) (*CompiledContext, error) {
	if in.Conv == nil || in.BuildPrefix == nil {
		// Misuse — return an "uncompiled" result with zero budget so
		// the caller falls back to the legacy path.
		return &CompiledContext{
			History:       in.History,
			MessagesTotal: len(in.History),
			MessagesSent:  len(in.History),
		}, nil
	}

	prefix, sources := in.BuildPrefix()
	out := &CompiledContext{
		SystemPrefix:  prefix,
		History:       in.History,
		MessagesTotal: len(in.History),
		MessagesSent:  len(in.History),
		Sources:       sources,
	}

	budget := c.budgetFor(in, prefix)
	out.Budget = budget

	historyTokens := estimateHistoryTokens(in.History, in.ImageURLs)
	if historyTokens <= budget.MessagesRemaining {
		// Happy path — no compaction needed.
		c.logCompiled(in, out, historyTokens, "fit")
		return out, nil
	}

	// Overflow — try to refresh the conversation state synchronously
	// so the messages we're about to drop end up represented in
	// the <conversation_state> block. Skipped when the conversation
	// opted out of memory (temporary chats); the trim still runs.
	if c.states != nil && in.Conv.MemoryEnabled {
		if c.stateIsStaleForCompaction(ctx, in) {
			out.ExtractionTriggered = true
			if _, err := c.states.GenerateOrUpdateState(ctx, in.Conv.ID, in.UserID, in.TenantID); err != nil {
				slog.Warn("context_compiler: pre-compaction state extraction failed",
					"conv", in.Conv.ID.String(), "err", err)
			} else {
				// Re-build the prefix so the new state block is
				// reflected in the system message — AND the source
				// list (the state row might be brand-new).
				prefix, sources = in.BuildPrefix()
				out.SystemPrefix = prefix
				out.Sources = sources
				budget = c.budgetFor(in, prefix)
				out.Budget = budget
			}
		}
	}

	// Trim the history slice from the head (oldest first) until it
	// fits the messages-remaining budget. Always keeps at least the
	// last message — we'd rather overflow slightly than send a
	// system-only request.
	trimmed := trimHistoryToBudget(in.History, in.ImageURLs, budget.MessagesRemaining)
	out.History = trimmed
	out.MessagesSent = len(trimmed)
	out.Compacted = len(trimmed) < len(in.History)

	// S7 — record the compaction event as a source so the UI can
	// show "📦 Conversation compacted (N messages summarized)"
	// under the response. The user learns that older context was
	// folded into the state block instead of being sent raw.
	if out.Compacted {
		dropped := len(in.History) - len(trimmed)
		out.Sources = append(out.Sources, ContextSource{
			SourceType: model.SourceTypeCompactedHistory,
			DisplayLabel: fmt.Sprintf(
				"Conversation compacted (%d older %s summarized)",
				dropped, pluralCompact(dropped),
			),
		})
	}

	finalTokens := estimateHistoryTokens(trimmed, in.ImageURLs)
	c.logCompiled(in, out, finalTokens, "compacted")
	return out, nil
}

// pluralCompact — tiny pluraliser for the compaction marker copy.
func pluralCompact(n int) string {
	if n == 1 {
		return "message"
	}
	return "messages"
}

// budgetFor measures each system-prompt component INSIDE the
// prefix string. The prefix is one concatenated blob today so we
// can't pull out memory / state / file totals individually without
// re-walking the markers — but the high-level "system prompt
// total" is what budget math needs; per-block detail is
// observability sugar.
//
// We DO measure the prefix as a single number for budget purposes
// and let MemoryTokens / StateTokens / FileContextTokens stay zero
// in the returned budget. Future work could break the prefix into
// labelled sub-strings for a richer /context-info response; for
// now the consolidated SystemPromptTokens is enough to gate
// compaction.
func (c *ContextCompiler) budgetFor(in CompileInputs, prefix string) ContextBudget {
	sysTokens := EstimateTokens(prefix)
	// Add the per-message framing overhead for the system message
	// itself — buildGatewayRequestWithImages prepends one system
	// message before the history.
	sysTokens += 4
	toolReserve := in.NumTools * perToolTokens
	return CalculateBudget(in.Conv.ModelID, sysTokens, 0, 0, 0, toolReserve)
}

// stateIsStaleForCompaction checks whether running a sync
// extraction would actually help — if the state row is fresh
// enough we save the gateway call and just trim.
//
// Mirror the auto-trigger thresholds in message_service.go but
// fire EARLIER (state missing at 6 msgs, refresh at +15) because
// compaction is the load-bearing reason to summarise, and the
// trade-off skews toward "spend the extraction call".
func (c *ContextCompiler) stateIsStaleForCompaction(
	ctx context.Context, in CompileInputs,
) bool {
	st, err := c.states.Get(ctx, in.Conv.ID, in.UserID, in.TenantID)
	if err != nil {
		return false
	}
	if st == nil {
		// Need at least a few messages to extract anything useful.
		return len(in.History) >= 6
	}
	// Use the inbound history length as a cheap proxy for "messages
	// since summary". This is a slight overcount (history is the
	// pre-compaction slice, not the full conversation), so we err on
	// the side of refreshing.
	if len(in.History)-st.MessageCountAtSummary >= 15 {
		return true
	}
	return false
}

// logCompiled emits the structured observability line. Single log
// line per turn, easy to grep, all numbers on one row.
func (c *ContextCompiler) logCompiled(
	in CompileInputs, out *CompiledContext, finalHistoryTokens int, status string,
) {
	slog.Info("context_compiled",
		"conv", in.Conv.ID.String(),
		"model", in.Conv.ModelID,
		"status", status,
		"budget_total", out.Budget.TotalBudget,
		"system_tokens", out.Budget.SystemPromptTokens,
		"output_reserve", out.Budget.OutputReserve,
		"messages_budget", out.Budget.MessagesRemaining,
		"messages_total", out.MessagesTotal,
		"messages_sent", out.MessagesSent,
		"history_tokens", finalHistoryTokens,
		"compacted", out.Compacted,
		"extraction_triggered", out.ExtractionTriggered,
		"tool_reserve", out.Budget.ToolReserve,
	)
}

// ---------------------------------------------------------------------------
// History-slice helpers
// ---------------------------------------------------------------------------

// estimateHistoryTokens walks the chronological history slice and
// sums the per-message cost. We don't actually build the
// GatewayMessage value here — the cost model is the same shape so
// estimateMessageTokens-equivalent math is inlined.
//
// imageURLs only affect the LAST user message's cost (vision
// blocks ship on the latest turn only — see
// buildGatewayRequestWithImages's lastUserIdx logic).
func estimateHistoryTokens(history []model.Message, imageURLs []string) int {
	total := 0
	lastUserIdx := -1
	if len(imageURLs) > 0 {
		for i := len(history) - 1; i >= 0; i-- {
			if history[i].Role == "user" {
				lastUserIdx = i
				break
			}
		}
	}
	for i := range history {
		t := EstimateTokens(history[i].ContentMasked) + 4
		if i == lastUserIdx {
			t += 500 * len(imageURLs)
		}
		total += t
	}
	return total
}

// trimHistoryToBudget drops messages from the HEAD of the slice
// (oldest first) until the total token cost fits within budget.
// Always keeps at least the last message in the slice — sending
// a system-only request would dead-end the AI loop.
//
// imageURLs cost is applied to whichever user-role message is last
// in the returned slice (matches buildGatewayRequestWithImages).
func trimHistoryToBudget(history []model.Message, imageURLs []string, budget int) []model.Message {
	if len(history) == 0 || budget <= 0 {
		return history
	}
	if estimateHistoryTokens(history, imageURLs) <= budget {
		return history
	}
	// Walk newest → oldest accumulating cost; cut when budget would
	// be exceeded.
	used := 0
	startIdx := len(history)
	for i := len(history) - 1; i >= 0; i-- {
		msgTokens := EstimateTokens(history[i].ContentMasked) + 4
		// Image cost lands on the last user message of the SLICE we
		// end up returning; conservatively include it once when we
		// consider the first eligible last-user candidate.
		if len(imageURLs) > 0 && history[i].Role == "user" && i == len(history)-1 {
			msgTokens += 500 * len(imageURLs)
		}
		if used+msgTokens > budget {
			break
		}
		used += msgTokens
		startIdx = i
	}
	if startIdx >= len(history) {
		startIdx = len(history) - 1
	}
	return history[startIdx:]
}
