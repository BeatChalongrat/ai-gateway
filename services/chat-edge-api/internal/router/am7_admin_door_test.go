package router_test

// AM7 — Agent Market's admin page moved to admin-ui. chat-edge mounts the
// same handlers under /api/v1/admin/agent-market behind its admin bearer,
// and an admin-ui role below admin (agent_publisher, named by the proxy in
// X-Admin-Role) reaches that door and nothing else behind the bearer.
//
// No database: Agent Market is a fake that answers 200 on both mounts.

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/handler"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/router"
)

const doorAdminKey = "am7-router-test-admin-key-not-real"

type fakeAgentMarket struct{}

func (fakeAgentMarket) Routes(r chi.Router) {
	r.Get("/availability", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
}

func (fakeAgentMarket) AdminRoutes(r chi.Router) {
	r.Get("/org", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
}

func doorRouter() http.Handler {
	cfg := &config.Config{PortalJWTSecret: revokePortalSecret, AdminAPIKey: doorAdminKey}
	h := router.Handlers{
		Auth:          &handler.AuthHandler{},
		Conversation:  &handler.ConversationHandler{},
		Message:       &handler.MessageHandler{},
		Model:         &handler.ModelHandler{},
		Feedback:      &handler.FeedbackHandler{},
		File:          &handler.FileHandler{},
		GeneratedFile: &handler.GeneratedFileHandler{},
		Preferences:   &handler.PreferencesHandler{},
		Share:         &handler.ShareHandler{},
		Admin:         &handler.AdminHandler{},
		AgentMarket:   fakeAgentMarket{},
	}
	return router.New(cfg, h, "", nil, fakeProvisioner{}, nil)
}

func doorCall(t *testing.T, h http.Handler, path, bearer, role string) int {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	if role != "" {
		req.Header.Set("X-Admin-Role", role)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec.Code
}

func TestAM7AgentMarketAdminDoorIsBehindTheAdminBearer(t *testing.T) {
	h := doorRouter()
	const door = "/api/v1/admin/agent-market/org"
	if got := doorCall(t, h, door, doorAdminKey, "admin"); got != http.StatusOK {
		t.Fatalf("bearer + admin → %d, want 200 (the door is mounted)", got)
	}
	if got := doorCall(t, h, door, "", "admin"); got != http.StatusUnauthorized {
		t.Fatalf("no bearer → %d, want 401", got)
	}
	if got := doorCall(t, h, door, "not-the-key", "admin"); got != http.StatusUnauthorized {
		t.Fatalf("wrong bearer → %d, want 401", got)
	}
}

func TestAM7APublisherReachesOnlyTheAgentMarketDoor(t *testing.T) {
	h := doorRouter()
	if got := doorCall(t, h, "/api/v1/admin/agent-market/org", doorAdminKey, "agent_publisher"); got != http.StatusOK {
		t.Fatalf("publisher on the Agent Market door → %d, want 200 (the handlers decide what it may do)", got)
	}
	// every other admin endpoint behind the bearer: refused before it runs
	for _, p := range []string{
		"/api/v1/admin/conversations", "/api/v1/admin/users", "/api/v1/admin/analytics/usage",
		"/api/v1/admin/knowledge-bases", "/api/v1/admin/memory-stats",
	} {
		if got := doorCall(t, h, p, doorAdminKey, "agent_publisher"); got != http.StatusForbidden {
			t.Fatalf("publisher GET %s → %d, want 403", p, got)
		}
		if got := doorCall(t, h, p, doorAdminKey, "viewer"); got != http.StatusForbidden {
			t.Fatalf("viewer GET %s → %d, want 403", p, got)
		}
	}
	// an admin, and a script holding the bearer without naming a role, keep the whole surface
	for _, role := range []string{"admin", "super_admin", ""} {
		if got := doorCall(t, h, "/api/v1/admin/agent-market/org", doorAdminKey, role); got != http.StatusOK {
			t.Fatalf("role %q on the door → %d, want 200", role, got)
		}
	}
}
