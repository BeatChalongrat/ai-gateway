package handler

// FIX132 B — POST /knowledge-bases/{id}/search: mode switch for the eval
// (dense | sparse | hybrid | hybrid_rerank), the contract's result shape and
// the timings.

import (
	"net/http"
	"testing"

	"github.com/google/uuid"
)

func TestFIX132_SearchEndpointModesShapeAndTimings(t *testing.T) {
	k := newKBHTTP(t)
	user := uuid.New()
	kbID := k.createKB(t, user, "HR")
	if rec := k.upload(t, user, kbID, "leave.txt", "text/plain", "Annual leave policy. Employees get ten days of annual leave each year."); rec.Code != http.StatusAccepted {
		t.Fatalf("upload: %d %s", rec.Code, rec.Body.String())
	}
	if rec := k.upload(t, user, kbID, "travel.txt", "text/plain", "Travel rules. Economy class for flights under six hours."); rec.Code != http.StatusAccepted {
		t.Fatalf("upload: %d", rec.Code)
	}
	k.drain(t)

	type hit struct {
		ChunkID     string   `json:"chunk_id"`
		DocumentID  string   `json:"document_id"`
		Filename    string   `json:"filename"`
		ChunkIndex  *int     `json:"chunk_index"`
		Content     string   `json:"content"`
		Score       *float64 `json:"score"`
		RerankScore *float64 `json:"rerank_score"`
	}
	type resp struct {
		Results []hit          `json:"results"`
		Mode    string         `json:"mode"`
		Timing  map[string]int `json:"timing_ms"`
	}
	for _, mode := range []string{"", "dense", "sparse", "hybrid", "hybrid_rerank"} {
		rec := k.do(t, user, http.MethodPost, "/knowledge-bases/"+kbID+"/search", map[string]any{"query": "annual leave days", "mode": mode})
		if rec.Code != http.StatusOK {
			t.Fatalf("mode %q: %d %s", mode, rec.Code, rec.Body.String())
		}
		var r resp
		decode(t, rec, &r)
		want := mode
		if want == "" {
			want = "hybrid" // what chat uses (reranker off by default)
		}
		if r.Mode != want {
			t.Fatalf("mode %q answered as %q", mode, r.Mode)
		}
		if len(r.Results) == 0 || r.Results[0].Filename != "leave.txt" || r.Results[0].ChunkID == "" ||
			r.Results[0].DocumentID == "" || r.Results[0].ChunkIndex == nil || r.Results[0].Score == nil || r.Results[0].Content == "" {
			t.Fatalf("mode %q: result shape %s", mode, rec.Body.String())
		}
		for _, key := range []string{"embed", "sql", "rerank", "total"} {
			if _, ok := r.Timing[key]; !ok {
				t.Fatalf("timing_ms.%s missing: %s", key, rec.Body.String())
			}
		}
		if mode == "hybrid_rerank" && r.Results[0].RerankScore == nil {
			t.Fatalf("hybrid_rerank returns rerank_score: %s", rec.Body.String())
		}
	}
	if rec := k.do(t, user, http.MethodPost, "/knowledge-bases/"+kbID+"/search", map[string]any{"query": "x", "mode": "fts"}); rec.Code != http.StatusBadRequest {
		t.Fatalf("an unknown mode is 400, got %d", rec.Code)
	}
	if rec := k.do(t, user, http.MethodPost, "/knowledge-bases/"+kbID+"/search", map[string]any{"query": "  "}); rec.Code != http.StatusBadRequest {
		t.Fatalf("an empty query is 400, got %d", rec.Code)
	}
	k.fake.SetStatus("loading")
	if rec := k.do(t, user, http.MethodPost, "/knowledge-bases/"+kbID+"/search", map[string]any{"query": "annual leave"}); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("models loading → 503, got %d", rec.Code)
	}
}
