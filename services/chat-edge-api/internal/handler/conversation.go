// Conversation handlers — CRUD over chat.conversations.
//
// All routes are behind ``RequireAuth`` (see router.go) so every
// handler can pull the caller from the request context via
// ``middleware.CallerFrom`` without re-validating the JWT.
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
}

func NewConversationHandler(c *service.ConversationService) *ConversationHandler {
	return &ConversationHandler{conversations: c}
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
}

func toResponse(c *model.Conversation) conversationResponse {
	return conversationResponse{
		ID:           c.ID.String(),
		Title:        c.Title,
		ModelID:      c.ModelID,
		MessageCount: c.MessageCount,
		CreatedAt:    c.CreatedAt,
		UpdatedAt:    c.UpdatedAt,
	}
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

// List supports query params ``limit`` (default 20, max 50) and
// ``cursor`` (UUID of the last-seen conversation from the previous
// page).
func (h *ConversationHandler) List(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	cursor := parseUUID(r.URL.Query().Get("cursor"))
	query := strings.TrimSpace(r.URL.Query().Get("q"))

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

	convs, next, err := h.conversations.List(r.Context(), caller.TenantUUID, caller.UserID, limit, cursor)
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
// title renames and model reassignment without a verb-explosion.
type updateRequest struct {
	Title   *string `json:"title,omitempty"`
	ModelID *string `json:"model_id,omitempty"`
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
	if req.Title == nil && req.ModelID == nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "no fields to update")
		return
	}

	if req.Title != nil {
		if err := h.conversations.Rename(r.Context(), caller.TenantUUID, convID, *req.Title); err != nil {
			h.writeUpdateError(w, err)
			return
		}
	}
	if req.ModelID != nil {
		if err := h.conversations.SetModel(r.Context(), caller.TenantUUID, convID, *req.ModelID); err != nil {
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
	if err := h.conversations.Delete(r.Context(), caller.TenantUUID, convID); err != nil {
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
