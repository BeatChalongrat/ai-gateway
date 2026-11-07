// Import handlers — desktop agent-conversation backup mirror.
//
// POST /conversations/import              — create-or-get by client id
// POST /conversations/{id}/messages/import — append without generation
//
// Both are behind RequireAuth and deliberately NOT behind sendRateLimit:
// nothing here reaches an LLM, and a first sync of a long-lived desktop
// install legitimately fires many requests in a burst.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type ImportHandler struct {
	imports *service.ImportService
}

func NewImportHandler(s *service.ImportService) *ImportHandler {
	return &ImportHandler{imports: s}
}

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type importConversationRequest struct {
	ClientID string `json:"client_id"`
	Title    string `json:"title"`
	ModelID  string `json:"model_id"`
	// CreatedAt is the desktop's historical timestamp (RFC3339, optional).
	CreatedAt string `json:"created_at"`
}

type importConversationResponse struct {
	Conversation conversationResponse `json:"conversation"`
	Created      bool                 `json:"created"`
}

type importMessageItem struct {
	ClientID string `json:"client_id"`
	Role     string `json:"role"`
	Content  string `json:"content"`
	ModelID  string `json:"model_id,omitempty"`
	// CreatedAt is required per message — historical data keeps its
	// original timestamp, never NOW().
	CreatedAt string `json:"created_at"`
	// FileIDs are server file ids already uploaded to this conversation,
	// to link to this message (FIX 10 — desktop attachment sync).
	FileIDs []string `json:"file_ids,omitempty"`
	// FIX118 — how the turn ended on the client (assistant rows only).
	// status: completed | error | cancelled (absent = completed, the
	// pre-FIX118 wire); terminal_cause: the closed set in
	// docs/contracts/terminal_cause.json; error_code: the catalogue code of
	// the failure. Before these the endpoint stored every row as
	// completed, so a failed desktop turn became a success on import.
	Status        string `json:"status,omitempty"`
	TerminalCause string `json:"terminal_cause,omitempty"`
	ErrorCode     string `json:"error_code,omitempty"`
	// FIX119 — the desktop's run id and why the model stopped.
	RunID      string `json:"run_id,omitempty"`
	StopReason string `json:"stop_reason,omitempty"`
	// FIX120 — the requested model when a fallback model answered the
	// desktop turn (model_id is the one that did).
	FallbackFrom string `json:"fallback_from,omitempty"`
	// FIX121 — the per-model attempt log of a turn that failed on the
	// desktop (the gateway error frame's attempts); stored bounded.
	ErrorAttempts []any `json:"error_attempts,omitempty"`
	// FIX124 — client id of the assistant row this one replaces (desktop
	// Regenerate / Retry); that row is retired as status=regenerated.
	ReplacesClientID string `json:"replaces_client_id,omitempty"`
}

type importMessagesRequest struct {
	Messages []importMessageItem `json:"messages"`
}

type importMessagesResponse struct {
	Imported int `json:"imported"`
	Skipped  int `json:"skipped"`
	// FIX121 — items that were NOT stored, each with its reason. Absent when
	// every item was stored or deduped, so an older client sees no change.
	Rejected []service.ImportRejection `json:"rejected,omitempty"`
}

// CodeRequestTooLarge is the transport ceiling's refusal (413).
const CodeRequestTooLarge = "REQUEST_TOO_LARGE"

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

// ImportConversation — POST /conversations/import. Upsert by
// (tenant, user, client_id): 201 with created=true on insert, 200 with
// created=false when the mirror already exists. Replays are no-ops.
func (h *ImportHandler) ImportConversation(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	var req importConversationRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}
	var createdAt *time.Time
	if req.CreatedAt != "" {
		t, err := time.Parse(time.RFC3339, req.CreatedAt)
		if err != nil {
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, "created_at must be RFC3339")
			return
		}
		createdAt = &t
	}

	c, created, err := h.imports.ImportConversation(r.Context(), service.ImportConversationInput{
		TenantID:  caller.TenantUUID,
		UserID:    caller.UserID,
		ClientID:  req.ClientID,
		Title:     req.Title,
		ModelID:   req.ModelID,
		CreatedAt: createdAt,
	})
	if err != nil {
		switch {
		case errors.Is(err, service.ErrImportInvalid):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		case errors.Is(err, service.ErrImportConversationGone):
			// Deleted on another client — the desktop must propagate the
			// delete locally, never resurrect (FIX 9 Part C).
			WriteError(w, http.StatusGone, CodeNotFound, "conversation deleted")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if created {
		w.WriteHeader(http.StatusCreated)
	}
	_ = json.NewEncoder(w).Encode(importConversationResponse{
		Conversation: toResponse(c),
		Created:      created,
	})
}

// ImportMessages — POST /conversations/{id}/messages/import. Appends
// historical messages in one transaction, deduped by client_id. Replaying
// the exact same request yields {"imported":0,"skipped":N}.
func (h *ImportHandler) ImportMessages(w http.ResponseWriter, r *http.Request) {
	caller, ok := middleware.CallerFrom(r.Context())
	if !ok {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "missing caller")
		return
	}
	convID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid conversation id")
		return
	}
	// FIX121 — the one size limit on an import is on the REQUEST, and it is
	// said out loud: 413 with a code, not a 422 about one message's bytes.
	r.Body = http.MaxBytesReader(w, r.Body, service.ImportMaxBodyBytes)
	var req importMessagesRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			WriteError(w, http.StatusRequestEntityTooLarge, CodeRequestTooLarge,
				"import request exceeds "+strconv.FormatInt(service.ImportMaxBodyBytes, 10)+" bytes; send the messages in smaller batches")
			return
		}
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}

	items := make([]service.ImportMessageInput, 0, len(req.Messages))
	for _, m := range req.Messages {
		// A per-item problem found here rejects that ITEM (the service
		// reports it); it no longer refuses the whole batch.
		invalid := ""
		ts, err := time.Parse(time.RFC3339, m.CreatedAt)
		if err != nil {
			ts = time.Time{}
		}
		var fileIDs []uuid.UUID
		for _, s := range m.FileIDs {
			id, err := uuid.Parse(s)
			if err != nil {
				invalid = "file_ids must be UUIDs"
				break
			}
			fileIDs = append(fileIDs, id)
		}
		items = append(items, service.ImportMessageInput{
			Invalid:          invalid,
			Attempts:         anyList(m.ErrorAttempts),
			ReplacesClientID: m.ReplacesClientID,
			ClientID:         m.ClientID,
			Role:             m.Role,
			Content:          m.Content,
			ModelID:          m.ModelID,
			CreatedAt:        ts,
			FileIDs:          fileIDs,
			Status:           m.Status,
			TerminalCause:    m.TerminalCause,
			ErrorCode:        m.ErrorCode,
			RunID:            m.RunID,
			StopReason:       m.StopReason,
			FallbackFrom:     m.FallbackFrom,
		})
	}

	imported, skipped, rejected, err := h.imports.ImportMessages(
		r.Context(), caller.TenantUUID, caller.UserID, convID, items,
	)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrConversationNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "conversation not found")
		case errors.Is(err, service.ErrImportInvalid):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError, err.Error())
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(importMessagesResponse{
		Imported: imported,
		Skipped:  skipped,
		Rejected: rejected,
	})
}

// anyList hands a decoded JSON array to the service as the any its
// normaliser takes; nil stays nil (no list on the item).
func anyList(v []any) any {
	if v == nil {
		return nil
	}
	return v
}
