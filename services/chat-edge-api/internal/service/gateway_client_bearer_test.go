package service

import (
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// FIX 1 / Option B: the chat path must authenticate with the SERVICE JWT, not a
// user-facing gw_ key — so a set/garbage/revoked GATEWAY_API_KEY can't break
// chat. This is the headline "decoupling" proof at the chat-edge boundary.

const testSvcSecret = "svc-secret-for-tests-0123456789abcdef"

func TestBearer_PrefersServiceJWT_EvenWhenGatewayKeyIsGarbage(t *testing.T) {
	g := NewGatewayClient(
		"http://gw", "gw_garbage_revoked_key_xxxxxxxx",
		testSvcSecret, 60*time.Second, "",
	)

	tok, err := g.bearer()
	if err != nil {
		t.Fatalf("bearer() error: %v", err)
	}
	if tok == "gw_garbage_revoked_key_xxxxxxxx" {
		t.Fatal("decoupling FAILED: bearer used the gw_ key while a service secret was set")
	}

	// It must be a valid service JWT signed with the service secret.
	claims := jwt.MapClaims{}
	parsed, err := jwt.ParseWithClaims(tok, claims, func(*jwt.Token) (any, error) {
		return []byte(testSvcSecret), nil
	})
	if err != nil || !parsed.Valid {
		t.Fatalf("bearer() did not return a valid service JWT: %v", err)
	}
	if iss, _ := claims["iss"].(string); iss != "chat-edge-api" {
		t.Fatalf("service JWT iss = %q, want chat-edge-api", iss)
	}
	// aud is ["gateway-api"].
	aud, _ := claims.GetAudience()
	found := false
	for _, a := range aud {
		if a == "gateway-api" {
			found = true
		}
	}
	if !found {
		t.Fatalf("service JWT aud = %v, want to contain gateway-api", aud)
	}
}

func TestBearer_FallsBackToGatewayKey_OnlyWhenNoServiceSecret(t *testing.T) {
	g := NewGatewayClient("http://gw", "gw_legacy_key", "", 60*time.Second, "")
	tok, err := g.bearer()
	if err != nil {
		t.Fatalf("bearer() error: %v", err)
	}
	if tok != "gw_legacy_key" {
		t.Fatalf("with no service secret, bearer should use the gw_ key, got %q", tok)
	}
}

func TestBearer_ErrorsWhenNothingConfigured(t *testing.T) {
	g := NewGatewayClient("http://gw", "", "", 60*time.Second, "")
	if _, err := g.bearer(); err == nil {
		t.Fatal("bearer() should error when neither a service secret nor a gw_ key is set")
	}
}
