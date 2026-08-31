// ContextCompiler — S4 token-budgeted context assembly, FIX33 shape.
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
//  1. Asks the caller for the system prefix (via a closure so the
//     compiler doesn't have to know which builders are wired).
//  2. Measures every block's token cost via the heuristic
//     EstimateTokens / EstimateGatewayMessagesTokens.
//  3. Subtracts from the model's working context window to compute
//     MessagesRemaining (see CalculateBudget).
//  4. FIX33 A-W2: if the loaded history is below TriggerRatio ×
//     MessagesRemaining (default 0.85), nothing was cut by the load
//     ceiling (A-W1) and no manual compact is pending (A-W7) →
//     returns it untouched.
//  5. Otherwise triggers a SYNCHRONOUS state-extraction pass that
//     covers everything up to the last message that will be dropped
//     (batched, oldest-first — so rows the load ceiling cut are
//     folded too), re-asks for the prefix, then trims the MIDDLE of
//     the history down to TargetRatio × budget: the first exchange
//     survives (A-W4, ported from the desktop compiler) and the
//     newest tail survives.
//  6. Appends a <context_note> to the system prefix telling the
//     model what is missing and to admit a lost detail rather than
//     guess (A-W5), and emits the compaction source marker so the
//     user sees it too. Nothing is dropped silently.
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

// Defaults for the FIX33 ratios (config: CONTEXT_COMPACT_TRIGGER_RATIO /
// CONTEXT_COMPACT_TARGET_RATIO). Trigger at 85 % of the messages budget,
// trim down to 60 % — the gap is hysteresis so a long conversation
// compacts in chunks instead of paying an extraction call every turn.
const (
	DefaultCompactTriggerRatio = 0.85
	DefaultCompactTargetRatio  = 0.60
)

// contextNoteReserve is the token allowance for the <context_note>
// that joins the system prefix when compaction happens.
const contextNoteReserve = 120

// ContextCompiler holds the dependencies the compile step needs.
// Decoupled from MessageService so unit tests can swap in fakes;
// MessageService wires both itself and the compiler in main.go.
type ContextCompiler struct {
	states       *ConversationStateService
	triggerRatio float64
	targetRatio  float64
}

func NewContextCompiler(states *ConversationStateService) *ContextCompiler {
	return &ContextCompiler{
		states:       states,
		triggerRatio: DefaultCompactTriggerRatio,
		targetRatio:  DefaultCompactTargetRatio,
	}
}

// SetRatios overrides the trigger / target ratios. Values outside
// (0, 1] fall back to the defaults; target is clamped to ≤ trigger.
func (c *ContextCompiler) SetRatios(trigger, target float64) {
	if trigger <= 0 || trigger > 1 {
		trigger = DefaultCompactTriggerRatio
	}
	if target <= 0 || target > 1 {
		target = DefaultCompactTargetRatio
	}
	if target > trigger {
		target = trigger
	}
	c.triggerRatio = trigger
	c.targetRatio = target
}

// TriggerRatio / TargetRatio expose the live values (context-info).
func (c *ContextCompiler) TriggerRatio() float64 { return c.triggerRatio }
func (c *ContextCompiler) TargetRatio() float64  { return c.targetRatio }

// CompileInputs bundles everything Compile needs.
//
// BuildPrefix is a closure so the compiler doesn't have to know the
// exact set of system-prompt builders MessageService has wired.
// The compiler may call it MORE THAN ONCE (initial + post-
// extraction rebuild), so it must be cheap and idempotent.
//
// History is the chronological-order slice prepareSend already
// loaded (newest at the tail). The compiler trims the MIDDLE when
// compaction is needed (the first exchange and the newest tail
// survive).
type CompileInputs struct {
	Conv      *model.Conversation
	UserID    uuid.UUID
	TenantID  uuid.UUID
	History   []model.Message
	ImageURLs []string
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
	// OmittedOlder (FIX33 A-W1) is how many active messages exist
	// BEFORE History[0] that the load ceiling did not fetch. > 0
	// forces the compaction path (marker + extraction coverage) even
	// when the loaded slice fits — the head is already gone from the
	// request and the user must see that.
	OmittedOlder int
	// DryRun (context-info) — measure and plan, but never run the
	// extraction call. The returned numbers describe what the next
	// real turn WOULD do.
	DryRun bool
	// OnExtractionStart (FIX33 A-W9) fires ONCE, just before the
	// synchronous extraction call — the streaming handler uses it to
	// open the SSE response and tell the user the pause is the
	// conversation being organised, not a hang. Nil = no-op.
	OnExtractionStart func()
	// BuildState (FIX33.1 Part 2) renders the <conversation_state> block
	// + its ContextSource (the prompt compiler's former layer 12). Called
	// AFTER extraction; when it returns a block and compaction happened,
	// the block is injected as a user/assistant pair at the head of the
	// sent history (StateInjectionMessages) — never into the system
	// message. Nil = no state injection.
	BuildState func() (string, []ContextSource)
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

	// FIX33 observability / context-meter fields.
	HistoryTokens   int  // tokens of the LOADED history (before trim)
	SentTokens      int  // tokens of the history actually sent
	ThresholdTokens int  // TriggerRatio × MessagesRemaining
	OmittedOlder    int  // rows the load ceiling cut (never loaded)
	Dropped         int  // loaded messages the trim removed
	ManualCompact   bool // a manual boundary drove (part of) the drop
	StatePresent    bool // a <conversation_state> block was available
	HeadKept        int  // messages kept from the head (0–2)
	// StateInjected (FIX33.1) — History[0..1] are the synthetic
	// user/assistant pair carrying the state block.
	StateInjected bool
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
		OmittedOlder:  in.OmittedOlder,
	}

	budget := c.budgetFor(in, prefix)
	out.Budget = budget

	historyTokens := estimateHistoryTokens(in.History, in.ImageURLs)
	out.HistoryTokens = historyTokens
	out.SentTokens = historyTokens
	out.ThresholdTokens = ratioOf(budget.MessagesRemaining, c.triggerRatio)

	// A-W7 — a pending manual compact boundary on the state row.
	var boundary *uuid.UUID
	var existing *model.ConversationState
	if c.states != nil && in.Conv.MemoryEnabled {
		if st, err := c.states.Get(ctx, in.Conv.ID, in.UserID, in.TenantID); err == nil && st != nil {
			existing = st
			boundary = st.ManualCompactBoundaryMessageID
			out.StatePresent = !isStateEmpty(st)
		}
	}

	needCompact := in.OmittedOlder > 0 || historyTokens >= out.ThresholdTokens || boundary != nil
	if !needCompact {
		// Happy path — no compaction needed.
		c.logCompiled(in, out, historyTokens, "fit")
		return out, nil
	}

	// The state pair (FIX33.1) is budgeted like the system message: its
	// tokens come off the history target so the send never overflows.
	stateBlock, stateSources := "", []ContextSource(nil)
	if in.BuildState != nil {
		stateBlock, stateSources = in.BuildState()
	}
	statePairTokens := func() int {
		if stateBlock == "" {
			return 0
		}
		return estimateHistoryTokens(StateInjectionMessages(stateBlock), nil)
	}

	// Plan the trim FIRST (pure) so extraction knows how far it must
	// cover: everything up to the last message that will be dropped.
	target := budget.MessagesRemaining
	if historyTokens >= out.ThresholdTokens {
		target = ratioOf(budget.MessagesRemaining, c.targetRatio)
	}
	plan := planHeadPreservingTrim(in.History, in.ImageURLs, target-contextNoteReserve-statePairTokens(), boundary)

	// Threshold / omitted / boundary — refresh the conversation
	// state synchronously so the messages we're about to drop end
	// up represented in the <conversation_state> block. Skipped
	// when the conversation opted out of memory (temporary chats)
	// or on a dry run; the trim still runs.
	if c.states != nil && in.Conv.MemoryEnabled && !in.DryRun {
		var upto *uuid.UUID
		if plan.dropEnd > 0 {
			id := in.History[plan.dropEnd-1].ID
			upto = &id
		} else if in.OmittedOlder > 0 && len(in.History) > 0 {
			// Nothing in the loaded slice is dropped, but rows before
			// it were never loaded: cover up to the first loaded
			// message so the omitted head is summarised.
			id := in.History[0].ID
			upto = &id
		}
		if upto != nil && c.stateIsStaleForCompaction(existing, in, *upto) {
			out.ExtractionTriggered = true
			if in.OnExtractionStart != nil {
				in.OnExtractionStart()
			}
			st, reached, err := c.states.EnsureStateCoversUpTo(ctx, in.Conv.ID, in.UserID, in.TenantID, upto, ExtractionOptions{Trigger: "auto"})
			if err != nil {
				slog.Warn("context_compiler: pre-compaction state extraction failed",
					"conv", in.Conv.ID.String(), "err", err)
			} else {
				if !reached {
					slog.Warn("context_compiler: state coverage incomplete before trim",
						"conv", in.Conv.ID.String())
				}
				out.StatePresent = !isStateEmpty(st)
				// Re-render the state (the row might be brand-new) and
				// re-plan: the pair's tokens come off the history target.
				if in.BuildState != nil {
					stateBlock, stateSources = in.BuildState()
				}
				plan = planHeadPreservingTrim(in.History, in.ImageURLs, target-contextNoteReserve-statePairTokens(), boundary)
			}
		}
	}

	out.History = plan.kept
	out.MessagesSent = len(plan.kept)
	out.Dropped = len(in.History) - len(plan.kept)
	out.HeadKept = plan.headKept
	out.ManualCompact = plan.boundaryApplied
	out.Compacted = out.Dropped > 0 || in.OmittedOlder > 0
	out.SentTokens = estimateHistoryTokens(plan.kept, in.ImageURLs)

	if !out.Compacted {
		// Reached the threshold but the plan kept everything (e.g. a
		// two-message conversation with one enormous paste): the
		// state was refreshed early, which is the point of the
		// threshold; nothing to mark.
		c.logCompiled(in, out, out.SentTokens, "threshold-nodrop")
		return out, nil
	}

	// FIX33.1 Part 2 — the state rides as a user/assistant pair at the
	// HEAD of the sent history (before the preserved first exchange),
	// exactly as the desktop does. Never in the system message.
	if stateBlock != "" {
		out.History = append(StateInjectionMessages(stateBlock), out.History...)
		out.MessagesSent = len(out.History)
		out.SentTokens = estimateHistoryTokens(out.History, in.ImageURLs)
		out.StatePresent = true
		out.StateInjected = true
		out.Sources = append(out.Sources, stateSources...)
	}

	// A-W5 — tell the model what is missing and to admit a lost
	// detail rather than guess. The <conversation_state> block (when
	// present) carries the honesty sentence too; this note covers the
	// memory-off case and names the counts.
	missing := out.Dropped + in.OmittedOlder
	out.SystemPrefix += compactionContextNote(missing, plan.tailKept, plan.headKept, out.StatePresent)

	// S7 — record the compaction event as a source so the UI can
	// show "📦 Conversation compacted (N messages summarized)"
	// under the response. The user learns that older context was
	// folded into the state block instead of being sent raw.
	out.Sources = append(out.Sources, ContextSource{
		SourceType:   model.SourceTypeCompactedHistory,
		DisplayLabel: compactionMarkerLabel(missing, out.StatePresent),
	})

	c.logCompiled(in, out, out.SentTokens, "compacted")
	return out, nil
}

// compactionMarkerLabel — the user-facing source row copy.
func compactionMarkerLabel(missing int, statePresent bool) string {
	if statePresent {
		return fmt.Sprintf("Conversation compacted (%d older %s summarized)", missing, pluralCompact(missing))
	}
	return fmt.Sprintf("Conversation compacted (%d older %s omitted — memory is off, so not summarized)", missing, pluralCompact(missing))
}

// compactionContextNote is the model-facing note (the desktop's
// context_note, now on web too).
func compactionContextNote(missing, tailKept, headKept int, statePresent bool) string {
	s := fmt.Sprintf("\n\n<context_note>\nThis conversation is long: %d earlier %s %s not included verbatim.",
		missing, pluralCompact(missing), isAre(missing))
	if headKept > 0 {
		s += fmt.Sprintf(" The first exchange and the most recent %d %s are included verbatim.", tailKept, pluralCompact(tailKept))
	} else {
		s += fmt.Sprintf(" The most recent %d %s are included verbatim.", tailKept, pluralCompact(tailKept))
	}
	if statePresent {
		s += " A summary of the older part is in <conversation_state> above."
	} else {
		s += " No summary of the older part is available."
	}
	s += " If the user refers to something that is in neither the summary nor the visible messages, say you may have lost that detail and ask them to restate it — do not guess.\n</context_note>"
	return s
}

func isAre(n int) string {
	if n == 1 {
		return "is"
	}
	return "are"
}

// pluralCompact — tiny pluraliser for the compaction marker copy.
func pluralCompact(n int) string {
	if n == 1 {
		return "message"
	}
	return "messages"
}

func ratioOf(n int, ratio float64) int {
	if ratio <= 0 {
		return n
	}
	return int(float64(n) * ratio)
}

// budgetFor measures each system-prompt component INSIDE the
// prefix string. The prefix is one concatenated blob today so we
// can't pull out memory / state / file totals individually without
// re-walking the markers — but the high-level "system prompt
// total" is what budget math needs; per-block detail is
// observability sugar.
func (c *ContextCompiler) budgetFor(in CompileInputs, prefix string) ContextBudget {
	sysTokens := EstimateTokens(prefix)
	// Add the per-message framing overhead for the system message
	// itself — buildGatewayRequestWithImages prepends one system
	// message before the history.
	sysTokens += 4
	toolReserve := in.NumTools * perToolTokens
	return CalculateBudget(in.Conv.ModelID, sysTokens, 0, 0, 0, toolReserve)
}

// stateIsStaleForCompaction — would an extraction pass add anything?
// It would when there is no state, when the anchor has not reached
// the message we are about to drop, or when a manual compact is
// pending (its instructions may have changed what to keep).
func (c *ContextCompiler) stateIsStaleForCompaction(
	existing *model.ConversationState, in CompileInputs, upto uuid.UUID,
) bool {
	if existing == nil {
		return len(in.History) >= 2 || in.OmittedOlder > 0
	}
	if existing.SummarizedUpToMessageID == nil {
		return true
	}
	if *existing.SummarizedUpToMessageID == upto {
		return false
	}
	// The anchor is somewhere else: stale unless it is AFTER upto in
	// the loaded slice (then everything we drop is already covered).
	anchorIdx, uptoIdx := -1, -1
	for i := range in.History {
		if in.History[i].ID == *existing.SummarizedUpToMessageID {
			anchorIdx = i
		}
		if in.History[i].ID == upto {
			uptoIdx = i
		}
	}
	if anchorIdx >= 0 && uptoIdx >= 0 && anchorIdx >= uptoIdx {
		return false
	}
	return true
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
		"threshold_tokens", out.ThresholdTokens,
		"messages_total", out.MessagesTotal,
		"messages_sent", out.MessagesSent,
		"omitted_older", out.OmittedOlder,
		"dropped", out.Dropped,
		"head_kept", out.HeadKept,
		"history_tokens", finalHistoryTokens,
		"compacted", out.Compacted,
		"state_injected", out.StateInjected,
		"manual", out.ManualCompact,
		"extraction_triggered", out.ExtractionTriggered,
		"tool_reserve", out.Budget.ToolReserve,
		"dry_run", in.DryRun,
	)
}

// ---------------------------------------------------------------------------
// History-slice helpers (pure — unit-tested without a DB)
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

// trimPlan is planHeadPreservingTrim's answer.
type trimPlan struct {
	kept            []model.Message
	headKept        int // messages kept from the head (0, 1 or 2)
	tailKept        int // messages kept from the tail
	dropEnd         int // History index one past the last DROPPED message (0 = nothing dropped)
	boundaryApplied bool
}

// planHeadPreservingTrim keeps the first exchange (the first user
// message and, when it directly follows, the assistant reply) and
// the newest tail that fits `budget`, dropping the MIDDLE. Ported
// from the desktop compiler (FIX32 divergence #1): the user's turn-1
// standing instruction is the last thing to die, not the first.
//
// boundary (A-W7): when set and present in the slice, every message
// at or before it is dropped regardless of budget (head excepted).
//
// The newest message always ships even if it alone overflows — a
// system-only request dead-ends the loop (the old rule, kept).
func planHeadPreservingTrim(history []model.Message, imageURLs []string, budget int, boundary *uuid.UUID) trimPlan {
	n := len(history)
	if n == 0 {
		return trimPlan{kept: history}
	}

	// Head: the first user message and the reply that answered it.
	headCount := 0
	if history[0].Role == "user" {
		headCount = 1
		if n > 1 && history[1].Role == "assistant" {
			headCount = 2
		}
	}

	// The tail may not start before the boundary (A-W7) or inside
	// the head.
	tailFloor := headCount
	boundaryApplied := false
	if boundary != nil {
		for i := range history {
			if history[i].ID == *boundary {
				if i+1 > tailFloor {
					tailFloor = i + 1
				}
				boundaryApplied = true
				break
			}
		}
	}
	if tailFloor > n-1 {
		// The boundary is the newest message: keep it anyway (the
		// last message always ships) — but everything before, bar
		// the head, goes.
		tailFloor = n - 1
	}

	cost := func(i int) int {
		t := EstimateTokens(history[i].ContentMasked) + 4
		if len(imageURLs) > 0 && history[i].Role == "user" && i == n-1 {
			t += 500 * len(imageURLs)
		}
		return t
	}

	headTokens := 0
	for i := 0; i < headCount; i++ {
		headTokens += cost(i)
	}

	// Fast path — everything fits and no boundary: untouched.
	if boundary == nil || !boundaryApplied {
		if estimateHistoryTokens(history, imageURLs) <= budget {
			return trimPlan{kept: history, headKept: headCount, tailKept: n - headCount}
		}
	}

	// Tail: newest → oldest until the budget (minus the head) is
	// spent, never past tailFloor. The newest message always ships.
	used := headTokens
	tailStart := n // first kept tail index
	for i := n - 1; i >= tailFloor; i-- {
		t := cost(i)
		if used+t > budget && i < n-1 {
			break
		}
		used += t
		tailStart = i
	}
	if tailStart >= n {
		tailStart = n - 1
	}
	if tailStart < headCount {
		tailStart = headCount
	}

	if tailStart == headCount {
		// Nothing dropped at all.
		return trimPlan{kept: history, headKept: headCount, tailKept: n - headCount}
	}

	kept := make([]model.Message, 0, headCount+(n-tailStart))
	kept = append(kept, history[:headCount]...)
	kept = append(kept, history[tailStart:]...)
	return trimPlan{
		kept:            kept,
		headKept:        headCount,
		tailKept:        n - tailStart,
		dropEnd:         tailStart,
		boundaryApplied: boundaryApplied,
	}
}

// trimHistoryToBudget is the legacy oldest-first trim, kept for the
// tests and callers that want the pre-FIX33 semantics. New code uses
// planHeadPreservingTrim.
func trimHistoryToBudget(history []model.Message, imageURLs []string, budget int) []model.Message {
	if len(history) == 0 || budget <= 0 {
		return history
	}
	if estimateHistoryTokens(history, imageURLs) <= budget {
		return history
	}
	used := 0
	startIdx := len(history)
	for i := len(history) - 1; i >= 0; i-- {
		msgTokens := EstimateTokens(history[i].ContentMasked) + 4
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
