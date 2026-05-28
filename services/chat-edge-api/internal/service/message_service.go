// MessageService — orchestrates the per-message lifecycle.
//
// Two entry points:
//
//	SendNonStream — synchronous: save user msg → call gateway →
//	                save assistant msg → return final response.
//	SendStream    — async: save user msg → open stream → yield
//	                events to the caller (handler forwards to
//	                browser SSE) → save assistant msg on completion.
//
// The Web Chat product stores REAL content, NOT pseudonymized text
// — that's a deliberate Phase 1 choice (DLP-on-save is an opt-in
// tenant policy, off by default). The column name in
// “chat.messages_masked“ keeps the schema forward-compatible.
package service

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrMessageConversationNotFound = errors.New("message: conversation not found")
	ErrMessageEmptyContent         = errors.New("message: content is required")
	ErrMessageGatewayUnavailable   = errors.New("message: gateway unavailable")
	ErrMessageNotFound             = errors.New("message: not found")
	ErrMessageNotAssistant         = errors.New("message: not an assistant message")
	ErrMessageModelNotAllowed      = errors.New("message: model not allowed")
)

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

type MessageService struct {
	conversations *repository.ConversationRepo
	messages      *repository.MessageRepo
	gateway       *GatewayClient
	titles        *TitleService
	models        ModelChecker
	// Optional. When set, the message service:
	//   - attaches file_ids passed on Send to the user message
	//   - injects a markdown context block of extracted file text
	//     into the gateway request's system prompt
	// Left nil for unit tests that don't exercise the file flow.
	fileSvc FileLinker
	// Optional. Pulls the caller's custom instructions and prepends
	// them to the system prompt.
	prefsSvc PreferencesReader
	// Optional. Queries the knowledge base bound to the conversation
	// and returns a retrieved-context block for the system prompt.
	ragSvc RAGReader
	// Optional. Reads conversation files (with their cached
	// inspection_json) so the Phase 4 Excel context block can
	// inject the workbook structure + action catalog into the
	// system prompt. Wired via SetExcelLookup.
	excelLookup ExcelLookup
	// Optional. Builds the <saved_user_memory> system-prompt block
	// (S1 — Personal Memory Core). Gated by conv.MemoryEnabled.
	memorySvc MemoryReader
	// Optional. Dispatches AI tool_calls (save_memory, list_memories
	// today; future tools register here). Same MemoryEnabled gate
	// hides the tools from the gateway request when the toggle is
	// off so the model doesn't try to call them.
	toolExec *ToolExecutor
	// Optional. Builds the <conversation_state> system-prompt block
	// (S3 — Structured Conversation State) AND backs the
	// update_conversation_state AI tool. Auto-extraction kicks in
	// from finalizeConversation when message count crosses the
	// thresholds defined below.
	stateSvc *ConversationStateService
	// Optional. Token-budgeted context assembly (S4). When wired,
	// every send path runs the loaded history + freshly-built
	// system prefix through Compile to trim to the model's working
	// context window before shipping to the gateway. Falls back to
	// the legacy "send everything prepareSend loaded" path when nil.
	compiler *ContextCompiler
	// Optional. Persists per-response context sources (S7). When
	// wired, every send path collects sources during compile + tool
	// execution and flushes them to chat.response_context_sources
	// in the finish callback. Nil-safe — no source rows when
	// unwired (tests, downgrade path).
	sourceRepo *repository.ResponseContextSourceRepo
	// Optional. Background pattern learner (S8). Fires from
	// finalizeConversation alongside the S3 state extractor — once
	// per qualifying conversation. Nil-safe.
	prefExtractor *PreferenceExtractor

	maxContext int // max history messages threaded to the gateway

	// activeStreams tracks in-flight streaming generations so the
	// POST /cancel endpoint can interrupt them. Keyed by
	// conversation_id (one in-flight generation per conversation in
	// Phase 1; the second concurrent send would race the first).
	activeStreams sync.Map // map[uuid.UUID]*streamRegistration
}

// FileLinker is the slice of FileService the message flow needs.
// Decouples message_service from the concrete file service so unit
// tests can pass a tiny fake.
//
// ImageDataURLsForMessage resolves the image attachments on a single
// message to a list of "data:image/...;base64,..." strings, ready
// to drop into a vision request. Returns an empty slice when the
// message has no image attachments, or when image processing is
// unavailable — the caller treats nil as "no images" and ships a
// plain text turn.
type FileLinker interface {
	AttachToMessage(ctx context.Context, tenantID, convID, messageID uuid.UUID, fileIDs []uuid.UUID) (int64, error)
	BuildContextBlock(ctx context.Context, tenantID, convID uuid.UUID) (string, error)
	ImageDataURLsForMessage(ctx context.Context, tenantID, messageID uuid.UUID) []string
}

// PreferencesReader pulls the user's custom instructions for system-
// prompt injection. Same decoupling trick as FileLinker — interface
// in this package, concrete impl wired in main.go.
type PreferencesReader interface {
	BuildInstructionsBlock(ctx context.Context, tenantID, userID uuid.UUID) string
}

// RAGReader queries the bound knowledge base for context relevant
// to the user's prompt. The message service hands it the most-recent
// user content; the returned string (possibly empty) is folded into
// the system prompt prefix.
type RAGReader interface {
	BuildContext(ctx context.Context, tenantID uuid.UUID, kbID uuid.UUID, query string) string
}

// MemoryReader is the slice of MemoryService the message flow needs.
// BuildMemoryContext returns the <saved_user_memory> block (or "")
// for the system prompt; the message service handles the
// conv.MemoryEnabled gate before calling.
type MemoryReader interface {
	BuildMemoryContext(ctx context.Context, userID, tenantID uuid.UUID) string
	// S7 — same shape but also returns the per-memory source
	// attribution list. Concrete MemoryService implements both;
	// fakes can implement either depending on what they exercise.
	BuildMemoryContextWithSources(ctx context.Context, userID, tenantID uuid.UUID) (string, []ContextSource)
}

// ExcelLookup is the slice of the file repo the Phase 4 Excel
// system-prompt injector needs. Interface so unit tests don't have
// to wire the full pgx pool.
type ExcelLookup interface {
	ListByConversation(ctx context.Context, tenantID, convID uuid.UUID) ([]model.ConversationFile, error)
}

// streamRegistration is what's stored in MessageService.activeStreams.
// Holds the cancel func + a flag the cancel path flips so the
// stream's “finish“ callback knows whether the termination was
// user-initiated (status="cancelled") vs upstream error
// (status="error") vs natural completion (status="completed").
type streamRegistration struct {
	cancel        context.CancelFunc
	mu            sync.Mutex
	userCancelled bool
}

func (r *streamRegistration) markCancelled() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.userCancelled = true
}

func (r *streamRegistration) isUserCancelled() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.userCancelled
}

func NewMessageService(
	conversations *repository.ConversationRepo,
	messages *repository.MessageRepo,
	gateway *GatewayClient,
	titles *TitleService,
	models ModelChecker,
	maxContext int,
) *MessageService {
	if maxContext <= 0 {
		maxContext = 50
	}
	return &MessageService{
		conversations: conversations,
		messages:      messages,
		gateway:       gateway,
		titles:        titles,
		models:        models,
		maxContext:    maxContext,
	}
}

// SetFileLinker wires in the file service after construction. Done
// in main.go so the dependency order (file_service needs the file
// repo, message_service needs the file_service) stays linear and
// the constructor signatures don't churn.
func (s *MessageService) SetFileLinker(fl FileLinker) {
	s.fileSvc = fl
}

// SetPreferencesReader hooks up the custom-instructions injector.
// Same lazy-wire pattern as SetFileLinker.
func (s *MessageService) SetPreferencesReader(p PreferencesReader) {
	s.prefsSvc = p
}

// SetExcelLookup wires the file lookup the Excel system-prompt
// injector needs. Optional — without it the Excel block simply
// never renders and the AI gets the legacy CSV body via
// FileLinker.BuildContextBlock.
func (s *MessageService) SetExcelLookup(e ExcelLookup) {
	s.excelLookup = e
}

// SetRAGReader wires the knowledge-base retrieval service. Same
// lazy-wire pattern — kept out of the constructor so the service
// boots fine when RAG isn't configured.
func (s *MessageService) SetRAGReader(r RAGReader) {
	s.ragSvc = r
}

// SetMemoryService wires the S1 memory layer. When set:
//   - buildSystemPrefix injects the <saved_user_memory> block
//     (gated by conv.MemoryEnabled)
//   - Send/Regenerate/Edit include MemoryToolDefs() in the gateway
//     request's tool list (same gate)
//   - tool_call responses from the gateway dispatch through a
//     ToolExecutor that wraps this service
//
// Optional — without it the message flow falls back to the legacy
// memory-free behaviour.
func (s *MessageService) SetMemoryService(m *MemoryService) {
	s.memorySvc = m
	if m != nil {
		s.toolExec = NewToolExecutor(m)
		// If the state service was wired BEFORE memory, re-attach it
		// here so the executor exposes the S3 tool too.
		if s.stateSvc != nil {
			s.toolExec.SetConversationStateService(s.stateSvc)
		}
	}
}

// SetConversationStateService wires the S3 conversation-state
// layer. When set:
//   - buildSystemPrefix injects the <conversation_state> block
//     between the user-memory block and the file/RAG blocks
//   - the ToolExecutor exposes update_conversation_state alongside
//     save_memory / list_memories
//   - finalizeConversation triggers async state extraction on
//     thresholds (10 / +20 messages)
func (s *MessageService) SetConversationStateService(svc *ConversationStateService) {
	s.stateSvc = svc
	if s.toolExec != nil {
		s.toolExec.SetConversationStateService(svc)
	}
}

// SetContextCompiler wires the S4 token-budgeted compiler. Without
// it the send paths fall through to the legacy "ship everything
// prepareSend loaded" behaviour — useful for tests + a graceful
// downgrade if the compiler ever needs to be disabled.
func (s *MessageService) SetContextCompiler(c *ContextCompiler) {
	s.compiler = c
}

// SetSearchService wires the S6 read-only search layer onto the
// ToolExecutor. Optional — when nil, search_past_conversations is
// hidden from the tool list. Must run after SetMemoryService
// because the executor is created inside SetMemoryService.
func (s *MessageService) SetSearchService(svc *SearchService) {
	if s.toolExec != nil {
		s.toolExec.SetSearchService(svc)
	}
}

// SetResponseSourceRepo wires the S7 attribution persist path.
// Without it, sources collected during compile + tool execution
// are simply dropped at the end of the turn (no errors, no rows).
func (s *MessageService) SetResponseSourceRepo(r *repository.ResponseContextSourceRepo) {
	s.sourceRepo = r
}

// SetPreferenceExtractor wires the S8 background pattern learner.
// Without it, finalizeConversation skips the
// maybeExtractPreferences hook entirely (no extraction goroutines,
// no pending suggestions).
func (s *MessageService) SetPreferenceExtractor(e *PreferenceExtractor) {
	s.prefExtractor = e
}

// persistSources flushes an accumulator's snapshot to
// chat.response_context_sources. Best-effort: a failed insert
// logs a warning but never blocks the turn. Uses background ctx
// so a client disconnect during the very last instant doesn't
// strand the rows.
func (s *MessageService) persistSources(
	parent context.Context,
	asstMsgID, userID, tenantID, convID uuid.UUID,
	acc *SourcesAccumulator,
) {
	if s.sourceRepo == nil || acc == nil {
		return
	}
	snapshot := acc.Snapshot()
	if len(snapshot) == 0 {
		return
	}
	rows := toModelSources(snapshot, asstMsgID.String(), userID.String(), tenantID.String(), convID.String())
	if len(rows) == 0 {
		return
	}
	// Detach: persist runs on whichever goroutine called finish,
	// but the request ctx may already be cancelled. Use background.
	bg := context.Background()
	if err := s.sourceRepo.CreateBatch(bg, rows); err != nil {
		slog.Warn("context sources: persist failed",
			"asst_msg", asstMsgID.String(), "count", len(rows), "err", err)
	}
	_ = parent // kept for signature symmetry with other persist helpers
}

// ---------------------------------------------------------------------------
// ListMessages — handler reads conversation history
// ---------------------------------------------------------------------------

func (s *MessageService) ListMessages(
	ctx context.Context,
	tenantID, userID, convID uuid.UUID,
	limit int,
	cursor uuid.UUID,
) ([]model.Message, bool, error) {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, false, ErrMessageConversationNotFound
		}
		return nil, false, err
	}
	if conv.UserID != userID {
		// Other tenant member's conversation — same tenant, but the
		// chat product is single-user-per-conversation in Phase 1.
		return nil, false, ErrMessageConversationNotFound
	}
	if limit <= 0 {
		limit = 50
	}
	if limit > 100 {
		limit = 100
	}
	return s.messages.ListByConversation(ctx, tenantID, convID, limit, cursor)
}

// ---------------------------------------------------------------------------
// SendNonStream — request → blocking gateway call → response
// ---------------------------------------------------------------------------

// SendInput is the typed payload from the handler.
type SendInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	Content        string
	RequestID      string
	// FileIDs is the (optional) list of conversation_files row ids
	// the user attached to this send. Each id is patched onto the
	// new user message + their extracted text is folded into the
	// gateway request's system context.
	FileIDs []uuid.UUID
}

// SendResult is what the non-stream handler returns to the client.
type SendResult struct {
	UserMessage      *model.Message
	AssistantMessage *model.Message
}

func (s *MessageService) SendNonStream(ctx context.Context, in SendInput) (*SendResult, error) {
	conv, userMsg, history, err := s.prepareSend(ctx, in)
	if err != nil {
		return nil, err
	}

	imageURLs := s.lastUserImageURLs(ctx, in.TenantID, history)
	sourcesAcc := NewSourcesAccumulator()
	gwReq := s.compileAndBuildRequest(ctx, conv, in.UserID, in.TenantID, in.Content, history, imageURLs, sourcesAcc)
	toolsAttached := s.attachTools(&gwReq, conv)
	gwResp, err := s.gateway.SendChatCompletion(
		ctx, gatewayCallContext(in, conv), gwReq,
	)
	if err != nil {
		// Persist a placeholder assistant row with status=error so the
		// conversation timeline reflects the failed turn — the
		// front-end can show a "Couldn't reach the AI" bubble.
		_ = s.persistError(ctx, in, conv, "")
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
		}
		return nil, err
	}

	// Tool-execution loop. When the model emits tool_calls instead of
	// (or alongside) content, we dispatch them through the executor,
	// append the results as role="tool" messages, and re-call the
	// gateway. Bounded by maxToolIterations.
	if toolsAttached {
		iter := 0
		for iter < maxToolIterations && hasToolCalls(gwResp) {
			tcMsg, results := s.executeToolCalls(ctx, toolCallsOf(gwResp), ToolExecContext{
				TenantID:       in.TenantID,
				UserID:         in.UserID,
				ConversationID: conv.ID,
				Sources:        sourcesAcc,
			})
			gwReq.Messages = append(gwReq.Messages, tcMsg)
			gwReq.Messages = append(gwReq.Messages, results...)
			next, err := s.gateway.SendChatCompletion(ctx, gatewayCallContext(in, conv), gwReq)
			if err != nil {
				_ = s.persistError(ctx, in, conv, "")
				var ue *GatewayUnavailableError
				if errors.As(err, &ue) {
					return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
				}
				return nil, err
			}
			gwResp = next
			iter++
		}
	}

	content := extractContent(gwResp)
	var tokInput, tokOutput *int
	if gwResp.Usage != nil {
		ti, to := gwResp.Usage.PromptTokens, gwResp.Usage.CompletionTokens
		tokInput, tokOutput = &ti, &to
	}

	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID:   conv.ID,
		TenantID:         in.TenantID,
		UserID:           in.UserID,
		Role:             "assistant",
		ContentMasked:    content,
		Status:           "completed",
		ModelID:          &conv.ModelID,
		TokenUsageInput:  tokInput,
		TokenUsageOutput: tokOutput,
	})
	if err != nil {
		return nil, err
	}

	s.finalizeConversation(ctx, conv, userMsg, asstMsg)
	// S7 — persist the per-turn source attribution. Best-effort;
	// nil-safe when the source repo isn't wired.
	s.persistSources(ctx, asstMsg.ID, in.UserID, in.TenantID, conv.ID, sourcesAcc)

	return &SendResult{UserMessage: userMsg, AssistantMessage: asstMsg}, nil
}

// ---------------------------------------------------------------------------
// SendStream — request → open gateway stream → yield events
// ---------------------------------------------------------------------------

// StreamHandle is what SendStream returns. The caller (the HTTP
// handler) ranges over Events, forwards each to the browser via
// SSE, then calls Finish to persist the accumulated content.
type StreamHandle struct {
	UserMessage    *model.Message
	AssistantID    uuid.UUID // pre-allocated; sent on message_start
	ConversationID uuid.UUID

	Events chan StreamUpdate
	finish func(content string, finishReason string, usage *GatewayChatUsage)
	cancel func()
}

// StreamUpdate is the per-event surface — one delta plus optional
// terminal metadata. The handler emits a “content_delta“ SSE
// event for every non-empty Delta and a “message_complete“ SSE
// event when Done is true.
type StreamUpdate struct {
	Delta        string
	Done         bool
	FinishReason string
	Usage        *GatewayChatUsage
	Err          error
}

// Cancel terminates the upstream gateway connection. Used by the
// HTTP handler when the client disconnects before completion.
func (h *StreamHandle) Cancel() { h.cancel() }

// Finish persists the final assistant message. MUST be called
// exactly once after the Events channel is fully drained, even on
// error (the handler still persists partial content with
// status=error so the conversation timeline survives).
func (h *StreamHandle) Finish(content, finishReason string, usage *GatewayChatUsage) {
	h.finish(content, finishReason, usage)
}

func (s *MessageService) SendStream(ctx context.Context, in SendInput) (*StreamHandle, error) {
	conv, userMsg, history, err := s.prepareSend(ctx, in)
	if err != nil {
		return nil, err
	}
	// Create the assistant placeholder row. Inherit branch info
	// from the just-saved user message so the placeholder lives
	// inside the same fork group.
	asstStatus := "streaming"
	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "assistant",
		ContentMasked:  "",
		Status:         asstStatus,
		ModelID:        &conv.ModelID,
		ParentID:       userMsg.ParentID,
		BranchIndex:    userMsg.BranchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, err
	}

	// Open the upstream stream. cancelCtx propagates the
	// handler-driven cancel through to the HTTP transport.
	// We use context.Background as the parent (not ctx) so the
	// HTTP request finishing doesn't immediately tear down the
	// streaming connection — the handler explicitly invokes
	// StreamHandle.Cancel() when the client disconnects, and the
	// external POST /cancel endpoint invokes CancelStream(convID).
	streamCtx, cancel := context.WithCancel(context.Background())
	imageURLs := s.lastUserImageURLs(ctx, in.TenantID, history)
	sourcesAcc := NewSourcesAccumulator()
	gwReq := s.compileAndBuildRequest(ctx, conv, in.UserID, in.TenantID, in.Content, history, imageURLs, sourcesAcc)
	toolsAttached := s.attachTools(&gwReq, conv)
	stream, err := s.gateway.SendChatCompletionStream(
		streamCtx, gatewayCallContext(in, conv), gwReq,
	)
	if err != nil {
		cancel()
		// Persist as error so the timeline reflects the failed turn.
		_ = s.messages.UpdateContent(ctx, asstMsg.ID, "", "error", nil, nil)
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
		}
		return nil, err
	}

	// Register the in-flight stream so the POST /cancel endpoint
	// can interrupt it. The registration is removed inside the
	// ``finish`` callback below regardless of how the stream ends.
	reg := &streamRegistration{cancel: cancel}
	s.activeStreams.Store(conv.ID, reg)

	ch := make(chan StreamUpdate, 16)
	go s.pumpStreamWithTools(streamPumpInput{
		initialStream: stream,
		initialReq:    gwReq,
		callCtx:       gatewayCallContext(in, conv),
		streamCtx:     streamCtx,
		toolsAttached: toolsAttached,
		toolExecCtx: ToolExecContext{
			TenantID:       in.TenantID,
			UserID:         in.UserID,
			ConversationID: conv.ID,
			MessageID:      asstMsg.ID,
			Sources:        sourcesAcc,
		},
		out: ch,
	})

	// finish closes the message lifecycle. Caller invokes exactly
	// once after consuming the events channel.
	finish := func(content, finishReason string, usage *GatewayChatUsage) {
		// Always free the active-stream slot so a future POST /cancel
		// for the same conversation doesn't dangle.
		s.activeStreams.Delete(conv.ID)

		// Use background ctx (not streamCtx, which is cancelled on
		// disconnect) so the persist write completes even if the
		// browser dropped the connection or external cancel fired.
		persistCtx := context.Background()

		// Status precedence:
		//   userCancelled  → "cancelled"  (POST /cancel was hit)
		//   finishReason=cancelled → "cancelled"  (defensive; gateway-side)
		//   finishReason=error → "error"  (handler bubbled an error)
		//   default        → "completed"
		status := "completed"
		switch {
		case reg.isUserCancelled():
			status = "cancelled"
		case finishReason == "cancelled":
			status = "cancelled"
		case finishReason == "error":
			status = "error"
		}
		var tokInput, tokOutput *int
		if usage != nil {
			ti, to := usage.PromptTokens, usage.CompletionTokens
			tokInput, tokOutput = &ti, &to
		}
		_ = s.messages.UpdateContent(
			persistCtx, asstMsg.ID, content, status, tokInput, tokOutput,
		)
		assistantFinal := *asstMsg
		assistantFinal.ContentMasked = content
		assistantFinal.Status = status
		assistantFinal.TokenUsageInput = tokInput
		assistantFinal.TokenUsageOutput = tokOutput
		s.finalizeConversation(persistCtx, conv, userMsg, &assistantFinal)
		// S7 — persist the per-turn source attribution after the
		// assistant message has been finalised. Best-effort; does
		// nothing when sourceRepo isn't wired.
		s.persistSources(persistCtx, asstMsg.ID, in.UserID, in.TenantID, conv.ID, sourcesAcc)
	}

	return &StreamHandle{
		UserMessage:    userMsg,
		AssistantID:    asstMsg.ID,
		ConversationID: conv.ID,
		Events:         ch,
		finish:         finish,
		cancel:         cancel,
	}, nil
}

// ---------------------------------------------------------------------------
// Cancel
// ---------------------------------------------------------------------------

// AssertConversationAccess verifies the caller owns the named
// conversation. Used by the Cancel handler before invoking
// “CancelStream“ so an attacker can't terminate streams for
// conversations they don't own by guessing IDs.
func (s *MessageService) AssertConversationAccess(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) error {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrMessageConversationNotFound
		}
		return err
	}
	if conv.UserID != userID {
		return ErrMessageConversationNotFound
	}
	return nil
}

// CancelStream interrupts the in-flight streaming generation for a
// conversation, if any. Returns true iff a stream existed and was
// cancelled. Idempotent: calling on a quiet conversation returns
// false; calling twice is harmless.
//
// The actual partial-content persist happens inside the stream's
// “finish“ callback — by the time CancelStream returns the
// stream goroutine has been notified but may not have flushed yet.
// Callers that need the partial-row guarantee should re-read via
// the message repo after a short delay (the handler does this).
func (s *MessageService) CancelStream(convID uuid.UUID) bool {
	v, ok := s.activeStreams.LoadAndDelete(convID)
	if !ok {
		return false
	}
	reg := v.(*streamRegistration)
	reg.markCancelled()
	reg.cancel()
	return true
}

// ---------------------------------------------------------------------------
// Regenerate
// ---------------------------------------------------------------------------

// RegenerateInput is the typed payload for the regenerate flow.
// “MessageID“ is the assistant message the user wants redone.
type RegenerateInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	MessageID      uuid.UUID
	RequestID      string
}

// Regenerate marks the named assistant message as “regenerated“
// and opens a fresh streaming generation off the same context that
// produced it (i.e. all messages BEFORE the target). Returns a
// StreamHandle that the handler consumes identically to SendStream.
func (s *MessageService) Regenerate(ctx context.Context, in RegenerateInput) (*StreamHandle, error) {
	conv, err := s.conversations.GetByID(ctx, in.TenantID, in.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageConversationNotFound
		}
		return nil, err
	}
	if conv.UserID != in.UserID {
		return nil, ErrMessageConversationNotFound
	}
	if !s.models.ModelAllowed(conv.ModelID) {
		return nil, ErrMessageModelNotAllowed
	}

	target, err := s.messages.GetByID(ctx, in.TenantID, in.MessageID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageNotFound
		}
		return nil, err
	}
	if target.ConversationID != conv.ID {
		return nil, ErrMessageNotFound
	}
	if target.Role != "assistant" {
		return nil, ErrMessageNotAssistant
	}

	// Mark the old assistant message superseded. Status flip lives
	// in its own UPDATE so the rest of the row (token usage,
	// timestamps) survives for audit.
	if err := s.messages.UpdateStatus(ctx, target.ID, "regenerated"); err != nil {
		return nil, err
	}

	// History = every message strictly BEFORE the target. The
	// final entry in the slice is the user prompt that produced the
	// original assistant reply.
	history, err := s.messages.ListContextBeforeMessage(
		ctx, in.TenantID, conv.ID, target.CreatedAt, s.maxContext,
	)
	if err != nil {
		return nil, err
	}

	// Fabricate a SendInput-style userMsg pointer for
	// finalizeConversation's auto-title branch. Auto-title only
	// fires on conversations with the default title, so for
	// already-titled conversations this is inert.
	var userMsg *model.Message
	if len(history) > 0 {
		last := history[len(history)-1]
		userMsg = &last
	}

	return s.openAssistantStream(ctx, conv, userMsg, history, in.RequestID, in.UserID)
}

// openAssistantStream is the shared streaming-open helper used by
// SendStream's primary path and by Regenerate. Pre-creates the
// assistant placeholder row, opens the upstream stream, wires the
// registration + finish callback. Extracted so Regenerate doesn't
// duplicate SendStream's stream-plumbing code.
func (s *MessageService) openAssistantStream(
	ctx context.Context,
	conv *model.Conversation,
	userMsg *model.Message,
	history []model.Message,
	requestID string,
	userID uuid.UUID,
) (*StreamHandle, error) {
	// Regenerate inherits the predecessor user message's branch
	// metadata so the new assistant turn lives in the same group.
	var parentID *uuid.UUID
	branchIndex := 0
	if userMsg != nil {
		parentID = userMsg.ParentID
		branchIndex = userMsg.BranchIndex
	}
	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       conv.TenantID,
		UserID:         userID,
		Role:           "assistant",
		ContentMasked:  "",
		Status:         "streaming",
		ModelID:        &conv.ModelID,
		ParentID:       parentID,
		BranchIndex:    branchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, err
	}

	streamCtx, cancel := context.WithCancel(context.Background())
	// Regenerate: the query for RAG is the last user message in the
	// history (the prompt the original assistant turn was answering).
	ragQuery := lastUserContent(history)
	imageURLs := s.lastUserImageURLs(ctx, conv.TenantID, history)
	sourcesAcc := NewSourcesAccumulator()
	gwReq := s.compileAndBuildRequest(ctx, conv, userID, conv.TenantID, ragQuery, history, imageURLs, sourcesAcc)
	toolsAttached := s.attachTools(&gwReq, conv)
	callCtx := GatewayCallContext{
		TenantID:       conv.TenantID,
		UserID:         userID,
		ConversationID: conv.ID,
		RequestID:      requestID,
	}
	stream, err := s.gateway.SendChatCompletionStream(streamCtx, callCtx, gwReq)
	if err != nil {
		cancel()
		_ = s.messages.UpdateContent(ctx, asstMsg.ID, "", "error", nil, nil)
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
		}
		return nil, err
	}

	reg := &streamRegistration{cancel: cancel}
	s.activeStreams.Store(conv.ID, reg)

	ch := make(chan StreamUpdate, 16)
	go s.pumpStreamWithTools(streamPumpInput{
		initialStream: stream,
		initialReq:    gwReq,
		callCtx:       callCtx,
		streamCtx:     streamCtx,
		toolsAttached: toolsAttached,
		toolExecCtx: ToolExecContext{
			TenantID:       conv.TenantID,
			UserID:         userID,
			ConversationID: conv.ID,
			MessageID:      asstMsg.ID,
			Sources:        sourcesAcc,
		},
		out: ch,
	})

	finish := func(content, finishReason string, usage *GatewayChatUsage) {
		s.activeStreams.Delete(conv.ID)
		persistCtx := context.Background()
		status := "completed"
		switch {
		case reg.isUserCancelled():
			status = "cancelled"
		case finishReason == "cancelled":
			status = "cancelled"
		case finishReason == "error":
			status = "error"
		}
		var tokInput, tokOutput *int
		if usage != nil {
			ti, to := usage.PromptTokens, usage.CompletionTokens
			tokInput, tokOutput = &ti, &to
		}
		_ = s.messages.UpdateContent(persistCtx, asstMsg.ID, content, status, tokInput, tokOutput)
		assistantFinal := *asstMsg
		assistantFinal.ContentMasked = content
		assistantFinal.Status = status
		assistantFinal.TokenUsageInput = tokInput
		assistantFinal.TokenUsageOutput = tokOutput
		if userMsg != nil {
			s.finalizeConversation(persistCtx, conv, userMsg, &assistantFinal)
		} else {
			_ = s.conversations.UpdateTimestamp(persistCtx, conv.ID)
		}
		// S7 — persist per-turn source attribution. Same pattern
		// as SendStream's finish callback.
		s.persistSources(persistCtx, asstMsg.ID, userID, conv.TenantID, conv.ID, sourcesAcc)
	}

	return &StreamHandle{
		UserMessage:    userMsg,
		AssistantID:    asstMsg.ID,
		ConversationID: conv.ID,
		Events:         ch,
		finish:         finish,
		cancel:         cancel,
	}, nil
}

// ---------------------------------------------------------------------------
// Edit + switch-branch
// ---------------------------------------------------------------------------

var (
	ErrEditWrongRole       = errors.New("edit: only user messages can be edited")
	ErrSwitchBranchNoFork  = errors.New("switch-branch: message is not part of a fork group")
	ErrSwitchBranchMissing = errors.New("switch-branch: target branch_index does not exist")
)

// EditInput is the typed payload for an edit.
type EditInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	MessageID      uuid.UUID
	NewContent     string
	RequestID      string
}

// EditMessage replaces a user message with a new edit, recording
// the old branch via parent_id + branch_index. The new user
// message + its streamed assistant response together form a new
// branch; the old branch's messages stay in the DB with
// is_active=false so the user can switch back.
//
// Returns a StreamHandle the HTTP handler consumes identically to
// SendStream (same SSE event format).
func (s *MessageService) EditMessage(ctx context.Context, in EditInput) (*StreamHandle, error) {
	if strings.TrimSpace(in.NewContent) == "" {
		return nil, ErrMessageEmptyContent
	}

	conv, err := s.conversations.GetByID(ctx, in.TenantID, in.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageConversationNotFound
		}
		return nil, err
	}
	if conv.UserID != in.UserID {
		return nil, ErrMessageConversationNotFound
	}
	if !s.models.ModelAllowed(conv.ModelID) {
		return nil, ErrMessageModelNotAllowed
	}

	target, err := s.messages.GetByID(ctx, in.TenantID, in.MessageID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageNotFound
		}
		return nil, err
	}
	if target.ConversationID != conv.ID {
		return nil, ErrMessageNotFound
	}
	if target.Role != "user" {
		return nil, ErrEditWrongRole
	}

	// fork_root: the marker id every sibling + descendant shares.
	// If this is the first edit at this point, the target becomes
	// the fork root (self-pointed). Otherwise, the target already
	// has parent_id set and we reuse that.
	forkRoot := target.ID
	if target.ParentID != nil {
		forkRoot = *target.ParentID
	} else {
		// First-time edit: stamp parent_id on the original AND on
		// every message that came after it so they all belong to
		// "branch 0" of the new fork group.
		if err := s.messages.SetParent(ctx, target.ID, forkRoot); err != nil {
			return nil, err
		}
		if err := s.messages.StampForkLineage(
			ctx, in.TenantID, conv.ID, forkRoot, target.CreatedAt,
		); err != nil {
			return nil, err
		}
	}

	// Compute the next branch index inside this group.
	maxIdx, err := s.messages.MaxBranchIndex(ctx, in.TenantID, forkRoot)
	if err != nil {
		return nil, err
	}
	newBranchIndex := maxIdx + 1

	// Kill the tail of the currently-active branch starting at the
	// target message. Both target and every downstream active
	// message go is_active=false; the new branch becomes the live
	// one once the next inserts land.
	if err := s.messages.DeactivateChain(
		ctx, in.TenantID, conv.ID, target.CreatedAt,
	); err != nil {
		return nil, err
	}

	// Insert the new user message into the new branch.
	newUser, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "user",
		ContentMasked:  in.NewContent,
		Status:         "completed",
		ModelID:        &conv.ModelID,
		ParentID:       &forkRoot,
		BranchIndex:    newBranchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, err
	}

	// Rebuild the context for the gateway from the *active* branch
	// up through the new user message.
	history, err := s.messages.ListContextByConversation(
		ctx, in.TenantID, conv.ID, s.maxContext,
	)
	if err != nil {
		return nil, err
	}

	return s.openAssistantStreamWithBranch(
		ctx, conv, newUser, history, in.RequestID, in.UserID,
		&forkRoot, newBranchIndex,
	)
}

// openAssistantStreamWithBranch is openAssistantStream's branched
// twin — same plumbing, but the assistant placeholder gets the
// new branch's parent_id + branch_index so the active flag stays
// consistent across the whole branch.
func (s *MessageService) openAssistantStreamWithBranch(
	ctx context.Context,
	conv *model.Conversation,
	userMsg *model.Message,
	history []model.Message,
	requestID string,
	userID uuid.UUID,
	parentID *uuid.UUID,
	branchIndex int,
) (*StreamHandle, error) {
	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       conv.TenantID,
		UserID:         userID,
		Role:           "assistant",
		ContentMasked:  "",
		Status:         "streaming",
		ModelID:        &conv.ModelID,
		ParentID:       parentID,
		BranchIndex:    branchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, err
	}

	streamCtx, cancel := context.WithCancel(context.Background())
	// Edit-branch RAG query: prefer the new user message's content
	// (which is the prompt that produced this branch); fall back to
	// the tail of the history if the caller didn't pass one.
	ragQuery := ""
	if userMsg != nil {
		ragQuery = userMsg.ContentMasked
	}
	if ragQuery == "" {
		ragQuery = lastUserContent(history)
	}
	imageURLs := s.lastUserImageURLs(ctx, conv.TenantID, history)
	sourcesAcc := NewSourcesAccumulator()
	gwReq := s.compileAndBuildRequest(ctx, conv, userID, conv.TenantID, ragQuery, history, imageURLs, sourcesAcc)
	toolsAttached := s.attachTools(&gwReq, conv)
	callCtx := GatewayCallContext{
		TenantID:       conv.TenantID,
		UserID:         userID,
		ConversationID: conv.ID,
		RequestID:      requestID,
	}
	stream, err := s.gateway.SendChatCompletionStream(streamCtx, callCtx, gwReq)
	if err != nil {
		cancel()
		_ = s.messages.UpdateContent(ctx, asstMsg.ID, "", "error", nil, nil)
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %s", ErrMessageGatewayUnavailable, ue.Error())
		}
		return nil, err
	}

	reg := &streamRegistration{cancel: cancel}
	s.activeStreams.Store(conv.ID, reg)

	ch := make(chan StreamUpdate, 16)
	go s.pumpStreamWithTools(streamPumpInput{
		initialStream: stream,
		initialReq:    gwReq,
		callCtx:       callCtx,
		streamCtx:     streamCtx,
		toolsAttached: toolsAttached,
		toolExecCtx: ToolExecContext{
			TenantID:       conv.TenantID,
			UserID:         userID,
			ConversationID: conv.ID,
			MessageID:      asstMsg.ID,
			Sources:        sourcesAcc,
		},
		out: ch,
	})

	finish := func(content, finishReason string, usage *GatewayChatUsage) {
		s.activeStreams.Delete(conv.ID)
		persistCtx := context.Background()
		status := "completed"
		switch {
		case reg.isUserCancelled():
			status = "cancelled"
		case finishReason == "cancelled":
			status = "cancelled"
		case finishReason == "error":
			status = "error"
		}
		var tokInput, tokOutput *int
		if usage != nil {
			ti, to := usage.PromptTokens, usage.CompletionTokens
			tokInput, tokOutput = &ti, &to
		}
		_ = s.messages.UpdateContent(persistCtx, asstMsg.ID, content, status, tokInput, tokOutput)
		// Bump message_count + updated_at so the sidebar refreshes.
		_ = s.conversations.IncrementMessageCount(persistCtx, conv.ID, 2)
		// S7 — persist per-turn source attribution.
		s.persistSources(persistCtx, asstMsg.ID, userID, conv.TenantID, conv.ID, sourcesAcc)
	}

	return &StreamHandle{
		UserMessage:    userMsg,
		AssistantID:    asstMsg.ID,
		ConversationID: conv.ID,
		Events:         ch,
		finish:         finish,
		cancel:         cancel,
	}, nil
}

// BranchCountsByParents is a thin pass-through used by the list
// handler to populate branch_count per message.
func (s *MessageService) BranchCountsByParents(
	ctx context.Context, tenantID uuid.UUID, parentIDs []uuid.UUID,
) (map[uuid.UUID]int, error) {
	return s.messages.BranchCountByParent(ctx, tenantID, parentIDs)
}

// SwitchBranch flips the active branch within a fork group. The
// caller addresses the fork by ANY message id inside it; the
// service resolves the fork-root by reading its parent_id.
func (s *MessageService) SwitchBranch(
	ctx context.Context,
	tenantID, userID, convID, messageID uuid.UUID,
	branchIndex int,
) error {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrMessageConversationNotFound
		}
		return err
	}
	if conv.UserID != userID {
		return ErrMessageConversationNotFound
	}

	m, err := s.messages.GetByID(ctx, tenantID, messageID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrMessageNotFound
		}
		return err
	}
	if m.ConversationID != conv.ID {
		return ErrMessageNotFound
	}
	if m.ParentID == nil {
		return ErrSwitchBranchNoFork
	}

	// Validate the target branch actually exists for this group.
	maxIdx, err := s.messages.MaxBranchIndex(ctx, tenantID, *m.ParentID)
	if err != nil {
		return err
	}
	if branchIndex < 0 || branchIndex > maxIdx {
		return ErrSwitchBranchMissing
	}

	if err := s.messages.SetActiveBranch(ctx, tenantID, *m.ParentID, branchIndex); err != nil {
		return err
	}
	_ = s.conversations.UpdateTimestamp(ctx, conv.ID)
	return nil
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

// fileContext is a tiny adapter around FileLinker.BuildContextBlock
// that swallows errors. Failing to assemble the context block must
// not block the message — the user still gets an AI reply, just
// without document context.
func (s *MessageService) fileContext(ctx context.Context, tenantID, convID uuid.UUID) string {
	if s.fileSvc == nil {
		return ""
	}
	block, err := s.fileSvc.BuildContextBlock(ctx, tenantID, convID)
	if err != nil {
		return ""
	}
	return block
}

// ragContext queries the conversation's bound KB (if any) for the
// most-recent user content. Returns "" when no KB is bound, RAG
// isn't configured, or retrieval came back empty. Errors are
// swallowed for the same reason file_context does — never block the
// reply because retrieval stuttered.
func (s *MessageService) ragContext(ctx context.Context, conv *model.Conversation, query string) string {
	if s.ragSvc == nil || conv == nil || conv.KnowledgeBaseID == nil {
		return ""
	}
	q := strings.TrimSpace(query)
	if q == "" {
		return ""
	}
	return s.ragSvc.BuildContext(ctx, conv.TenantID, *conv.KnowledgeBaseID, q)
}

// buildSystemPrefix concatenates the user's custom instructions, the
// RAG-retrieved KB context, and the file-context block into a single
// system-prompt prefix. The order is deliberate:
//
//	persona  — shapes tone (must come first so RAG / file content
//	           doesn't accidentally override the "respond as X" rule)
//	RAG      — authoritative facts the model should cite
//	files    — supplementary attached documents
//	excel    — Phase 4 workbook structure + action catalog so the
//	           model knows how to emit :::excel-plan blocks
//
// Any subset may be empty. “ragQuery“ is the most-recent user
// message content used as the retrieval query; pass "" to skip RAG.
func (s *MessageService) buildSystemPrefix(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	conv *model.Conversation,
	ragQuery string,
) string {
	text, _ := s.buildSystemPrefixWithSources(ctx, tenantID, userID, conv, ragQuery)
	return text
}

// buildSystemPrefixWithSources is the S7 variant: assembles the
// same prefix as buildSystemPrefix AND returns the ContextSource
// list each block contributed (memory entries, state goal).
//
// File / RAG / Excel / Doc / Pptx blocks DO NOT emit sources yet
// — wiring them is a separate session (each context builder needs
// its own *WithSources variant). For S7 the attribution covers
// memory + state + compaction + search-tool which captures the
// primary "how did the AI know X?" stories.
func (s *MessageService) buildSystemPrefixWithSources(
	ctx context.Context,
	tenantID, userID uuid.UUID,
	conv *model.Conversation,
	ragQuery string,
) (string, []ContextSource) {
	parts := make([]string, 0, 5)
	sources := make([]ContextSource, 0, 8)

	if s.prefsSvc != nil {
		if p := s.prefsSvc.BuildInstructionsBlock(ctx, tenantID, userID); p != "" {
			parts = append(parts, p)
		}
	}
	if s.memorySvc != nil && conv != nil && conv.MemoryEnabled {
		// Policy block fires whenever memory is enabled — even for a
		// brand-new user with zero saved memories — so the model
		// always knows it should save proactively. The saved-facts
		// block (if any) is appended right after so the model sees
		// the directive first and then what's already on file.
		parts = append(parts, MemoryPolicyBlock)
		if mem, srcs := s.memorySvc.BuildMemoryContextWithSources(ctx, userID, tenantID); mem != "" {
			parts = append(parts, mem)
			sources = append(sources, srcs...)
		}
	}
	if s.stateSvc != nil && conv != nil {
		if st, srcs := s.stateSvc.BuildStateContextWithSources(ctx, conv.ID, userID, tenantID); st != "" {
			parts = append(parts, st)
			sources = append(sources, srcs...)
		}
	}
	if conv != nil {
		if rag := s.ragContext(ctx, conv, ragQuery); rag != "" {
			parts = append(parts, rag)
		}
		if f := s.fileContext(ctx, tenantID, conv.ID); f != "" {
			parts = append(parts, f)
		}
		if x := s.excelContext(ctx, tenantID, conv.ID); x != "" {
			parts = append(parts, x)
		}
		if d := s.docContext(ctx, tenantID, conv.ID); d != "" {
			parts = append(parts, d)
		}
		if p := s.pptxContext(ctx, tenantID, conv.ID); p != "" {
			parts = append(parts, p)
		}
	}
	return strings.Join(parts, "\n\n"), sources
}

// excelContext renders the Phase-4 Excel system block when the
// conversation has at least one xlsx file with a cached inspection.
// The block teaches the model the workbook structure + the
// :::excel-plan output contract; the frontend intercepts those
// fenced blocks and renders the plan card.
//
// To keep the system prompt bounded we only embed the MOST RECENT
// xlsx's inspection_json. When users iterate on a workbook the
// generated versions land as new files (is_generated=true) and the
// newest version is what the model needs to plan further edits
// against.
func (s *MessageService) excelContext(
	ctx context.Context, tenantID, convID uuid.UUID,
) string {
	if s.excelLookup == nil {
		return ""
	}
	files, err := s.excelLookup.ListByConversation(ctx, tenantID, convID)
	if err != nil || len(files) == 0 {
		return ""
	}
	// Newest-first — pick the first xlsx with a cached inspection.
	var latest *model.ConversationFile
	for i := len(files) - 1; i >= 0; i-- {
		f := &files[i]
		if !IsExcelContentType(f.ContentType) {
			continue
		}
		if f.InspectionJSON == nil || *f.InspectionJSON == "" {
			continue
		}
		latest = f
		break
	}
	if latest == nil {
		return ""
	}
	return renderExcelSystemBlock(latest)
}

// renderExcelSystemBlock builds the literal text injected at the
// end of the system prompt. Kept as a free function so unit tests
// can assert on the snapshot without booting a full MessageService.
func renderExcelSystemBlock(f *model.ConversationFile) string {
	var b strings.Builder
	b.WriteString("## Excel File Context\n\n")
	b.WriteString("You have access to an Excel workbook. Here is its complete structure:\n\n")
	b.WriteString("file_id: ")
	b.WriteString(f.ID.String())
	b.WriteString("\nfilename: ")
	b.WriteString(f.OriginalFilename)
	b.WriteString("\n\n```json\n")
	b.WriteString(*f.InspectionJSON)
	b.WriteString("\n```\n\n")
	b.WriteString(excelCapabilitiesPrompt)
	return b.String()
}

// excelCapabilitiesPrompt is the static portion of the Excel system
// block — the contract the model must follow when the user asks to
// edit or generate a workbook. Kept as a package-level constant so
// adjusting the prompt doesn't require a rebuild of the message
// service tests' fixtures.
const excelCapabilitiesPrompt = `## Your Capabilities

You can perform three types of operations:

### 1. ANALYZE — Answer questions about the data
Just respond in chat with your analysis. Reference specific sheets, cells, and formulas.

### 2. EDIT — Modify the existing workbook
When the user asks you to edit, add, modify, generate, summarize, format, sort, filter, or otherwise change the Excel file, you MUST output an edit plan in this EXACT format (the triple-colon fences are required). Output the plan immediately — do NOT ask for confirmation, do NOT say "let me know if this is okay", do NOT say "let's proceed". A plan block is your action; the user runs it by clicking Execute on the rendered card.

:::excel-plan
{
  "operation": "modify_workbook",
  "source_file_id": "<file_id from above>",
  "output_filename": "<original>_edited.xlsx",
  "description": "Brief description of what will change",
  "actions": [
    {"type": "create_sheet", "sheet_name": "Summary", "position": 0},
    {"type": "write_cells", "sheet_name": "Summary", "cells": [
      {"cell": "A1", "value": "Total Revenue", "bold": true, "font_size": 14},
      {"cell": "B1", "value_formula": "=SUM(Sales_2025!D2:D13)", "number_format": "#,##0"}
    ]},
    {"type": "format_range", "sheet_name": "Summary", "range": "A1:B1",
     "fill_color": "1F4E79", "font_color": "FFFFFF", "bold": true}
  ],
  "preserve": {
    "existing_formulas": true,
    "existing_styles": true,
    "existing_charts": true,
    "hidden_sheets": true
  }
}
:::

CRITICAL: every action MUST be a flat object with a "type" field — NEVER nest the body under the type name. Use {"type": "create_sheet", "sheet_name": "X"}, NOT {"create_sheet": {"sheet_name": "X"}}.

### 3. GENERATE — Create a new Excel file from scratch
Same format as EDIT but with "operation": "create_workbook".

## Rules — MUST follow
- For ANY edit / generate / modify / format request: output exactly one :::excel-plan ... ::: block. No exceptions, no hedging, no asking permission first.
- BEFORE the plan, write 1-2 short sentences in the user's language describing what you'll change (this is the explanation rendered above the plan card).
- AFTER the closing ::: fence, write nothing further. The plan IS the deliverable.
- NEVER output raw CSV or text tables when the user wants an Excel file.
- ALWAYS preserve existing formulas, styles, and charts unless explicitly asked to remove them.
- Use ` + "`value_formula`" + ` instead of hardcoded values when the data should be dynamic.
- Use source_file_id = the file_id printed above the workbook structure.

## Action Types Available
- create_sheet { sheet_name, position }
- delete_sheet { sheet_name }
- rename_sheet { sheet_name, new_name }
- write_cells { sheet_name, cells: [ { cell, value | value_formula, bold, font_size, number_format } ] }
- write_table { sheet_name, start_cell, headers: [...], data: [[...]] }
- create_chart { sheet_name, chart_type (bar|line|pie|column|area|scatter), title, anchor_cell, data_range, category_range, width, height }
- format_range { sheet_name, range, fill_color, font_color, bold, font_size }
- set_column_width { sheet_name, columns: { "A": 20, "B": 16 } }
- merge_cells { sheet_name, range }
- auto_filter { sheet_name, range }
- freeze_panes { sheet_name, cell }
`

// docContext renders the Phase-4 Session 2 doc system block when the
// conversation has at least one docx or pdf file with a cached
// inspection. Same shape as excelContext: the model gets the
// inspection JSON + a contract to emit :::doc-plan blocks that the
// frontend turns into the DocPlanCard.
func (s *MessageService) docContext(
	ctx context.Context, tenantID, convID uuid.UUID,
) string {
	if s.excelLookup == nil {
		return ""
	}
	files, err := s.excelLookup.ListByConversation(ctx, tenantID, convID)
	if err != nil || len(files) == 0 {
		return ""
	}
	// docx + pdf only — pptx is owned by pptxContext below so the
	// two capabilities prompts stay orthogonal (the AI gets a
	// separate :::doc-plan and :::pptx-plan contract).
	var latest *model.ConversationFile
	for i := len(files) - 1; i >= 0; i-- {
		f := &files[i]
		if !(IsDocxContentType(f.ContentType) || IsPdfContentType(f.ContentType)) {
			continue
		}
		if f.InspectionJSON == nil || *f.InspectionJSON == "" {
			continue
		}
		latest = f
		break
	}
	if latest == nil {
		// No uploaded doc — still inject the capabilities prompt so
		// the model can generate from-scratch reports when the user
		// asks ("create a PDF report about X"). Without the catalog
		// it would just write prose.
		return docCapabilitiesPromptHeaderless
	}
	return renderDocSystemBlock(latest)
}

func renderDocSystemBlock(f *model.ConversationFile) string {
	var b strings.Builder
	b.WriteString("## Document Context\n\n")
	b.WriteString("You have access to a document file. Here is its structure:\n\n")
	b.WriteString("file_id: ")
	b.WriteString(f.ID.String())
	b.WriteString("\nfilename: ")
	b.WriteString(f.OriginalFilename)
	b.WriteString("\ncontent_type: ")
	b.WriteString(f.ContentType)
	b.WriteString("\n\n```json\n")
	b.WriteString(*f.InspectionJSON)
	b.WriteString("\n```\n\n")
	b.WriteString(docCapabilitiesPrompt)
	return b.String()
}

// docCapabilitiesPrompt is the static contract injected when an
// uploaded doc is present. The headerless variant goes out when no
// doc is uploaded but the user might still ask for one.
const docCapabilitiesPrompt = `## Document Report Capabilities

When the user asks you to create, edit, or summarize a Word document (.docx) or PDF report, output a JSON plan in this EXACT format. The frontend renders the plan as a card; the user clicks Generate to execute.

:::doc-plan
{
  "format": "docx",
  "source_file_id": "<file_id from above; omit for create-from-scratch>",
  "output_filename": "report.docx",
  "document_settings": {
    "page_size": "A4",
    "orientation": "portrait",
    "margins": {"top": 1.0, "bottom": 1.0, "left": 1.0, "right": 1.0},
    "font_family": "Sarabun",
    "base_font_size": 16,
    "language": "th",
    "header_text": "SRAN AI Report",
    "footer_text": "หน้า {page} จาก {total}",
    "title_page": {
      "title": "รายงานสรุปผลประจำเดือน",
      "subtitle": "พฤษภาคม 2569",
      "author": "SRAN AI",
      "date": "27 พ.ค. 2569"
    }
  },
  "actions": [
    {"type": "add_title_page"},
    {"type": "add_heading", "level": 1, "text": "บทสรุปผู้บริหาร"},
    {"type": "add_paragraph", "text": "เนื้อหา..."},
    {"type": "add_bullet_list", "items": ["ข้อหนึ่ง", "ข้อสอง"]},
    {"type": "add_table", "headers": ["หัวข้อ", "ค่า"], "rows": [["A", "1"]], "style": "professional"}
  ]
}
:::

## Rules — MUST follow
- For ANY create/edit/generate request: output exactly one :::doc-plan ... ::: block. No hedging.
- Output the explanation in 1-2 short sentences BEFORE the fence; nothing after it.
- Detect the user's language from the prompt and the inspection metadata:
  * Thai input → language: "th", font_family: "Sarabun", base_font_size: 16.
  * English input → language: "en", font_family: "Arial", base_font_size: 12.
- "format" is "docx" or "pdf" depending on the user's request. If unclear, ask.
- When source_file_id is set and format is "docx", the generator preserves the source layout; for pdf the source is treated as reference content.
- Use professional table style (corporate navy headers) for any reporting table.

## Action Types
CREATE: add_title_page, add_heading {level, text}, add_paragraph {text, bold, italic, alignment}, add_bullet_list {items}, add_numbered_list {items}, add_table {headers, rows, style}, add_page_break, add_spacer {height}, add_horizontal_rule, add_blockquote {text}, add_code_block {code, language}
EDIT (docx only): replace_text {find, replace}, set_header {text}, set_footer {text}
`

// docCapabilitiesPromptHeaderless is what we ship when no
// document is uploaded. Skips the "Here is its structure" preamble
// since there's nothing to describe, but still gives the model the
// :::doc-plan contract so create-from-scratch works.
const docCapabilitiesPromptHeaderless = "## Document Report Capabilities\n\nNo document is currently uploaded, but if the user asks you to create a Word or PDF report you should still emit a :::doc-plan block (omit source_file_id; the generator runs in create mode).\n\n" + docCapabilitiesPrompt

// pptxContext is the Phase-4 Session-3 sibling of docContext.
// Picks the most-recent pptx with cached inspection, or injects the
// headerless capabilities prompt when no pptx is uploaded but the
// user might still ask for one.
func (s *MessageService) pptxContext(
	ctx context.Context, tenantID, convID uuid.UUID,
) string {
	if s.excelLookup == nil {
		return ""
	}
	files, err := s.excelLookup.ListByConversation(ctx, tenantID, convID)
	if err != nil || len(files) == 0 {
		return ""
	}
	var latest *model.ConversationFile
	for i := len(files) - 1; i >= 0; i-- {
		f := &files[i]
		if !IsPptxContentType(f.ContentType) {
			continue
		}
		if f.InspectionJSON == nil || *f.InspectionJSON == "" {
			continue
		}
		latest = f
		break
	}
	if latest == nil {
		return pptxCapabilitiesPromptHeaderless
	}
	return renderPptxSystemBlock(latest)
}

func renderPptxSystemBlock(f *model.ConversationFile) string {
	var b strings.Builder
	b.WriteString("## Presentation Context\n\n")
	b.WriteString("You have access to a PowerPoint file. Here is its structure:\n\n")
	b.WriteString("file_id: ")
	b.WriteString(f.ID.String())
	b.WriteString("\nfilename: ")
	b.WriteString(f.OriginalFilename)
	b.WriteString("\ncontent_type: ")
	b.WriteString(f.ContentType)
	b.WriteString("\n\n```json\n")
	b.WriteString(*f.InspectionJSON)
	b.WriteString("\n```\n\n")
	b.WriteString(pptxCapabilitiesPrompt)
	return b.String()
}

// pptxCapabilitiesPrompt teaches the AI to emit :::pptx-plan blocks
// against the 9-slide-type generator catalog. Same MUST/structure
// rules as the doc prompt so the LLM's behaviour stays consistent
// across the three formats.
const pptxCapabilitiesPrompt = `## Presentation Generation Capabilities

When the user asks you to create, edit, or summarize a PowerPoint presentation / slide deck, output a JSON plan in this EXACT format. The frontend renders the plan as a card; the user clicks Generate to execute.

:::pptx-plan
{
  "format": "pptx",
  "source_file_id": "<file_id from above; omit for create-from-scratch>",
  "output_filename": "presentation.pptx",
  "presentation_settings": {
    "title": "Monthly Report",
    "author": "SRAN AI",
    "layout": "16:9",
    "language": "th",
    "color_scheme": {
      "primary": "0A1230", "secondary": "0F1835", "accent": "2E75B6",
      "light_bg": "F6F7F9", "text_dark": "1A1A2E", "text_light": "FFFFFF"
    },
    "font": {"heading": "Sarabun", "body": "Sarabun"}
  },
  "slides": [
    {"type": "title", "title": "รายงานสรุปผลประจำเดือน", "subtitle": "พฤษภาคม 2569", "author": "SRAN AI"},
    {"type": "section_divider", "title": "บทสรุปผู้บริหาร"},
    {"type": "content", "title": "ภาพรวม", "body": "...", "bullets": ["...","..."], "speaker_notes": "..."},
    {"type": "two_column", "title": "เปรียบเทียบ", "left": {"heading": "Q1", "bullets": ["..."]}, "right": {"heading": "Q2", "bullets": ["..."]}},
    {"type": "stats", "title": "ตัวเลขสำคัญ", "stats": [{"value": "5,200", "label": "รายการ", "color": "2E75B6"}, {"value": "98.5%", "label": "พึงพอใจ", "color": "10B981"}]},
    {"type": "table", "title": "ตารางสรุป", "headers": ["เดือน","ยอดขาย"], "rows": [["ม.ค.","1,200"]]},
    {"type": "chart", "title": "แนวโน้ม", "chart_type": "bar", "data": {"categories": ["ม.ค.","ก.พ."], "series": [{"name": "ยอดขาย", "values": [1200, 1350]}]}},
    {"type": "closing", "title": "ขอบคุณครับ", "subtitle": "team@sran.ai"}
  ]
}
:::

## Rules — MUST follow
- For ANY presentation request: output exactly one :::pptx-plan ... ::: block. No hedging.
- Output the explanation in 1-2 short sentences BEFORE the fence; nothing after it.
- Mix slide types — never just a stack of content/bullet slides. Use stats for KPIs, chart for trends, table for data, two_column for comparisons, section_divider between major topics.
- Always start with a "title" slide and end with a "closing" slide.
- Detect language from the prompt:
  * Thai input → language: "th", font Sarabun.
  * English input → language: "en", font Calibri.
- Keep bullets short (under 10 words each); 6 bullets max per slide.
- 8-12 slides is the sweet spot for most topics.
- "chart_type" is one of: bar, column, line, pie, doughnut, area.

## Slide Types
- title { title, subtitle, author }          — dark cover slide
- section_divider { title }                  — dark section header
- content { title, body, bullets, speaker_notes } — standard content slide
- two_column { title, left:{heading,bullets}, right:{heading,bullets} }
- stats { title, stats:[{value,label,color}] } — 2-4 KPI cards
- table { title, headers, rows }             — professional data table
- chart { title, chart_type, data:{categories, series:[{name,values}]} }
- content_with_image { title, body, bullets, image_placeholder } — content + image note
- closing { title, subtitle }                — dark thank-you slide
`

const pptxCapabilitiesPromptHeaderless = "## Presentation Generation Capabilities\n\nNo presentation is currently uploaded, but if the user asks you to create a slide deck you should still emit a :::pptx-plan block (omit source_file_id; the generator runs in create mode).\n\n" + pptxCapabilitiesPrompt

// keep “time“ import referenced even when no other code in this
// file uses it directly (the GatewayChatUsage struct references
// it transitively through the gateway client).
var _ time.Duration

// prepareSend runs the gating checks shared by stream + non-stream:
// validate input, verify conversation ownership, persist the user
// message, build the gateway-context history.
func (s *MessageService) prepareSend(ctx context.Context, in SendInput) (
	*model.Conversation, *model.Message, []model.Message, error,
) {
	if strings.TrimSpace(in.Content) == "" {
		return nil, nil, nil, ErrMessageEmptyContent
	}
	conv, err := s.conversations.GetByID(ctx, in.TenantID, in.ConversationID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, nil, nil, ErrMessageConversationNotFound
		}
		return nil, nil, nil, err
	}
	if conv.UserID != in.UserID {
		return nil, nil, nil, ErrMessageConversationNotFound
	}
	// Model-allowed gate. The conversation's model_id could
	// have been recorded BEFORE an admin removed that model from
	// the allow-list; we treat a now-disallowed model as a
	// hard reject so the user can't keep using it.
	if !s.models.ModelAllowed(conv.ModelID) {
		return nil, nil, nil, ErrMessageModelNotAllowed
	}

	// If the conversation already has a fork group, new messages
	// inherit the currently-active branch so they stay on the same
	// side of the tree.
	var parentID *uuid.UUID
	branchIndex := 0
	if recent, err := s.messages.ListContextByConversation(ctx, in.TenantID, conv.ID, 1); err == nil && len(recent) > 0 {
		latest := recent[len(recent)-1]
		if latest.ParentID != nil {
			parentID = latest.ParentID
			branchIndex = latest.BranchIndex
		}
	}

	userMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "user",
		ContentMasked:  in.Content,
		Status:         "completed",
		ModelID:        &conv.ModelID,
		ParentID:       parentID,
		BranchIndex:    branchIndex,
		IsActive:       true,
	})
	if err != nil {
		return nil, nil, nil, err
	}

	// Link any attached files to the new user message. Non-fatal:
	// if the linker isn't wired (tests) or the SQL fails, the user
	// message persists and the file just doesn't show up in the
	// history response. Logged so an operator can chase it down.
	if s.fileSvc != nil && len(in.FileIDs) > 0 {
		if _, err := s.fileSvc.AttachToMessage(
			ctx, in.TenantID, conv.ID, userMsg.ID, in.FileIDs,
		); err != nil {
			// Don't return — the user's prompt is already saved and
			// failing the whole send because the attach query
			// stuttered would be a worse outcome than missing
			// metadata.
			_ = err
		}
	}

	// History includes the just-saved user message — that becomes
	// the final "user" turn in the gateway request.
	history, err := s.messages.ListContextByConversation(
		ctx, in.TenantID, conv.ID, s.maxContext,
	)
	if err != nil {
		return nil, nil, nil, err
	}

	return conv, userMsg, history, nil
}

// finalizeConversation runs the post-message housekeeping:
//   - bump message_count by 2 (user + assistant)
//   - auto-title from the first user message if the conversation
//     is still on its default title
//   - S3: trigger async conversation-state extraction when the
//     message count crosses one of the thresholds (10 / +20)
func (s *MessageService) finalizeConversation(
	ctx context.Context,
	conv *model.Conversation,
	userMsg, asstMsg *model.Message,
) {
	_ = s.conversations.IncrementMessageCount(ctx, conv.ID, 2)

	// Auto-title: triggered on the FIRST user/assistant pair.
	// message_count before this call was 0 → 2 after.
	if conv.MessageCount == 0 && conv.Title == "New conversation" {
		title := makeTitleFrom(userMsg.ContentMasked)
		_ = s.conversations.UpdateTitle(ctx, conv.TenantID, conv.ID, title)
	}

	// S3 — kick the state-extraction trigger. Runs async so the
	// user's response isn't blocked on the gateway extraction call.
	s.maybeUpdateConversationState(ctx, conv)
	// S8 — kick the preference-pattern extractor on the same path.
	// Skipped when memory is off (temporary chats) and capped to
	// once per conversation lifetime.
	s.maybeExtractPreferences(ctx, conv)
}

// State-extraction triggers.
//
// firstExtractionAtMessages: conversations under this size still
// fit in the rolling window; no need to spend a gateway call yet.
//
// extractEveryNMessages: once a state row exists, re-extract every
// time this many new messages accumulate. Bigger = cheaper but
// staler; smaller = fresher but more $$.
const (
	firstExtractionAtMessages = 10
	extractEveryNMessages     = 20
)

// maybeUpdateConversationState decides whether the conversation
// has accumulated enough new messages to warrant another
// extraction pass and, if so, fires it on a background goroutine.
//
// Disabled when:
//   - the state service isn't wired (tests, legacy builds)
//   - conv.MemoryEnabled is false (temporary chat — the user
//     opted out of persisted state)
//
// Read uses background ctx so the trigger survives the request
// ctx cancellation that fires on stream completion.
func (s *MessageService) maybeUpdateConversationState(
	parent context.Context, conv *model.Conversation,
) {
	if s.stateSvc == nil || conv == nil || !conv.MemoryEnabled {
		return
	}

	// Use the parent ctx for the cheap "should we run?" checks so a
	// shutdown still cancels us cleanly. The actual extraction goroutine
	// gets context.Background() because we want it to complete after
	// the request ends.
	count, err := s.messages.CountMessages(parent, conv.TenantID, conv.ID)
	if err != nil {
		return
	}
	existing, _ := s.stateSvc.Get(parent, conv.ID, conv.UserID, conv.TenantID)

	run := false
	if existing == nil && count >= firstExtractionAtMessages {
		run = true
	} else if existing != nil && count-existing.MessageCountAtSummary >= extractEveryNMessages {
		run = true
	}
	if !run {
		return
	}

	convID, userID, tenantID := conv.ID, conv.UserID, conv.TenantID
	go func() {
		bg := context.Background()
		if _, err := s.stateSvc.GenerateOrUpdateState(bg, convID, userID, tenantID); err != nil {
			slog.Warn("conversation state: auto-extraction failed",
				"conv", convID.String(), "err", err)
		}
	}()
}

// preferenceExtractAtMessages — minimum conversation size before
// the S8 background pattern learner runs. Higher than the S3
// state trigger (10) because preference patterns need MORE signal
// to surface reliably; one short conversation rarely shows a
// behaviour.
const preferenceExtractAtMessages = 20

// maybeExtractPreferences fires the S8 PreferenceExtractor at most
// ONCE per conversation, on a background goroutine, when:
//   - the extractor is wired
//   - the conv has memory enabled (temp chats opt out — they
//     mustn't surface suggestions either)
//   - the conv has crossed preferenceExtractAtMessages messages
//   - no inferred_preference row from THIS conv exists yet (dedup
//     check via the source_conversation_id filter)
//
// Same async-on-background-ctx pattern as
// maybeUpdateConversationState so the user's response is never
// blocked on the extraction call.
func (s *MessageService) maybeExtractPreferences(
	parent context.Context, conv *model.Conversation,
) {
	if s.prefExtractor == nil || conv == nil || !conv.MemoryEnabled {
		return
	}

	count, err := s.messages.CountMessages(parent, conv.TenantID, conv.ID)
	if err != nil {
		return
	}
	if count < preferenceExtractAtMessages {
		return
	}

	// Only extract once per conversation. We use the memory service's
	// list path with the source_conversation_id field on a single
	// scan — cheaper than a dedicated count query for a row that
	// realistically has at most a handful of suggestions.
	existing, err := s.prefExtractor.memoryRepo.ListByUser(
		parent, conv.UserID, conv.TenantID, repository.MemoryListFilters{
			MemoryType: model.MemoryTypeInferredPreference,
			Status:     model.MemoryStatusPendingConfirmation,
			Limit:      100,
		},
	)
	if err == nil {
		for i := range existing {
			if existing[i].SourceConversationID != nil && *existing[i].SourceConversationID == conv.ID {
				return // already extracted from this conv
			}
		}
	}

	convID, userID, tenantID := conv.ID, conv.UserID, conv.TenantID
	go func() {
		bg := context.Background()
		if _, err := s.prefExtractor.ExtractPreferences(bg, convID, userID, tenantID); err != nil {
			slog.Warn("preference extraction failed",
				"conv", convID.String(), "err", err)
		}
	}()
}

// persistError saves a placeholder assistant row with status=error
// so the conversation timeline reflects the failed turn. The
// non-stream path uses this when the gateway call itself fails
// (before any content arrived).
func (s *MessageService) persistError(
	ctx context.Context,
	in SendInput,
	conv *model.Conversation,
	content string,
) error {
	_, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "assistant",
		ContentMasked:  content,
		Status:         "error",
		ModelID:        &conv.ModelID,
		IsActive:       true,
	})
	return err
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

// compileAndBuildRequest is the S4 integration shim. Every send
// path (Send / SendStream / Regenerate / Edit) used to call
// buildSystemPrefix + buildGatewayRequestWithImages back-to-back;
// they now call this instead so the compiler can trim the history
// to the model's working budget before the request goes out.
//
// When the compiler isn't wired (legacy / test setup), falls back
// to the original behaviour: prefix + history shipped verbatim.
// numTools is the count of GatewayTool entries the caller plans
// to attach AFTER this call — used by the compiler's budget math
// to reserve room for tool-definition payload.
func (s *MessageService) compileAndBuildRequest(
	ctx context.Context,
	conv *model.Conversation,
	userID uuid.UUID,
	tenantID uuid.UUID,
	ragQuery string,
	history []model.Message,
	imageURLs []string,
	sources *SourcesAccumulator,
) GatewayChatRequest {
	numTools := 0
	if s.toolExec != nil {
		numTools = len(s.toolExec.ToolDefs(conv))
	}
	// Auto-model resolution. The conversation row stores the user's
	// preference verbatim (so "auto" persists across turns); the
	// concrete model id only materialises at request time. This
	// keeps the heuristic responsive to the CURRENT turn's signals
	// (images / files / message length / code markers) rather than
	// being locked in at conversation create.
	modelID := s.resolveAutoIfNeeded(ctx, conv, tenantID, ragQuery, imageURLs, len(history))

	// Source-aware closure: the same call site can run multiple
	// times inside Compile (initial + post-extraction rebuild),
	// so the closure returns both the prefix text AND the
	// ContextSource list each block contributed. Compile uses the
	// MOST RECENT call's sources via CompiledContext.Sources.
	build := func() (string, []ContextSource) {
		return s.buildSystemPrefixWithSources(ctx, tenantID, userID, conv, ragQuery)
	}

	if s.compiler == nil {
		// Legacy fallback: no compiler wired. Still collect S7
		// sources from the single prefix build so attribution
		// works in test setups.
		prefix, srcs := build()
		sources.Add(srcs...)
		return buildGatewayRequestWithImages(modelID, history, prefix, imageURLs)
	}

	compiled, err := s.compiler.Compile(ctx, CompileInputs{
		Conv:        conv,
		UserID:      userID,
		TenantID:    tenantID,
		History:     history,
		ImageURLs:   imageURLs,
		BuildPrefix: build,
		NumTools:    numTools,
	})
	if err != nil || compiled == nil {
		// Defensive fallback — should never happen since Compile
		// doesn't return an error today, but the contract is
		// "always send something rather than fail the turn".
		slog.Warn("context_compiler: fell back to legacy assembly",
			"conv", conv.ID.String(), "err", err)
		prefix, srcs := build()
		sources.Add(srcs...)
		return buildGatewayRequestWithImages(modelID, history, prefix, imageURLs)
	}
	// S7 — flush the compiler's sources (memory + state +
	// compaction marker) into the per-turn accumulator so the
	// finish() callback can persist them alongside any search-
	// tool hits the model adds during the turn.
	sources.Add(compiled.Sources...)
	return buildGatewayRequestWithImages(modelID, compiled.History, compiled.SystemPrefix, imageURLs)
}

// resolveAutoIfNeeded returns the model id to ship to the gateway
// this turn. Pass-through for explicit picks; runs the heuristic
// ResolveAutoModel for the "auto" sentinel (and treats empty as
// auto so a migration row without a model_id still resolves).
//
// Per-turn signals used by the resolver:
//   - hasImages: the LAST user turn carries at least one image.
//   - hasFiles : the conversation has any extracted file context.
//   - len(message) / history length: complexity proxies.
//
// Logs the decision once per turn so an operator reading the slog
// stream can always reconstruct WHY a given turn was upgraded to
// flagship (or stayed fast).
func (s *MessageService) resolveAutoIfNeeded(
	ctx context.Context,
	conv *model.Conversation,
	tenantID uuid.UUID,
	message string,
	imageURLs []string,
	historyLen int,
) string {
	if !IsAutoModel(conv.ModelID) {
		return conv.ModelID
	}
	hasFiles := strings.TrimSpace(s.fileContext(ctx, tenantID, conv.ID)) != ""
	hasImages := len(imageURLs) > 0
	available := []string{}
	if s.models != nil {
		available = s.models.ModelIDs()
	}
	resolved := ResolveAutoModel(message, hasFiles, hasImages, historyLen, available)
	slog.Info("auto_model_resolved",
		"conversation_id", conv.ID.String(),
		"resolved_model", resolved,
		"message_length", len(message),
		"has_files", hasFiles,
		"has_images", hasImages,
		"message_count", historyLen,
	)
	return resolved
}

// InspectContext is the public dry-run entry point — runs the
// compiler against the current state of a conversation without
// actually sending a request. Backs GET /conversations/{id}/context
// -info so the frontend (or a curl from the operator) can see
// the budget breakdown before chatting.
//
// Returns nil + ErrMessageConversationNotFound when the caller
// doesn't own the conversation; tenant + user are pulled from
// the supplied IDs so the handler does ownership verification
// before calling this.
func (s *MessageService) InspectContext(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (*CompiledContext, error) {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrMessageConversationNotFound
		}
		return nil, err
	}
	if conv.UserID != userID {
		return nil, ErrMessageConversationNotFound
	}
	history, err := s.messages.ListContextByConversation(ctx, tenantID, convID, s.maxContext)
	if err != nil {
		return nil, err
	}
	imageURLs := s.lastUserImageURLs(ctx, tenantID, history)
	ragQuery := lastUserContent(history)
	numTools := 0
	if s.toolExec != nil {
		numTools = len(s.toolExec.ToolDefs(conv))
	}
	if s.compiler == nil {
		// Synthesize a CompiledContext so callers see the same
		// shape they'd get with the compiler wired — just with no
		// compaction ever applied.
		prefix := s.buildSystemPrefix(ctx, tenantID, userID, conv, ragQuery)
		budget := CalculateBudget(conv.ModelID, EstimateTokens(prefix)+4, 0, 0, 0, numTools*perToolTokens)
		return &CompiledContext{
			SystemPrefix:  prefix,
			History:       history,
			Budget:        budget,
			MessagesTotal: len(history),
			MessagesSent:  len(history),
		}, nil
	}
	return s.compiler.Compile(ctx, CompileInputs{
		Conv:     conv,
		UserID:   userID,
		TenantID: tenantID,
		History:  history,
		ImageURLs: imageURLs,
		BuildPrefix: func() (string, []ContextSource) {
			return s.buildSystemPrefixWithSources(ctx, tenantID, userID, conv, ragQuery)
		},
		NumTools: numTools,
	})
}

func buildGatewayRequest(model string, history []model.Message, systemPrefix string) GatewayChatRequest {
	return buildGatewayRequestWithImages(model, history, systemPrefix, nil)
}

// buildGatewayRequestWithImages is the vision-aware variant.
// When `lastUserImageURLs` is non-empty, the LAST user-role
// message in the history is rendered as a multimodal content
// array: one image_url block per data URL followed by a single
// text block carrying the user's prompt. All other messages
// (system, prior turns, assistant replies) ship as plain strings.
//
// Only the last user turn gets vision blocks because:
//   1. Vision tokens cost per image. Re-sending every historical
//      image on every turn would balloon the bill.
//   2. The user is asking about the CURRENT turn's images. The
//      model only needs to "see" what's in front of it.
func buildGatewayRequestWithImages(
	model string, history []model.Message, systemPrefix string, lastUserImageURLs []string,
) GatewayChatRequest {
	msgs := make([]GatewayMessage, 0, len(history)+1)
	if strings.TrimSpace(systemPrefix) != "" {
		msgs = append(msgs, GatewayMessage{
			Role:    "system",
			Content: systemPrefix,
		})
	}

	// Pre-compute the index of the last user-role entry — that's
	// the slot we decorate with image blocks (if any).
	lastUserIdx := -1
	if len(lastUserImageURLs) > 0 {
		for i := len(history) - 1; i >= 0; i-- {
			if history[i].Role == "user" {
				lastUserIdx = i
				break
			}
		}
	}

	for i, m := range history {
		var content any = m.ContentMasked
		if i == lastUserIdx {
			blocks := make([]GatewayContentBlock, 0, len(lastUserImageURLs)+1)
			for _, url := range lastUserImageURLs {
				blocks = append(blocks, GatewayContentBlock{
					Type: "image_url",
					ImageURL: &GatewayImageURL{
						URL:    url,
						Detail: "auto",
					},
				})
			}
			text := m.ContentMasked
			if strings.TrimSpace(text) == "" {
				text = "What's in this image?"
			}
			blocks = append(blocks, GatewayContentBlock{
				Type: "text",
				Text: text,
			})
			content = blocks
		}
		msgs = append(msgs, GatewayMessage{Role: m.Role, Content: content})
	}
	return GatewayChatRequest{Model: model, Messages: msgs}
}

func gatewayCallContext(in SendInput, conv *model.Conversation) GatewayCallContext {
	return GatewayCallContext{
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		ConversationID: conv.ID,
		RequestID:      in.RequestID,
	}
}

// lastUserContent walks the history slice tail-first looking for the
// last "user" turn. Used as the RAG query for regenerate / edit
// flows where the in-flight prompt isn't on a SendInput.
func lastUserContent(history []model.Message) string {
	for i := len(history) - 1; i >= 0; i-- {
		if history[i].Role == "user" {
			return history[i].ContentMasked
		}
	}
	return ""
}

// lastUserImageURLs resolves the image attachments on the latest
// user-role message in `history` to a slice of base64 data URLs.
// Returns nil (not an empty slice) when there are no images or
// the file linker isn't wired — callers treat nil as "ship text
// only" and skip the vision-format content array entirely.
func (s *MessageService) lastUserImageURLs(
	ctx context.Context, tenantID uuid.UUID, history []model.Message,
) []string {
	if s.fileSvc == nil {
		return nil
	}
	for i := len(history) - 1; i >= 0; i-- {
		if history[i].Role != "user" {
			continue
		}
		urls := s.fileSvc.ImageDataURLsForMessage(ctx, tenantID, history[i].ID)
		if len(urls) == 0 {
			return nil
		}
		return urls
	}
	return nil
}

// extractContent pulls the assistant text from the non-stream
// gateway response. Defensive against empty / missing fields.
// Content is `any` to accommodate vision-format user messages,
// but assistant replies are always strings — assert and fall
// back to empty when the gateway ever surprises us.
func extractContent(r *GatewayChatResponse) string {
	if r == nil || len(r.Choices) == 0 {
		return ""
	}
	if s, ok := r.Choices[0].Message.Content.(string); ok {
		return s
	}
	return ""
}

// makeTitleFrom condenses the first user message into a 100-char
// title (rune-counted so we don't truncate mid-codepoint on Thai
// input). Appends "…" when truncation occurred.
func makeTitleFrom(content string) string {
	content = strings.TrimSpace(content)
	const maxRunes = 100
	runes := []rune(content)
	if len(runes) <= maxRunes {
		return content
	}
	return string(runes[:maxRunes]) + "…"
}

// ---------------------------------------------------------------------------
// Tool calling (S1 — memory)
// ---------------------------------------------------------------------------

// maxToolIterations caps the tool-call loop so a model that keeps
// asking for tools can't run away. After this many rounds the loop
// closes the stream and lets the model's last partial text (if any)
// stand as the final answer.
const maxToolIterations = 5

// attachTools mutates the request to expose the per-conversation
// tool set. The executor's ToolDefs(conv) already gates each tool
// by what's appropriate for THIS conv: memory-write tools require
// conv.MemoryEnabled, the S6 read-only search tool stays
// available in temporary chats too.
//
// Returns true when at least one tool was attached (caller uses
// this to decide whether to enter the tool-loop accumulator —
// no tools → no tool_calls to worry about).
func (s *MessageService) attachTools(req *GatewayChatRequest, conv *model.Conversation) bool {
	if s.toolExec == nil {
		return false
	}
	defs := s.toolExec.ToolDefs(conv)
	if len(defs) == 0 {
		return false
	}
	req.Tools = append(req.Tools, defs...)
	// tool_choice="auto" lets the model decide whether to call a
	// tool — most turns won't, and forcing "required" would make
	// the model invent tool calls for vanilla chat.
	req.ToolChoice = "auto"
	return true
}

// accumulateToolCallDeltas merges a partial tool_calls delta into
// the in-flight accumulator slice, growing as needed. OpenAI ships
// tool calls in stream as a sparse sequence of (index, partial)
// updates; this normalises them into a flat slice the executor can
// dispatch against.
func accumulateToolCallDeltas(acc []GatewayToolCall, deltas []StreamToolCallDelta) []GatewayToolCall {
	for _, d := range deltas {
		for len(acc) <= d.Index {
			acc = append(acc, GatewayToolCall{Type: "function"})
		}
		t := &acc[d.Index]
		if d.ID != "" {
			t.ID = d.ID
		}
		if d.Type != "" {
			t.Type = d.Type
		}
		if d.FunctionName != "" {
			t.Function.Name = d.FunctionName
		}
		if d.FunctionArgsFrag != "" {
			t.Function.Arguments += d.FunctionArgsFrag
		}
	}
	return acc
}

// streamPumpInput bundles the parameters pumpStreamWithTools needs.
// Keeping them in a struct keeps the call site at Send/Regenerate/
// Edit readable (each only differs in callCtx + toolExecCtx).
type streamPumpInput struct {
	initialStream *StreamReader
	initialReq    GatewayChatRequest
	callCtx       GatewayCallContext
	streamCtx     context.Context
	toolsAttached bool
	toolExecCtx   ToolExecContext
	out           chan<- StreamUpdate
}

// pumpStreamWithTools reads chunks from the initial gateway stream,
// forwards content deltas to `out`, and on a tool_calls finish:
//
//   1. Accumulates the streamed tool_call deltas into a flat slice.
//   2. Executes each tool via the executor, capturing result text.
//   3. Appends (assistant tool_calls msg, tool result msgs) to the
//      running request's Messages slice.
//   4. Opens a follow-up gateway stream with the augmented request
//      and continues forwarding deltas.
//
// Loops until either a non-tool finish (forwards Done + exits),
// maxToolIterations is hit (emits Done + exits — the model's last
// partial text stands as the final answer), or an error / EOF.
//
// The caller's `finish` callback handles persistence — pump only
// feeds the events channel and closes it when no more streams.
func (s *MessageService) pumpStreamWithTools(in streamPumpInput) {
	defer close(in.out)

	current := in.initialStream
	req := in.initialReq
	var lastUsage *GatewayChatUsage

	for outerIter := 0; outerIter < maxToolIterations; outerIter++ {
		var acc []GatewayToolCall
		finishReason := ""
		isToolCallStream := false

		for {
			ev, err := current.Next()
			if errors.Is(err, io.EOF) {
				break
			}
			if err != nil {
				in.out <- StreamUpdate{Err: err}
				_ = current.Close()
				return
			}
			if ev.Usage != nil {
				lastUsage = ev.Usage
			}
			if len(ev.ToolCallDeltas) > 0 {
				acc = accumulateToolCallDeltas(acc, ev.ToolCallDeltas)
			}
			if ev.FinishReason != "" {
				finishReason = ev.FinishReason
			}
			if ev.Delta != "" {
				in.out <- StreamUpdate{
					Delta:        ev.Delta,
					Usage:        ev.Usage,
					FinishReason: ev.FinishReason,
				}
			}
			if ev.Done {
				// finish_reason="tool_calls" means another round.
				// Anything else (stop / length / cancelled) ends.
				if finishReason == "tool_calls" && in.toolsAttached && len(acc) > 0 {
					isToolCallStream = true
					break
				}
				in.out <- StreamUpdate{Done: true, Usage: ev.Usage}
				_ = current.Close()
				return
			}
		}
		_ = current.Close()

		if !isToolCallStream {
			// Stream ended without a Done event (EOF path) — emit a
			// synthetic Done so the handler's terminator fires.
			in.out <- StreamUpdate{Done: true, Usage: lastUsage}
			return
		}

		// Dispatch tools + extend the request for the next round.
		asstToolMsg, results := s.executeToolCalls(in.streamCtx, acc, in.toolExecCtx)
		req.Messages = append(req.Messages, asstToolMsg)
		req.Messages = append(req.Messages, results...)

		next, err := s.gateway.SendChatCompletionStream(in.streamCtx, in.callCtx, req)
		if err != nil {
			in.out <- StreamUpdate{Err: err}
			return
		}
		current = next
	}

	// Hit the iteration cap — close the current stream and emit Done.
	_ = current.Close()
	in.out <- StreamUpdate{Done: true, Usage: lastUsage}
}

// hasToolCalls reports whether a non-stream gateway response wants
// us to dispatch tools. Defensive against nil + empty choices so a
// surprise upstream shape doesn't panic the message flow.
func hasToolCalls(r *GatewayChatResponse) bool {
	if r == nil || len(r.Choices) == 0 {
		return false
	}
	return len(r.Choices[0].Message.ToolCalls) > 0
}

// toolCallsOf is the matching extractor — returns nil-safe slice.
func toolCallsOf(r *GatewayChatResponse) []GatewayToolCall {
	if !hasToolCalls(r) {
		return nil
	}
	return r.Choices[0].Message.ToolCalls
}

// executeToolCalls runs every accumulated tool call against the
// executor and returns (assistantToolMsg, []toolResultMsgs).
//
// assistantToolMsg is the synthetic assistant message that carries
// the tool_calls — appended to the running history so the model's
// next turn has the call/response pair available.
//
// toolResultMsgs is one role="tool" message per tool call, in
// the order the model emitted them.
func (s *MessageService) executeToolCalls(
	ctx context.Context, toolCalls []GatewayToolCall, ec ToolExecContext,
) (GatewayMessage, []GatewayMessage) {
	asstMsg := GatewayMessage{
		Role:      "assistant",
		Content:   "",
		ToolCalls: toolCalls,
	}
	results := make([]GatewayMessage, 0, len(toolCalls))
	for _, tc := range toolCalls {
		out := s.toolExec.Execute(ctx, tc, ec)
		results = append(results, GatewayMessage{
			Role:       "tool",
			ToolCallID: tc.ID,
			Name:       tc.Function.Name,
			Content:    out,
		})
	}
	return asstMsg, results
}
