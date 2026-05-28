// Message handlers — list history + send (stream + non-stream).
//
// The send path is the privacy-sensitive code in the service. The
// contract:
//   - Persist the user's real text via MessageService.SendStream /
//     SendNonStream BEFORE the gateway call.
//   - Forward each gateway content delta to the browser as an SSE
//     “content_delta“ event. The handler never logs the deltas.
//   - On completion, MessageService persists the accumulated text;
//     this handler never writes content to the DB directly.
package handler

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type MessageHandler struct {
	messages *service.MessageService
	// Optional. When set, the list endpoint joins file metadata
	// into each message's response. Construction in main.go wires
	// this immediately after the file repo is built.
	files *repository.FileRepo
	// Optional. When set, the list endpoint joins S7 context-source
	// attribution onto each assistant message via a single batched
	// query (no N+1).
	sources *repository.ResponseContextSourceRepo
}

func NewMessageHandler(m *service.MessageService) *MessageHandler {
	return &MessageHandler{messages: m}
}

// SetFileRepo wires in the file repo after construction so list
// responses can embed attachments. Optional — when nil, the
// "files" field is simply omitted from the wire response.
func (h *MessageHandler) SetFileRepo(fr *repository.FileRepo) {
	h.files = fr
}

// SetSourceRepo wires the S7 attribution repo so the list
// endpoint can render each assistant message's "Sources used"
// card without a per-message round-trip.
func (h *MessageHandler) SetSourceRepo(sr *repository.ResponseContextSourceRepo) {
	h.sources = sr
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type messageResponse struct {
	ID             string                          `json:"id"`
	Role           string                          `json:"role"`
	Content        string                          `json:"content"`
	Status         string                          `json:"status"`
	ModelID        *string                         `json:"model_id,omitempty"`
	TokenUsage     *tokenUsage                     `json:"token_usage,omitempty"`
	CreatedAt      string                          `json:"created_at"`
	Files          []fileResponse                  `json:"files,omitempty"`
	BranchIndex    int                             `json:"branch_index"`
	BranchCount    int                             `json:"branch_count"`
	HasBranches    bool                            `json:"has_branches"`
	// S7 — populated only on assistant messages that have at least
	// one context source recorded. Empty / nil for user messages
	// and for assistants that drew on no special context (memory,
	// state, search, compaction). omitempty keeps the wire size
	// honest.
	ContextSources []contextSourceResponse `json:"context_sources,omitempty"`
}

// contextSourceResponse mirrors model.ResponseContextSource on the
// wire. Defined here (handler package) rather than reused from
// model so the metadata JSON stays a typed map[string]any for the
// frontend's convenience.
type contextSourceResponse struct {
	ID           string         `json:"id"`
	SourceType   string         `json:"source_type"`
	SourceID     *string        `json:"source_id,omitempty"`
	DisplayLabel string         `json:"display_label"`
	Metadata     map[string]any `json:"metadata,omitempty"`
	CreatedAt    string         `json:"created_at"`
}

type tokenUsage struct {
	Input  int `json:"input"`
	Output int `json:"output"`
}

// sourceToWire formats one S7 source row for the wire response.
// Metadata is decoded into a generic map so the frontend gets
// typed access to category / conversation_title / hit_type without
// re-parsing JSON on the client.
func sourceToWire(s *model.ResponseContextSource) contextSourceResponse {
	out := contextSourceResponse{
		ID:           s.ID.String(),
		SourceType:   s.SourceType,
		DisplayLabel: s.DisplayLabel,
		CreatedAt:    s.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
	}
	if s.SourceID != nil {
		sid := s.SourceID.String()
		out.SourceID = &sid
	}
	if len(s.Metadata) > 0 {
		var meta map[string]any
		if err := json.Unmarshal(s.Metadata, &meta); err == nil {
			out.Metadata = meta
		}
	}
	return out
}

func messageToWire(m *model.Message) messageResponse {
	r := messageResponse{
		ID:          m.ID.String(),
		Role:        m.Role,
		Content:     m.ContentMasked,
		Status:      m.Status,
		ModelID:     m.ModelID,
		CreatedAt:   m.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
		BranchIndex: m.BranchIndex,
		BranchCount: 1,
	}
	if m.TokenUsageInput != nil && m.TokenUsageOutput != nil {
		r.TokenUsage = &tokenUsage{
			Input:  *m.TokenUsageInput,
			Output: *m.TokenUsageOutput,
		}
	}
	return r
}

// ---------------------------------------------------------------------------
// List messages
// ---------------------------------------------------------------------------

func (h *MessageHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	cursor := parseUUID(r.URL.Query().Get("before"))

	msgs, hasMore, err := h.messages.ListMessages(
		r.Context(), caller.TenantUUID, caller.UserID, convID, limit, cursor,
	)
	if err != nil {
		if errors.Is(err, service.ErrMessageConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	items := make([]messageResponse, 0, len(msgs))
	for i := range msgs {
		items = append(items, messageToWire(&msgs[i]))
	}

	// Branch counts. One batched query per parent_id; messages
	// without parent_id stay at the default branch_count=1.
	parentIDs := make([]uuid.UUID, 0)
	seen := map[uuid.UUID]bool{}
	for i := range msgs {
		if msgs[i].ParentID != nil && !seen[*msgs[i].ParentID] {
			seen[*msgs[i].ParentID] = true
			parentIDs = append(parentIDs, *msgs[i].ParentID)
		}
	}
	if len(parentIDs) > 0 {
		counts, err := h.messages.BranchCountsByParents(r.Context(), caller.TenantUUID, parentIDs)
		if err == nil {
			for i := range msgs {
				if msgs[i].ParentID == nil {
					continue
				}
				if c, ok := counts[*msgs[i].ParentID]; ok && c > 1 {
					items[i].BranchCount = c
					items[i].HasBranches = true
				}
			}
		}
	}

	// S7 — Join context-source attribution for assistant messages.
	// One batched query for every assistant id; skipped silently
	// when the source repo isn't wired so test/legacy setups keep
	// working without the column.
	if h.sources != nil && len(msgs) > 0 {
		asstIDs := make([]uuid.UUID, 0, len(msgs))
		for i := range msgs {
			if msgs[i].Role == "assistant" {
				asstIDs = append(asstIDs, msgs[i].ID)
			}
		}
		if len(asstIDs) > 0 {
			byMsg, err := h.sources.ListByMessages(r.Context(), asstIDs, caller.UserID, caller.TenantUUID)
			if err == nil {
				for i := range items {
					if srcs, ok := byMsg[msgs[i].ID]; ok && len(srcs) > 0 {
						wire := make([]contextSourceResponse, 0, len(srcs))
						for j := range srcs {
							wire = append(wire, sourceToWire(&srcs[j]))
						}
						items[i].ContextSources = wire
					}
				}
			}
		}
	}

	// Join file attachments. Skipped silently if the file repo
	// isn't wired (unit tests / older deployments) — the wire
	// response just omits ``files`` per message.
	if h.files != nil && len(msgs) > 0 {
		ids := make([]uuid.UUID, 0, len(msgs))
		for i := range msgs {
			ids = append(ids, msgs[i].ID)
		}
		byMsg, err := h.files.ListByMessageIDs(r.Context(), caller.TenantUUID, ids)
		if err == nil {
			for i := range items {
				if attached, ok := byMsg[msgs[i].ID]; ok {
					out := make([]fileResponse, 0, len(attached))
					for j := range attached {
						out = append(out, FileToWire(&attached[j]))
					}
					items[i].Files = out
				}
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"messages": items,
		"has_more": hasMore,
	})
}

// ---------------------------------------------------------------------------
// Send (stream OR non-stream depending on the request body's `stream` flag)
// ---------------------------------------------------------------------------

type sendRequest struct {
	Content string   `json:"content"`
	Stream  bool     `json:"stream"`
	FileIDs []string `json:"file_ids,omitempty"`
}

func (h *MessageHandler) Send(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req sendRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	in := service.SendInput{
		TenantID:       caller.TenantUUID,
		UserID:         caller.UserID,
		ConversationID: convID,
		Content:        req.Content,
		RequestID:      r.Header.Get("X-Request-Id"),
	}
	if len(req.FileIDs) > 0 {
		in.FileIDs = make([]uuid.UUID, 0, len(req.FileIDs))
		for _, s := range req.FileIDs {
			id, err := uuid.Parse(s)
			if err != nil {
				WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid file id")
				return
			}
			in.FileIDs = append(in.FileIDs, id)
		}
	}
	if req.Stream {
		h.sendStream(w, r, in)
		return
	}
	h.sendNonStream(w, r, in)
}

// ---------------------------------------------------------------------------
// Non-stream path
// ---------------------------------------------------------------------------

func (h *MessageHandler) sendNonStream(w http.ResponseWriter, r *http.Request, in service.SendInput) {
	res, err := h.messages.SendNonStream(r.Context(), in)
	if err != nil {
		h.writeSendError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"message": messageToWire(res.AssistantMessage),
	})
}

// ---------------------------------------------------------------------------
// Stream path (SSE)
// ---------------------------------------------------------------------------

// sendStream opens the gateway stream, forwards each delta to the
// browser, and persists the accumulated text on completion.
//
// SSE event format (per Phase 1 contract):
//
//	event: message_start
//	data: {"message_id":"uuid","conversation_id":"uuid"}
//
//	event: content_delta
//	data: {"delta":"text"}
//
//	event: message_complete
//	data: {"message_id":"uuid","token_usage":{"input":N,"output":N}}
//
//	event: error
//	data: {"code":"PROVIDER_UNAVAILABLE","message":"..."}
//
// The handler MUST flush after every event so the browser sees
// incremental output; chi's default ResponseWriter implements
// http.Flusher.
func (h *MessageHandler) sendStream(w http.ResponseWriter, r *http.Request, in service.SendInput) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}

	stream, err := h.messages.SendStream(r.Context(), in)
	if err != nil {
		h.writeSendError(w, err)
		return
	}

	// SSE headers — must be set BEFORE any write, and BEFORE the
	// first flush.
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	writeSSEEvent(w, flusher, "message_start", map[string]any{
		"message_id":      stream.AssistantID.String(),
		"conversation_id": stream.ConversationID.String(),
	})

	var (
		content      strings.Builder
		finalUsage   *service.GatewayChatUsage
		finishReason string
		gotErr       error
	)

Loop:
	for {
		select {
		case <-r.Context().Done():
			// Client disconnected — tell the service to cancel the
			// upstream call, drain any pending event, then persist
			// what we have.
			stream.Cancel()
		case ev, ok := <-stream.Events:
			if !ok {
				break Loop
			}
			if ev.Err != nil {
				gotErr = ev.Err
				writeSSEEvent(w, flusher, "error", map[string]any{
					"code":    CodeInternalError,
					"message": fmt.Sprintf("stream error: %v", ev.Err),
				})
				break Loop
			}
			if ev.Delta != "" {
				content.WriteString(ev.Delta)
				writeSSEEvent(w, flusher, "content_delta", map[string]any{
					"delta": ev.Delta,
				})
			}
			if ev.FinishReason != "" {
				finishReason = ev.FinishReason
			}
			if ev.Usage != nil {
				finalUsage = ev.Usage
			}
			if ev.Done {
				break Loop
			}
		}
	}

	// Persist via the handle's finish callback. Uses
	// context.Background() internally so the write completes even
	// after the request context is cancelled (client disconnected).
	persistFinishReason := finishReason
	if gotErr != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.Finish(content.String(), persistFinishReason, finalUsage)

	// Emit final completion frame. Even on error we send a
	// message_complete so the client always has a deterministic
	// terminator to clean up its UI state.
	completeData := map[string]any{
		"message_id": stream.AssistantID.String(),
	}
	if finalUsage != nil {
		completeData["token_usage"] = map[string]int{
			"input":  finalUsage.PromptTokens,
			"output": finalUsage.CompletionTokens,
		}
	}
	writeSSEEvent(w, flusher, "message_complete", completeData)
}

// writeSSEEvent serialises one SSE frame and flushes. “data“ is
// JSON-marshalled inline; failures are silently dropped because at
// this point in the request we've already committed to a 200
// response — the client will see a missing frame, the SSE clean-up
// loop on the browser handles the missing terminator.
func writeSSEEvent(w http.ResponseWriter, flusher http.Flusher, event string, data any) {
	buf, err := json.Marshal(data)
	if err != nil {
		return
	}
	_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, buf)
	flusher.Flush()
}

// writeSendError maps service errors to HTTP responses for the
// non-stream path AND for the pre-stream error path (when
// SendStream returns before any SSE frame goes out).
func (h *MessageHandler) writeSendError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrMessageEmptyContent):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "content is required")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", err.Error())
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// ---------------------------------------------------------------------------
// Cancel
// ---------------------------------------------------------------------------

// Cancel interrupts the in-flight streaming generation, if any.
// Idempotent: when no stream is active the response still returns
// 200 with “cancelled=false“ so the front-end can retry without
// branching on 404 vs success.
//
//	POST /api/v1/conversations/{id}/cancel
//	→ 200 {"cancelled": true|false}
func (h *MessageHandler) Cancel(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	// Tenant-scope check: verify caller owns the conversation before
	// invoking cancel. Without this an attacker with any valid JWT
	// could DoS another tenant's streams by guessing conversation IDs.
	if err := h.messages.AssertConversationAccess(
		r.Context(), caller.TenantUUID, caller.UserID, convID,
	); err != nil {
		if errors.Is(err, service.ErrMessageConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	cancelled := h.messages.CancelStream(convID)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"cancelled": cancelled})
}

// ---------------------------------------------------------------------------
// Regenerate
// ---------------------------------------------------------------------------

// Regenerate replays the assistant message identified by
// “messageID“ using the same conversation context the original
// reply was built from. Streams the new content over SSE the same
// way Send does, so the front-end can share its rendering code.
//
//	POST /api/v1/conversations/{id}/messages/{messageID}/regenerate
//	→ SSE stream (same event format as Send with stream=true).
func (h *MessageHandler) Regenerate(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	msgID, err := uuid.Parse(chi.URLParam(r, "messageID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message id")
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}

	stream, err := h.messages.Regenerate(r.Context(), service.RegenerateInput{
		TenantID:       caller.TenantUUID,
		UserID:         caller.UserID,
		ConversationID: convID,
		MessageID:      msgID,
		RequestID:      r.Header.Get("X-Request-Id"),
	})
	if err != nil {
		h.writeRegenerateError(w, err)
		return
	}

	// SSE headers, then consume + forward the events.
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	writeSSEEvent(w, flusher, "message_start", map[string]any{
		"message_id":      stream.AssistantID.String(),
		"conversation_id": stream.ConversationID.String(),
	})

	var (
		content      strings.Builder
		finalUsage   *service.GatewayChatUsage
		finishReason string
		gotErr       error
	)
Loop:
	for {
		select {
		case <-r.Context().Done():
			stream.Cancel()
		case ev, ok := <-stream.Events:
			if !ok {
				break Loop
			}
			if ev.Err != nil {
				gotErr = ev.Err
				writeSSEEvent(w, flusher, "error", map[string]any{
					"code":    CodeInternalError,
					"message": fmt.Sprintf("stream error: %v", ev.Err),
				})
				break Loop
			}
			if ev.Delta != "" {
				content.WriteString(ev.Delta)
				writeSSEEvent(w, flusher, "content_delta", map[string]any{"delta": ev.Delta})
			}
			if ev.FinishReason != "" {
				finishReason = ev.FinishReason
			}
			if ev.Usage != nil {
				finalUsage = ev.Usage
			}
			if ev.Done {
				break Loop
			}
		}
	}

	persistFinishReason := finishReason
	if gotErr != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.Finish(content.String(), persistFinishReason, finalUsage)

	completeData := map[string]any{"message_id": stream.AssistantID.String()}
	if finalUsage != nil {
		completeData["token_usage"] = map[string]int{
			"input":  finalUsage.PromptTokens,
			"output": finalUsage.CompletionTokens,
		}
	}
	writeSSEEvent(w, flusher, "message_complete", completeData)
}

// ---------------------------------------------------------------------------
// Edit — re-streams a user message with new content (branching fork)
// ---------------------------------------------------------------------------

type editRequest struct {
	Content string `json:"content"`
}

func (h *MessageHandler) Edit(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	msgID, err := uuid.Parse(chi.URLParam(r, "messageID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message id")
		return
	}
	var req editRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}

	stream, err := h.messages.EditMessage(r.Context(), service.EditInput{
		TenantID:       caller.TenantUUID,
		UserID:         caller.UserID,
		ConversationID: convID,
		MessageID:      msgID,
		NewContent:     req.Content,
		RequestID:      r.Header.Get("X-Request-Id"),
	})
	if err != nil {
		h.writeEditError(w, err)
		return
	}

	// SSE — identical event format to Send so the front-end shares
	// its rendering pipeline.
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	writeSSEEvent(w, flusher, "message_start", map[string]any{
		"message_id":      stream.AssistantID.String(),
		"conversation_id": stream.ConversationID.String(),
		"user_message_id": stream.UserMessage.ID.String(),
	})

	var (
		content      strings.Builder
		finalUsage   *service.GatewayChatUsage
		finishReason string
		gotErr       error
	)
Loop:
	for {
		select {
		case <-r.Context().Done():
			stream.Cancel()
		case ev, ok := <-stream.Events:
			if !ok {
				break Loop
			}
			if ev.Err != nil {
				gotErr = ev.Err
				writeSSEEvent(w, flusher, "error", map[string]any{
					"code":    CodeInternalError,
					"message": fmt.Sprintf("stream error: %v", ev.Err),
				})
				break Loop
			}
			if ev.Delta != "" {
				content.WriteString(ev.Delta)
				writeSSEEvent(w, flusher, "content_delta", map[string]any{"delta": ev.Delta})
			}
			if ev.FinishReason != "" {
				finishReason = ev.FinishReason
			}
			if ev.Usage != nil {
				finalUsage = ev.Usage
			}
			if ev.Done {
				break Loop
			}
		}
	}
	persistFinishReason := finishReason
	if gotErr != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.Finish(content.String(), persistFinishReason, finalUsage)

	completeData := map[string]any{"message_id": stream.AssistantID.String()}
	if finalUsage != nil {
		completeData["token_usage"] = map[string]int{
			"input":  finalUsage.PromptTokens,
			"output": finalUsage.CompletionTokens,
		}
	}
	writeSSEEvent(w, flusher, "message_complete", completeData)
}

func (h *MessageHandler) writeEditError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrMessageNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
	case errors.Is(err, service.ErrEditWrongRole):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"only user messages can be edited")
	case errors.Is(err, service.ErrMessageEmptyContent):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "content is required")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", err.Error())
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// ---------------------------------------------------------------------------
// Switch branch
// ---------------------------------------------------------------------------

type switchBranchRequest struct {
	BranchIndex int `json:"branch_index"`
}

// SwitchBranch toggles the active branch inside a fork group. The
// front-end re-fetches the message list after a 200 to pick up the
// new active chain.
func (h *MessageHandler) SwitchBranch(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	msgID, err := uuid.Parse(chi.URLParam(r, "messageID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message id")
		return
	}
	var req switchBranchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if err := h.messages.SwitchBranch(
		r.Context(), caller.TenantUUID, caller.UserID, convID, msgID, req.BranchIndex,
	); err != nil {
		switch {
		case errors.Is(err, service.ErrMessageConversationNotFound),
			errors.Is(err, service.ErrMessageNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
		case errors.Is(err, service.ErrSwitchBranchNoFork):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"message is not part of a fork group")
		case errors.Is(err, service.ErrSwitchBranchMissing):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"branch_index does not exist")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// writeRegenerateError maps service errors to HTTP responses for
// the pre-stream phase. Once SSE headers are out we can't change
// status, so only callers BEFORE the first frame use this.
func (h *MessageHandler) writeRegenerateError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrMessageNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
	case errors.Is(err, service.ErrMessageNotAssistant):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"can only regenerate assistant messages")
	case errors.Is(err, service.ErrMessageModelNotAllowed):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
			"conversation model is no longer allowed")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", err.Error())
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// Ensure imports compile when only a subset of variables are used.
var _ = context.Background
