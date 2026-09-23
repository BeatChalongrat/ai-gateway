// Internal revocation endpoint — FIX129 B2.
//
//	POST /internal/auth/revoke     (outside /api/v1; not user-facing)
//
// The FastAPI backend calls this on logout so the access token the
// browser still holds stops working here at once, instead of for the
// rest of its 60 minutes. Body:
//
//	{"jti": "<token id>", "exp": <unix>, "sub": "<user uuid>", "all_before": <unix or 0>}
//
// jti + exp mark that one token dead until it would have expired
// anyway; sub + all_before mark every token of that user issued at or
// before that instant. Either half may be omitted; an empty body is a
// 400.
//
// Gate: X-Internal-Service-Secret must equal INTERNAL_SERVICE_SECRET.
// When that secret is empty the router does not register the route at
// all, so the path answers 404 (feature off). 401 on mismatch, 204 on
// success, 503 when the store could not be written (the caller must
// know the revocation did not land).
package handler

import (
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
)

// InternalServiceSecretHeader carries the shared secret on internal calls.
const InternalServiceSecretHeader = "X-Internal-Service-Secret"

type InternalAuthHandler struct {
	secret      string
	revocations auth.Revocations
}

// NewInternalAuthHandler builds the handler. It is only registered by the
// router when Enabled() is true.
func NewInternalAuthHandler(secret string, revocations auth.Revocations) *InternalAuthHandler {
	return &InternalAuthHandler{secret: secret, revocations: revocations}
}

// Enabled reports whether the endpoint should exist: a secret to gate it
// with and a store to write to.
func (h *InternalAuthHandler) Enabled() bool {
	return h != nil && h.secret != "" && h.revocations != nil
}

type revokeRequest struct {
	JTI       string `json:"jti"`
	Exp       int64  `json:"exp"`
	Sub       string `json:"sub"`
	AllBefore int64  `json:"all_before"`
}

func (h *InternalAuthHandler) Revoke(w http.ResponseWriter, r *http.Request) {
	if !h.Enabled() {
		http.NotFound(w, r)
		return
	}
	got := r.Header.Get(InternalServiceSecretHeader)
	if subtle.ConstantTimeCompare([]byte(got), []byte(h.secret)) != 1 {
		WriteError(w, http.StatusUnauthorized, CodeUnauthorized, "unauthorized")
		return
	}

	var req revokeRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid json body")
		return
	}
	req.JTI = strings.TrimSpace(req.JTI)
	req.Sub = strings.TrimSpace(req.Sub)
	if req.JTI == "" && req.AllBefore <= 0 {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "nothing to revoke: give jti or all_before")
		return
	}
	if req.AllBefore > 0 {
		if _, err := uuid.Parse(req.Sub); err != nil {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "all_before needs a uuid sub")
			return
		}
	}

	ctx := r.Context()
	if req.JTI != "" {
		ttl := auth.MinRevocationTTL
		if req.Exp > 0 {
			ttl = time.Until(time.Unix(req.Exp, 0))
		} else {
			// No exp given: keep the mark as long as an access token
			// could possibly live.
			ttl = auth.UserRevocationTTL
		}
		if err := h.revocations.RevokeJTI(ctx, req.JTI, ttl); err != nil {
			slog.Warn("auth_revocation_write_failed", "kind", "jti", "error", err.Error())
			WriteError(w, http.StatusServiceUnavailable, CodeInternalError, "revocation store unavailable")
			return
		}
	}
	if req.AllBefore > 0 {
		if err := h.revocations.RevokeUserBefore(ctx, req.Sub, time.Unix(req.AllBefore, 0)); err != nil {
			slog.Warn("auth_revocation_write_failed", "kind", "user", "error", err.Error())
			WriteError(w, http.StatusServiceUnavailable, CodeInternalError, "revocation store unavailable")
			return
		}
	}
	slog.Info("auth_token_revoked", "jti_given", req.JTI != "", "all_before", req.AllBefore, "sub", req.Sub)
	w.WriteHeader(http.StatusNoContent)
}
