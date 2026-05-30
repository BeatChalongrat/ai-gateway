package middleware

import (
	"net/http"
	"testing"
)

// extractToken is the token-source resolver for RequireAuth. Post the
// HttpOnly-cookie migration the cookie is the primary source (browsers
// can't set an Authorization header on EventSource/SSE), with the Bearer
// header kept as a fallback for non-browser API clients.

func reqWith(cookie, bearer string) *http.Request {
	r, _ := http.NewRequest(http.MethodGet, "http://localhost:4000/api/v1/conversations", nil)
	if cookie != "" {
		r.AddCookie(&http.Cookie{Name: "portal_access_token", Value: cookie})
	}
	if bearer != "" {
		r.Header.Set("Authorization", "Bearer "+bearer)
	}
	return r
}

func TestExtractToken_CookieTakesPriority(t *testing.T) {
	got, err := extractToken(reqWith("cookie-jwt", "header-jwt"))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "cookie-jwt" {
		t.Fatalf("cookie must win when both present; got %q", got)
	}
}

func TestExtractToken_CookieOnly(t *testing.T) {
	got, err := extractToken(reqWith("cookie-jwt", ""))
	if err != nil || got != "cookie-jwt" {
		t.Fatalf("cookie-only: got %q err %v", got, err)
	}
}

func TestExtractToken_BearerFallback(t *testing.T) {
	got, err := extractToken(reqWith("", "header-jwt"))
	if err != nil || got != "header-jwt" {
		t.Fatalf("bearer fallback: got %q err %v", got, err)
	}
}

func TestExtractToken_NeitherIsError(t *testing.T) {
	if _, err := extractToken(reqWith("", "")); err == nil {
		t.Fatal("expected error when no token is present")
	}
}

func TestExtractToken_EmptyCookieFallsBackToBearer(t *testing.T) {
	// An empty cookie value must not shadow a valid Bearer header.
	got, err := extractToken(reqWith("", "header-jwt"))
	if err != nil || got != "header-jwt" {
		t.Fatalf("empty cookie should fall back to bearer: got %q err %v", got, err)
	}
}
