package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/kbfake"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// FIX130 C2 — GET /api/v1/knowledge-bases/availability says whether a
// knowledge base can work. FIX132 B — from kb-models' own /health.
func TestFIX130_KBAvailabilityEndpointReportsTheModels(t *testing.T) {
	fake := kbfake.New()
	defer fake.Close()
	fake.SetStatus("loading")
	c, err := service.NewKBModelsClient(fake.URL)
	if err != nil {
		t.Fatal(err)
	}
	h := NewKnowledgeHandler(nil, nil)
	h.SetAvailability(service.NewKnowledgeAvailability(c.Probe))

	var got struct {
		Available *bool  `json:"available"`
		Reason    string `json:"reason"`
		CheckedAt string `json:"checked_at"`
	}
	rec := httptest.NewRecorder()
	h.Availability(rec, httptest.NewRequest(http.MethodGet, "/api/v1/knowledge-bases/availability", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("body %q: %v", rec.Body.String(), err)
	}
	if got.Available == nil || *got.Available || got.Reason != service.KBUnavailableLoading || got.CheckedAt == "" {
		t.Fatalf("want {available:false, reason:models_loading}, got %s", rec.Body.String())
	}

	// Ready → available (a fresh tracker, so the loading verdict is not cached).
	fake.SetStatus("ready")
	h.SetAvailability(service.NewKnowledgeAvailability(c.Probe))
	rec = httptest.NewRecorder()
	h.Availability(rec, httptest.NewRequest(http.MethodGet, "/", nil))
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil || !*got.Available {
		t.Fatalf("want available, got %s", rec.Body.String())
	}

	// No kb-models wired at all → models_unavailable, and no call is made.
	rec = httptest.NewRecorder()
	NewKnowledgeHandler(nil, nil).Availability(rec, httptest.NewRequest(http.MethodGet, "/", nil))
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil || *got.Available || got.Reason != service.KBUnavailableModels {
		t.Fatalf("want models_unavailable, got %s", rec.Body.String())
	}
}
