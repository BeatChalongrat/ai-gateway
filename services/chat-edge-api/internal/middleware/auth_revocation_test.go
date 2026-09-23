package middleware

// FIX129 B2 — RequireAuth refuses a revoked token.
//
// Before this card the middleware had no notion of revocation: a token
// the Portal had logged out kept working for the rest of its 60
// minutes. These tests run against a fake provisioner (no database)
// and the in-memory revocation store.

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
)

const revSecret = "fix129-middleware-test-secret-not-a-real-key"

type fakeProvisioner struct{ tenant uuid.UUID }

func (f fakeProvisioner) EnsureUserExists(_ context.Context, _ uuid.UUID, _, _, _ string) (uuid.UUID, error) {
	return f.tenant, nil
}

// failingRevocations stands for a Redis that is down.
type failingRevocations struct{}

func (failingRevocations) IsRevoked(context.Context, string, string, time.Time) (bool, error) {
	return false, errors.New("redis: connection refused")
}
func (failingRevocations) RevokeJTI(context.Context, string, time.Duration) error { return nil }
func (failingRevocations) RevokeUserBefore(context.Context, string, time.Time) error {
	return nil
}

func mintRevToken(t *testing.T, sub uuid.UUID, jti string, issuedAt time.Time) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": sub.String(), "email": "fix129@local",
		"type": "portal", "token_use": "access",
		"tenant_id": uuid.NewString(), "tenant_role": "owner", "token_version": 1,
		"iat": issuedAt.Unix(), "exp": issuedAt.Add(time.Hour).Unix(), "jti": jti,
	}
	s, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(revSecret))
	if err != nil {
		t.Fatalf("sign: %v", err)
	}
	return s
}

func serveWith(rev auth.Revocations, token string) *httptest.ResponseRecorder {
	ok := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) })
	h := RequireAuth(revSecret, fakeProvisioner{tenant: uuid.New()}, rev)(ok)
	req := httptest.NewRequest(http.MethodGet, "/api/v1/conversations", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestRequireAuth_RevokedJTIIs401(t *testing.T) {
	sub := uuid.New()
	jti := uuid.NewString()
	token := mintRevToken(t, sub, jti, time.Now().Add(-time.Minute))
	store := auth.NewMemoryRevocations()

	if rec := serveWith(store, token); rec.Code != http.StatusOK {
		t.Fatalf("before revocation: status = %d, want 200", rec.Code)
	}
	if err := store.RevokeJTI(context.Background(), jti, time.Hour); err != nil {
		t.Fatalf("RevokeJTI: %v", err)
	}
	if rec := serveWith(store, token); rec.Code != http.StatusUnauthorized {
		t.Fatalf("after RevokeJTI: status = %d, want 401 (a signed-out token must be refused)", rec.Code)
	}
}

func TestRequireAuth_UserCutRevokesOlderTokensOnly(t *testing.T) {
	sub := uuid.New()
	older := mintRevToken(t, sub, uuid.NewString(), time.Now().Add(-5*time.Minute))
	store := auth.NewMemoryRevocations()
	cut := time.Now()
	if err := store.RevokeUserBefore(context.Background(), sub.String(), cut); err != nil {
		t.Fatalf("RevokeUserBefore: %v", err)
	}
	if rec := serveWith(store, older); rec.Code != http.StatusUnauthorized {
		t.Fatalf("token issued before the cut: status = %d, want 401", rec.Code)
	}
	newer := mintRevToken(t, sub, uuid.NewString(), cut.Add(2*time.Second))
	if rec := serveWith(store, newer); rec.Code != http.StatusOK {
		t.Fatalf("token issued after the cut: status = %d, want 200", rec.Code)
	}
}

func TestRequireAuth_NilRevocationsMeansNoCheck(t *testing.T) {
	token := mintRevToken(t, uuid.New(), uuid.NewString(), time.Now())
	if rec := serveWith(nil, token); rec.Code != http.StatusOK {
		t.Fatalf("nil store: status = %d, want 200", rec.Code)
	}
}

// Redis down: serve (availability first, the Portal's own choice for its
// refresh blacklist) — the token's own expiry bounds the window.
func TestRequireAuth_StoreErrorFailsOpen(t *testing.T) {
	token := mintRevToken(t, uuid.New(), uuid.NewString(), time.Now())
	if rec := serveWith(failingRevocations{}, token); rec.Code != http.StatusOK {
		t.Fatalf("store error: status = %d, want 200 (fail open)", rec.Code)
	}
}
