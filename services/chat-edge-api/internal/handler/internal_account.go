// Internal account-deletion endpoints — FIX132 C.
//
//	POST /internal/accounts/{userID}/deletion-request
//	POST /internal/accounts/{userID}/purge
//
// The backend (portal) owns the account and calls these: the first when
// the user confirms "delete my account" (hide every chat now, stop their
// tasks, refuse their tokens here), the second when the 30-day window has
// passed (erase everything the user owns in this database). Both are
// idempotent and answer JSON counts only.
//
// Gate: X-Internal-Service-Secret must equal INTERNAL_SERVICE_SECRET —
// the same trust anchor as POST /internal/auth/revoke. With the secret
// empty the router does not register the routes (404).
package handler

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// accountPurgeTimeout bounds one purge call. It runs on its own context,
// not the request's: a caller that gives up waiting must not cut an
// erasure off half-way (each conversation is its own transaction, so a
// cut is safe, but finishing is better). A repeat call resumes.
const accountPurgeTimeout = 10 * time.Minute

type InternalAccountHandler struct {
	secret string
	svc    *service.AccountDeletionService
}

func NewInternalAccountHandler(secret string, svc *service.AccountDeletionService) *InternalAccountHandler {
	return &InternalAccountHandler{secret: secret, svc: svc}
}

// Enabled reports whether the routes should exist.
func (h *InternalAccountHandler) Enabled() bool {
	return h != nil && h.secret != "" && h.svc != nil
}

func (h *InternalAccountHandler) authorised(w http.ResponseWriter, r *http.Request) (uuid.UUID, bool) {
	if !h.Enabled() {
		http.NotFound(w, r)
		return uuid.Nil, false
	}
	got := r.Header.Get(InternalServiceSecretHeader)
	if subtle.ConstantTimeCompare([]byte(got), []byte(h.secret)) != 1 {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "unauthorized")
		return uuid.Nil, false
	}
	id, err := uuid.Parse(chi.URLParam(r, "userID"))
	if err != nil || id == uuid.Nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid user id")
		return uuid.Nil, false
	}
	return id, true
}

func (h *InternalAccountHandler) DeletionRequest(w http.ResponseWriter, r *http.Request) {
	userID, ok := h.authorised(w, r)
	if !ok {
		return
	}
	res, err := h.svc.RequestDeletion(r.Context(), userID)
	if err != nil {
		slog.Error("internal account deletion request failed", "user", userID.String(), "err", err)
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "deletion request failed")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(res)
}

func (h *InternalAccountHandler) Purge(w http.ResponseWriter, r *http.Request) {
	userID, ok := h.authorised(w, r)
	if !ok {
		return
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), accountPurgeTimeout)
	defer cancel()
	counts, err := h.svc.Purge(ctx, userID)
	if err != nil {
		slog.Error("internal account purge failed", "user", userID.String(), "err", err)
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "purge failed")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"purged": true, "counts": counts})
}
