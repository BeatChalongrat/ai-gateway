// Auth handlers — DEPRECATED in Phase 1.5.
//
// The FastAPI Portal (services/portal/auth_service.py) is now the
// sole identity authority. Browser flows for register / login /
// refresh / logout all happen against the Portal on port 3001; the
// chat-edge UI is a pure token consumer. The four legacy endpoints
// still exist on the router for backward compatibility but each
// returns ``410 Gone`` + a pointer to the Portal so any stale client
// fails loudly rather than silently.
//
// The underlying AuthService / RefreshTokenRepo / CSRF + cookie
// helpers stay in the tree for now (no behavioural risk in keeping
// dead code that's unreachable from the router) — they will be
// removed in a follow-up cleanup pass once we're confident no
// internal tooling still calls them.
package handler

import (
	"encoding/json"
	"net/http"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// AuthHandler is the legacy struct retained so main.go's wiring
// doesn't need to change shape. All four methods now respond 410.
type AuthHandler struct {
	// kept so main.go's NewAuthHandler(authSvc, ...) call compiles
	auth         *service.AuthService
	csrfSecret   string
	cookieSecure bool
}

func NewAuthHandler(a *service.AuthService, csrfSecret string, cookieSecure bool) *AuthHandler {
	return &AuthHandler{auth: a, csrfSecret: csrfSecret, cookieSecure: cookieSecure}
}

// gone writes the 410 envelope pointing callers at the Portal.
func gone(w http.ResponseWriter, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusGone)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error": map[string]string{
			"code":    "AUTH_MOVED",
			"message": msg,
			"portal":  "http://localhost:3001",
		},
	})
}

func (h *AuthHandler) Register(w http.ResponseWriter, r *http.Request) {
	gone(w, "Registration is handled by the SRAN Portal. Sign up at the Portal, then return to Chat.")
}

func (h *AuthHandler) Login(w http.ResponseWriter, r *http.Request) {
	gone(w, "Login is handled by the SRAN Portal. Sign in there; this app reads the portal_access_token cookie.")
}

func (h *AuthHandler) Refresh(w http.ResponseWriter, r *http.Request) {
	gone(w, "Refresh is handled by the SRAN Portal at /portal/auth/refresh.")
}

func (h *AuthHandler) Logout(w http.ResponseWriter, r *http.Request) {
	gone(w, "Logout is handled by the SRAN Portal. Clearing the portal_access_token cookie also signs you out of Chat.")
}
