// ConversationStateService — S3 Structured Conversation State.
//
// Solves the "AI forgets after 80 messages" problem: as a
// conversation grows, this service periodically extracts a
// structured summary (goal, decisions, artifacts, tasks, rejected
// approaches, unresolved questions) via an LLM call and persists
// it to chat.conversation_states. The message flow then injects
// the rendered state into the system prompt so the model has
// durable context independent of the rolling message window.
//
// Two entry points feed the state:
//
//	GenerateOrUpdateState — full incremental extraction. Triggered
//	  async after every Nth message (see maybeUpdateState in
//	  message_service.go). Calls the gateway with a low-temperature
//	  JSON-only prompt and merges the result with the existing
//	  state.
//
//	ApplyToolUpdate — surgical incremental update. Driven by the
//	  update_conversation_state AI tool so the model can record a
//	  decision the instant it lands, without waiting for the next
//	  extraction window.
//
// BuildStateContext is the render path: it shapes the persisted
// state into the <conversation_state> system-prompt block the
// MessageService.buildSystemPrefix concatenates onto every turn.
package service

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrStateNotFound = errors.New("conversation state: not found")
)

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

// The narrow interfaces the service actually needs (FIX33). The
// concrete pgx repos satisfy them; tests substitute in-memory fakes
// so the extraction flow (batching, failure → keep state, no tools)
// is provable without a database.
type stateStore interface {
	Get(ctx context.Context, convID, userID, tenantID uuid.UUID) (*model.ConversationState, error)
	Upsert(ctx context.Context, s *model.ConversationState) error
}

type stateMessageSource interface {
	ListAfter(ctx context.Context, tenantID, convID, afterMessageID uuid.UUID, limit int) ([]model.Message, error)
	CountMessages(ctx context.Context, tenantID, convID uuid.UUID) (int, error)
	// CountAfterUpTo (FIX117) — how many messages a compaction has to fold:
	// after the anchor, up to and including uptoID.
	CountAfterUpTo(ctx context.Context, tenantID, convID, afterMessageID uuid.UUID, uptoID *uuid.UUID) (int, error)
}

type stateConvSource interface {
	GetByID(ctx context.Context, tenantID, convID uuid.UUID) (*model.Conversation, error)
}

type extractionCaller interface {
	SendChatCompletion(ctx context.Context, callCtx GatewayCallContext, req GatewayChatRequest) (*GatewayChatResponse, error)
}

// CompactionUsage is one extraction call's token bill (FIX33 A-W8 —
// Claude's `usage.iterations[]`): the cost of compacting, kept apart
// from the cost of answering so users and admins can see it.
type CompactionUsage struct {
	ConversationID uuid.UUID
	UserID         uuid.UUID
	TenantID       uuid.UUID
	ModelID        string
	// Trigger: CompactionTriggerAuto | CompactionTriggerManual |
	// CompactionTriggerBackground. FIX113: rows written between FIX111
	// (d1a4223, the desktop started compacting through POST /compact) and
	// FIX113 carry "manual" for the desktop's AUTOMATIC compactions too —
	// the endpoint had no way to say otherwise. Those rows are history, not
	// corrected; see docs/DATA_RETENTION_PLAN.md (chat.compaction_usage).
	Trigger        string
	PromptTokens   int
	OutputTokens   int
	CostUSD        float64
	MessagesFolded int
}

// The trigger vocabulary of a compaction_usage row.
const (
	CompactionTriggerAuto       = "auto"       // the compiler, or a client compacting on its own trigger
	CompactionTriggerManual     = "manual"     // a person pressed "summarise and continue"
	CompactionTriggerBackground = "background" // the async threshold extraction
)

// CompactionTriggerFromClient maps what a client sent on POST /compact to
// the ledger vocabulary. FIX113: only "auto" and "manual" may come from a
// client; anything else — unknown, empty, or "background", which no client
// causes — is "manual", the value the endpoint always recorded before.
func CompactionTriggerFromClient(raw string) string {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case CompactionTriggerAuto:
		return CompactionTriggerAuto
	default:
		return CompactionTriggerManual
	}
}

// CompactionUsageRecorder persists CompactionUsage rows. Optional;
// nil = log only.
type CompactionUsageRecorder interface {
	RecordCompactionUsage(ctx context.Context, u CompactionUsage) error
}

// ConversationStateService owns the lifecycle of
// chat.conversation_states. The fields are private so test wiring
// uses the typed constructor.
type ConversationStateService struct {
	states   stateStore
	messages stateMessageSource
	conv     stateConvSource
	gateway  extractionCaller
	// Model used for extraction. When empty, GenerateOrUpdateState
	// falls back to the conversation's own model_id (so the bill
	// stays inside the conversation's allocated budget). Future
	// work could pin this to a cheap "summarisation" model.
	extractionModel string
	usage           CompactionUsageRecorder
}

func NewConversationStateService(
	states *repository.ConversationStateRepo,
	messages *repository.MessageRepo,
	conv *repository.ConversationRepo,
	gateway *GatewayClient,
) *ConversationStateService {
	return &ConversationStateService{
		states:   states,
		messages: messages,
		conv:     conv,
		gateway:  gateway,
	}
}

// newConversationStateServiceWith is the test seam: any
// implementation of the four narrow interfaces.
func newConversationStateServiceWith(
	states stateStore, messages stateMessageSource, conv stateConvSource, gateway extractionCaller,
) *ConversationStateService {
	return &ConversationStateService{states: states, messages: messages, conv: conv, gateway: gateway}
}

// SetCompactionUsageRecorder wires the A-W8 usage sink.
func (s *ConversationStateService) SetCompactionUsageRecorder(r CompactionUsageRecorder) {
	s.usage = r
}

// ExtractionOptions tunes one extraction pass (FIX33).
// ExtractionProgress (FIX117) — what EnsureStateCoversUpTo has folded so
// far, in raw counts: the client works out a percentage (or shows the
// counts). Total is the number of messages the run has to fold, known
// before the first batch from CountAfterUpTo; TotalKnown is false when
// that count failed — then Total is meaningless and the client must not
// invent one. Done is set exactly once, on the last report; Reached says
// whether coverage got to the requested message. Folded never decreases
// and never equals Total before Done.
type ExtractionProgress struct {
	Folded     int
	Total      int
	TotalKnown bool
	Done       bool
	Reached    bool
}

type ExtractionOptions struct {
	// BatchLimit caps how many new messages one gateway call folds.
	// 0 = extractionBatchLimit.
	BatchLimit int
	// OnProgress (FIX117) is told after every batch, best-effort: a sink
	// that panics never changes what gets folded. Nil = no reports.
	OnProgress func(ExtractionProgress)
	// Instructions is the user's compaction guidance (A-W7). Empty =
	// use the instructions already stored on the state row, if any.
	Instructions string
	// Trigger labels the usage row: "auto" | "manual" | "background".
	Trigger string
	// ChatModel (FIX102 Part D) is the model the conversation is actually
	// answered by — for "auto", the one that answered its last turn. The
	// summary falls back to it when no summariser is configured or the
	// summariser produced nothing. Empty = the conversation's model_id.
	ChatModel string
}

// extractionBatchLimit — new messages per extraction call. Each
// message is truncated to 500 bytes in the prompt, so 120 messages ≈
// 60K bytes ≈ 15–20K tokens: comfortably inside every model window
// we budget against, and small enough that a 1000-message backlog
// is a handful of calls, not one 250K-token request.
const extractionBatchLimit = 120

// extractionMaxBatches bounds EnsureStateCoversUpTo — 8 × 120 = 960
// messages per turn is the most the pre-trim refresh will fold before
// it gives up and trims anyway (with the marker still shown).
const extractionMaxBatches = 8

// SetExtractionModel sets the summariser (FIX102 Part D,
// CONTEXT_SUMMARY_MODEL): the model tried FIRST for every summary,
// whichever model the conversation chose. Empty = no summariser, the chat
// model writes it, as before.
//
// FIX101 measured why this exists: asked about two turns that had been
// folded away, an 8B model got one right and one wrong with both facts in
// the summary — an 8B misreading an 8B's summary. The model that answers
// in Thai does not have to be the model that summarises.
//
// It is not a way round the tenant's entitlements: the call goes through
// the gateway with the conversation's own tenant and user, exactly like the
// chat turn — the same credit balance, rate limit, DLP and project policy —
// and chat-edge never chooses a provider or a key. A summariser the tenant
// cannot use fails there, and the chat model is tried next.
func (s *ConversationStateService) SetExtractionModel(modelID string) {
	s.extractionModel = strings.TrimSpace(modelID)
}

// extractionModels is the order a summary is attempted in: the summariser,
// then the chat model (opts.ChatModel, else the conversation's model_id).
func (s *ConversationStateService) extractionModels(chatModel, convModel string) []string {
	chat := strings.TrimSpace(chatModel)
	if chat == "" {
		chat = strings.TrimSpace(convModel)
	}
	out := make([]string, 0, 2)
	for _, id := range []string{s.extractionModel, chat} {
		if id != "" && (len(out) == 0 || out[0] != id) {
			out = append(out, id)
		}
	}
	return out
}

// ---------------------------------------------------------------------------
// GenerateOrUpdateState — extract structured state from messages
// ---------------------------------------------------------------------------

// extractionTimeout caps the gateway call that the extractor makes
// — runs in a background goroutine, so a hung provider can't pile
// up.
const extractionTimeout = 90 * time.Second

// extractionMaxTokens bounds the JSON envelope size. ~1500 tokens
// is enough for a 50-message conversation with rich state.
const extractionMaxTokens = 1500

// extractionTemperature — low so the JSON envelope stays
// deterministic. 0.1 has been good enough for OpenAI / Anthropic
// to consistently emit parseable JSON.
const extractionTemperature = 0.1

// GenerateOrUpdateState is the main extraction entry point. Reads
// the existing state (if any), fetches the messages since the last
// summary (or all messages on first run), shells out to the gateway
// for structured JSON, merges, and upserts.
//
// Safe to call from a background goroutine: every step uses its
// own bounded ctx, and a failure at any stage is logged but never
// panics. Returns the post-write state when extraction succeeded,
// the existing state when nothing new was available, or an error
// only on terminal infrastructure failures.
func (s *ConversationStateService) GenerateOrUpdateState(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (*model.ConversationState, error) {
	st, _, err := s.GenerateOrUpdateStateOpts(ctx, convID, userID, tenantID, ExtractionOptions{Trigger: "background"})
	return st, err
}

// GenerateOrUpdateStateOpts is GenerateOrUpdateState with FIX33's
// knobs. Returns (state, messagesFolded, err). messagesFolded == 0
// with a nil error means "nothing new to summarise" (state returned
// as-is); a failed gateway call ALSO returns the existing state
// untouched — a compaction pass never wipes what it already knew.
//
// Coverage is INCREMENTAL and starts from the OLDEST message: the
// first pass anchors at uuid.Nil (ListAfter treats an unknown anchor
// as "epoch"), so a 1000-message backlog is folded oldest-first in
// BatchLimit slices — A-W1's contract that the rows the load ceiling
// cut are summarised before anything is trimmed.
func (s *ConversationStateService) GenerateOrUpdateStateOpts(
	ctx context.Context, convID, userID, tenantID uuid.UUID, opts ExtractionOptions,
) (*model.ConversationState, int, error) {
	p, err := s.extractOnce(ctx, convID, userID, tenantID, opts)
	if err != nil {
		return nil, 0, err
	}
	return p.state, len(p.folded), nil
}

// extractPass is one extraction call's outcome (FIX102 Part C). A failed
// call and "nothing new to fold" both leave the state untouched and fold
// nothing, so GenerateOrUpdateStateOpts cannot tell them apart; the
// coverage check needs to.
type extractPass struct {
	state  *model.ConversationState
	folded []model.Message // the slice the state now covers; nil when nothing was folded
	failed bool            // there was something to fold and no usable summary came back
}

func (s *ConversationStateService) extractOnce(
	ctx context.Context, convID, userID, tenantID uuid.UUID, opts ExtractionOptions,
) (extractPass, error) {
	// 1. Existing state (may be nil for first-time extraction).
	existing, err := s.states.Get(ctx, convID, userID, tenantID)
	if err != nil {
		return extractPass{}, fmt.Errorf("state lookup: %w", err)
	}

	batch := opts.BatchLimit
	if batch <= 0 {
		batch = extractionBatchLimit
	}

	// 2. Fetch the next slice of messages to summarise, oldest first.
	anchor := uuid.Nil
	if existing != nil && existing.SummarizedUpToMessageID != nil {
		anchor = *existing.SummarizedUpToMessageID
	}
	newMessages, err := s.messages.ListAfter(ctx, tenantID, convID, anchor, batch)
	if err != nil {
		return extractPass{}, fmt.Errorf("list after: %w", err)
	}
	if len(newMessages) == 0 {
		return extractPass{state: existing}, nil
	}

	// 3. Build the extraction prompt + call the gateway.
	conv, err := s.conv.GetByID(ctx, tenantID, convID)
	if err != nil {
		return extractPass{}, fmt.Errorf("conv lookup: %w", err)
	}
	instructions := strings.TrimSpace(opts.Instructions)
	if instructions == "" && existing != nil {
		instructions = strings.TrimSpace(existing.CompactionInstructions)
	}
	prompt := s.buildExtractionPrompt(existing, newMessages, instructions)
	callCtx := GatewayCallContext{
		TenantID:       tenantID,
		UserID:         userID,
		ConversationID: convID,
	}

	// 4. Call, then parse + merge with existing. FIX102 Part D: the
	//    operator's summariser first, the model that answers the
	//    conversation second. A failed call or a reply that is not JSON
	//    (the model called a tool, or chatted) moves on to the next model;
	//    when none produces one, the existing content is kept and the anchor
	//    does NOT advance, so the slice is retried next time.
	models := s.extractionModels(opts.ChatModel, conv.ModelID)
	var parsed extractedState
	for i, modelID := range models {
		rawJSON, usage := s.callLLMForExtraction(ctx, modelID, prompt, callCtx)
		if usage != nil {
			s.recordUsage(ctx, CompactionUsage{
				ConversationID: convID, UserID: userID, TenantID: tenantID,
				ModelID: modelID, Trigger: firstNonEmpty(opts.Trigger, "auto"),
				PromptTokens: usage.PromptTokens, OutputTokens: usage.CompletionTokens,
				CostUSD: usage.EstimatedCostUSD, MessagesFolded: len(newMessages),
			})
		}
		next := ""
		if i+1 < len(models) {
			next = models[i+1]
		}
		if strings.TrimSpace(rawJSON) == "" {
			// Failure detail is logged inside the LLM caller.
			if next != "" {
				slog.Warn("conversation state: no summary from this model — trying the next",
					"conv", convID.String(), "model", modelID, "next", next)
			}
			continue
		}
		if p := parseExtractionResponse(rawJSON); p.parsed {
			parsed = p
			break
		}
		slog.Warn("conversation state: extraction response was not JSON",
			"conv", convID.String(), "model", modelID, "len", len(rawJSON), "next", next)
	}
	if !parsed.parsed {
		return extractPass{state: existing, failed: true}, nil
	}
	merged := mergeExtractionIntoState(existing, parsed, convID, userID, tenantID)
	if instructions != "" {
		merged.CompactionInstructions = instructions
	}

	// 5. Stamp compaction tracking.
	lastMsg := newMessages[len(newMessages)-1]
	mid := lastMsg.ID
	merged.SummarizedUpToMessageID = &mid
	count, cerr := s.messages.CountMessages(ctx, tenantID, convID)
	if cerr == nil {
		merged.MessageCountAtSummary = count
	}
	merged.SummaryTokenCount = estimateTokenCount(merged)

	// 6. Persist.
	if err := s.states.Upsert(ctx, merged); err != nil {
		return extractPass{}, fmt.Errorf("upsert state: %w", err)
	}
	return extractPass{state: merged, folded: newMessages}, nil
}

// EnsureStateCoversUpTo runs extraction passes, oldest-first, until
// the state's anchor has reached uptoID (or nothing older remains),
// bounded by extractionMaxBatches. This is what the pre-trim refresh
// calls (A-W1: the rows the load ceiling cut are folded first) and
// what manual compact calls with the newest message (A-W7). Returns
// the final state (possibly the untouched existing one) and whether
// coverage reached uptoID.
//
// FIX102 Part C — "reached" used to mean "the anchor landed EXACTLY on
// uptoID". A batch folds everything after the anchor, up to 120 messages,
// so it almost always lands past uptoID, and a successful extraction was
// reported as incomplete: FIX102's probe logged "state coverage incomplete
// before trim" 0.03 s after five out of five successful extractions. And a
// FAILED call looked like "nothing new", so it could be reported as reached.
// Now reached is true when uptoID is inside a folded batch, when a short
// batch drained everything after the anchor, or when there was nothing after
// the anchor to fold — and false when a call failed or the batch bound ran
// out first.
func (s *ConversationStateService) EnsureStateCoversUpTo(
	ctx context.Context, convID, userID, tenantID uuid.UUID, uptoID *uuid.UUID, opts ExtractionOptions,
) (*model.ConversationState, bool, error) {
	batch := opts.BatchLimit
	if batch <= 0 {
		batch = extractionBatchLimit
	}
	// FIX117 — progress in raw counts. The denominator is read once, before
	// the first batch; a count that fails makes every report say "unknown"
	// rather than a guess. Reports are best-effort: the sink can never
	// break the fold.
	progress := newExtractionProgress(ctx, s.messages, s.states, convID, userID, tenantID, uptoID, opts.OnProgress)
	progress.report(false, false)
	var st *model.ConversationState
	for i := 0; i < extractionMaxBatches; i++ {
		p, err := s.extractOnce(ctx, convID, userID, tenantID, opts)
		if err != nil {
			progress.report(true, false)
			return st, false, err
		}
		st = p.state
		if p.failed {
			progress.report(true, false)
			return st, false, nil
		}
		if len(p.folded) == 0 {
			reached := uptoID == nil || (st != nil && st.SummarizedUpToMessageID != nil)
			progress.report(true, reached)
			return st, reached, nil
		}
		progress.folded += len(p.folded)
		if uptoID != nil && containsMessage(p.folded, *uptoID) {
			progress.report(true, true)
			return st, true, nil
		}
		if len(p.folded) < batch {
			progress.report(true, true)
			return st, true, nil
		}
		progress.report(false, false)
	}
	slog.Warn("conversation state: coverage did not reach the requested message within the batch bound",
		"conv", convID.String(), "batches", extractionMaxBatches)
	progress.report(true, false)
	return st, false, nil
}

// extractionProgress carries the counts between batches and guards the
// sink. See ExtractionProgress for the contract.
type extractionProgress struct {
	sink       func(ExtractionProgress)
	folded     int
	total      int
	totalKnown bool
	lastSent   int
	done       bool
}

func newExtractionProgress(
	ctx context.Context, messages stateMessageSource, states stateStore,
	convID, userID, tenantID uuid.UUID, uptoID *uuid.UUID, sink func(ExtractionProgress),
) *extractionProgress {
	p := &extractionProgress{sink: sink}
	if sink == nil {
		return p
	}
	anchor := uuid.Nil
	if existing, err := states.Get(ctx, convID, userID, tenantID); err == nil && existing != nil && existing.SummarizedUpToMessageID != nil {
		anchor = *existing.SummarizedUpToMessageID
	}
	total, err := messages.CountAfterUpTo(ctx, tenantID, convID, anchor, uptoID)
	if err != nil {
		slog.Warn("conversation state: progress total unknown — reporting counts without a denominator",
			"conv", convID.String(), "err", err)
		return p
	}
	p.total, p.totalKnown = total, true
	return p
}

// report sends one ExtractionProgress. Folded is clamped so it never goes
// backwards and never equals the total before done; done is sent once.
func (p *extractionProgress) report(done, reached bool) {
	if p == nil || p.sink == nil || p.done {
		return
	}
	folded := p.folded
	if p.totalKnown {
		if done && reached {
			folded = p.total
		} else if folded >= p.total && p.total > 0 {
			folded = p.total - 1
		}
	}
	if folded < p.lastSent {
		folded = p.lastSent
	}
	p.lastSent = folded
	if done {
		p.done = true
	}
	defer func() {
		if r := recover(); r != nil {
			slog.Warn("conversation state: progress sink panicked — fold continues", "panic", fmt.Sprint(r))
		}
	}()
	p.sink(ExtractionProgress{Folded: folded, Total: p.total, TotalKnown: p.totalKnown, Done: done, Reached: reached})
}

func containsMessage(msgs []model.Message, id uuid.UUID) bool {
	for i := range msgs {
		if msgs[i].ID == id {
			return true
		}
	}
	return false
}

// SetManualCompactBoundary records A-W7's "drop everything at or
// before this message on the next turn" on the state row. Called
// ONLY after EnsureStateCoversUpTo(boundary) succeeded — a compact
// that could not summarise must not drop anything.
func (s *ConversationStateService) SetManualCompactBoundary(
	ctx context.Context, convID, userID, tenantID, boundary uuid.UUID, instructions string,
) (*model.ConversationState, error) {
	st, err := s.states.Get(ctx, convID, userID, tenantID)
	if err != nil {
		return nil, err
	}
	if st == nil {
		st = &model.ConversationState{ConversationID: convID, UserID: userID, TenantID: tenantID}
	}
	b := boundary
	st.ManualCompactBoundaryMessageID = &b
	if strings.TrimSpace(instructions) != "" {
		st.CompactionInstructions = strings.TrimSpace(instructions)
	}
	if err := s.states.Upsert(ctx, st); err != nil {
		return nil, err
	}
	return st, nil
}

func (s *ConversationStateService) recordUsage(ctx context.Context, u CompactionUsage) {
	// Always the log line (A-W8: the cost of compacting is visible in
	// the observability stream even before the DB sink is wired).
	slog.Info("compaction_usage",
		"conv", u.ConversationID.String(),
		"user", u.UserID.String(),
		"model", u.ModelID,
		"trigger", u.Trigger,
		"prompt_tokens", u.PromptTokens,
		"output_tokens", u.OutputTokens,
		"cost_usd", u.CostUSD,
		"messages_folded", u.MessagesFolded,
	)
	if s.usage == nil {
		return
	}
	if err := s.usage.RecordCompactionUsage(ctx, u); err != nil {
		slog.Warn("compaction_usage: record failed", "conv", u.ConversationID.String(), "err", err)
	}
}

// compactionUsageSink adapts the repository to CompactionUsageRecorder
// (the repository package cannot import service).
type compactionUsageSink struct {
	repo *repository.CompactionUsageRepo
}

func NewCompactionUsageSink(repo *repository.CompactionUsageRepo) CompactionUsageRecorder {
	return &compactionUsageSink{repo: repo}
}

func (k *compactionUsageSink) RecordCompactionUsage(ctx context.Context, u CompactionUsage) error {
	if k.repo == nil {
		return nil
	}
	return k.repo.Insert(ctx, repository.CompactionUsageRow{
		ConversationID: u.ConversationID, TenantID: u.TenantID, UserID: u.UserID,
		ModelID: u.ModelID, Trigger: u.Trigger,
		PromptTokens: u.PromptTokens, OutputTokens: u.OutputTokens,
		CostUSD: u.CostUSD, MessagesFolded: u.MessagesFolded,
	})
}

// ---------------------------------------------------------------------------
// LLM extraction prompt + call + parse
// ---------------------------------------------------------------------------

// buildExtractionPrompt assembles the user-message body sent to the
// gateway. The system message is a separate const so the prompt
// here stays focused on "what to analyse"; the system message
// owns "what shape to emit". Existing state is embedded so the
// model can merge rather than re-derive on every pass.
func (s *ConversationStateService) buildExtractionPrompt(
	existing *model.ConversationState, newMessages []model.Message, instructions string,
) string {
	var sb strings.Builder
	sb.WriteString("Analyse the following conversation messages and produce the structured state summary described in your instructions.\n")

	// A-W7 — the user's own guidance on what to keep / drop (Claude's
	// `instructions`). Precedes the state and the messages so it
	// frames how the model reads them.
	if strings.TrimSpace(instructions) != "" {
		sb.WriteString("\n--- USER INSTRUCTIONS FOR THIS SUMMARY (take precedence over the defaults) ---\n")
		sb.WriteString(strings.TrimSpace(instructions))
		sb.WriteString("\n")
	}

	// FIX102 Part D — the summary is written in the conversation's
	// language. The shared asset says "preserve the user's language", and a
	// Thai thread summarised into English still loses the thing that
	// matters most; with a summariser that is not the chat model the
	// language has to be stated, not inferred.
	if lang := conversationLanguageName(newMessages); lang != "" {
		sb.WriteString("\n--- LANGUAGE ---\n")
		sb.WriteString(fmt.Sprintf("This conversation is in %s. Write every value in the JSON in %s; do not translate it. The JSON keys stay as specified.\n", lang, lang))
	}

	// Embed existing state as merge context. The model is told to
	// "update/merge" so it doesn't have to re-derive every fact.
	if existing != nil && (existing.CurrentGoal != nil ||
		!isEmptyJSON(existing.StructuredSummary) ||
		!isEmptyJSON(existing.ConfirmedDecisions) ||
		!isEmptyJSON(existing.OpenTasks)) {
		sb.WriteString("\n--- EXISTING STATE (update/merge with new information) ---\n")
		blob := map[string]any{
			"current_goal":         derefStr(existing.CurrentGoal),
			"structured_summary":   rawOrEmptyObject(existing.StructuredSummary),
			"confirmed_decisions":  rawOrEmptyArray(existing.ConfirmedDecisions),
			"active_artifacts":     rawOrEmptyArray(existing.ActiveArtifacts),
			"open_tasks":           rawOrEmptyArray(existing.OpenTasks),
			"rejected_approaches":  rawOrEmptyArray(existing.RejectedApproaches),
			"unresolved_questions": rawOrEmptyArray(existing.UnresolvedQuestions),
		}
		// FIX106 — no HTML escaping. json.Marshal rewrites every < and > as a
		// unicode escape, so the placeholder <<PHONE_1>> reached the model as
		// escape sequences — valid JSON, but not the text the prompt tells it to
		// copy exactly — and the desktop never escaped. encoding/json sorts the
		// map keys; the desktop writes the same order.
		// testdata/state_contract.json pins both.
		var buf bytes.Buffer
		enc := json.NewEncoder(&buf)
		enc.SetEscapeHTML(false)
		if err := enc.Encode(blob); err == nil {
			sb.Write(buf.Bytes()) // Encode ends the line
		}
	}

	sb.WriteString("\n--- NEW MESSAGES TO ANALYSE ---\n")
	for i := range newMessages {
		msg := &newMessages[i]
		sb.WriteString(fmt.Sprintf("[%s] %s\n", msg.Role, truncateForExtraction(msg.ContentMasked)))
	}
	return sb.String()
}

// extractionMessageLimit is how much of each message the summariser reads,
// in UTF-8 bytes — the budget chat-edge has always used (about 166 Thai
// characters, 500 English ones).
//
// FIX106: the cut now lands on a character boundary. It used to slice the
// bytes wherever 500 fell, splitting a Thai character and sending invalid
// UTF-8. The desktop cut at 500 UTF-16 units instead — three times as much
// Thai — and now cuts exactly as this does (testdata/state_contract.json).
// Reading more of each message was NOT adopted: a 120-message Thai batch
// would roughly triple in size and could overflow a 16k model's window, and
// no measurement this round covers that.
const extractionMessageLimit = 500

func truncateForExtraction(content string) string {
	if len(content) <= extractionMessageLimit {
		return content
	}
	cut := extractionMessageLimit
	for cut > 0 && !utf8.RuneStart(content[cut]) {
		cut--
	}
	return content[:cut] + "... [truncated]"
}

// FIX33 A-D3 — the extraction prompt is ONE asset shared with the
// desktop (apps/desktop/src/main/compaction-prompt.ts holds the same
// bytes; apps/desktop/test/compaction-prompt.test.ts byte-compares the
// two and fails on drift — the same guard FIX32 put on the language
// rules). It is the SYSTEM message of every extraction call, and it
// carries A-W3's "do not call any tool" sentence.
//
//go:embed prompt_compaction_extraction.txt
var compactionExtractionPromptRaw string

// ExtractionSystemPrompt is the trimmed shared asset (exported for the
// byte-compare test and the report).
func ExtractionSystemPrompt() string { return strings.TrimSpace(compactionExtractionPromptRaw) }

// FIX33.1 Part 2 — the state INJECTION wording is one asset too. Both
// surfaces now carry <conversation_state> as a user/assistant pair at the
// head of the history (never inside the system message, which the default
// DLP profile does not scan outbound — FIX32 §1.3 C3). The user line
// frames the block as reference material; the assistant line is the
// acknowledgement. apps/desktop/src/main/state-injection-prompt.ts holds
// the same bytes; the desktop test byte-compares them.
//
//go:embed prompt_state_injection.txt
var stateInjectionRaw string

const stateInjectionSeparator = "\n---ASSISTANT---\n"

// StateInjectionTexts returns (userPreamble, assistantAck) from the asset.
func StateInjectionTexts() (string, string) {
	raw := strings.ReplaceAll(stateInjectionRaw, "\r\n", "\n")
	parts := strings.SplitN(raw, stateInjectionSeparator, 2)
	if len(parts) != 2 {
		return strings.TrimSpace(raw), "Understood."
	}
	return strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
}

// StateInjectionMessages renders the rendered <conversation_state> block
// as the two synthetic history rows the request carries. IDs are Nil —
// they are never persisted; every consumer keyed by message id (image
// map, generated-image notes) simply finds no entry.
func StateInjectionMessages(block string) []model.Message {
	userText, ack := StateInjectionTexts()
	return []model.Message{
		{Role: "user", ContentMasked: userText + "\n" + block, Status: "completed"},
		{Role: "assistant", ContentMasked: ack, Status: "completed"},
	}
}

// callLLMForExtraction issues the non-streaming gateway call.
// Returns "" on any failure so the caller falls back to keeping the
// existing state rather than crashing the extraction trigger. The
// usage (when the gateway reported one) rides along for A-W8.
//
// A-W3: the request carries NO tool definitions and no tool_choice —
// the model has nothing to call — and the system prompt says so in
// words too (Claude's documented failure: with tools present the
// model calls one instead of writing the summary → empty block).
func (s *ConversationStateService) callLLMForExtraction(
	parent context.Context, modelID, userPrompt string, callCtx GatewayCallContext,
) (string, *GatewayChatUsage) {
	ctx, cancel := context.WithTimeout(parent, extractionTimeout)
	defer cancel()

	req := GatewayChatRequest{
		Model: modelID,
		Messages: []GatewayMessage{
			{Role: "system", Content: ExtractionSystemPrompt()},
			{Role: "user", Content: userPrompt},
		},
		MaxTokens:   extractionMaxTokens,
		Temperature: extractionTemperature,
		Tools:       nil,
		ToolChoice:  nil,
		// FIX102 Part D — no thinking. A reasoning model's thinking shares
		// the 1,500-token cap: the only qwen3.8-27b-fp8 extraction on record
		// used exactly 1,500 output tokens. "off" sends the Qwen/vLLM
		// enable_thinking switch and no reasoning parameters elsewhere.
		Reasoning: &GatewayReasoningConfig{Mode: "off"},
	}
	resp, err := s.gateway.SendChatCompletion(ctx, callCtx, req)
	if err != nil {
		slog.Warn("conversation state: extraction call failed",
			"conv", callCtx.ConversationID.String(), "err", err)
		return "", nil
	}
	if resp == nil || len(resp.Choices) == 0 {
		return "", nil
	}
	if str, ok := resp.Choices[0].Message.Content.(string); ok {
		return str, resp.Usage
	}
	return "", resp.Usage
}

// parseExtractionResponse decodes the gateway's JSON envelope.
// Tolerant of markdown fences (some providers add them even when
// asked not to). Returns a zero-valued extractedState on any error
// so the merge step can fall through to "keep existing".
type extractedState struct {
	CurrentGoal         string          `json:"current_goal"`
	StructuredSummary   json.RawMessage `json:"structured_summary"`
	ConfirmedDecisions  json.RawMessage `json:"confirmed_decisions"`
	ActiveArtifacts     json.RawMessage `json:"active_artifacts"`
	OpenTasks           json.RawMessage `json:"open_tasks"`
	RejectedApproaches  json.RawMessage `json:"rejected_approaches"`
	UnresolvedQuestions json.RawMessage `json:"unresolved_questions"`
	parsed              bool
}

func parseExtractionResponse(raw string) extractedState {
	cleaned := strings.TrimSpace(raw)
	cleaned = strings.TrimPrefix(cleaned, "```json")
	cleaned = strings.TrimPrefix(cleaned, "```")
	cleaned = strings.TrimSuffix(cleaned, "```")
	cleaned = strings.TrimSpace(cleaned)

	var out extractedState
	if err := json.Unmarshal([]byte(cleaned), &out); err != nil {
		return out
	}
	out.parsed = true
	return out
}

// mergeExtractionIntoState resolves the (existing, fresh) → state
// merge. Today we trust the extractor's output verbatim (it was
// given the existing state as context and told to merge) — that
// keeps the merge logic linear and lets future prompt tuning shift
// behaviour without re-coding the Go side.
//
// On a parse failure we keep the existing state's content but
// still return a state object so the caller can stamp compaction
// fields and re-upsert (handy for keeping the "last attempted"
// timestamp fresh even when extraction failed).
func mergeExtractionIntoState(
	existing *model.ConversationState,
	fresh extractedState,
	convID, userID, tenantID uuid.UUID,
) *model.ConversationState {
	out := &model.ConversationState{
		ConversationID: convID,
		UserID:         userID,
		TenantID:       tenantID,
	}
	if existing != nil {
		out.CurrentGoal = existing.CurrentGoal
		out.StructuredSummary = existing.StructuredSummary
		out.ConfirmedDecisions = existing.ConfirmedDecisions
		out.ActiveArtifacts = existing.ActiveArtifacts
		out.OpenTasks = existing.OpenTasks
		out.RejectedApproaches = existing.RejectedApproaches
		out.UnresolvedQuestions = existing.UnresolvedQuestions
		// FIX33 — the compaction controls persist across passes.
		out.SummarizedUpToMessageID = existing.SummarizedUpToMessageID
		out.MessageCountAtSummary = existing.MessageCountAtSummary
		out.ManualCompactBoundaryMessageID = existing.ManualCompactBoundaryMessageID
		out.CompactionInstructions = existing.CompactionInstructions
	}
	if !fresh.parsed {
		return out
	}
	if fresh.CurrentGoal != "" {
		g := fresh.CurrentGoal
		out.CurrentGoal = &g
	}
	if len(fresh.StructuredSummary) > 0 {
		out.StructuredSummary = fresh.StructuredSummary
	}
	if len(fresh.ConfirmedDecisions) > 0 {
		out.ConfirmedDecisions = fresh.ConfirmedDecisions
	}
	if len(fresh.ActiveArtifacts) > 0 {
		out.ActiveArtifacts = fresh.ActiveArtifacts
	}
	if len(fresh.OpenTasks) > 0 {
		out.OpenTasks = fresh.OpenTasks
	}
	if len(fresh.RejectedApproaches) > 0 {
		out.RejectedApproaches = fresh.RejectedApproaches
	}
	if len(fresh.UnresolvedQuestions) > 0 {
		out.UnresolvedQuestions = fresh.UnresolvedQuestions
	}
	return out
}

// ---------------------------------------------------------------------------
// Tool-driven incremental updates
// ---------------------------------------------------------------------------

// ToolStateUpdate carries the field-level deltas the
// update_conversation_state AI tool emits. All fields are
// optional; empty strings are treated as "skip this field".
type ToolStateUpdate struct {
	CurrentGoal     string
	AddDecision     string
	AddTask         string
	CompleteTask    string
	AddArtifact     string
	RejectApproach  string
	AddQuestion     string
	ResolveQuestion string
}

// ApplyToolUpdate fetches the current state (creating a zero-row
// in memory when none exists), applies each non-empty delta, and
// upserts. Returns a human-readable list of changes so the tool
// dispatcher can echo them back to the AI as the tool result.
func (s *ConversationStateService) ApplyToolUpdate(
	ctx context.Context, convID, userID, tenantID uuid.UUID, in ToolStateUpdate,
) ([]string, error) {
	state, err := s.states.Get(ctx, convID, userID, tenantID)
	if err != nil {
		return nil, err
	}
	if state == nil {
		state = &model.ConversationState{
			ConversationID: convID,
			UserID:         userID,
			TenantID:       tenantID,
		}
	}

	changes := make([]string, 0, 8)
	if strings.TrimSpace(in.CurrentGoal) != "" {
		g := strings.TrimSpace(in.CurrentGoal)
		state.CurrentGoal = &g
		changes = append(changes, "Goal set: "+g)
	}
	if v := strings.TrimSpace(in.AddDecision); v != "" {
		state.ConfirmedDecisions = appendToJSONArray(state.ConfirmedDecisions, "decision", v)
		changes = append(changes, "Decision recorded: "+v)
	}
	if v := strings.TrimSpace(in.AddTask); v != "" {
		state.OpenTasks = appendToJSONArray(state.OpenTasks, "task", v)
		changes = append(changes, "Task added: "+v)
	}
	if v := strings.TrimSpace(in.CompleteTask); v != "" {
		state.OpenTasks = removeFromJSONArray(state.OpenTasks, "task", v)
		changes = append(changes, "Task completed: "+v)
	}
	if v := strings.TrimSpace(in.AddArtifact); v != "" {
		state.ActiveArtifacts = appendToJSONArray(state.ActiveArtifacts, "name", v)
		changes = append(changes, "Artifact tracked: "+v)
	}
	if v := strings.TrimSpace(in.RejectApproach); v != "" {
		state.RejectedApproaches = appendToJSONArray(state.RejectedApproaches, "approach", v)
		changes = append(changes, "Approach rejected: "+v)
	}
	if v := strings.TrimSpace(in.AddQuestion); v != "" {
		state.UnresolvedQuestions = appendToJSONArray(state.UnresolvedQuestions, "question", v)
		changes = append(changes, "Question noted: "+v)
	}
	if v := strings.TrimSpace(in.ResolveQuestion); v != "" {
		state.UnresolvedQuestions = removeFromJSONArray(state.UnresolvedQuestions, "question", v)
		changes = append(changes, "Question resolved: "+v)
	}

	if len(changes) == 0 {
		return changes, nil
	}
	state.SummaryTokenCount = estimateTokenCount(state)
	if err := s.states.Upsert(ctx, state); err != nil {
		return nil, err
	}
	return changes, nil
}

// Get exposes the repo's read path through the service so handlers
// can fetch a conversation's state without taking a direct repo
// dependency.
func (s *ConversationStateService) Get(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (*model.ConversationState, error) {
	return s.states.Get(ctx, convID, userID, tenantID)
}

// ---------------------------------------------------------------------------
// BuildStateContext — render <conversation_state> system-prompt block
// ---------------------------------------------------------------------------

// BuildStateContext is the read path called by MessageService.
// buildSystemPrefix on every turn. Returns "" when no state is
// persisted yet — the caller treats that as "no block, skip".
//
// We DO NOT require a non-null current_goal anymore — a state row
// with only tool-driven updates (e.g. "added one task") should
// still render. The injection block opens with the goal when
// present and falls back to a generic header otherwise.
func (s *ConversationStateService) BuildStateContext(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) string {
	text, _ := s.BuildStateContextWithSources(ctx, convID, userID, tenantID)
	return text
}

// BuildStateContextWithSources is the S7 variant: returns the
// rendered <conversation_state> block AND a single ContextSource
// that summarises the row (goal if present, else "summary
// available"). The SourcesCard renders this as
// "📋 Goal: …" so the user knows the AI's answer drew on
// structured state, not just raw messages.
func (s *ConversationStateService) BuildStateContextWithSources(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (string, []ContextSource) {
	state, err := s.states.Get(ctx, convID, userID, tenantID)
	if err != nil || state == nil {
		return "", nil
	}
	if isStateEmpty(state) {
		return "", nil
	}

	text := renderStateBlock(state)

	label := "Conversation state available"
	if state.CurrentGoal != nil && *state.CurrentGoal != "" {
		label = "Goal: " + *state.CurrentGoal
	}
	sources := []ContextSource{{
		SourceType:   model.SourceTypeConversationState,
		SourceID:     state.ConversationID.String(),
		DisplayLabel: TruncateLabel(label),
	}}
	return text, sources
}

// stateSummarySections is the order and heading of every structured_summary
// key in the rendered block — the three keys the extraction prompt asks for.
// The desktop renders the same list (conversation-state.ts);
// testdata/state_contract.json pins both.
//
// FIX106 measured a form with verbatim sections (standing rules, the user's
// requests, exact details, current work) against this prompt and did not ship
// it (scripts/probes/summary-recall): one run recalled 4 of 7 facts where this
// prompt recalled 7, the other returned JSON that did not parse.
var stateSummarySections = []struct{ key, label string }{
	{"key_context", "Key context"},
	{"progress", "Progress"},
	{"technical_details", "Technical details"},
}

// renderStateBlock is the <conversation_state> text for one state row. Pure,
// so the cross-tree contract test can call it.
func renderStateBlock(state *model.ConversationState) string {
	var sb strings.Builder
	sb.WriteString("<conversation_state>\n")
	sb.WriteString("Summary of this conversation so far (older messages have been compacted):\n")

	if state.CurrentGoal != nil && *state.CurrentGoal != "" {
		sb.WriteString("\nGoal: ")
		sb.WriteString(*state.CurrentGoal)
		sb.WriteString("\n")
	}

	if len(state.StructuredSummary) > 2 { // not "{}"
		var summary map[string]any
		if err := json.Unmarshal(state.StructuredSummary, &summary); err == nil {
			for _, sec := range stateSummarySections {
				val := summaryText(summary[sec.key])
				if val == "" {
					continue
				}
				sb.WriteString("\n")
				sb.WriteString(sec.label)
				sb.WriteString(": ")
				sb.WriteString(val)
				sb.WriteString("\n")
			}
		}
	}

	writeJSONArray(&sb, "Confirmed decisions", state.ConfirmedDecisions, "decision")
	writeArtifacts(&sb, state.ActiveArtifacts)
	writeJSONArray(&sb, "Open tasks", state.OpenTasks, "task")
	writeJSONArray(&sb, "Rejected approaches", state.RejectedApproaches, "approach")
	writeJSONArray(&sb, "Unresolved questions", state.UnresolvedQuestions, "question")

	// FIX33 A-W5 — the desktop's honesty rule, now on web too: the
	// summary is what the model has of the compacted part, so a
	// detail that is in neither the summary nor the visible messages
	// must be reported as lost, not guessed.
	sb.WriteString("\n")
	sb.WriteString(StateHonestyNote)
	sb.WriteString("\n</conversation_state>\n")
	return sb.String()
}

// summaryText renders one structured_summary value: a string as written, a
// list of strings (models sometimes return one) as "- " lines. Anything else
// is left out rather than failing the whole block — before FIX106 one
// non-string value made the entire summary disappear from the prompt.
func summaryText(v any) string {
	switch t := v.(type) {
	case string:
		return strings.TrimSpace(t)
	case []any:
		lines := make([]string, 0, len(t))
		for _, item := range t {
			str, ok := item.(string)
			if !ok || strings.TrimSpace(str) == "" {
				continue
			}
			str = strings.TrimSpace(str)
			if !strings.HasPrefix(str, "- ") {
				str = "- " + str
			}
			lines = append(lines, str)
		}
		return strings.Join(lines, "\n")
	}
	return ""
}

// StateHonestyNote is the sentence appended inside every rendered
// <conversation_state> block (A-W5). Exported so the desktop test can
// pin the same wording.
const StateHonestyNote = "This summary stands in for older messages that are no longer sent verbatim. If the user refers to something that is in neither this summary nor the visible messages, say you may have lost that detail and ask them to restate it — do not guess."

// writeJSONArray renders one "Label:\n- item\n- item\n" block.
// `field` is the single load-bearing key inside each object entry
// (e.g. "decision" / "task" / "question"). Silently skips when the
// array is empty or the unmarshal fails — these are best-effort
// blocks and a single malformed entry shouldn't fail the turn.
func writeJSONArray(sb *strings.Builder, label string, data json.RawMessage, field string) {
	if len(data) <= 2 { // "[]"
		return
	}
	var items []map[string]any
	if err := json.Unmarshal(data, &items); err != nil || len(items) == 0 {
		return
	}
	wrote := false
	for _, item := range items {
		val, _ := item[field].(string)
		val = strings.TrimSpace(val)
		if val == "" {
			continue
		}
		if !wrote {
			sb.WriteString("\n")
			sb.WriteString(label)
			sb.WriteString(":\n")
			wrote = true
		}
		sb.WriteString("- ")
		sb.WriteString(val)
		sb.WriteString("\n")
	}
}

// writeArtifacts is the artifact-specific renderer — each entry has
// name + status (+ optional version). Status renders inline so the
// model gets "report.docx (in_progress)" on one line.
func writeArtifacts(sb *strings.Builder, data json.RawMessage) {
	if len(data) <= 2 {
		return
	}
	var items []map[string]any
	if err := json.Unmarshal(data, &items); err != nil || len(items) == 0 {
		return
	}
	wrote := false
	for _, item := range items {
		name, _ := item["name"].(string)
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		if !wrote {
			sb.WriteString("\nActive artifacts:\n")
			wrote = true
		}
		status, _ := item["status"].(string)
		if status != "" {
			sb.WriteString("- ")
			sb.WriteString(name)
			sb.WriteString(" (")
			sb.WriteString(status)
			sb.WriteString(")\n")
		} else {
			sb.WriteString("- ")
			sb.WriteString(name)
			sb.WriteString("\n")
		}
	}
}

// ---------------------------------------------------------------------------
// JSON-array helpers (used by tool dispatcher + service)
// ---------------------------------------------------------------------------

// appendToJSONArray adds {key: value} to the array stored in `data`.
// Dedups (case-insensitive equality) against existing entries.
// Returns a fresh json.RawMessage. On marshal failure returns the
// original blob unchanged — same defensive pattern as the rest of
// the service.
func appendToJSONArray(data json.RawMessage, key, value string) json.RawMessage {
	var items []map[string]any
	if len(data) > 2 {
		_ = json.Unmarshal(data, &items)
	}
	for _, item := range items {
		if v, ok := item[key].(string); ok && strings.EqualFold(strings.TrimSpace(v), strings.TrimSpace(value)) {
			return data
		}
	}
	items = append(items, map[string]any{key: value})
	out, err := json.Marshal(items)
	if err != nil {
		return data
	}
	return out
}

// removeFromJSONArray drops any entry whose `key` value contains
// `value` (case-insensitive substring match). The fuzzy match is
// intentional — the AI's "complete this task" call rarely
// reproduces the exact stored wording, so we want close-enough
// matches to delete cleanly.
func removeFromJSONArray(data json.RawMessage, key, value string) json.RawMessage {
	if len(data) <= 2 {
		return data
	}
	var items []map[string]any
	if err := json.Unmarshal(data, &items); err != nil {
		return data
	}
	target := strings.ToLower(strings.TrimSpace(value))
	if target == "" {
		return data
	}
	filtered := make([]map[string]any, 0, len(items))
	for _, item := range items {
		v, _ := item[key].(string)
		if v != "" && strings.Contains(strings.ToLower(v), target) {
			continue
		}
		filtered = append(filtered, item)
	}
	out, err := json.Marshal(filtered)
	if err != nil {
		return data
	}
	return out
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

var languageNames = map[string]string{"th": "Thai", "en": "English", "ja": "Japanese", "ko": "Korean", "zh": "Chinese"}

// conversationLanguageName names the language the user writes in across
// msgs (detectLanguage per user message, the most frequent wins; assistant
// messages decide only when no user message is present). "" for no messages.
func conversationLanguageName(msgs []model.Message) string {
	count := func(role string) map[string]int {
		c := map[string]int{}
		for i := range msgs {
			if role == "" || msgs[i].Role == role {
				c[detectLanguage(msgs[i].ContentMasked)]++
			}
		}
		return c
	}
	counts := count("user")
	if len(counts) == 0 {
		counts = count("")
	}
	best, n := "", 0
	for _, code := range []string{"th", "ja", "ko", "zh", "en"} {
		if counts[code] > n {
			best, n = code, counts[code]
		}
	}
	return languageNames[best]
}

func isStateEmpty(s *model.ConversationState) bool {
	if s == nil {
		return true
	}
	if s.CurrentGoal != nil && strings.TrimSpace(*s.CurrentGoal) != "" {
		return false
	}
	if !isEmptyJSON(s.StructuredSummary) {
		return false
	}
	for _, blob := range []json.RawMessage{
		s.ConfirmedDecisions, s.ActiveArtifacts, s.OpenTasks,
		s.RejectedApproaches, s.UnresolvedQuestions,
	} {
		if !isEmptyJSON(blob) {
			return false
		}
	}
	return true
}

func isEmptyJSON(b json.RawMessage) bool {
	t := strings.TrimSpace(string(b))
	return t == "" || t == "{}" || t == "[]" || t == "null"
}

func rawOrEmptyObject(b json.RawMessage) json.RawMessage {
	if len(b) == 0 {
		return json.RawMessage(`{}`)
	}
	return b
}

func rawOrEmptyArray(b json.RawMessage) json.RawMessage {
	if len(b) == 0 {
		return json.RawMessage(`[]`)
	}
	return b
}

func derefStr(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

// estimateTokenCount — rough 4-chars-per-token heuristic across all
// state fields. Good enough for the future "is the block too big?"
// decision; not accurate enough to bill against.
func estimateTokenCount(s *model.ConversationState) int {
	if s == nil {
		return 0
	}
	total := 0
	if s.CurrentGoal != nil {
		total += len(*s.CurrentGoal)
	}
	for _, b := range []json.RawMessage{
		s.StructuredSummary, s.ConfirmedDecisions, s.ActiveArtifacts,
		s.OpenTasks, s.RejectedApproaches, s.UnresolvedQuestions,
	} {
		total += len(b)
	}
	return total / 4
}

// titleCase is a stdlib-free uppercase-first-letter for "key context"
// → "Key context". Avoids the deprecated strings.Title.
func titleCase(s string) string {
	if s == "" {
		return s
	}
	r := []rune(s)
	if r[0] >= 'a' && r[0] <= 'z' {
		r[0] = r[0] - 32
	}
	return string(r)
}
