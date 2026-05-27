// Feedback handler: thumbs-up / thumbs-down on a specific assistant
// message.
//
// Phase 1 contract:
//
//	POST /api/v1/messages/{messageID}/feedback
//	{"rating": "thumbs_up" | "thumbs_down"}
//
//	→ 200 {"feedback": {"id":"…","message_id":"…","rating":"…","created_at":"…"}}
//
// The endpoint is upsert by (message_id, user_id) — toggling vote
// updates the row in place via INSERT … ON CONFLICT. No free-text
// “comment“ field accepted in Phase 1: comments are a new PII
// channel and are deferred until DLP-on-save lands.
package handler

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

type FeedbackHandler struct {
	feedback *service.FeedbackService
}

func NewFeedbackHandler(s *service.FeedbackService) *FeedbackHandler {
	return &FeedbackHandler{feedback: s}
}

type feedbackRequest struct {
	Rating string `json:"rating"`
}

type feedbackResponseEnvelope struct {
	Feedback feedbackResponse `json:"feedback"`
}

type feedbackResponse struct {
	ID        string `json:"id"`
	MessageID string `json:"message_id"`
	Rating    string `json:"rating"`
	CreatedAt string `json:"created_at"`
}

func (h *FeedbackHandler) Create(w http.ResponseWriter, r *http.Request) {
	caller, _ := middleware.CallerFrom(r.Context())
	msgID, err := uuid.Parse(chi.URLParam(r, "messageID"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid message id")
		return
	}
	var req feedbackRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "malformed JSON body")
		return
	}

	out, err := h.feedback.Create(r.Context(), service.FeedbackCreateInput{
		TenantID:  caller.TenantUUID,
		UserID:    caller.UserID,
		MessageID: msgID,
		Rating:    req.Rating,
	})
	if err != nil {
		switch {
		case errors.Is(err, service.ErrFeedbackBadRating):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"rating must be thumbs_up or thumbs_down")
		case errors.Is(err, service.ErrFeedbackTargetNotFound):
			WriteError(w, http.StatusNotFound, CodeNotFound, "message not found")
		case errors.Is(err, service.ErrFeedbackNotAssistantMsg):
			WriteError(w, http.StatusUnprocessableEntity, CodeValidationError,
				"feedback only allowed on assistant messages")
		default:
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "internal error")
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(feedbackResponseEnvelope{
		Feedback: feedbackResponse{
			ID:        out.ID.String(),
			MessageID: out.MessageID.String(),
			Rating:    out.Rating,
			CreatedAt: out.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
		},
	})
}
