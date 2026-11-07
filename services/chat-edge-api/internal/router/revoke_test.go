package router_test

// FIX129 B2 — the revoke endpoint and the session heartbeat, through the
// real router.
//
//	POST /internal/auth/revoke   404 when INTERNAL_SERVICE_SECRET is empty
//	                             401 on a wrong X-Internal-Service-Secret
//	                             204 on the right one — and the token is
//	                             refused from then on
//	GET  /api/v1/auth/session    204 while the token is good, 401 after
//
// No database: the provisioner is a fake, the store is in-memory.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/handler"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/router"
)

const (
	revokePortalSecret   = "fix129-router-test-portal-secret-not-real"
	revokeInternalSecret = "fix129-router-test-internal-secret-not-real"
)

type fakeProvisioner struct{}

func (fakeProvisioner) EnsureUserExists(_ context.Context, _ uuid.UUID, _, _, _ string) (uuid.UUID, error) {
	return uuid.New(), nil
}

func revokeRouter(internalSecret string, store auth.Revocations) http.Handler {
	cfg := &config.Config{PortalJWTSecret: revokePortalSecret, InternalServiceSecret: internalSecret}
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
	}
	return router.New(cfg, h, "", nil, fakeProvisioner{}, store)
}

type revokeToken struct {
	raw string
	jti string
	sub uuid.UUID
	exp time.Time
}

func mintRevokeToken(t *testing.T) revokeToken {
	t.Helper()
	tok := revokeToken{jti: uuid.NewString(), sub: uuid.New(), exp: time.Now().Add(time.Hour)}
	claims := jwt.MapClaims{
		"sub": tok.sub.String(), "email": "fix129@local",
		"type": "portal", "token_use": "access",
		"tenant_id": uuid.NewString(), "tenant_role": "owner", "token_version": 1,
		"iat": time.Now().Add(-time.Minute).Unix(), "exp": tok.exp.Unix(), "jti": tok.jti,
	}
	s, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(revokePortalSecret))
	if err != nil {
		t.Fatalf("sign: %v", err)
	}
	tok.raw = s
	return tok
}

func session(mux http.Handler, token string) int {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/auth/session", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	return rec.Code
}

func revoke(mux http.Handler, secret, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/internal/auth/revoke", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if secret != "" {
		req.Header.Set(handler.InternalServiceSecretHeader, secret)
	}
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	return rec
}

func TestRevoke_NoSecretConfiguredIs404(t *testing.T) {
	mux := revokeRouter("", auth.NewMemoryRevocations())
	if rec := revoke(mux, revokeInternalSecret, `{"jti":"x","exp":0}`); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404 (feature off when INTERNAL_SERVICE_SECRET is empty)", rec.Code)
	}
}

func TestRevoke_WrongSecretIs401(t *testing.T) {
	mux := revokeRouter(revokeInternalSecret, auth.NewMemoryRevocations())
	if rec := revoke(mux, "not-the-secret", `{"jti":"x","exp":0}`); rec.Code != http.StatusUnauthorized {
		t.Fatalf("wrong secret: status = %d, want 401", rec.Code)
	}
	if rec := revoke(mux, "", `{"jti":"x","exp":0}`); rec.Code != http.StatusUnauthorized {
		t.Fatalf("missing secret: status = %d, want 401", rec.Code)
	}
}

func TestRevoke_RightSecretIs204AndTheTokenIsThenRejected(t *testing.T) {
	store := auth.NewMemoryRevocations()
	mux := revokeRouter(revokeInternalSecret, store)
	tok := mintRevokeToken(t)

	if code := session(mux, tok.raw); code != http.StatusNoContent {
		t.Fatalf("GET /auth/session before revoke: status = %d, want 204", code)
	}
	body := `{"jti":"` + tok.jti + `","exp":` + itoa(tok.exp.Unix()) + `,"sub":"` + tok.sub.String() + `","all_before":0}`
	if rec := revoke(mux, revokeInternalSecret, body); rec.Code != http.StatusNoContent {
		t.Fatalf("revoke: status = %d body %s, want 204", rec.Code, rec.Body.String())
	}
	if code := session(mux, tok.raw); code != http.StatusUnauthorized {
		t.Fatalf("GET /auth/session after revoke: status = %d, want 401", code)
	}
	// A protected data route is refused the same way.
	req := httptest.NewRequest(http.MethodGet, "/api/v1/models", nil)
	req.Header.Set("Authorization", "Bearer "+tok.raw)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("GET /api/v1/models after revoke: status = %d, want 401", rec.Code)
	}
}

func TestRevoke_AllBeforeEndsEverySessionOfTheUser(t *testing.T) {
	store := auth.NewMemoryRevocations()
	mux := revokeRouter(revokeInternalSecret, store)
	tok := mintRevokeToken(t)
	if code := session(mux, tok.raw); code != http.StatusNoContent {
		t.Fatalf("before: status = %d, want 204", code)
	}
	body := `{"sub":"` + tok.sub.String() + `","all_before":` + itoa(time.Now().Unix()) + `}`
	if rec := revoke(mux, revokeInternalSecret, body); rec.Code != http.StatusNoContent {
		t.Fatalf("revoke all_before: status = %d body %s, want 204", rec.Code, rec.Body.String())
	}
	if code := session(mux, tok.raw); code != http.StatusUnauthorized {
		t.Fatalf("after all_before: status = %d, want 401", code)
	}
}

func TestRevoke_EmptyBodyIs400(t *testing.T) {
	mux := revokeRouter(revokeInternalSecret, auth.NewMemoryRevocations())
	if rec := revoke(mux, revokeInternalSecret, `{}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("empty revoke: status = %d, want 400", rec.Code)
	}
}

func itoa(v int64) string { return strconv.FormatInt(v, 10) }
