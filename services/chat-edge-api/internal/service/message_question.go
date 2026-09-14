package service

// FIX74 — parking and resuming a web turn around ask_user_question.
//
// The pump (message_service.go) intercepts a VALID ask_user_question call,
// writes the paused turn to chat.pending_questions (parkedTurn below, as
// JSON), emits the `question` frame and a `parked` terminal, and returns.
// Nothing stays alive: no goroutine, no held response, no upstream
// connection. ResumeQuestion claims the row, renders the answer as the
// tool result (the desktop's exact text), and opens a NEW generation on
// the same assistant message — the ordinary pump, continuing the
// iteration count. See FIX74_REPORT.md Part A for the decision.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

var (
	ErrQuestionNotFound   = errors.New("question not found")
	ErrQuestionNotPending = errors.New("question is no longer pending")
	ErrQuestionExpired    = errors.New("question expired")
	ErrQuestionInvalid    = errors.New("invalid answer")
)

// PendingQuestionStore is what the service needs from the repository —
// an interface so the pump tests run on an in-memory fake.
type PendingQuestionStore interface {
	Create(ctx context.Context, q *model.PendingQuestion) (*model.PendingQuestion, error)
	GetByID(ctx context.Context, tenantID, id uuid.UUID) (*model.PendingQuestion, error)
	PendingForConversation(ctx context.Context, tenantID, convID uuid.UUID) (*model.PendingQuestion, error)
	Claim(ctx context.Context, tenantID, id uuid.UUID, status string, now time.Time) (*model.PendingQuestion, error)
	Close(ctx context.Context, id uuid.UUID, status string, now time.Time) (bool, error)
	CloseForConversation(ctx context.Context, tenantID, convID uuid.UUID, status string, now time.Time) (*model.PendingQuestion, error)
}

// SetPendingQuestionStore wires the FIX74 store. Nil (unit tests that
// construct the service directly, older deployments without migration
// 042) means the tool is never offered — a question nobody can park is
// not asked.
func (s *MessageService) SetPendingQuestionStore(st PendingQuestionStore) { s.pendingQuestions = st }

// parkedTurn is state_json: everything the continuation needs.
type parkedTurn struct {
	Request       GatewayChatRequest `json:"request"`
	Iteration     int                `json:"iteration"`
	ToolsAttached bool               `json:"tools_attached"`
	ServedModel   string             `json:"served_model,omitempty"`
	// The text and reasoning already streamed before the park; the
	// continuation is appended to these when the row is finally persisted.
	ContentPrefix   string `json:"content_prefix"`
	ReasoningPrefix string `json:"reasoning_prefix,omitempty"`
	// Later tool_use blocks of the same round (never executed): answered
	// with the batch-contract text when the turn resumes.
	Deferred []GatewayToolCall `json:"deferred,omitempty"`
	RequestID string          `json:"request_id,omitempty"`
}

// QuestionUpdate is the `question` frame payload — the same shape the
// desktop bridge posts, so QuestionCard.tsx renders both.
type QuestionUpdate struct {
	PromptID   string        `json:"promptId"`
	MessageID  string        `json:"messageId"`
	ToolCallID string        `json:"toolCallId"`
	Questions  []AskQuestion `json:"questions"`
}

// parkQuestion persists the paused turn. Returns the pending row; the
// caller emits the frame. Uses a background context: the park must land
// even when the browser has already gone (that is the point of it).
func (s *MessageService) parkQuestion(
	ec ToolExecContext, req GatewayChatRequest, iteration int, toolsAttached bool, servedModel string,
	contentPrefix, reasoningPrefix string, call GatewayToolCall, questions []AskQuestion, deferred []GatewayToolCall,
) (*model.PendingQuestion, error) {
	if s.pendingQuestions == nil {
		return nil, errors.New("pending question store not wired")
	}
	state, err := json.Marshal(parkedTurn{
		Request: req, Iteration: iteration, ToolsAttached: toolsAttached, ServedModel: servedModel,
		ContentPrefix: contentPrefix, ReasoningPrefix: reasoningPrefix, Deferred: deferred,
	})
	if err != nil {
		return nil, err
	}
	qjson, err := json.Marshal(questions)
	if err != nil {
		return nil, err
	}
	now := s.nowQuestion()
	row, err := s.pendingQuestions.Create(context.Background(), &model.PendingQuestion{
		TenantID: ec.TenantID, UserID: ec.UserID, ConversationID: ec.ConversationID, MessageID: ec.MessageID,
		ToolCallID: call.ID, Questions: qjson, State: state, Status: model.PendingQuestionPending,
		ExpiresAt: now.Add(pendingQuestionTTL),
	})
	if err != nil {
		return nil, err
	}
	slog.Info("question_parked", "conversation_id", ec.ConversationID.String(), "message_id", ec.MessageID.String(),
		"prompt_id", row.ID.String(), "questions", len(questions), "iteration", iteration)
	return row, nil
}

func (s *MessageService) nowQuestion() time.Time {
	if s.questionClock != nil {
		return s.questionClock()
	}
	return time.Now()
}

// firstValidAsk returns the index of the first VALID ask_user_question call
// in a round, or -1. An invalid one is left to the executor, which answers
// it with the validation error (never a card).
func firstValidAsk(acc []GatewayToolCall) (int, []AskQuestion) {
	for i, tc := range acc {
		if tc.Function.Name != AskUserTool {
			continue
		}
		if blocked, _ := dlpBlockedMarker(tc.Function.Arguments); blocked {
			continue
		}
		if qs, err := validateAskArgs(tc.Function.Arguments); err == nil {
			return i, qs
		}
	}
	return -1, nil
}

// AnswerQuestionInput is the page's answer to a parked question.
type AnswerQuestionInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	PromptID       uuid.UUID
	Answer         QuestionAnswer
	RequestID      string
	RunID          string
}

// PendingQuestionFor returns the conversation's pending card for the
// message list, settling an expired one on the way (the assistant row
// becomes cancelled; nothing is answered on the user's behalf).
func (s *MessageService) PendingQuestionFor(ctx context.Context, tenantID, userID, convID uuid.UUID) (*model.PendingQuestion, error) {
	if s.pendingQuestions == nil {
		return nil, nil
	}
	q, err := s.pendingQuestions.PendingForConversation(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, nil
		}
		return nil, err
	}
	if q.UserID != userID {
		return nil, nil
	}
	now := s.nowQuestion()
	if q.IsPending(now) {
		return q, nil
	}
	s.settlePending(ctx, q, model.PendingQuestionExpired, now)
	return nil, nil
}

// settlePending closes a pending row without an answer and turns its
// assistant message into a cancelled one — the honest state for a turn
// that will not continue.
func (s *MessageService) settlePending(ctx context.Context, q *model.PendingQuestion, status string, now time.Time) {
	if closed, err := s.pendingQuestions.Close(ctx, q.ID, status, now); err != nil || !closed {
		return
	}
	_ = s.messages.UpdateStatus(ctx, q.MessageID, "cancelled")
	slog.Info("question_settled", "prompt_id", q.ID.String(), "status", status, "message_id", q.MessageID.String())
}

// supersedePending is called when the user sends a NEW message while a
// question is pending: the question is closed as superseded and its
// assistant row as cancelled; the new turn's history holds both texts.
func (s *MessageService) supersedePending(ctx context.Context, tenantID, convID uuid.UUID) {
	if s.pendingQuestions == nil {
		return
	}
	now := s.nowQuestion()
	q, err := s.pendingQuestions.CloseForConversation(ctx, tenantID, convID, model.PendingQuestionSuperseded, now)
	if err != nil || q == nil {
		return
	}
	_ = s.messages.UpdateStatus(ctx, q.MessageID, "cancelled")
	slog.Info("question_superseded", "prompt_id", q.ID.String(), "message_id", q.MessageID.String())
}

// CancelPendingQuestion is POST /cancel's half for a parked turn (there
// is no stream to cancel). Returns true when a pending question was
// closed.
func (s *MessageService) CancelPendingQuestion(ctx context.Context, tenantID, userID, convID uuid.UUID) bool {
	if s.pendingQuestions == nil {
		return false
	}
	q, err := s.pendingQuestions.PendingForConversation(ctx, tenantID, convID)
	if err != nil || q == nil || q.UserID != userID {
		return false
	}
	s.settlePending(ctx, q, model.PendingQuestionCancelled, s.nowQuestion())
	return true
}

// ResumeQuestion claims the parked turn and opens its continuation. The
// returned handle streams exactly like SendStream's; the handler runs it
// through the same drain/finish path.
func (s *MessageService) ResumeQuestion(ctx context.Context, in AnswerQuestionInput) (*StreamHandle, error) {
	if s.pendingQuestions == nil {
		return nil, ErrQuestionNotFound
	}
	if err := validateQuestionAnswer(&in.Answer); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrQuestionInvalid, err)
	}
	q, err := s.pendingQuestions.GetByID(ctx, in.TenantID, in.PromptID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrQuestionNotFound
		}
		return nil, err
	}
	if q.ConversationID != in.ConversationID || q.UserID != in.UserID {
		return nil, ErrQuestionNotFound
	}
	now := s.nowQuestion()
	if q.Status != model.PendingQuestionPending {
		return nil, ErrQuestionNotPending
	}
	if !now.Before(q.ExpiresAt) {
		s.settlePending(ctx, q, model.PendingQuestionExpired, now)
		return nil, ErrQuestionExpired
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
	var state parkedTurn
	if err := json.Unmarshal(q.State, &state); err != nil {
		return nil, fmt.Errorf("parked turn unreadable: %w", err)
	}
	var questions []AskQuestion
	if err := json.Unmarshal(q.Questions, &questions); err != nil {
		return nil, fmt.Errorf("parked questions unreadable: %w", err)
	}
	// The claim is the two-tabs rule: one conditional UPDATE, first wins.
	claimStatus := model.PendingQuestionAnswered
	if in.Answer.Outcome == "skipped" {
		claimStatus = model.PendingQuestionSkipped
	}
	if _, err := s.pendingQuestions.Claim(ctx, in.TenantID, q.ID, claimStatus, now); err != nil {
		if errors.Is(err, repository.ErrQuestionNotPending) {
			return nil, ErrQuestionNotPending
		}
		return nil, err
	}

	// The answer, as the tool result of the same call — the desktop's text.
	req := state.Request
	req.Messages = append(req.Messages, GatewayMessage{
		Role: "tool", ToolCallID: q.ToolCallID, Name: AskUserTool,
		Content: formatAnswerForModel(questions, in.Answer),
	})
	for _, d := range state.Deferred {
		req.Messages = append(req.Messages, GatewayMessage{Role: "tool", ToolCallID: d.ID, Name: d.Function.Name, Content: askNotExecutedAfterQuestion})
	}

	// The row streams again: UpdateContent has no status guard, but the
	// orphan sweep and the resume path both key on "streaming".
	if err := s.messages.UpdateStatus(ctx, q.MessageID, "streaming"); err != nil {
		return nil, err
	}
	streamCtx, cancel := context.WithCancel(context.Background())
	callCtx := GatewayCallContext{TenantID: in.TenantID, UserID: in.UserID, ConversationID: conv.ID, RequestID: in.RequestID}
	stream, err := s.gateway.SendChatCompletionStream(streamCtx, callCtx, req)
	if err != nil {
		cancel()
		// Nothing invented: the turn simply could not continue. The row
		// says so.
		_ = s.messages.UpdateContent(ctx, q.MessageID, state.ContentPrefix, "error", nil, nil)
		var ue *GatewayUnavailableError
		if errors.As(err, &ue) {
			return nil, fmt.Errorf("%w: %w", ErrMessageGatewayUnavailable, ue)
		}
		return nil, err
	}
	reg := &streamRegistration{cancel: cancel}
	s.activeStreams.Store(conv.ID, reg)
	slog.Info("question_resumed", "prompt_id", q.ID.String(), "message_id", q.MessageID.String(),
		"outcome", in.Answer.Outcome, "iteration", state.Iteration+1)

	// The continuation must always read the stream just opened: a turn
	// parked on its last allowed round still gets its answer round (the cap
	// counts model rounds the model chose, not the user's reply).
	startIter := state.Iteration + 1
	if startIter >= maxToolIterations {
		startIter = maxToolIterations - 1
	}
	ch := make(chan StreamUpdate, 16)
	go s.pumpStreamWithTools(streamPumpInput{
		initialStream: stream,
		initialReq:    req,
		callCtx:       callCtx,
		streamCtx:     streamCtx,
		toolsAttached: state.ToolsAttached,
		toolExecCtx: ToolExecContext{
			TenantID: in.TenantID, UserID: in.UserID, ConversationID: conv.ID, ProjectID: conv.ProjectID,
			MessageID: q.MessageID, Sources: NewSourcesAccumulator(),
		},
		out:             ch,
		startIter:       startIter,
		contentPrefix:   state.ContentPrefix,
		reasoningPrefix: state.ReasoningPrefix,
		servedModel:     state.ServedModel,
	})
	msgID := q.MessageID
	prefix, rprefix := state.ContentPrefix, state.ReasoningPrefix
	finish := func(content, reasoning, finishReason string, usage *GatewayChatUsage, term TerminalInfo) {
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
		case finishReason == "parked":
			status = model.MessageStatusWaitingUser
		}
		full := prefix + content
		if status == "completed" && finishReason == "" && strings.TrimSpace(full) == "" {
			status = "error"
		}
		var tokInput, tokOutput *int
		if usage != nil {
			ti, to := usage.PromptTokens, usage.CompletionTokens
			tokInput, tokOutput = &ti, &to
		}
		// The prefix was produced by an earlier generation; the canonical
		// artifact of THIS stream would not cover it, so the row is
		// persisted display-only (canonical unavailable → never replayed
		// as provider history by the canonical path, the legacy text is).
		s.persistDisplayOnly(persistCtx, msgID, full, status, tokInput, tokOutput)
		s.persistTerminal(persistCtx, msgID, status, term)
		if r := rprefix + reasoning; r != "" && status == "completed" {
			_ = s.messages.SetReasoningContent(persistCtx, msgID, r)
		}
		if status == "completed" {
			s.maybeUpdateConversationState(persistCtx, conv)
		}
	}
	events, runID := s.teeStream(msgID, in.RunID, ch)
	return &StreamHandle{
		AssistantID:    msgID,
		ConversationID: conv.ID,
		RunID:          runID,
		Events:         events,
		finish:         finish,
		cancel:         cancel,
		cancelled:      reg.isUserCancelled,
	}, nil
}
