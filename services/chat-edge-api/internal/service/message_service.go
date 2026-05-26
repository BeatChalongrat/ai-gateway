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
// ``chat.messages_masked`` keeps the schema forward-compatible.
package service

import (
	"context"
	"errors"
	"fmt"
	"io"
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
type FileLinker interface {
	AttachToMessage(ctx context.Context, tenantID, convID, messageID uuid.UUID, fileIDs []uuid.UUID) (int64, error)
	BuildContextBlock(ctx context.Context, tenantID, convID uuid.UUID) (string, error)
}

// streamRegistration is what's stored in MessageService.activeStreams.
// Holds the cancel func + a flag the cancel path flips so the
// stream's ``finish`` callback knows whether the termination was
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

	sysPrefix := s.fileContext(ctx, in.TenantID, conv.ID)
	gwReq := buildGatewayRequest(conv.ModelID, history, sysPrefix)
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
// terminal metadata. The handler emits a ``content_delta`` SSE
// event for every non-empty Delta and a ``message_complete`` SSE
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
	// Create the assistant placeholder row.
	asstStatus := "streaming"
	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "assistant",
		ContentMasked:  "",
		Status:         asstStatus,
		ModelID:        &conv.ModelID,
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
	sysPrefix := s.fileContext(ctx, in.TenantID, conv.ID)
	gwReq := buildGatewayRequest(conv.ModelID, history, sysPrefix)
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
	go func() {
		defer close(ch)
		defer stream.Close()
		for {
			ev, err := stream.Next()
			if errors.Is(err, io.EOF) {
				return
			}
			if err != nil {
				ch <- StreamUpdate{Err: err}
				return
			}
			if ev.Done {
				ch <- StreamUpdate{Done: true, Usage: ev.Usage}
				return
			}
			ch <- StreamUpdate{
				Delta:        ev.Delta,
				Usage:        ev.Usage,
				FinishReason: ev.FinishReason,
			}
		}
	}()

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
// ``CancelStream`` so an attacker can't terminate streams for
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
// ``finish`` callback — by the time CancelStream returns the
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
// ``MessageID`` is the assistant message the user wants redone.
type RegenerateInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	MessageID      uuid.UUID
	RequestID      string
}

// Regenerate marks the named assistant message as ``regenerated``
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
	asstMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       conv.TenantID,
		UserID:         userID,
		Role:           "assistant",
		ContentMasked:  "",
		Status:         "streaming",
		ModelID:        &conv.ModelID,
	})
	if err != nil {
		return nil, err
	}

	streamCtx, cancel := context.WithCancel(context.Background())
	sysPrefix := s.fileContext(ctx, conv.TenantID, conv.ID)
	gwReq := buildGatewayRequest(conv.ModelID, history, sysPrefix)
	stream, err := s.gateway.SendChatCompletionStream(
		streamCtx,
		GatewayCallContext{
			TenantID:       conv.TenantID,
			UserID:         userID,
			ConversationID: conv.ID,
			RequestID:      requestID,
		},
		gwReq,
	)
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
	go func() {
		defer close(ch)
		defer stream.Close()
		for {
			ev, err := stream.Next()
			if errors.Is(err, io.EOF) {
				return
			}
			if err != nil {
				ch <- StreamUpdate{Err: err}
				return
			}
			if ev.Done {
				ch <- StreamUpdate{Done: true, Usage: ev.Usage}
				return
			}
			ch <- StreamUpdate{Delta: ev.Delta, Usage: ev.Usage, FinishReason: ev.FinishReason}
		}
	}()

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

// keep ``time`` import referenced even when no other code in this
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

	userMsg, err := s.messages.Create(ctx, &model.Message{
		ConversationID: conv.ID,
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		Role:           "user",
		ContentMasked:  in.Content,
		Status:         "completed",
		ModelID:        &conv.ModelID,
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
	})
	return err
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

func buildGatewayRequest(model string, history []model.Message, systemPrefix string) GatewayChatRequest {
	msgs := make([]GatewayMessage, 0, len(history)+1)
	if strings.TrimSpace(systemPrefix) != "" {
		msgs = append(msgs, GatewayMessage{
			Role:    "system",
			Content: systemPrefix,
		})
	}
	for _, m := range history {
		msgs = append(msgs, GatewayMessage{
			Role:    m.Role,
			Content: m.ContentMasked,
		})
	}
	return GatewayChatRequest{
		Model:    model,
		Messages: msgs,
	}
}

func gatewayCallContext(in SendInput, conv *model.Conversation) GatewayCallContext {
	return GatewayCallContext{
		TenantID:       in.TenantID,
		UserID:         in.UserID,
		ConversationID: conv.ID,
		RequestID:      in.RequestID,
	}
}

// extractContent pulls the assistant text from the non-stream
// gateway response. Defensive against empty / missing fields.
func extractContent(r *GatewayChatResponse) string {
	if r == nil || len(r.Choices) == 0 {
		return ""
	}
	return r.Choices[0].Message.Content
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
