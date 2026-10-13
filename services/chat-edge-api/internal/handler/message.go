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
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

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
	// Optional (FIX131 A3). When set, the list endpoint returns each
	// assistant message's failed plan executions as plan_outcomes, so the
	// plan card after a reload says "ไม่สำเร็จ — …" instead of offering to
	// generate again as if nothing had happened.
	planOutcomes service.PlanOutcomeStore
}

// SetPlanOutcomeStore wires FIX131 A3's failed-execution record.
func (h *MessageHandler) SetPlanOutcomeStore(s service.PlanOutcomeStore) { h.planOutcomes = s }

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
	ID         string      `json:"id"`
	Role       string      `json:"role"`
	Content    string      `json:"content"`
	Status     string      `json:"status"`
	ModelID    *string     `json:"model_id,omitempty"`
	TokenUsage *tokenUsage `json:"token_usage,omitempty"`
	CreatedAt  string      `json:"created_at"`
	// Auto Thinking Mode — the model's thinking summary for this
	// assistant turn (masked, persisted in migration 019). nil for
	// user turns and assistant turns that didn't think, so the wire
	// stays unchanged for non-reasoning messages. Lets the
	// ThinkingBlock re-render after a page reload (the live SSE
	// reasoning_delta stream is gone once the turn completes).
	ReasoningContent *string         `json:"reasoning_content,omitempty"`
	SearchSources    json.RawMessage `json:"search_sources,omitempty"`
	// ClientID is the desktop's own message id for rows that arrived via
	// the desktop import endpoints (FIX 9). The desktop uses it to
	// correlate a server row back to its local copy; nil / omitted for
	// native chat-edge rows so the web wire is unchanged.
	ClientID *string `json:"client_id,omitempty"`
	// FIX118 — how the turn ended (closed set) and the catalogue code of a
	// failed turn, so a reload shows the same sentence the live frame did
	// instead of "this answer failed" with no reason. Omitted on user rows
	// and rows written before migration 043.
	TerminalCause *string `json:"terminal_cause,omitempty"`
	ErrorCode     *string `json:"error_code,omitempty"`
	// FIX119 — the run id every frame of the turn carried (the reference
	// the person quotes) and why the model stopped.
	RunID       *string        `json:"run_id,omitempty"`
	StopReason  *string        `json:"stop_reason,omitempty"`
	// FIX120 — the requested model when a fallback model answered
	// (model_id names the one that did). Omitted otherwise.
	FallbackFrom *string `json:"fallback_from,omitempty"`
	// FIX121 — the per-model attempt log of a failed turn (migration 047),
	// under the name the page already reads off the live error frame's
	// row (Message.error_attempts), so a reload renders the same list.
	ErrorAttempts json.RawMessage `json:"error_attempts,omitempty"`
	Files       []fileResponse `json:"files,omitempty"`
	BranchIndex int            `json:"branch_index"`
	BranchCount int            `json:"branch_count"`
	HasBranches bool           `json:"has_branches"`
	// S7 — populated only on assistant messages that have at least
	// one context source recorded. Empty / nil for user messages
	// and for assistants that drew on no special context (memory,
	// state, search, compaction). omitempty keeps the wire size
	// honest.
	ContextSources []contextSourceResponse `json:"context_sources,omitempty"`
	// FIX74 — the question card a parked assistant turn is waiting on
	// (status waiting_user). Same shape the desktop bridge posts, so the
	// page renders one component for both.
	PendingQuestion *pendingQuestionResponse `json:"pending_question,omitempty"`
	// FIX131 A3 — the plan cards of this assistant message whose last
	// execution failed, and why. A success is not listed: it is the file
	// in `files`.
	PlanOutcomes []planOutcomeResponse `json:"plan_outcomes,omitempty"`
}

// planOutcomeResponse is one failed plan execution on the wire.
type planOutcomeResponse struct {
	Fence          string `json:"fence"`
	Format         string `json:"format"`
	OutputFilename string `json:"output_filename,omitempty"`
	Status         string `json:"status"`
	Reason         string `json:"reason"`
	SourceFileID   string `json:"source_file_id,omitempty"`
	SourceFilename string `json:"source_filename,omitempty"`
	At             string `json:"at"`
}

type pendingQuestionResponse struct {
	PromptID   string          `json:"promptId"`
	ToolCallID string          `json:"toolCallId"`
	Questions  json.RawMessage `json:"questions"`
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
		ClientID:    m.ClientID,
	}
	if m.TerminalCause != nil && *m.TerminalCause != "" {
		r.TerminalCause = m.TerminalCause
	}
	if m.ErrorCode != nil && *m.ErrorCode != "" {
		r.ErrorCode = m.ErrorCode
	}
	if m.RunID != nil && *m.RunID != "" {
		r.RunID = m.RunID
	}
	if m.StopReason != nil && *m.StopReason != "" {
		r.StopReason = m.StopReason
	}
	if m.FallbackFrom != nil && *m.FallbackFrom != "" {
		r.FallbackFrom = m.FallbackFrom
	}
	if len(m.Attempts) > 0 && string(m.Attempts) != "null" {
		r.ErrorAttempts = m.Attempts
	}
	if m.ReasoningContent != nil && *m.ReasoningContent != "" {
		r.ReasoningContent = m.ReasoningContent
	}
	// Web-search citations — forward the stored JSON array verbatim so
	// the frontend renders citation chips on reload.
	if len(m.SearchSources) > 0 {
		r.SearchSources = json.RawMessage(m.SearchSources)
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

	var (
		msgs    []model.Message
		hasMore bool
	)
	// FIX132 F12 — `through=<message id>` (or `first`): reach back to that
	// message in one response, so a search hit opens the chat AT the hit.
	// `limit` then only caps the response (up to 1000).
	if raw := r.URL.Query().Get("through"); raw != "" {
		through := uuid.Nil
		if raw != "first" {
			id, perr := uuid.Parse(raw)
			if perr != nil {
				WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid through message id")
				return
			}
			through = id
		}
		if r.URL.Query().Get("limit") == "" {
			limit = 0
		}
		msgs, hasMore, err = h.messages.ListMessagesThrough(
			r.Context(), caller.TenantUUID, caller.UserID, convID, limit, cursor, through,
		)
	} else {
		msgs, hasMore, err = h.messages.ListMessages(
			r.Context(), caller.TenantUUID, caller.UserID, convID, limit, cursor,
		)
	}
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

	// Phase 4A.2.4 — display lifecycle. When enabled, the persisted
	// content_masked is no longer the restored raw display, so the reload read
	// goes through the owner restore-on-read policy: restored (Vault V2) or a
	// tokenized/redacted fallback. No-op when the feature is off (items keep
	// the legacy content_masked passthrough).
	if h.messages.DisplayLifecycleEnabled() {
		actor := service.DisplayActor{UserID: caller.UserID, TenantID: caller.TenantUUID}
		for i := range msgs {
			rep := h.messages.ResolveRepresentation(
				r.Context(), service.ConsumerBrowserReloadOwner, msgs[i], actor,
			)
			items[i].Content = rep.Content
		}
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

	// FIX74 — the parked question, if any. One query per list; an expired
	// one is settled on the way (its row is then listed as cancelled).
	if pq, err := h.messages.PendingQuestionFor(r.Context(), caller.TenantUUID, caller.UserID, convID); err == nil {
		for i := range msgs {
			if pq != nil && msgs[i].ID == pq.MessageID {
				items[i].PendingQuestion = &pendingQuestionResponse{PromptID: pq.ID.String(), ToolCallID: pq.ToolCallID, Questions: json.RawMessage(pq.Questions)}
			} else if pq == nil && msgs[i].Status == model.MessageStatusWaitingUser {
				// The row says waiting but nothing is pending any more
				// (expired / superseded / cancelled) — say stopped, not waiting.
				items[i].Status = "cancelled"
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
			known := filesByID(byMsg)
			for i := range items {
				if attached, ok := byMsg[msgs[i].ID]; ok {
					out := make([]fileResponse, 0, len(attached))
					for j := range attached {
						wire := FileToWire(&attached[j])
						// FIX131 A4 — "from fixture.pdf": the file a
						// generated document was made from, and how.
						if p := attached[j].ParentFileID; attached[j].IsGenerated && p != nil {
							if parent := h.fileForCaller(r.Context(), caller.TenantUUID, caller.UserID, *p, known); parent != nil {
								wire.MadeFrom = madeFrom(&attached[j], parent)
							}
						}
						out = append(out, wire)
					}
					items[i].Files = out
				}
			}
		}
	}

	// FIX131 A3 — failed plan executions, on the message that wrote the plan.
	if h.planOutcomes != nil && len(msgs) > 0 {
		pos := make(map[uuid.UUID]int, len(msgs))
		for i := range msgs {
			if msgs[i].Role == "assistant" {
				pos[msgs[i].ID] = i
			}
		}
		for _, o := range h.planOutcomes.List(r.Context(), convID) {
			i, ok := pos[o.MessageID]
			if !ok {
				continue
			}
			wire := planOutcomeResponse{
				Fence: o.Fence, Format: o.Format, OutputFilename: o.OutputFilename,
				Status: o.Status, Reason: o.Reason, At: o.At.UTC().Format("2006-01-02T15:04:05Z"),
			}
			if o.SourceFileID != nil && h.files != nil {
				wire.SourceFileID = o.SourceFileID.String()
				if src := h.fileForCaller(r.Context(), caller.TenantUUID, caller.UserID, *o.SourceFileID, nil); src != nil {
					wire.SourceFilename = src.OriginalFilename
				}
			}
			items[i].PlanOutcomes = append(items[i].PlanOutcomes, wire)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"messages": items,
		"has_more": hasMore,
	})
}

// filesByID indexes the page's attachments by id.
func filesByID(byMsg map[uuid.UUID][]model.ConversationFile) map[uuid.UUID]*model.ConversationFile {
	out := map[uuid.UUID]*model.ConversationFile{}
	for _, list := range byMsg {
		for j := range list {
			out[list[j].ID] = &list[j]
		}
	}
	return out
}

// fileForCaller returns a file of this caller by id: from the page's own
// attachments when it is there, else one tenant-scoped lookup. nil when it
// is gone or belongs to someone else.
func (h *MessageHandler) fileForCaller(
	ctx context.Context, tenantID, userID, id uuid.UUID, known map[uuid.UUID]*model.ConversationFile,
) *model.ConversationFile {
	if f, ok := known[id]; ok {
		if f.UserID != userID {
			return nil
		}
		return f
	}
	if h.files == nil {
		return nil
	}
	f, err := h.files.GetByID(ctx, tenantID, id)
	if err != nil || f.UserID != userID {
		return nil
	}
	if known != nil {
		known[id] = f
	}
	return f
}

// ---------------------------------------------------------------------------
// Send (stream OR non-stream depending on the request body's `stream` flag)
// ---------------------------------------------------------------------------

type sendRequest struct {
	Content string   `json:"content"`
	Stream  bool     `json:"stream"`
	FileIDs []string `json:"file_ids,omitempty"`
	// ImageMode (Phase C) — set by the web-chat 🎨 composer toggle.
	// Biases a tool-capable model to call generate_image instead of
	// replying in text. Ignored when the model has no tools.
	ImageMode bool `json:"image_mode,omitempty"`
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
		RequestID:      middleware.RequestIDFrom(r.Context()),
		ImageMode:      req.ImageMode,
	}
	if in.RequestID == "" {
		in.RequestID = uuid.NewString()
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
	// FIX121 — an X-Request-Id the CLIENT chose is this send's idempotency
	// key: a repeat is answered with the turn the first one produced, never
	// run again. No supplied id, no key, no change.
	if key := middleware.SuppliedRequestIDFrom(r.Context()); key != "" {
		in.IdempotencyKey = key
		if h.replayIfRepeated(w, r, in, req.Stream) {
			return
		}
	}
	if req.Stream {
		h.sendStream(w, r, in)
		return
	}
	h.sendNonStream(w, r, in)
}

// replayEndedRow answers a repeated streaming send whose turn ended in error
// or was stopped, from the stored row: message_start, the text the row kept,
// and the row's own terminal — the same code, cause and attempt log for a
// failure; ``cancelled`` for a stop.
//
// One thing is not reproduced: a send refused BEFORE its stream opened was
// answered with an HTTP status and a JSON body, and that status is not stored.
// The repeat gets the same code on a stream error frame; clients render both
// by code (STREAM_CONTRACT §12.1).
func (h *MessageHandler) replayEndedRow(w http.ResponseWriter, convID uuid.UUID, replay *service.SendReplay) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}
	asst := replay.AssistantMessage
	runID := "replay-" + uuid.NewString()
	if asst.RunID != nil && *asst.RunID != "" {
		runID = *asst.RunID
	}
	rw := newReplayWriter(w, runID)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	rw.started = true
	writeSSEEvent(rw, flusher, "message_start", map[string]any{
		"message_id":      asst.ID.String(),
		"user_message_id": replay.UserMessage.ID.String(),
		"conversation_id": convID.String(),
	})
	if asst.ContentMasked != "" {
		writeSSEEvent(rw, flusher, "content_delta", map[string]any{"delta": asst.ContentMasked})
	}
	if asst.Status == "cancelled" {
		writeSSEEvent(rw, flusher, "cancelled", map[string]any{
			"message_id": asst.ID.String(),
			"partial":    true,
			"cause":      service.TerminalCauseStoppedByUser,
		})
		return
	}
	code := "send_failed"
	if asst.ErrorCode != nil && *asst.ErrorCode != "" {
		code = *asst.ErrorCode
	}
	frame := map[string]any{
		"code":    code,
		"message": "This request already ran and did not produce an answer.",
	}
	if asst.TerminalCause != nil && *asst.TerminalCause != "" {
		frame["cause"] = *asst.TerminalCause
	}
	if len(asst.Attempts) > 0 && string(asst.Attempts) != "null" {
		frame["attempts"] = asst.Attempts
	}
	writeSSEEvent(rw, flusher, "error", frame)
}

// Wire codes of the send path's idempotency contract (409).
const (
	CodeIdempotencyKeyReused = "IDEMPOTENCY_KEY_REUSED"
	CodeRequestInProgress    = "REQUEST_IN_PROGRESS"
	// CodeConversationBusy (FIX125 M5) — the conversation already has a
	// turn in flight (another tab, another device). Nothing was written;
	// the client retries once that turn has ended.
	CodeConversationBusy = "CONVERSATION_BUSY"
)

const conversationBusyMessage = "this conversation is already answering a message; wait for it to finish or stop it"

// CodeTooManyFiles (FIX126 F1) — a send named more file ids than one message
// may carry (MAX_FILES_PER_MESSAGE, default 5). Nothing was written.
const CodeTooManyFiles = "TOO_MANY_FILES"

// writeTooManyFiles answers a send refused by the files-per-message cap.
// False when err is not that refusal.
func writeTooManyFiles(w http.ResponseWriter, err error) bool {
	var tm *service.TooManyFilesError
	if !errors.As(err, &tm) {
		return false
	}
	WriteError(w, http.StatusUnprocessableEntity, CodeTooManyFiles, tooManyFilesMessage(tm))
	return true
}

func tooManyFilesMessage(tm *service.TooManyFilesError) string {
	return fmt.Sprintf("at most %d files per message (this message has %d)", tm.Max, tm.Got)
}

// writeConversationBusy answers a turn refused by the per-conversation
// in-flight guard. False when err is not that refusal.
//
// FIX131 E2 — it also answers a turn refused because this process is
// shutting down (every turn error writer calls it first).
func writeConversationBusy(w http.ResponseWriter, err error) bool {
	if writeServerDraining(w, err) {
		return true
	}
	if !errors.Is(err, service.ErrConversationBusy) {
		return false
	}
	WriteError(w, http.StatusConflict, CodeConversationBusy, conversationBusyMessage)
	return true
}

// CodeServerDraining (FIX131 E2) — the process is restarting and took no
// new turn; nothing was written. 503 + Retry-After: the client sends the
// same request again (same X-Request-Id, so a copy that did land is
// replayed, never run twice) once the new process is up.
const CodeServerDraining = "SERVER_DRAINING"

// serverDrainingRetryAfter is the Retry-After (seconds) of that refusal: a
// restart of this service takes a few seconds once the turns in flight
// are done.
const serverDrainingRetryAfter = "5"

// writeServerDraining answers a turn refused by the shutdown drain. False
// when err is not that refusal.
func writeServerDraining(w http.ResponseWriter, err error) bool {
	if !errors.Is(err, service.ErrServerDraining) {
		return false
	}
	w.Header().Set("Retry-After", serverDrainingRetryAfter)
	WriteError(w, http.StatusServiceUnavailable, CodeServerDraining,
		"the chat service is restarting; send the message again in a few seconds")
	return true
}

// replayIfRepeated answers a send whose idempotency key this conversation
// has already seen, and reports whether it did. The contract is the one
// payment APIs made familiar:
//
//	same key, same content      → the first turn again, marked
//	                              ``Idempotent-Replayed: true``
//	same key, different content → 409 IDEMPOTENCY_KEY_REUSED
//	same key, no answer row yet → 409 REQUEST_IN_PROGRESS (retry)
//
// A streaming caller is attached to the first turn through the resume path:
// the recorded frames replayed verbatim with their original seq, then the
// live tail if the run is still going — the same bytes the first response
// carried, so nothing is generated, charged or written twice.
func (h *MessageHandler) replayIfRepeated(w http.ResponseWriter, r *http.Request, in service.SendInput, stream bool) bool {
	replay, err := h.messages.FindSendReplay(
		r.Context(), in.TenantID, in.UserID, in.ConversationID, in.IdempotencyKey, in.Content,
	)
	switch {
	case errors.Is(err, service.ErrIdempotencyKeyReused):
		WriteError(w, http.StatusConflict, CodeIdempotencyKeyReused,
			"this X-Request-Id was already used for a different message in this conversation")
		return true
	case errors.Is(err, service.ErrSendInProgress):
		WriteError(w, http.StatusConflict, CodeRequestInProgress,
			"a request with this X-Request-Id is still being accepted; retry shortly")
		return true
	case err != nil:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return true
	case replay == nil:
		return false
	}
	w.Header().Set("Idempotent-Replayed", "true")
	if stream {
		switch replay.AssistantMessage.Status {
		case "error", "cancelled":
			// An ended-badly turn is answered from its ROW. The resume path
			// cannot do it: a send refused before its stream opened has no
			// recorded frames, and resume's snapshot fallback calls every
			// errored row GENERATION_INTERRUPTED — a different answer from
			// the one the first request got.
			h.replayEndedRow(w, in.ConversationID, replay)
		default:
			h.serveResume(w, r, in.TenantID, in.UserID, in.ConversationID,
				replay.AssistantMessage.ID, replay.UserMessage.ID, 0)
		}
		return true
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"message": messageToWire(replay.AssistantMessage),
	})
	return true
}

// ---------------------------------------------------------------------------
// Non-stream path
// ---------------------------------------------------------------------------

func (h *MessageHandler) sendNonStream(w http.ResponseWriter, r *http.Request, in service.SendInput) {
	res, err := h.messages.SendNonStream(r.Context(), in)
	if errors.Is(err, service.ErrSendDuplicate) {
		// FIX121 — another copy of this request inserted its user row
		// between our lookup and our insert (migration 047's index said so).
		if !h.replayIfRepeated(w, r, in, false) {
			WriteError(w, http.StatusConflict, CodeRequestInProgress,
				"a request with this X-Request-Id is still being accepted; retry shortly")
		}
		return
	}
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

// streamDrainResult carries what drainStreamEvents accumulated so
// the caller can persist the turn (StreamHandle.Finish) and emit its
// message_complete terminator.
type streamDrainResult struct {
	content      string
	reasoning    string
	finishReason string
	usage        *service.GatewayChatUsage
	err          error
	// servedModel (FIX35 Part B) — the model that actually answered, as
	// the gateway named it; "" when the stream never said.
	servedModel string
	// fallbackFrom (FIX120) — the model the turn asked for when the gateway's
	// fallback_info frame named a different answering model; persisted on
	// the row, repeated on message_complete as ``fallback_from``.
	fallbackFrom string
	// FIX41 — what to put in the TERMINAL error frame. The drain no
	// longer writes its own error frame: a failed run has to END on
	// ``error``, and it cannot if the drain spends that event mid-stream
	// and the handler then appends message_complete after it (which is
	// what P4 measured: the last frame of a failed turn was the success
	// terminator).
	errCode     string
	errMessage  string
	errDetails  any
	errAttempts any
	// errRetryable is the gateway's retryable verdict, forwarded as-is.
	errRetryable *bool
	// FIX118 — why the run ended, from the closed set
	// (service.TerminalCauses). "" until the drain learned it; the finish
	// callback derives one from the status when it is still empty.
	cause string
	// FIX118 — the transport error text, kept OUT of the message the
	// person reads and shipped under details.transport instead.
	transport string
	// FIX119 — why the model stopped (closed set) and the ids of the call
	// that answered, from the Done update; the backend's request id on a
	// provider error frame.
	stopReason        string
	toolLimit         int // FIX124 — tool rounds run when the turn ended at the cap unanswered
	providerRequestID string
	gatewayRequestID  string
	// FIX74 — the run ended because the turn parked on a question.
	parked         bool
	parkedPromptID string
}

// terminalInfo is what the handler tells the service's finish callback
// about the ending (FIX118): the cause and, on a failed run, the code.
func (res streamDrainResult) terminalInfo() service.TerminalInfo {
	code := res.errCode
	if res.err != nil && code == "" {
		code = CodeInternalError
	}
	if res.err == nil {
		code = ""
	}
	return service.TerminalInfo{Cause: res.cause, ErrorCode: code, StopReason: res.stopReason}
}

// terminalErrorPayload builds the terminal “error“ frame's data from
// whatever the drain learned. Falls back to a generic internal error so a
// failed run always carries a code the client can localise.
func (res streamDrainResult) terminalErrorPayload() map[string]any {
	code, msg := res.errCode, res.errMessage
	if code == "" {
		code = CodeInternalError
	}
	if msg == "" {
		msg = "the stream ended without a result"
	}
	out := map[string]any{"code": code, "message": msg}
	if res.cause != "" {
		out["cause"] = res.cause
	}
	if res.errDetails != nil {
		out["details"] = res.errDetails
	}
	if res.transport != "" {
		// FIX118 — the Go error text is diagnostic, not a sentence for a
		// person; it rides details so a log reader can still see it.
		d, _ := out["details"].(map[string]any)
		if d == nil {
			d = map[string]any{}
		}
		d["transport"] = res.transport
		out["details"] = d
	}
	if res.gatewayRequestID != "" || res.providerRequestID != "" {
		// FIX119 — the backend's and the provider's ids for the call that
		// failed, so support can walk from the card to the logs.
		d, _ := out["details"].(map[string]any)
		if d == nil {
			d = map[string]any{}
		}
		if res.gatewayRequestID != "" {
			d["gateway_request_id"] = res.gatewayRequestID
		}
		if res.providerRequestID != "" {
			d["provider_request_id"] = res.providerRequestID
		}
		out["details"] = d
	}
	if res.errAttempts != nil {
		out["attempts"] = res.errAttempts
	}
	if res.errRetryable != nil {
		out["retryable"] = *res.errRetryable
	}
	return out
}

// completeWithModel adds the served model to a “message_complete“
// payload (FIX35 Part B) so the client's per-message meta line can name
// the model an "auto" turn was answered by without a reload.
func completeWithModel(completeData map[string]any, servedModel string) {
	if servedModel != "" {
		completeData["model_id"] = servedModel
	}
}

// completeWithFallback (FIX120) — the requested model when a fallback
// answered, beside model_id on the terminal events.
func completeWithFallback(completeData map[string]any, fallbackFrom string) {
	if fallbackFrom != "" {
		completeData["fallback_from"] = fallbackFrom
	}
}

// drainStreamEvents forwards service stream updates to the browser
// as SSE frames until the events channel yields Done, an error, or
// closes. Shared by sendStream, Regenerate and Edit — the three
// loops were byte-identical.
//
// “done“ is the request context's Done channel. When it fires
// (client disconnected) the loop invokes “onDisconnect“ ONCE and
// then sets “done“ to nil: a cancelled context's Done channel is
// permanently ready in select, so continuing to select on it would
// busy-spin at 100% CPU until the service pump emits its terminal
// event. A nil channel blocks forever in select, so after the first
// firing only “events“ drives the drain. The drain always
// terminates: pumpStreamWithTools sends a terminal update (Err or
// Done) and defer-closes the channel on every exit path.
//
// Phase 5 — what “onDisconnect“ should be:
//   - primary streams (Send/Regenerate/Edit): “detachOnDisconnect“.
//     The generation KEEPS RUNNING server-side (writes to the dead
//     ResponseWriter fail silently), the drain still accumulates the
//     full content, and Finish persists it — so a refreshed client
//     can reattach via GET .../messages/{id}/stream and receive the
//     rest. The user-initiated Stop path is unaffected: it goes
//     through POST /cancel → CancelStream, never through this hook.
//   - resume streams: the broker unsubscribe — detaching a viewer
//     must never touch the generation itself.
//
// detachOnDisconnect is the Phase 5 disconnect hook for primary
// streams: do nothing — let the generation finish server-side so the
// client can resume. (Pre-Phase-5 behaviour was stream.Cancel here,
// which killed the generation on every refresh/network blip.)
func detachOnDisconnect() {}

// heartbeatInterval is how often an SSE comment frame (": ping") goes
// out while a generation is in flight with no real event to send
// (Batch 2 T2). Two jobs: intermediate proxies stop idle-killing
// quiet streams (long tool runs), and the clients' idle watchdogs get
// a liveness signal to reset on — so the watchdog fires only on a dead
// connection, a hung chat-edge, or a buffering proxy. A stalled
// UPSTREAM is NOT covered: the ticker fires during any quiet stretch,
// including one where the provider has gone silent, so the client
// clock keeps resetting and the stall stays invisible (verified live,
// Batch 2 A2: 130s provider pause → pings every 15s, no pill).
// Detecting that needs upstream-activity tracking in the pump —
// tracked as the provider-stall-detection follow-up.
// Comment frames are ignored by both clients' parsers (web
// lib/stream.ts parseFrame yields nothing without a data: line;
// desktop SseStreamReader skips lines starting with ':') and are
// written at the HTTP edge ONLY — they never enter the stream broker,
// so resume replays stay heartbeat-free by construction, and they
// never touch the persistence/orphan-settlement clocks, which read
// database timestamps.
// A var (not const) purely so the stream tests can shorten it.
var heartbeatInterval = 15 * time.Second

// writeSSEHeartbeat emits the comment frame. Failures are silently
// dropped like writeSSEEvent's — a dead connection is detected by the
// request context, not by write errors.
func writeSSEHeartbeat(w http.ResponseWriter, flusher http.Flusher) {
	_, _ = fmt.Fprint(w, ": ping\n\n")
	flusher.Flush()
}

// startPreDrainHeartbeat (FIX130 C1) pings an SSE response that is open
// before its drain loop runs — the FIX33 early open for compaction. Each
// ping is written under mu, the lock every other writer of that stretch
// holds. The returned stop is idempotent and waits for the pinger to exit,
// so nothing it wrote can follow a frame the caller writes afterwards.
func startPreDrainHeartbeat(w http.ResponseWriter, flusher http.Flusher, mu *sync.Mutex) (stop func()) {
	quit := make(chan struct{})
	exited := make(chan struct{})
	go func() {
		defer close(exited)
		t := time.NewTicker(heartbeatInterval)
		defer t.Stop()
		for {
			select {
			case <-quit:
				return
			case <-t.C:
				mu.Lock()
				writeSSEHeartbeat(w, flusher)
				mu.Unlock()
			}
		}
	}()
	var once sync.Once
	return func() {
		once.Do(func() {
			close(quit)
			<-exited
		})
	}
}

func drainStreamEvents(
	w http.ResponseWriter,
	flusher http.Flusher,
	done <-chan struct{},
	events <-chan service.StreamUpdate,
	onDisconnect func(),
) streamDrainResult {
	var (
		content   strings.Builder
		reasoning strings.Builder
		res       streamDrainResult
	)
	// T2 — keepalive ticker. Fires only when neither `done` nor
	// `events` is ready, i.e. exactly the quiet stretches (slow
	// provider, long tool run) that need a heartbeat. Stops with the
	// drain; writes to an already-disconnected client fail silently.
	heartbeat := time.NewTicker(heartbeatInterval)
	defer heartbeat.Stop()
Loop:
	for {
		select {
		case <-heartbeat.C:
			writeSSEHeartbeat(w, flusher)
		case <-done:
			// Client disconnected — fire the disconnect hook once,
			// keep draining until the pump's terminal event (see doc
			// comment: nil channel = no busy-spin).
			onDisconnect()
			done = nil
		case ev, ok := <-events:
			if !ok {
				break Loop
			}
			if ev.Err != nil {
				// FIX41: record it, do NOT spend the ``error`` event
				// here. The caller emits exactly one terminal after the
				// files/sources read-back, and it may not be ``error`` at
				// all — a user pressing Stop arrives on this same branch
				// as "context canceled", and reporting that to every
				// other viewer as an internal error was P3's finding.
				//
				// FIX118 — a transport-level failure is classified into a
				// catalogue code + terminal cause HERE, so the terminal
				// frame carries the same code the non-streaming path would
				// and never the raw Go error text as its message. A user
				// cancel also lands here (context canceled); finishStream
				// asks the registration first and takes the `cancelled`
				// branch, so the classification below is only what a
				// stream that ended by itself gets.
				res.err = ev.Err
				res.transport = ev.Err.Error()
				f := service.ClassifyStreamError(ev.Err)
				res.errCode = f.Code
				res.errMessage = f.Message
				res.cause = f.Cause
				retry := f.Retryable
				res.errRetryable = &retry
				if f.Unmapped {
					slog.Warn("stream_error_unmapped",
						"err_type", fmt.Sprintf("%T", ev.Err), "err", ev.Err.Error(),
						"code", f.Code, "cause", f.Cause)
				}
				break Loop
			}
			if ev.Delta != "" {
				content.WriteString(ev.Delta)
				writeSSEEvent(w, flusher, "content_delta", map[string]any{
					"delta": ev.Delta,
				})
			}
			// T4: reasoning delta + analysis status forwarded as
			// dedicated SSE event verbs so the frontend can render
			// the ThinkingBlock without parsing inside content_delta.
			if ev.ReasoningDelta != "" {
				reasoning.WriteString(ev.ReasoningDelta)
				writeSSEEvent(w, flusher, "reasoning_delta", map[string]any{
					"delta": ev.ReasoningDelta,
				})
			}
			if ev.AnalysisStatus != "" {
				writeSSEEvent(w, flusher, "analysis_status", map[string]any{
					"status": ev.AnalysisStatus,
				})
			}
			// FIX133 A2 — where the turn stands in the gateway's queue.
			if ev.Queued != nil {
				writeSSEEvent(w, flusher, "queued", ev.Queued)
			}
			// FIX133 E6a — answered inside the self-hosted boundary (KB passages).
			if ev.KBNotice != nil {
				writeSSEEvent(w, flusher, "kb_notice", ev.KBNotice)
			}
			// Image-generation phase marker — the frontend swaps the
			// typing dots for an image placeholder until message_files
			// (or a terminal frame) arrives.
			if ev.ImageStatus != "" {
				writeSSEEvent(w, flusher, "image_generating", map[string]any{
					"status": ev.ImageStatus,
				})
			}
			if ev.VideoStatus != "" {
				writeSSEEvent(w, flusher, "video_generating", map[string]any{
					"status": ev.VideoStatus,
				})
			}
			// Web-search phase marker — the frontend shows a "searching
			// the web" indicator until the answer streams / sources land.
			if ev.SearchStatus != "" {
				writeSSEEvent(w, flusher, "search_status", map[string]any{
					"status": ev.SearchStatus,
				})
			}
			// FIX33 A-W9 — compaction phase marker ("summarizing").
			if ev.CompactionStatus != "" {
				writeSSEEvent(w, flusher, "compaction_status", map[string]any{
					"status": ev.CompactionStatus,
				})
			}
			// Phase 8 Task 2 — run_python progress: the code the model
			// wrote, then its stdout/stderr + exit status. Rendered as a
			// code+output card. Deliberately NOT persisted on the message
			// row: chat-edge stores DLP-masked content only and has no
			// masking path of its own, so writing raw sandbox stdout at
			// rest would open a new plaintext surface. It survives a
			// mid-stream reconnect via the resume snapshot below; after
			// the turn completes only the produced files remain.
			if ev.CodeExecution != nil {
				writeSSEEvent(w, flusher, "code_execution", ev.CodeExecution)
			}
			// Generic per-tool activity (tools without a dedicated
			// event). Ephemeral like code_execution: not persisted,
			// survives mid-stream reconnects via the resume snapshot.
			if ev.ToolActivity != nil {
				writeSSEEvent(w, flusher, "tool_activity", ev.ToolActivity)
			}
			// B1: a provider error frame from the Gateway. Forward it as
			// an ``error`` event so the client renders an error bubble
			// rather than ending blank. Not a ``break`` — the Gateway
			// closes the stream after, which yields the synthetic Done +
			// message_complete terminator in the caller.
			if ev.ProviderError != nil {
				// FIX41: same as ev.Err — carried to the terminal frame
				// instead of being spent here, so the run ends on
				// ``error`` and the client never has to guess whether a
				// mid-stream error frame was the end or not. The cost is
				// that the error surfaces one read-back later (a DB round
				// trip on a turn that produced no files anyway); the
				// benefit is one unambiguous terminal.
				res.err = fmt.Errorf("provider error: %s", ev.ProviderError.Code)
				res.errCode = ev.ProviderError.Code
				res.errMessage = ev.ProviderError.Message
				res.cause = service.TerminalCauseError
				if ev.ProviderError.Details != nil {
					res.errDetails = ev.ProviderError.Details
				}
				if len(ev.ProviderError.Attempts) > 0 {
					res.errAttempts = ev.ProviderError.Attempts
				}
				res.errRetryable = ev.ProviderError.Retryable
				if ev.ProviderError.RequestID != "" {
					res.gatewayRequestID = ev.ProviderError.RequestID
				}
			}
			// B2: a fallback notice — which model actually answered.
			if ev.FallbackInfo != nil {
				writeSSEEvent(w, flusher, "fallback_info", ev.FallbackInfo)
				// FIX120 — keep the requested model for the row so a reload
				// shows the same notice; the answering model reaches
				// servedModel through the chunks' own ``model``.
				if from, actual := service.FallbackFromInfo(ev.FallbackInfo); from != "" {
					res.fallbackFrom = from
					if res.servedModel == "" {
						res.servedModel = actual
					}
				}
			}
			// FIX50 P1-C: the question was partially masked before the
			// model read it — counts only, never values.
			if ev.DlpNotice != nil {
				writeSSEEvent(w, flusher, "dlp_notice", ev.DlpNotice)
			}
			// FIX35 Part B — which model IS answering, as soon as the
			// gateway names it (before content on an auto /
			// openrouter/auto route), not only at the end.
			if ev.ServedModel != "" {
				res.servedModel = ev.ServedModel
				writeSSEEvent(w, flusher, "model_resolved", map[string]any{"model": ev.ServedModel})
			}
			// Reasoning downgrade notice — informational, never an
			// error: the answer arrived without the requested thinking.
			if ev.ReasoningNotice != nil {
				writeSSEEvent(w, flusher, "reasoning_notice", ev.ReasoningNotice)
			}
			// Provider stall / recovery. Unlike the heartbeat (an HTTP-edge
			// comment frame), this is a real typed event: only chat-edge
			// can tell provider silence from server work, so it says so
			// explicitly instead of leaving the client to guess from bytes.
			if ev.ProviderStall != nil {
				writeSSEEvent(w, flusher, "provider_stall", ev.ProviderStall)
			}
			// One or more attached images couldn't be read — informational,
			// never an error: the turn proceeds text-only for those images.
			if ev.ImageNotice != nil {
				writeSSEEvent(w, flusher, "image_notice", ev.ImageNotice)
			}
			// FIX102 Part C — older messages left out of this turn without a
			// summary standing in for all of them. Informational, not an error.
			if ev.CompactionNotice != nil {
				writeSSEEvent(w, flusher, "compaction_notice", ev.CompactionNotice)
			}
			// FIX130 A3 — the answer used saved memories / saved one.
			if ev.MemoryUsed != nil {
				writeSSEEvent(w, flusher, "memory_used", ev.MemoryUsed)
			}
			if ev.MemorySaved != nil {
				writeSSEEvent(w, flusher, "memory_saved", ev.MemorySaved)
			}
			// FIX74 — the model asked the user a question. The card goes out
			// as its own frame (recorded in the ledger, so a reattach replays
			// it), then the run ends on message_paused below.
			if ev.Question != nil {
				res.parkedPromptID = ev.Question.PromptID
				writeSSEEvent(w, flusher, "question", ev.Question)
			}
			if ev.FinishReason != "" {
				res.finishReason = ev.FinishReason
			}
			if ev.StopReason != "" {
				res.stopReason = ev.StopReason
			}
			if ev.ToolLimit > 0 {
				res.toolLimit = ev.ToolLimit
			}
			if ev.ProviderRequestID != "" {
				res.providerRequestID = ev.ProviderRequestID
			}
			if ev.GatewayRequestID != "" {
				res.gatewayRequestID = ev.GatewayRequestID
			}
			if ev.Usage != nil {
				res.usage = ev.Usage
			}
			if ev.Parked {
				res.parked = true
				break Loop
			}
			if ev.Done {
				break Loop
			}
		}
	}
	res.content = content.String()
	res.reasoning = reasoning.String()
	return res
}

func (h *MessageHandler) sendStream(w http.ResponseWriter, r *http.Request, in service.SendInput) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}

	// FIX41 — the run id is minted HERE, not in the service, because the
	// early-open path below writes frames before SendStream returns. One
	// id, one seq counter, one ledger for the whole run.
	//
	// FIX119 — and it IS the request id: the one the Logging middleware
	// minted or honoured, the one on this response's X-Request-Id header,
	// the one sent upstream as X-Request-Id, the one the row stores.
	runID := in.RequestID
	if runID == "" {
		runID = uuid.NewString()
		in.RequestID = runID
	}
	in.RunID = runID
	rw := newRunWriter(w, runID, nil)
	// Whatever happens below, the run ends on a terminal frame. A `return`
	// that leaves the pipe silent is not a valid ending (STREAM_CONTRACT
	// §3) — and there were two such returns before FIX41.
	defer rw.ensureTerminal(flusher)

	// SSE headers — must be set BEFORE any write, and BEFORE the
	// first flush. Factored so the FIX33 A-W9 early-open path below and
	// the normal path write them exactly once.
	started := false
	openStream := func(assistantID, userMessageID uuid.UUID) {
		if started {
			return
		}
		started = true
		rw.started = true
		// Register the run with the resume broker now, so the frames
		// written during the early-open pause are numbered by the same
		// counter as the ones the pump will produce.
		h.messages.OpenRun(assistantID, runID)
		rw.record = func(event string, build func(seq, final int64) []byte) (int64, bool) {
			return h.messages.RecordFrame(assistantID, event, build)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.Header().Set("X-Accel-Buffering", "no")
		w.WriteHeader(http.StatusOK)
		// FIX28 §2 (F3): the client renders the user turn under an optimistic
		// temp id; hand it the persisted row id here (Edit already did) so an
		// edit → fork can name a real message without a reload.
		startPayload := map[string]any{
			"message_id":      assistantID.String(),
			"conversation_id": in.ConversationID.String(),
		}
		if userMessageID != uuid.Nil {
			startPayload["user_message_id"] = userMessageID.String()
		}
		writeSSEEvent(rw, flusher, "message_start", startPayload)
	}
	// FIX33 A-W9 — when the compiler is about to run the synchronous
	// state extraction (a pause of several seconds), open the stream NOW
	// and tell the user the conversation is being organised. Without
	// this the first byte only arrives after the extraction, and the
	// pause looks like a hang.
	//
	// FIX130 C1 — from that early open until the drain starts nothing
	// heartbeats: the extraction (bounded at 90 s) and the model call run
	// with the response already open, and the clients' read deadlines would
	// read the silence as a dead connection. A pinger covers that stretch;
	// every write before the drain takes preMu so a ping never lands inside
	// a frame, and stopEarlyBeat returns only once the pinger is gone.
	var preMu sync.Mutex
	stopEarlyBeat := func() {}
	in.OnCompactionStart = func(assistantID, userMessageID uuid.UUID) {
		preMu.Lock()
		first := !started
		openStream(assistantID, userMessageID)
		writeSSEEvent(rw, flusher, "compaction_status", map[string]any{"status": "summarizing"})
		preMu.Unlock()
		if first {
			stopEarlyBeat = startPreDrainHeartbeat(rw, flusher, &preMu)
		}
	}
	// FIX117 — after every extraction batch, raw counts; compaction_status
	// above is untouched, so a client that only knows that frame sees
	// exactly what it saw before.
	in.OnCompactionProgress = func(p service.ExtractionProgress) {
		preMu.Lock()
		defer preMu.Unlock()
		writeSSEEvent(rw, flusher, "compaction_progress", compactionProgressPayload(p))
	}

	stream, err := h.messages.SendStream(r.Context(), in)
	stopEarlyBeat()
	if errors.Is(err, service.ErrSendDuplicate) && !started {
		// FIX121 — lost the user-row insert to another copy of this request.
		if !h.replayIfRepeated(w, r, in, true) {
			WriteError(w, http.StatusConflict, CodeRequestInProgress,
				"a request with this X-Request-Id is still being accepted; retry shortly")
		}
		return
	}
	if err != nil {
		if started {
			// The response is already an open SSE stream: the error must
			// travel as a frame, not as a JSON status the client will never
			// parse.
			// Terminal: this run failed before it produced anything.
			writeSSEEvent(rw, flusher, "error", sendErrorFrame(err))
			return
		}
		h.writeSendError(w, err)
		return
	}

	userMessageID := uuid.Nil
	if stream.UserMessage != nil {
		userMessageID = stream.UserMessage.ID
	}
	openStream(stream.AssistantID, userMessageID)

	res := drainStreamEvents(rw, flusher, r.Context().Done(), stream.Events, detachOnDisconnect)

	// Persist via the handle's finish callback. Uses
	// context.Background() internally so the write completes even
	// after the request context is cancelled (client disconnected).
	persistFinishReason := res.finishReason
	if res.err != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.FinishWith(res.content, res.reasoning, persistFinishReason, res.usage, res.terminalInfo())
	// FIX35 Part B — attribute the row to the model that answered.
	if res.servedModel != "" {
		_ = h.messages.SetServedModel(context.Background(), stream.AssistantID, res.servedModel)
	}
	if res.fallbackFrom != "" { // FIX120
		_ = h.messages.SetFallbackFrom(context.Background(), stream.AssistantID, res.fallbackFrom)
	}
	if res.errAttempts != nil { // FIX121 — a failed turn keeps what was tried
		_ = h.messages.SetAttempts(context.Background(), stream.AssistantID, res.errAttempts)
	}
	h.finishStream(rw, flusher, r.Context(), in.TenantID, stream, res)
}

// finishStream writes the files/sources read-back and then the run's ONE
// terminal event (STREAM_CONTRACT §3). Shared by Send / Regenerate / Edit
// so all three answer the same question the same way: a run that a person
// stopped says “cancelled“, a run that failed ENDS on “error“, and
// only a run that produced its answer says “message_complete“.
//
// Before FIX41 each of the three ended unconditionally on
// message_complete, so the last frame of a failed turn was the success
// terminator and a cancel was indistinguishable from a normal finish
// (FIX41 Part A's baseline record, P3 and P4).
func (h *MessageHandler) finishStream(
	rw *runWriter,
	flusher http.Flusher,
	ctx context.Context,
	tenantID uuid.UUID,
	stream *service.StreamHandle,
	res streamDrainResult,
) {
	h.writeMessageFiles(rw, flusher, ctx, tenantID, stream.AssistantID)
	h.writeMessageSources(rw, flusher, ctx, tenantID, stream.AssistantID)

	tokens := map[string]int(nil)
	if res.usage != nil {
		tokens = map[string]int{
			"input":  res.usage.PromptTokens,
			"output": res.usage.CompletionTokens,
		}
		// T4: surface reasoning_tokens when the Gateway broke it out
		// (OpenAI o-series / Anthropic estimate / Gemini thoughts).
		if res.usage.ReasoningTokens > 0 {
			tokens["reasoning"] = res.usage.ReasoningTokens
		}
	}

	// FIX118 — a person's Stop is a cause of its own, never an error.
	if stream.UserCancelled() {
		res.cause = service.TerminalCauseStoppedByUser
	}
	switch {
	case res.parked:
		// FIX74 — the turn is waiting for the user's answer, in the
		// database. The card was sent as its own frame; this terminal tells
		// the client the stream is over WITHOUT calling the turn finished.
		data := map[string]any{
			"message_id": stream.AssistantID.String(),
			"prompt_id":  res.parkedPromptID,
		}
		completeWithModel(data, res.servedModel)
		writeSSEEvent(rw, flusher, "message_paused", data)
	case stream.UserCancelled():
		// A person pressed Stop. Everything produced so far stands; the
		// answer is simply partial, and the client is told so rather than
		// being left to infer it from a stream that went quiet.
		data := map[string]any{
			"message_id": stream.AssistantID.String(),
			"partial":    true,
			"cause":      service.TerminalCauseStoppedByUser,
		}
		completeWithModel(data, res.servedModel)
		completeWithFallback(data, res.fallbackFrom)
		if tokens != nil {
			data["token_usage"] = tokens
		}
		writeSSEEvent(rw, flusher, "cancelled", data)
	case stream.Interrupted():
		// FIX131 E2 — the shutdown drain cut this run at its deadline. The
		// live stream ends the way the row does (and the way a resume after
		// the restart replays it): interrupted, retryable, text kept.
		writeSSEEvent(rw, flusher, "error", map[string]any{
			"code":      service.CodeGenerationInterrupted,
			"message":   "generation was interrupted",
			"cause":     service.TerminalCauseInterrupted,
			"retryable": true,
		})
	case res.err != nil:
		writeSSEEvent(rw, flusher, "error", res.terminalErrorPayload())
	default:
		data := map[string]any{"message_id": stream.AssistantID.String()}
		completeWithModel(data, res.servedModel)
		completeWithFallback(data, res.fallbackFrom)
		if tokens != nil {
			data["token_usage"] = tokens
		}
		// FIX119 — why the model stopped, so the page can say "the answer
		// was cut off" instead of presenting a max_tokens cut as finished.
		if res.stopReason != "" {
			data["stop_reason"] = res.stopReason
		}
		// FIX124 — the turn stopped at the tool-round cap with no answer:
		// stop_reason is tool_use, and this is how many rounds ran, for the
		// page's "stopped after N tool steps".
		if res.toolLimit > 0 {
			data["tool_rounds"] = res.toolLimit
		}
		if res.gatewayRequestID != "" {
			data["gateway_request_id"] = res.gatewayRequestID
		}
		writeSSEEvent(rw, flusher, "message_complete", data)
	}
}

// writeSSEEvent serialises one SSE frame and flushes. “data“ is
// JSON-marshalled inline; failures are silently dropped because at
// this point in the request we've already committed to a 200
// response — the client will see a missing frame, the SSE clean-up
// loop on the browser handles the missing terminator.
func (h *MessageHandler) writeMessageFiles(
	w http.ResponseWriter,
	flusher http.Flusher,
	ctx context.Context,
	tenantID uuid.UUID,
	messageID uuid.UUID,
) {
	if h.files == nil {
		return
	}
	byMsg, err := h.files.ListByMessageIDs(ctx, tenantID, []uuid.UUID{messageID})
	if err != nil {
		return
	}
	files := byMsg[messageID]
	if len(files) == 0 {
		return
	}
	out := make([]fileResponse, 0, len(files))
	for i := range files {
		out = append(out, FileToWire(&files[i]))
	}
	writeSSEEvent(w, flusher, "message_files", map[string]any{
		"message_id": messageID.String(),
		"files":      out,
	})
	slog.Info("sse_message_files_sent",
		"tenant_id", tenantID.String(),
		"message_id", messageID.String(),
		"file_count", len(out),
	)
}

// writeMessageSources emits the web_search citations persisted on the
// assistant message (read-back pattern matching writeMessageFiles). The
// frontend renders these as citation chips and clears the searching
// indicator. No-op when the turn didn't search.
func (h *MessageHandler) writeMessageSources(
	w http.ResponseWriter,
	flusher http.Flusher,
	ctx context.Context,
	tenantID uuid.UUID,
	messageID uuid.UUID,
) {
	raw, err := h.messages.GetSearchSources(ctx, tenantID, messageID)
	if err != nil || len(raw) == 0 {
		return
	}
	// Forward the stored JSON array verbatim under "sources".
	writeSSEEvent(w, flusher, "message_sources", map[string]any{
		"message_id": messageID.String(),
		"sources":    json.RawMessage(raw),
	})
	slog.Info("sse_message_sources_sent",
		"tenant_id", tenantID.String(),
		"message_id", messageID.String(),
	)
}

func writeSSEEvent(w http.ResponseWriter, flusher http.Flusher, event string, data any) {
	buf, err := json.Marshal(data)
	if err != nil {
		return
	}
	// FIX41 — a stream that was opened for a run writes through the
	// envelope (run_id / seq / final_seq + the ledger). Everything else
	// keeps the pre-FIX41 shape, so a caller that never wraps w — a unit
	// test, a future non-run stream — is unaffected.
	if rw, ok := w.(*runWriter); ok {
		rw.emit(flusher, event, buf)
		return
	}
	_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, buf)
	flusher.Flush()
}

// writeSendError maps service errors to HTTP responses for the
// non-stream path AND for the pre-stream error path (when
// SendStream returns before any SSE frame goes out).
// sendErrorMessage is writeSendError's message half, for the FIX33 A-W9
// case where the SSE stream is already open and the error must travel
// as a frame.
// gatewayEnvelope finds the gateway's typed error envelope in a send
// error, when the gateway answered the request with one (a 4xx before
// the stream opened: no model can serve the request's tools, the prompt
// is over the context window, a policy block). The client localises on
// the code; the status is the gateway's own.
func gatewayEnvelope(err error) (status int, env service.GatewayErrorEnvelope, ok bool) {
	var ue *service.GatewayUnavailableError
	if !errors.As(err, &ue) {
		return 0, service.GatewayErrorEnvelope{}, false
	}
	env, ok = ue.Envelope()
	return ue.Status, env, ok
}

// sendErrorFrame is the code + message for an "error" frame on a send
// that failed before the stream produced anything.
func sendErrorFrame(err error) map[string]any {
	if errors.Is(err, service.ErrConversationBusy) {
		return map[string]any{"code": CodeConversationBusy, "message": conversationBusyMessage}
	}
	var tm *service.TooManyFilesError
	if errors.As(err, &tm) {
		return map[string]any{"code": CodeTooManyFiles, "message": tooManyFilesMessage(tm)}
	}
	if _, env, ok := gatewayEnvelope(err); ok {
		out := map[string]any{"code": env.Code, "message": env.Message}
		if env.Retryable != nil {
			out["retryable"] = *env.Retryable
		}
		return out
	}
	return map[string]any{"code": "send_failed", "message": sendErrorMessage(err)}
}

// writeGatewayEnvelope answers a not-yet-streaming request with the
// gateway's own status, code and message. False when there was none.
func writeGatewayEnvelope(w http.ResponseWriter, err error) bool {
	status, env, ok := gatewayEnvelope(err)
	if !ok {
		return false
	}
	if status < 400 || status > 599 {
		status = http.StatusBadGateway
	}
	WriteError(w, status, env.Code, env.Message)
	return true
}

func sendErrorMessage(err error) string {
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		return "conversation not found"
	case errors.Is(err, service.ErrMessageEmptyContent):
		return "content is required"
	case strings.Contains(err.Error(), "insufficient_credits"):
		return "เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต"
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		return err.Error()
	default:
		return "internal error"
	}
}

func (h *MessageHandler) writeSendError(w http.ResponseWriter, err error) {
	if writeConversationBusy(w, err) {
		return
	}
	if writeTooManyFiles(w, err) {
		return
	}
	switch {
	case errors.Is(err, service.ErrMessageConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrMessageEmptyContent):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "content is required")
	case strings.Contains(err.Error(), "insufficient_credits"):
		// Gateway's portal credit gate fired (cost-based deduction).
		// 402 + a stable code so the frontend shows the Thai
		// credits-exhausted notice instead of a generic provider error.
		WriteError(w, http.StatusPaymentRequired, "INSUFFICIENT_CREDITS",
			"เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		if writeGatewayEnvelope(w, err) {
			return
		}
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", "the AI service is unavailable")
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
	// FIX74 — a parked turn has no stream; Stop closes its question as
	// cancelled (never as an answer).
	if h.messages.CancelPendingQuestion(r.Context(), caller.TenantUUID, caller.UserID, convID) {
		cancelled = true
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"cancelled": cancelled})
}

// ---------------------------------------------------------------------------
// FIX74 — answer a parked question
// ---------------------------------------------------------------------------

type answerQuestionRequest struct {
	Outcome  string              `json:"outcome"`
	Answers  map[string][]string `json:"answers"`
	Response *string             `json:"response"`
}

// AnswerQuestion resumes a parked turn with the user's answer and streams
// the continuation on the SAME assistant message id.
//
//	POST /api/v1/conversations/{id}/questions/{promptID}
//	→ SSE stream (message_start … message_complete)
//	→ 409 QUESTION_ALREADY_ANSWERED when another tab got there first
//	→ 410 QUESTION_EXPIRED after the 24 h window
func (h *MessageHandler) AnswerQuestion(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	promptID, err := uuid.Parse(chi.URLParam(r, "promptID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid question id")
		return
	}
	var body answerQuestionRequest
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}
	in := service.AnswerQuestionInput{
		TenantID:       caller.TenantUUID,
		UserID:         caller.UserID,
		ConversationID: convID,
		PromptID:       promptID,
		Answer:         service.QuestionAnswer{Outcome: body.Outcome, Answers: body.Answers, Response: body.Response},
		RequestID:      middleware.RequestIDFrom(r.Context()),
	}
	if in.RequestID == "" {
		in.RequestID = uuid.NewString()
	}
	in.RunID = in.RequestID // FIX119 — one id per turn
	stream, err := h.messages.ResumeQuestion(r.Context(), in)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrQuestionInvalid):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "invalid answer")
		case errors.Is(err, service.ErrQuestionNotFound), errors.Is(err, service.ErrMessageConversationNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "question not found")
		case errors.Is(err, service.ErrQuestionNotPending):
			WriteError(w, http.StatusConflict, "QUESTION_ALREADY_ANSWERED", "this question was already answered")
		case errors.Is(err, service.ErrQuestionExpired):
			WriteError(w, http.StatusGone, "QUESTION_EXPIRED", "this question expired before it was answered")
		default:
			h.writeSendError(w, err)
		}
		return
	}
	rw := newRunWriter(w, in.RunID, nil)
	defer rw.ensureTerminal(flusher)
	rw.started = true
	h.messages.OpenRun(stream.AssistantID, in.RunID)
	rw.record = func(event string, build func(seq, final int64) []byte) (int64, bool) {
		return h.messages.RecordFrame(stream.AssistantID, event, build)
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	writeSSEEvent(rw, flusher, "message_start", map[string]any{
		"message_id":      stream.AssistantID.String(),
		"conversation_id": convID.String(),
		"resumed":         true,
		"prompt_id":       promptID.String(),
	})
	res := drainStreamEvents(rw, flusher, r.Context().Done(), stream.Events, detachOnDisconnect)
	persistFinishReason := res.finishReason
	if res.err != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.FinishWith(res.content, res.reasoning, persistFinishReason, res.usage, res.terminalInfo())
	if res.servedModel != "" {
		_ = h.messages.SetServedModel(context.Background(), stream.AssistantID, res.servedModel)
	}
	if res.fallbackFrom != "" { // FIX120
		_ = h.messages.SetFallbackFrom(context.Background(), stream.AssistantID, res.fallbackFrom)
	}
	if res.errAttempts != nil { // FIX121 — a failed turn keeps what was tried
		_ = h.messages.SetAttempts(context.Background(), stream.AssistantID, res.errAttempts)
	}
	h.finishStream(rw, flusher, r.Context(), caller.TenantUUID, stream, res)
}

// ---------------------------------------------------------------------------
// Resume (Phase 5 — stream resume)
// ---------------------------------------------------------------------------

// ResumeStream reattaches a client to an in-flight generation and
// replays what it missed. Emits the SAME SSE event vocabulary as Send
// (message_start / content_delta / … / message_complete) so the
// frontend consumes it with the identical pipeline — the prefix simply
// arrives as one large content_delta.
//
//	GET /api/v1/conversations/{id}/messages/{messageID}/stream
//	→ SSE stream
//	→ 409 RESUME_UNAVAILABLE when the generation isn't attachable yet
//	  (client retries with backoff)
//
// This endpoint NEVER calls StreamHandle.Finish — persistence belongs
// to the primary drain (which keeps running detached after the
// original client disconnected). Disconnecting from a resume stream
// only unsubscribes the viewer; the generation is untouched.
func (h *MessageHandler) ResumeStream(w http.ResponseWriter, r *http.Request) {
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
	// FIX41 / STREAM_CONTRACT section 4 - starting_after is the client's
	// cursor: replay only what it has not already rendered. Absent (or 0)
	// keeps the pre-FIX41 behaviour of replaying the whole run, so a
	// client that has not been updated is unaffected.
	startingAfter := int64(0)
	if raw := strings.TrimSpace(r.URL.Query().Get("starting_after")); raw != "" {
		n, convErr := strconv.ParseInt(raw, 10, 64)
		if convErr != nil || n < 0 {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "starting_after must be a non-negative integer")
			return
		}
		startingAfter = n
	}
	h.serveResume(w, r, caller.TenantUUID, caller.UserID, convID, msgID, uuid.Nil, startingAfter)
}

// serveResume attaches this response to a generation and replays what the
// caller has not seen. Two callers: ResumeStream (a viewer reattaching) and,
// since FIX121, a repeated send (replayIfRepeated), which names the user row
// too so the replayed message_start can hand the client its persisted id.
func (h *MessageHandler) serveResume(
	w http.ResponseWriter, r *http.Request,
	tenantID, userID, convID, msgID, userMsgID uuid.UUID, startingAfter int64,
) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "streaming not supported by transport")
		return
	}
	att, err := h.messages.ResumeAttachFrom(
		r.Context(), tenantID, userID, convID, msgID, startingAfter,
	)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrResumeUnavailable):
			WriteError(w, http.StatusConflict, "RESUME_UNAVAILABLE",
				"generation not attachable yet")
		case errors.Is(err, service.ErrResumeTooOld):
			// The frames this client is missing are gone. Saying
			// "complete" here would show a truncated answer as if it were
			// the whole one; the client must reload the message instead.
			WriteError(w, http.StatusConflict, "RESUME_TOO_OLD",
				"the stream position requested is older than the retained ledger; reload the message")
		case errors.Is(err, service.ErrMessageConversationNotFound),
			errors.Is(err, service.ErrMessageNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
		case errors.Is(err, service.ErrMessageNotAssistant):
			WriteError(w, http.StatusBadRequest, CodeBadRequest,
				"only assistant messages can be resumed")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	defer att.Cancel()

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	// FIX41 - the ledger path. Frames are replayed BYTE FOR BYTE with the
	// seq they originally went out under, then the live tail follows off
	// the same ledger. Nothing is re-derived, so nothing can be renumbered
	// or reordered: replaying a folded snapshot is what turned eight
	// frames into three and put the tool cards ahead of the text
	// (FIX41 Part A's baseline record, P1b).
	if att.Frames != nil {
		h.resumeFromLedger(w, flusher, r, msgID, att)
		return
	}

	rw := newReplayWriter(w, replayRunID(att))
	rw.started = true
	defer rw.ensureTerminal(flusher)

	startPayload := map[string]any{
		"message_id":      msgID.String(),
		"conversation_id": convID.String(),
	}
	if userMsgID != uuid.Nil {
		startPayload["user_message_id"] = userMsgID.String()
	}
	writeSSEEvent(rw, flusher, "message_start", startPayload)
	h.writeResumeSnapshot(rw, flusher, att.Snapshot)

	if att.Events != nil {
		// Live tail — same drain as the primary stream; disconnect
		// only unsubscribes this viewer.
		res := drainStreamEvents(rw, flusher, r.Context().Done(), att.Events, att.Cancel)
		if att.Lagged() {
			// This subscriber fell behind and was cut loose mid-stream.
			// Tell the client to reattach fresh instead of pretending
			// the message completed. This IS this response's terminal:
			// the run is fine, but this view of it ends here, and the
			// client must be able to tell that from a finished answer.
			writeSSEEvent(rw, flusher, "error", map[string]any{
				"code":    "RESUME_LAGGED",
				"message": "resume stream fell behind; reattach",
			})
			return
		}
		// The primary drain persists files/sources in its Finish
		// callback, which runs just after the same terminal event we
		// saw — give it a beat before reading them back.
		if r.Context().Err() == nil {
			time.Sleep(250 * time.Millisecond)
		}
		h.writeMessageFiles(rw, flusher, r.Context(), tenantID, msgID)
		h.writeMessageSources(rw, flusher, r.Context(), tenantID, msgID)
		if res.err != nil {
			writeSSEEvent(rw, flusher, "error", res.terminalErrorPayload())
			return
		}
		completeData := map[string]any{"message_id": msgID.String()}
		completeWithModel(completeData, att.Snapshot.ServedModel) // FIX35 Part B
		if res.usage != nil {
			tokenUsage := map[string]int{
				"input":  res.usage.PromptTokens,
				"output": res.usage.CompletionTokens,
			}
			if res.usage.ReasoningTokens > 0 {
				tokenUsage["reasoning"] = res.usage.ReasoningTokens
			}
			completeData["token_usage"] = tokenUsage
		}
		writeSSEEvent(rw, flusher, "message_complete", completeData)
		return
	}

	// Terminal replay — the generation already ended; the snapshot
	// carries the persisted row.
	interrupted := false
	if t := att.Snapshot.Terminal; t != nil && t.Err != nil {
		interrupted = true
	}
	h.writeMessageFiles(rw, flusher, r.Context(), tenantID, msgID)
	h.writeMessageSources(rw, flusher, r.Context(), tenantID, msgID)
	if interrupted {
		// FIX41: an interrupted generation ENDS on error. Before, this
		// wrote error and then message_complete, so the last thing a
		// client learned about a lost turn was the success terminator.
		writeSSEEvent(rw, flusher, "error", map[string]any{
			"code":    "GENERATION_INTERRUPTED",
			"message": "generation was interrupted",
		})
		return
	}
	completeData := map[string]any{"message_id": msgID.String()}
	completeWithModel(completeData, att.Snapshot.ServedModel) // FIX35 Part B
	if t := att.Snapshot.Terminal; t != nil && t.Usage != nil {
		tokenUsage := map[string]int{
			"input":  t.Usage.PromptTokens,
			"output": t.Usage.CompletionTokens,
		}
		if t.Usage.ReasoningTokens > 0 {
			tokenUsage["reasoning"] = t.Usage.ReasoningTokens
		}
		completeData["token_usage"] = tokenUsage
	}
	writeSSEEvent(rw, flusher, "message_complete", completeData)
}

// replayRunID names a replay that has no live run behind it (the row is
// already terminal). A synthetic id, not the message id: the client keys
// its cursor on run_id, and a replay must not be mistaken for a
// continuation of the generation that produced the row.
func replayRunID(att *service.ResumeAttachment) string {
	if att.Frames != nil && att.Frames.RunID != "" {
		return att.Frames.RunID
	}
	return "replay-" + uuid.NewString()
}

// resumeFromLedger serves a reattach off the recorded frames.
//
// The replay set and the live subscription were taken under one lock in
// SubscribeFrames, so no frame can fall between them and none can arrive
// twice. Every write here is verbatim: this response never mints a seq,
// because the run already has one for every frame, and two writers
// numbering the same run independently is precisely the gap-and-duplicate
// problem the ledger exists to remove.
func (h *MessageHandler) resumeFromLedger(
	w http.ResponseWriter,
	flusher http.Flusher,
	r *http.Request,
	msgID uuid.UUID,
	att *service.ResumeAttachment,
) {
	fa := att.Frames
	// Any frame this response has to synthesise continues the run's own
	// numbering — see newReplayWriterFrom.
	after := fa.FinalSeq
	if n := len(fa.Replay); n > 0 && fa.Replay[n-1].Seq > after {
		after = fa.Replay[n-1].Seq
	}
	rw := newReplayWriterFrom(w, fa.RunID, after)
	rw.started = true
	defer rw.ensureTerminal(flusher)

	for _, le := range fa.Replay {
		rw.writeRecorded(flusher, le.Event, le.Data)
	}
	if fa.Live == nil {
		if rw.terminalWritten() {
			return
		}
		if fa.Interrupted {
			// FIX41 chunk 2 — these frames came from the durable mirror
			// and stop without a terminal, because the process producing
			// them went away. The user gets everything that was produced,
			// then the truth. Writing message_complete here would present
			// a truncated answer as a finished one, which is the exact
			// failure the whole change exists to remove.
			writeSSEEvent(rw, flusher, "error", map[string]any{
				"code":    "GENERATION_INTERRUPTED",
				"message": "generation was interrupted",
			})
			return
		}
		// The run already ended; its terminal was inside the replay. When
		// the client's cursor was already at or past the end the replay is
		// empty, so the ending is re-sent rather than leaving it on an
		// open stream it would have to time out on (STREAM_CONTRACT 4).
		writeSSEEvent(rw, flusher, "message_complete", map[string]any{
			"message_id": msgID.String(),
		})
		return
	}

	done := r.Context().Done()
	// FIX130 C1 — the live tail heartbeats like the primary stream does.
	// A reattached viewer waiting on a quiet stretch (a slow provider, a
	// tool run) otherwise got no byte at all, and the clients' read
	// deadlines read that silence as a dead connection. The ping is written
	// at the HTTP edge, never into the ledger (same as drainStreamEvents).
	heartbeat := time.NewTicker(heartbeatInterval)
	defer heartbeat.Stop()
	for {
		select {
		case <-heartbeat.C:
			writeSSEHeartbeat(w, flusher)
		case <-done:
			// This viewer went away. The run is untouched; only this
			// subscription ends. Nothing is owed to a client that is no
			// longer there.
			return
		case le, ok := <-fa.Live:
			if !ok {
				if fa.Lagged() {
					writeSSEEvent(rw, flusher, "error", map[string]any{
						"code":    "RESUME_LAGGED",
						"message": "resume stream fell behind; reattach",
					})
				}
				return
			}
			rw.writeRecorded(flusher, le.Event, le.Data)
		}
	}
}

// writeResumeSnapshot replays the already-generated prefix in the same
// event order a live stream would have produced it: phase markers →
// reasoning → content, so the client's ThinkingBlock/placeholder logic
// behaves identically to a live stream.
func (h *MessageHandler) writeResumeSnapshot(
	w http.ResponseWriter, flusher http.Flusher, snap service.StreamSnapshot,
) {
	if snap.AnalysisStatus != "" {
		writeSSEEvent(w, flusher, "analysis_status", map[string]any{"status": "started"})
	}
	if snap.Reasoning != "" {
		writeSSEEvent(w, flusher, "reasoning_delta", map[string]any{"delta": snap.Reasoning})
	}
	if snap.AnalysisStatus == "completed" {
		writeSSEEvent(w, flusher, "analysis_status", map[string]any{"status": "completed"})
	}
	if snap.ImageStatus != "" {
		writeSSEEvent(w, flusher, "image_generating", map[string]any{"status": snap.ImageStatus})
	}
	if snap.VideoStatus != "" {
		writeSSEEvent(w, flusher, "video_generating", map[string]any{"status": snap.VideoStatus})
	}
	if snap.SearchStatus != "" {
		writeSSEEvent(w, flusher, "search_status", map[string]any{"status": snap.SearchStatus})
	}
	if snap.FallbackInfo != nil {
		writeSSEEvent(w, flusher, "fallback_info", snap.FallbackInfo)
	}
	if snap.DlpNotice != nil { // FIX50 P1-C — sticky, like fallback_info
		writeSSEEvent(w, flusher, "dlp_notice", snap.DlpNotice)
	}
	if snap.ServedModel != "" { // FIX35 Part B — sticky, like fallback_info
		writeSSEEvent(w, flusher, "model_resolved", map[string]any{"model": snap.ServedModel})
	}
	if snap.ReasoningNotice != nil {
		writeSSEEvent(w, flusher, "reasoning_notice", snap.ReasoningNotice)
	}
	// Latest stall state — a client reattaching mid-stall is told the
	// turn is stuck; one reattaching after recovery gets Stalled=false.
	if snap.KBNotice != nil {
		writeSSEEvent(w, flusher, "kb_notice", snap.KBNotice) // FIX133 E6a
	}
	if snap.Queued != nil && snap.Queued.Position > 0 {
		writeSSEEvent(w, flusher, "queued", snap.Queued) // FIX133 A2 — still waiting
	}
	if snap.ProviderStall != nil {
		writeSSEEvent(w, flusher, "provider_stall", snap.ProviderStall)
	}
	if snap.ImageNotice != nil {
		writeSSEEvent(w, flusher, "image_notice", snap.ImageNotice)
	}
	if snap.CompactionNotice != nil {
		writeSSEEvent(w, flusher, "compaction_notice", snap.CompactionNotice)
	}
	if snap.MemoryUsed != nil { // FIX130 A3 — sticky, like compaction_notice
		writeSSEEvent(w, flusher, "memory_used", snap.MemoryUsed)
	}
	if snap.MemorySaved != nil {
		writeSSEEvent(w, flusher, "memory_saved", snap.MemorySaved)
	}
	// run_python cards, latest frame each — a refresh in the middle of a
	// 60s sandbox run must not lose the code the model is running.
	for i := range snap.CodeExecutions {
		writeSSEEvent(w, flusher, "code_execution", snap.CodeExecutions[i])
	}
	// Generic tool activity, latest frame each — a reconnect mid-tool
	// restores the "using <tool>…" indicator.
	for i := range snap.ToolActivities {
		writeSSEEvent(w, flusher, "tool_activity", snap.ToolActivities[i])
	}
	if snap.Content != "" {
		writeSSEEvent(w, flusher, "content_delta", map[string]any{"delta": snap.Content})
	}
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
		RequestID:      middleware.RequestIDFrom(r.Context()),
	})
	if err != nil {
		h.writeRegenerateError(w, err)
		return
	}

	// SSE headers, then consume + forward the events.
	rw := h.openRunStream(w, stream)
	defer rw.ensureTerminal(flusher)

	// FIX28 §2 (F3): the client renders the user turn under an optimistic
	// temp id; hand it the persisted row id here (Edit already did) so an
	// edit → fork can name a real message without a reload.
	startPayload := map[string]any{
		"message_id":      stream.AssistantID.String(),
		"conversation_id": stream.ConversationID.String(),
	}
	if stream.UserMessage != nil {
		startPayload["user_message_id"] = stream.UserMessage.ID.String()
	}
	writeSSEEvent(rw, flusher, "message_start", startPayload)

	res := drainStreamEvents(rw, flusher, r.Context().Done(), stream.Events, detachOnDisconnect)

	persistFinishReason := res.finishReason
	if res.err != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.FinishWith(res.content, res.reasoning, persistFinishReason, res.usage, res.terminalInfo())
	if res.servedModel != "" { // FIX35 Part B
		_ = h.messages.SetServedModel(context.Background(), stream.AssistantID, res.servedModel)
	}
	if res.fallbackFrom != "" { // FIX120
		_ = h.messages.SetFallbackFrom(context.Background(), stream.AssistantID, res.fallbackFrom)
	}
	if res.errAttempts != nil { // FIX121 — a failed turn keeps what was tried
		_ = h.messages.SetAttempts(context.Background(), stream.AssistantID, res.errAttempts)
	}
	h.finishStream(rw, flusher, r.Context(), caller.TenantUUID, stream, res)
}

// openRunStream writes the SSE headers and returns the writer that owns
// this run's envelope + ledger. Send has its own copy because its
// early-open path has to run before the StreamHandle exists.
func (h *MessageHandler) openRunStream(w http.ResponseWriter, stream *service.StreamHandle) *runWriter {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	asstID := stream.AssistantID
	rw := newRunWriter(w, stream.RunID, func(event string, build func(seq, final int64) []byte) (int64, bool) {
		return h.messages.RecordFrame(asstID, event, build)
	})
	rw.started = true
	return rw
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
		RequestID:      middleware.RequestIDFrom(r.Context()),
	})
	if err != nil {
		h.writeEditError(w, err)
		return
	}

	// SSE — identical event format to Send so the front-end shares
	// its rendering pipeline.
	rw := h.openRunStream(w, stream)
	defer rw.ensureTerminal(flusher)

	writeSSEEvent(rw, flusher, "message_start", map[string]any{
		"message_id":      stream.AssistantID.String(),
		"conversation_id": stream.ConversationID.String(),
		"user_message_id": stream.UserMessage.ID.String(),
	})

	res := drainStreamEvents(rw, flusher, r.Context().Done(), stream.Events, detachOnDisconnect)

	persistFinishReason := res.finishReason
	if res.err != nil && persistFinishReason == "" {
		persistFinishReason = "error"
	}
	stream.FinishWith(res.content, res.reasoning, persistFinishReason, res.usage, res.terminalInfo())
	if res.servedModel != "" { // FIX35 Part B
		_ = h.messages.SetServedModel(context.Background(), stream.AssistantID, res.servedModel)
	}
	if res.fallbackFrom != "" { // FIX120
		_ = h.messages.SetFallbackFrom(context.Background(), stream.AssistantID, res.fallbackFrom)
	}
	if res.errAttempts != nil { // FIX121 — a failed turn keeps what was tried
		_ = h.messages.SetAttempts(context.Background(), stream.AssistantID, res.errAttempts)
	}
	h.finishStream(rw, flusher, r.Context(), caller.TenantUUID, stream, res)
}

func (h *MessageHandler) writeEditError(w http.ResponseWriter, err error) {
	if writeConversationBusy(w, err) {
		return
	}
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
	case strings.Contains(err.Error(), "insufficient_credits"):
		// Gateway's portal credit gate fired (cost-based deduction).
		// 402 + a stable code so the frontend shows the Thai
		// credits-exhausted notice instead of a generic provider error.
		WriteError(w, http.StatusPaymentRequired, "INSUFFICIENT_CREDITS",
			"เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		if writeGatewayEnvelope(w, err) {
			return
		}
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", "the AI service is unavailable")
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
	if writeConversationBusy(w, err) {
		return
	}
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
	case strings.Contains(err.Error(), "insufficient_credits"):
		// Gateway's portal credit gate fired (cost-based deduction).
		// 402 + a stable code so the frontend shows the Thai
		// credits-exhausted notice instead of a generic provider error.
		WriteError(w, http.StatusPaymentRequired, "INSUFFICIENT_CREDITS",
			"เครดิตหมดแล้ว กรุณาติดต่อผู้ดูแลระบบเพื่อเติมเครดิต")
	case errors.Is(err, service.ErrMessageGatewayUnavailable):
		if writeGatewayEnvelope(w, err) {
			return
		}
		WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", "the AI service is unavailable")
	default:
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
	}
}

// Ensure imports compile when only a subset of variables are used.
var _ = context.Background
