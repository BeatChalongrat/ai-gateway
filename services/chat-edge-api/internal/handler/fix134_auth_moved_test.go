package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// FIX134 D1 — the retired auth endpoints point at the portal's public URL,
// https by default, never the old plain-http port.
func TestFix134AuthMovedPointsAtThePublicPortal(t *testing.T) {
	h := &AuthHandler{}
	portalOf := func() string {
		rec := httptest.NewRecorder()
		h.Login(rec, httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", nil))
		if rec.Code != http.StatusGone {
			t.Fatalf("status %d, want 410", rec.Code)
		}
		var body struct {
			Error map[string]string `json:"error"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatal(err)
		}
		return body.Error["portal"]
	}
	t.Setenv("PORTAL_PUBLIC_URL", "")
	if got := portalOf(); got != "https://localhost:3001" {
		t.Fatalf("default portal %q, want https://localhost:3001", got)
	}
	t.Setenv("PORTAL_PUBLIC_URL", "https://sran.example.co.th/")
	if got := portalOf(); got != "https://sran.example.co.th" {
		t.Fatalf("configured portal %q", got)
	}
}
