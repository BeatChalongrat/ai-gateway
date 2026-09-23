package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// FIX130 C2 — GET /api/v1/knowledge-bases/availability says whether a
// knowledge base can work, from the embeddings provider's real answer.
func TestFIX130_KBAvailabilityEndpointReportsTheProvider(t *testing.T) {
	gw := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/embeddings" {
			t.Errorf("unexpected gateway path %s", r.URL.Path)
		}
		w.WriteHeader(http.StatusServiceUnavailable) // the live shape: OpenAI out of credit
		_, _ = w.Write([]byte(`{"error":{"code":"provider_unavailable","message":"embeddings provider returned 429"}}`))
	}))
	defer gw.Close()
	emb := service.NewEmbeddingService(service.NewGatewayClient(gw.URL, "k", "", time.Minute, ""))
	avail := service.NewKnowledgeAvailability(emb.Probe)
	h := NewKnowledgeHandler(nil, nil)
	h.SetAvailability(avail)

	rec := httptest.NewRecorder()
	h.Availability(rec, httptest.NewRequest(http.MethodGet, "/api/v1/knowledge-bases/availability", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	var got struct {
		Available *bool  `json:"available"`
		Reason    string `json:"reason"`
		CheckedAt string `json:"checked_at"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("body %q: %v", rec.Body.String(), err)
	}
	if got.Available == nil || *got.Available || got.Reason != service.KBUnavailableProviderFailed || got.CheckedAt == "" {
		t.Fatalf("want {available:false, reason:embeddings_failed}, got %s", rec.Body.String())
	}

	// No embeddings wired at all → not configured, and no call is made.
	rec = httptest.NewRecorder()
	NewKnowledgeHandler(nil, nil).Availability(rec, httptest.NewRequest(http.MethodGet, "/", nil))
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil || *got.Available || got.Reason != service.KBUnavailableNotConfigured {
		t.Fatalf("want not configured, got %s", rec.Body.String())
	}
}
