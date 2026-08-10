// Conversation handlers — CRUD over chat.conversations.
//
// All routes are behind “RequireAuth“ (see router.go) so every
// handler can pull the caller from the request context via
// “middleware.CallerFrom“ without re-validating the JWT.
package handler

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type ConversationHandler struct {
	conversations *service.ConversationService
	// Optional. Populated by main.go via SetExportSources so the
	// /export endpoint can pull full conversation state without
	// running through the service layer's tenant gates twice.
	convRepo *repository.ConversationRepo
	msgRepo  *repository.MessageRepo
	fileRepo *repository.FileRepo
	cfg      *config.Config
	// Optional. Used by Update to verify the KB belongs to the
	// tenant before binding it to a conversation.
	knowledge *service.KnowledgeService
	// Optional. Powers POST /conversations/{id}/generate-title.
	// Left nil in tests that don't exercise title generation.
	titles *service.TitleService
	// Optional. Powers GET /conversations/{id}/state — the S3
	// read-only surface for the structured conversation-state row.
	states *service.ConversationStateService
	// Optional. Powers GET /conversations/{id}/context-info —
	// dry-run of the S4 context compiler so the operator (or a
	// future UI debug panel) can see the budget breakdown without
	// actually sending a request.
	messages *service.MessageService
}

func NewConversationHandler(c *service.ConversationService) *ConversationHandler {
	return &ConversationHandler{conversations: c}
}

// SetKnowledgeService wires the KB service so PATCH /conversations/{id}
// can validate knowledge_base_id bindings before persisting. Kept
// out of the constructor so the conversation handler stays usable
// in tests that don't exercise the KB path.
func (h *ConversationHandler) SetKnowledgeService(k *service.KnowledgeService) {
	h.knowledge = k
}

// SetTitleService wires the title-generation service so
// POST /conversations/{id}/generate-title can produce AI-summarized
// titles after the first turn lands.
func (h *ConversationHandler) SetTitleService(t *service.TitleService) {
	h.titles = t
}

// SetConversationStateService wires the S3 state read path so
// GET /conversations/{id}/state can serve the structured-state
// row to the frontend.
func (h *ConversationHandler) SetConversationStateService(s *service.ConversationStateService) {
	h.states = s
}

// SetMessageService wires the dry-run path for the S4
// /context-info endpoint. Optional — without it the endpoint
// returns 503.
func (h *ConversationHandler) SetMessageService(m *service.MessageService) {
	h.messages = m
}

// SetExportSources wires the dependencies the /export endpoint
// needs. Kept out of the constructor so unit tests that don't
// exercise export don't need to fabricate every repo.
func (h *ConversationHandler) SetExportSources(
	conv *repository.ConversationRepo,
	msg *repository.MessageRepo,
	files *repository.FileRepo,
	cfg *config.Config,
) {
	h.convRepo = conv
	h.msgRepo = msg
	h.fileRepo = files
	h.cfg = cfg
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type createConversationRequest struct {
	ModelID string `json:"model_id"`
}

type conversationResponse struct {
	ID           string    `json:"id"`
	Title        string    `json:"title"`
	ModelID      string    `json:"model_id"`
	MessageCount int       `json:"message_count"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
	// KnowledgeBaseID is null when the conversation has no RAG
	// source bound — most conversations.
	KnowledgeBaseID *string `json:"knowledge_base_id"`
	// MemoryEnabled exposes the per-conversation memory toggle
	// (S1 — temporary chat). Default true; the frontend renders a
	// chip when false so the user knows nothing they say here will
	// feed into their long-term memory.
	MemoryEnabled bool `json:"memory_enabled"`
	// T4 — Thinking Mode. Per-conversation reasoning preference
	// surfaced so the frontend can restore the toggle state when
	// the user reopens the conversation. Defaults match the DB
	// defaults (enabled=false, mode="auto", show_summary=true).
	ReasoningEnabled     bool   `json:"reasoning_enabled"`
	ReasoningMode        string `json:"reasoning_mode"`
	ShowReasoningSummary bool   `json:"show_reasoning_summary"`
	// IsPinned surfaces the sidebar pin flag (migration 021) so the
	// frontend can render the "PINNED" section + the pin/unpin toggle.
	IsPinned bool `json:"is_pinned"`
	// IsArchived surfaces the archive flag (migration 032). The sidebar
	// hides archived chats; the archive view lists only these. Archiving
	// is reversible and does NOT delete anything.
	IsArchived bool `json:"is_archived"`
	// ProjectID is the project membership (migration 029). null when the
	// conversation is not in a project.
	ProjectID *string `json:"project_id"`
	// FolderID is the sidebar-folder membership (migration 022). null when
	// the conversation is unfiled; the frontend groups by this id.
	FolderID *string `json:"folder_id"`
}

func toResponse(c *model.Conversation) conversationResponse {
	r := conversationResponse{
		ID:                   c.ID.String(),
		Title:                c.Title,
		ModelID:              c.ModelID,
		MessageCount:         c.MessageCount,
		CreatedAt:            c.CreatedAt,
		UpdatedAt:            c.UpdatedAt,
		MemoryEnabled:        c.MemoryEnabled,
		ReasoningEnabled:     c.ReasoningEnabled,
		ReasoningMode:        c.ReasoningMode,
		ShowReasoningSummary: c.ShowReasoningSummary,
		IsPinned:             c.IsPinned,
		IsArchived:           c.IsArchived,
	}
	if c.KnowledgeBaseID != nil {
		s := c.KnowledgeBaseID.String()
		r.KnowledgeBaseID = &s
	}
	if c.FolderID != nil {
		s := c.FolderID.String()
		r.FolderID = &s
	}
	if c.ProjectID != nil {
		s := c.ProjectID.String()
		r.ProjectID = &s
	}
	return r
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

func (h *ConversationHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	var req createConversationRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	c, err := h.conversations.Create(r.Context(), service.CreateInput{
		UserID:   caller.UserID,
		TenantID: caller.TenantUUID,
		ModelID:  req.ModelID,
	})
	if err != nil {
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(toResponse(c))
}

// List supports query params “limit“ (default 20, max 50) and
// “cursor“ (UUID of the last-seen conversation from the previous
// page).
func (h *ConversationHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	cursor := parseUUID(r.URL.Query().Get("cursor"))
	query := strings.TrimSpace(r.URL.Query().Get("q"))
	// ?archived=1 switches this to the archive view. Anything else
	// (absent, "0", garbage) is the normal sidebar list, so a
	// malformed value can never accidentally hide the user's chats.
	archived := r.URL.Query().Get("archived") == "1"

	// Title search deliberately spans BOTH lists: a user searching for a
	// chat they archived should still find it. The archive view's own
	// filtering happens in the paginated branch below.

	// Search and cursor pagination are mutually exclusive: ?q=
	// returns the top N matches in one shot (no cursor) so the
	// front-end can swap between paginated browsing and search
	// without a complex merge.
	if query != "" {
		convs, err := h.conversations.Search(
			r.Context(), caller.TenantUUID, caller.UserID, query, limit,
		)
		if err != nil {
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
			return
		}
		items := make([]conversationResponse, 0, len(convs))
		for i := range convs {
			items = append(items, toResponse(&convs[i]))
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"conversations": items,
			"has_more":      false,
			"next_cursor":   nil,
		})
		return
	}

	convs, next, err := h.conversations.List(r.Context(), caller.TenantUUID, caller.UserID, limit, cursor, archived)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}

	items := make([]conversationResponse, 0, len(convs))
	for i := range convs {
		items = append(items, toResponse(&convs[i]))
	}
	resp := map[string]any{
		"conversations": items,
		"has_more":      next != uuid.Nil,
	}
	if next != uuid.Nil {
		resp["next_cursor"] = next.String()
	} else {
		resp["next_cursor"] = nil
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

func (h *ConversationHandler) Get(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	c, err := h.conversations.Get(r.Context(), caller.TenantUUID, convID)
	if err != nil {
		if errors.Is(err, service.ErrConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	// Same-tenant but different-user → return 404 (don't leak existence).
	if c.UserID != caller.UserID {
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(toResponse(c))
}

// updateRequest covers every mutable field on chat.conversations.
// All fields are optional; the handler applies whichever ones are
// present. The route is still PATCH so the same endpoint serves
// title renames, model reassignment, and KB binding without a
// verb-explosion.
//
// KnowledgeBaseID uses json.RawMessage so the handler can distinguish
// three states: "field omitted" (no change), "field set to null"
// (unbind), and "field set to UUID string" (bind to that KB).
type updateRequest struct {
	Title           *string         `json:"title,omitempty"`
	ModelID         *string         `json:"model_id,omitempty"`
	KnowledgeBaseID json.RawMessage `json:"knowledge_base_id,omitempty"`
	// MemoryEnabled toggles the S1 cross-conversation memory layer
	// for this conversation. Pointer so the handler can distinguish
	// "field omitted" from "field set to false".
	MemoryEnabled *bool `json:"memory_enabled,omitempty"`
	// T4 — Thinking Mode. All three pointer-typed so the frontend
	// can patch any subset (e.g. mode without flipping enabled).
	// reasoning_mode must be one of: "off" / "auto" / "low" /
	// "medium" / "high"; anything else gets a 422.
	ReasoningEnabled     *bool   `json:"reasoning_enabled,omitempty"`
	ReasoningMode        *string `json:"reasoning_mode,omitempty"`
	ShowReasoningSummary *bool   `json:"show_reasoning_summary,omitempty"`
	// IsArchived files the conversation away from the sidebar
	// (migration 032). Pointer so the field is patchable independently.
	IsArchived *bool `json:"is_archived,omitempty"`
	// IsPinned toggles the sidebar pin (migration 021). Pointer so the
	// handler can distinguish "field omitted" from "set to false".
	IsPinned *bool `json:"is_pinned,omitempty"`
}

// validReasoningModes mirrors the Gateway's ReasoningConfig.mode
// Literal (backend/app/schemas/public.py — T1). Kept here as a flat
// set so the handler can reject unknown values with a clean 422.
var validReasoningModes = map[string]struct{}{
	"off":    {},
	"auto":   {},
	"low":    {},
	"medium": {},
	"high":   {},
}

func (h *ConversationHandler) Update(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	var req updateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	if req.Title == nil && req.ModelID == nil && len(req.KnowledgeBaseID) == 0 &&
		req.MemoryEnabled == nil && req.ReasoningEnabled == nil &&
		req.ReasoningMode == nil && req.ShowReasoningSummary == nil &&
		req.IsPinned == nil && req.IsArchived == nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "no fields to update")
		return
	}
	// T4: validate reasoning_mode up-front so a typo doesn't sneak
	// past the repo's COALESCE into the DB.
	if req.ReasoningMode != nil {
		if _, ok := validReasoningModes[*req.ReasoningMode]; !ok {
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"reasoning_mode must be one of: off, auto, low, medium, high")
			return
		}
	}

	if req.Title != nil {
		if err := h.conversations.Rename(r.Context(), caller.TenantUUID, caller.UserID, convID, *req.Title); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}
	if req.ModelID != nil {
		if err := h.conversations.SetModel(r.Context(), caller.TenantUUID, caller.UserID, convID, *req.ModelID); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}
	if len(req.KnowledgeBaseID) > 0 {
		kbID, perr := parseKBRef(req.KnowledgeBaseID)
		if perr != nil {
			WriteError(w, http.StatusBadRequest, CodeValidationError, perr.Error())
			return
		}
		// Verify the KB exists for this tenant before binding (so we
		// don't store a dangling FK and so the user gets a clean 404
		// when they tap an unknown KB id).
		if kbID != uuid.Nil && h.knowledge != nil {
			if _, err := h.knowledge.GetKB(r.Context(), caller.TenantUUID, kbID); err != nil {
				if errors.Is(err, service.ErrKBNotFound) {
					WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
					return
				}
				WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
				return
			}
		}
		if err := h.conversations.SetKnowledgeBase(r.Context(), caller.TenantUUID, caller.UserID, convID, kbID); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}
	if req.MemoryEnabled != nil {
		if err := h.conversations.SetMemoryEnabled(r.Context(), caller.TenantUUID, caller.UserID, convID, *req.MemoryEnabled); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}
	if req.ReasoningEnabled != nil || req.ReasoningMode != nil || req.ShowReasoningSummary != nil {
		if err := h.conversations.SetReasoning(
			r.Context(), caller.TenantUUID, caller.UserID, convID,
			req.ReasoningEnabled, req.ReasoningMode, req.ShowReasoningSummary,
		); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}
	if req.IsArchived != nil {
		if err := h.conversations.SetArchived(r.Context(), caller.TenantUUID, caller.UserID, convID, *req.IsArchived); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}
	if req.IsPinned != nil {
		if err := h.conversations.SetPinned(r.Context(), caller.TenantUUID, caller.UserID, convID, *req.IsPinned); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}

	// Re-fetch + return so the client gets the updated timestamp.
	c, err := h.conversations.Get(r.Context(), caller.TenantUUID, convID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(toResponse(c))
}

func (h *ConversationHandler) writeUpdateError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrConversationNotFound):
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
	case errors.Is(err, service.ErrConversationBadModel):
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "model not allowed")
	default:
		WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
	}
}

func (h *ConversationHandler) Delete(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	if err := h.conversations.Delete(r.Context(), caller.TenantUUID, caller.UserID, convID); err != nil {
		if errors.Is(err, service.ErrConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// ---------------------------------------------------------------------------
// Generate title — POST /conversations/{id}/generate-title
// ---------------------------------------------------------------------------

// GenerateTitle asks the gateway to summarize the conversation's
// first turn into a 3-6 word sidebar title and persists the result.
// The frontend fires this once after the first assistant stream
// completes; the response carries the new title so the client can
// update the sidebar without an extra round-trip.
//
// Response shape: { "title": "..." } on success, standard
// errorEnvelope on failure. 404 when the conversation isn't owned
// by the caller; 409 when the conversation has no messages yet;
// 502 when the upstream gateway is unreachable.
func (h *ConversationHandler) GenerateTitle(w http.ResponseWriter, r *http.Request) {
	if h.titles == nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "title service not configured")
		return
	}
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}

	title, err := h.titles.Generate(r.Context(), caller.TenantUUID, caller.UserID, convID)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrTitleConversationNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
		case errors.Is(err, service.ErrTitleNoMessages):
			WriteError(w, http.StatusConflict, CodeConflict, "conversation has no messages yet")
		default:
			var ue *service.GatewayUnavailableError
			if errors.As(err, &ue) {
				WriteError(w, http.StatusBadGateway, "PROVIDER_UNAVAILABLE", "title generation upstream unavailable")
				return
			}
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}
	if err := h.conversations.Rename(r.Context(), caller.TenantUUID, caller.UserID, convID, title); err != nil {
		// Title generation succeeded but the persist write failed —
		// surface 500 so the client knows the sidebar is stale, but
		// still return the title so a retry isn't strictly required
		// for display purposes.
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "couldn't persist title")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"title": title})
}

// ---------------------------------------------------------------------------
// Get structured conversation state — GET /conversations/{id}/state
// ---------------------------------------------------------------------------

// GetState returns the S3 structured-state row for the
// conversation. Ownership is enforced via AssertOwnership; missing
// state rows return an empty envelope (200) rather than 404 so the
// frontend can render an empty "no state yet" surface without
// branching on the error path.
func (h *ConversationHandler) GetState(w http.ResponseWriter, r *http.Request) {
	if h.states == nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "state service not configured")
		return
	}
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	if _, err := h.conversations.AssertOwnership(r.Context(), caller.TenantUUID, caller.UserID, convID); err != nil {
		if errors.Is(err, service.ErrConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	state, err := h.states.Get(r.Context(), convID, caller.UserID, caller.TenantUUID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	if state == nil {
		// Empty envelope — same shape as a populated row, just with
		// null / empty arrays. Saves the frontend a branch on 404.
		_ = json.NewEncoder(w).Encode(map[string]any{
			"conversation_id":      convID.String(),
			"current_goal":         nil,
			"structured_summary":   map[string]any{},
			"confirmed_decisions":  []any{},
			"active_artifacts":     []any{},
			"open_tasks":           []any{},
			"rejected_approaches":  []any{},
			"unresolved_questions": []any{},
		})
		return
	}
	_ = json.NewEncoder(w).Encode(state)
}

// ---------------------------------------------------------------------------
// Get compiled-context preview — GET /conversations/{id}/context-info
// ---------------------------------------------------------------------------

// GetContextInfo runs the S4 ContextCompiler in dry-run mode against
// the current conversation state. Returns the budget breakdown +
// "messages we'd send" preview so the operator (and a future
// debug UI panel) can see how compaction will behave before
// sending a real message.
//
// Ownership is enforced inside MessageService.InspectContext; this
// handler only translates auth + URL params.
func (h *ConversationHandler) GetContextInfo(w http.ResponseWriter, r *http.Request) {
	if h.messages == nil {
		WriteError(w, http.StatusServiceUnavailable, CodeInternalError, "context inspection unavailable")
		return
	}
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	compiled, err := h.messages.InspectContext(r.Context(), convID, caller.UserID, caller.TenantUUID)
	if err != nil {
		if errors.Is(err, service.ErrMessageConversationNotFound) {
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"conversation_id":      convID.String(),
		"total_budget":         compiled.Budget.TotalBudget,
		"system_prompt_tokens": compiled.Budget.SystemPromptTokens,
		"output_reserve":       compiled.Budget.OutputReserve,
		"tool_reserve":         compiled.Budget.ToolReserve,
		"messages_budget":      compiled.Budget.MessagesRemaining,
		"messages_total":       compiled.MessagesTotal,
		"messages_would_send":  compiled.MessagesSent,
		"compacted":            compiled.Compacted,
		"extraction_triggered": compiled.ExtractionTriggered,
	})
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

// Export streams the conversation as markdown or JSON for the user
// to save locally. Tenant-scoped via the conversation lookup.
//
//	GET /api/v1/conversations/{id}/export?format=markdown|json
//	→ Content-Disposition: attachment; filename="{title}_{date}.{ext}"
func (h *ConversationHandler) Export(w http.ResponseWriter, r *http.Request) {
	if h.convRepo == nil || h.msgRepo == nil {
		// SetExportSources was never called — wiring bug.
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "export not configured")
		return
	}
	caller, _ := middleware.CallerFrom(r.Context())
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	format := strings.ToLower(r.URL.Query().Get("format"))
	if format == "" {
		format = "markdown"
	}
	if format != "markdown" && format != "json" {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "unsupported export format")
		return
	}

	conv, err := h.convRepo.GetByID(r.Context(), caller.TenantUUID, convID)
	if err != nil {
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
		return
	}
	if conv.UserID != caller.UserID {
		WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
		return
	}
	// Pull a generous slice — 1000 messages is well past any
	// realistic conversation; if a conversation ever grows past
	// that we'll need pagination on export.
	msgs, _, err := h.msgRepo.ListByConversation(r.Context(), caller.TenantUUID, convID, 1000, uuid.Nil)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		return
	}
	// Phase 4A.2.4 — export is a non-restoring consumer: under display
	// lifecycle it must never emit restored raw display. canonical_safe /
	// redacted rows already carry safe content; raw-sensitive legacy rows are
	// redacted. No-op when the feature is off.
	if h.cfg != nil && h.cfg.DLPDisplayLifecycleEnabled {
		for i := range msgs {
			msgs[i].ContentMasked = service.NonRestoringRepresentation(true, msgs[i])
		}
	}

	// File join — same batched query used by /messages so exports
	// match what the user sees in the chat.
	filesByMsg := map[uuid.UUID][]model.ConversationFile{}
	if h.fileRepo != nil && len(msgs) > 0 {
		ids := make([]uuid.UUID, 0, len(msgs))
		for i := range msgs {
			ids = append(ids, msgs[i].ID)
		}
		filesByMsg, _ = h.fileRepo.ListByMessageIDs(r.Context(), caller.TenantUUID, ids)
	}

	filenameStem := exportFilenameStem(conv.Title, conv.CreatedAt)
	if format == "json" {
		writeExportJSON(w, conv, msgs, filesByMsg, filenameStem)
	} else {
		writeExportMarkdown(w, conv, msgs, filesByMsg, filenameStem, h.modelName(conv.ModelID))
	}
}

func (h *ConversationHandler) modelName(modelID string) string {
	if h.cfg == nil {
		return modelID
	}
	for _, m := range h.cfg.Models() {
		if m.ID == modelID {
			return m.Name
		}
	}
	return modelID
}

// writeExportMarkdown renders the human-readable export. Keeps the
// shape stable so existing markdown viewers + grep workflows stay
// useful as the chat surface evolves.
func writeExportMarkdown(
	w http.ResponseWriter,
	conv *model.Conversation,
	msgs []model.Message,
	filesByMsg map[uuid.UUID][]model.ConversationFile,
	stem, modelName string,
) {
	var b strings.Builder
	fmt.Fprintf(&b, "# %s\n\n", conv.Title)
	fmt.Fprintf(&b, "**Model:** %s  \n", modelName)
	fmt.Fprintf(&b, "**Date:** %s  \n", conv.CreatedAt.UTC().Format("2006-01-02 15:04 UTC"))
	fmt.Fprintf(&b, "**Messages:** %d\n\n", len(msgs))
	b.WriteString("---\n\n")
	for _, m := range msgs {
		role := strings.ToUpper(m.Role[:1]) + m.Role[1:]
		ts := m.CreatedAt.UTC().Format("3:04 PM")
		if m.Role == "assistant" {
			fmt.Fprintf(&b, "**%s** (%s, %s):\n", role, modelName, ts)
		} else {
			fmt.Fprintf(&b, "**%s** (%s):\n", role, ts)
		}
		b.WriteString(m.ContentMasked)
		b.WriteString("\n")
		if attached := filesByMsg[m.ID]; len(attached) > 0 {
			b.WriteString("\n_Attachments:_ ")
			for i, f := range attached {
				if i > 0 {
					b.WriteString(", ")
				}
				fmt.Fprintf(&b, "`%s`", f.OriginalFilename)
			}
			b.WriteString("\n")
		}
		b.WriteString("\n---\n\n")
	}
	w.Header().Set("Content-Type", "text/markdown; charset=utf-8")
	w.Header().Set("Content-Disposition",
		`attachment; filename="`+stem+`.md"`)
	_, _ = w.Write([]byte(b.String()))
}

// writeExportJSON renders a structured dump suitable for re-import
// or programmatic processing. Field names mirror the wire format.
func writeExportJSON(
	w http.ResponseWriter,
	conv *model.Conversation,
	msgs []model.Message,
	filesByMsg map[uuid.UUID][]model.ConversationFile,
	stem string,
) {
	type fileExport struct {
		ID            string `json:"id"`
		Filename      string `json:"filename"`
		ContentType   string `json:"content_type"`
		FileSizeBytes int64  `json:"file_size_bytes"`
	}
	type messageExport struct {
		ID        string       `json:"id"`
		Role      string       `json:"role"`
		Content   string       `json:"content"`
		Status    string       `json:"status"`
		ModelID   *string      `json:"model_id,omitempty"`
		CreatedAt string       `json:"created_at"`
		Files     []fileExport `json:"files,omitempty"`
	}
	convExport := map[string]any{
		"id":         conv.ID.String(),
		"title":      conv.Title,
		"model_id":   conv.ModelID,
		"created_at": conv.CreatedAt.UTC().Format(time.RFC3339),
		"updated_at": conv.UpdatedAt.UTC().Format(time.RFC3339),
	}
	out := make([]messageExport, 0, len(msgs))
	for _, m := range msgs {
		me := messageExport{
			ID:        m.ID.String(),
			Role:      m.Role,
			Content:   m.ContentMasked,
			Status:    m.Status,
			ModelID:   m.ModelID,
			CreatedAt: m.CreatedAt.UTC().Format(time.RFC3339),
		}
		if attached := filesByMsg[m.ID]; len(attached) > 0 {
			me.Files = make([]fileExport, 0, len(attached))
			for _, f := range attached {
				me.Files = append(me.Files, fileExport{
					ID:            f.ID.String(),
					Filename:      f.OriginalFilename,
					ContentType:   f.ContentType,
					FileSizeBytes: f.FileSizeBytes,
				})
			}
		}
		out = append(out, me)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Disposition",
		`attachment; filename="`+stem+`.json"`)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"conversation": convExport,
		"messages":     out,
	})
}

// exportFilenameStem builds a filesystem-safe filename stem like
// "How-do-PostgreSQL-indexes-work_2026-05-26". Spaces / slashes
// collapse to dashes; everything outside [a-z0-9_-] is stripped.
func exportFilenameStem(title string, created time.Time) string {
	stem := strings.ToLower(strings.TrimSpace(title))
	if stem == "" {
		stem = "conversation"
	}
	var b strings.Builder
	for _, r := range stem {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == ' ', r == '-', r == '_':
			b.WriteByte('-')
		}
	}
	clean := strings.Trim(b.String(), "-")
	if clean == "" {
		clean = "conversation"
	}
	// Collapse runs of dashes.
	for strings.Contains(clean, "--") {
		clean = strings.ReplaceAll(clean, "--", "-")
	}
	// 80-char cap so the final filename stays under 200.
	if len(clean) > 80 {
		clean = strings.TrimRight(clean[:80], "-")
	}
	return clean + "_" + created.UTC().Format("2006-01-02")
}

// parseUUID is a small helper: returns uuid.Nil on empty / malformed
// input rather than erroring out so query-param parsing stays tidy.
func parseUUID(s string) uuid.UUID {
	if s == "" {
		return uuid.Nil
	}
	u, err := uuid.Parse(s)
	if err != nil {
		return uuid.Nil
	}
	return u
}

// parseKBRef interprets the “knowledge_base_id“ field from a PATCH
// body. Three cases:
//
//	raw == "null"        → unbind (returns uuid.Nil, nil)
//	raw == "\"...\""     → bind to the parsed UUID
//	anything else        → returns an error so the handler responds
//	                       with a clean 400 instead of corrupting state
func parseKBRef(raw json.RawMessage) (uuid.UUID, error) {
	if len(raw) == 0 {
		return uuid.Nil, fmt.Errorf("knowledge_base_id missing")
	}
	if string(raw) == "null" {
		return uuid.Nil, nil
	}
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return uuid.Nil, fmt.Errorf("knowledge_base_id must be a uuid string or null")
	}
	if s == "" {
		return uuid.Nil, nil
	}
	id, err := uuid.Parse(s)
	if err != nil {
		return uuid.Nil, fmt.Errorf("knowledge_base_id is not a valid uuid")
	}
	return id, nil
}
