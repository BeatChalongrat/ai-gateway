package service

// FIX132 B — retrieval: one query embedding (dense + sparse), exact dense and
// sparse arms over indexed chunks only, fusion by score (cosine + w·lexical),
// optional rerank within a time budget with fallback, no similarity
// threshold. Real pgvector (testdb.Fresh), kb-models faked (word-overlap
// vectors), so the expected ranking is known.

import (
	"context"
	"math"
	"testing"
	"time"

	"github.com/google/uuid"
)

// newRAGHarness indexes a small knowledge base and returns it with a
// RAGService built from cfg.
func newRAGHarness(t *testing.T, cfg RAGConfig) (*kbHarness, *RAGService, map[string]uuid.UUID) {
	t.Helper()
	h := newKBHarness(t)
	docs := map[string]string{
		"leave.txt":  "Annual leave policy. Employees get ten days of annual leave each year, booked through the HR portal.",
		"travel.txt": "Travel rules. Book economy class for flights under six hours; business class needs a director's approval.",
		"hotel.txt":  "ราคาห้องพัก ห้องละ 1200 บาท รวมอาหาร และ บริการ ท่องเที่ยว",
		"it.txt":     "IT support. Reset your password on the portal; the help desk answers within one working day.",
	}
	ids := map[string]uuid.UUID{}
	for name, body := range docs {
		ids[name] = h.upload(t, name, "text/plain", body).ID
	}
	h.drain(t)
	return h, NewRAGService(h.repo, h.models, cfg), ids
}

func TestFIX132_SearchModesRankTheRightPassage(t *testing.T) {
	h, rag, ids := newRAGHarness(t, DefaultRAGConfig())
	ctx := context.Background()
	for _, mode := range []SearchMode{SearchDense, SearchSparse, SearchHybrid} {
		res, err := rag.Search(ctx, h.tenant, h.kb.ID, "How many days of annual leave do employees get?", mode, 0)
		if err != nil {
			t.Fatalf("%s: %v", mode, err)
		}
		if res.Mode != mode || len(res.Chunks) == 0 || res.Chunks[0].DocumentID != ids["leave.txt"] {
			t.Fatalf("%s: top hit should be leave.txt, got %+v", mode, res.Chunks)
		}
		top := res.Chunks[0]
		want := map[SearchMode]float64{SearchDense: top.Cosine, SearchSparse: top.Lexical, SearchHybrid: top.Cosine + 0.3*top.Lexical}[mode]
		if math.Abs(top.Score-want) > 1e-9 {
			t.Fatalf("%s: score %v, want %v (cosine %v lexical %v)", mode, top.Score, want, top.Cosine, top.Lexical)
		}
		if res.Timing.Total < res.Timing.Embed || res.Timing.Total < res.Timing.SQL {
			t.Fatalf("%s: timing %+v", mode, res.Timing)
		}
		for i := 1; i < len(res.Chunks); i++ {
			if res.Chunks[i].Score > res.Chunks[i-1].Score {
				t.Fatalf("%s: results not ordered by score", mode)
			}
		}
	}
	// Thai through the sparse (keyword) arm: bge-m3's lexical weights need
	// no word segmentation.
	res, err := rag.Search(ctx, h.tenant, h.kb.ID, "ราคาห้องพัก", SearchSparse, 5)
	if err != nil || len(res.Chunks) == 0 || res.Chunks[0].DocumentID != ids["hotel.txt"] {
		t.Fatalf("Thai keyword: %+v %v", res, err)
	}
	// Sparse keeps only chunks that share a token with the question.
	for _, c := range res.Chunks {
		if c.Lexical <= 0 {
			t.Fatalf("sparse returned a chunk with no shared token: %+v", c)
		}
	}
	// One embeddings call per search, as a query; no segmentation.
	var queries int
	for _, p := range h.fake.Priorities {
		if p == "query" {
			queries++
		}
	}
	if queries != 4 || h.fake.SegmentCalls.Load() != 0 {
		t.Fatalf("want 4 query embeddings and no segment calls, got %d / %d", queries, h.fake.SegmentCalls.Load())
	}
}

// The 0.70 cosine cut-off (tuned to OpenAI's embedding scale) is gone: the
// dense view always returns its top candidates. (The measured floor applies
// to the fused score — TestFIX132_MinScoreKeepsOffTopicQuestionsOut.)
func TestFIX132_NoSimilarityThreshold(t *testing.T) {
	h, rag, _ := newRAGHarness(t, DefaultRAGConfig())
	res, err := rag.Search(context.Background(), h.tenant, h.kb.ID, "zebra migration patterns", SearchDense, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Chunks) != 4 {
		t.Fatalf("an unrelated question still gets the ranked passages (4 chunks), got %d", len(res.Chunks))
	}
}

func TestFIX132_OnlyIndexedDocumentsAreSearched(t *testing.T) {
	h, rag, ids := newRAGHarness(t, DefaultRAGConfig())
	ctx := context.Background()
	// A pending upload about the same topic is not searchable yet.
	pending := h.upload(t, "leave-2027.txt", "text/plain", "Annual leave policy 2027: employees get twelve days of annual leave.")
	// And a document mid-index (status processing) with some chunks stored.
	_, _ = h.pool.Exec(ctx, `UPDATE chat.knowledge_documents SET status='processing' WHERE id=$1`, ids["travel.txt"])
	for _, mode := range []SearchMode{SearchDense, SearchSparse, SearchHybrid} {
		res, err := rag.Search(ctx, h.tenant, h.kb.ID, "annual leave days economy flights", mode, 50)
		if err != nil {
			t.Fatal(err)
		}
		for _, c := range res.Chunks {
			if c.DocumentID == pending.ID || c.DocumentID == ids["travel.txt"] {
				t.Fatalf("%s returned a chunk of a document that is not indexed", mode)
			}
		}
	}
	// Another tenant's knowledge base id finds nothing.
	res, err := rag.Search(ctx, uuid.New(), h.kb.ID, "annual leave", SearchHybrid, 0)
	if err != nil || len(res.Chunks) != 0 {
		t.Fatalf("tenant scope: %d chunks, %v", len(res.Chunks), err)
	}
}

func TestFIX132_SparseWeightZeroIsDenseOnly(t *testing.T) {
	h, _, _ := newRAGHarness(t, DefaultRAGConfig())
	// KB_SPARSE_WEIGHT=0 means dense only.
	dense := NewRAGService(h.repo, h.models, withCfg(func(c *RAGConfig) { c.SparseWeight, c.MinScore = 0, 0 }))
	if dense.Config().SparseWeight != 0 {
		t.Fatalf("an explicit 0 stays 0, got %v", dense.Config().SparseWeight)
	}
	a, _ := dense.Search(context.Background(), h.tenant, h.kb.ID, "annual leave portal", SearchHybrid, 0)
	b, _ := dense.Search(context.Background(), h.tenant, h.kb.ID, "annual leave portal", SearchDense, 0)
	if len(a.Chunks) != len(b.Chunks) {
		t.Fatalf("weight 0: hybrid = dense, got %d vs %d", len(a.Chunks), len(b.Chunks))
	}
	for i := range a.Chunks {
		if a.Chunks[i].ChunkID != b.Chunks[i].ChunkID || a.Chunks[i].Score != b.Chunks[i].Cosine {
			t.Fatalf("weight 0: hybrid order/score must equal dense at %d", i)
		}
	}
}

func TestFIX132_RerankOrdersAndFloorFilters(t *testing.T) {
	h, _, ids := newRAGHarness(t, DefaultRAGConfig())
	rag := NewRAGService(h.repo, h.models, withCfg(func(c *RAGConfig) { c.RerankEnabled, c.RerankFloor, c.RerankBudget = true, 0.5, 5*time.Second }))
	if rag.DefaultMode() != SearchHybridRerank {
		t.Fatalf("rerank on → chat uses hybrid_rerank, got %s", rag.DefaultMode())
	}
	res, err := rag.Search(context.Background(), h.tenant, h.kb.ID, "annual leave days", "", 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.Mode != SearchHybridRerank || res.RerankFallback != "" || h.fake.RerankCalls.Load() != 1 {
		t.Fatalf("reranked: mode=%s fallback=%q calls=%d", res.Mode, res.RerankFallback, h.fake.RerankCalls.Load())
	}
	if len(res.Chunks) != 1 || res.Chunks[0].DocumentID != ids["leave.txt"] || res.Chunks[0].Rerank == nil || *res.Chunks[0].Rerank < 0.5 {
		t.Fatalf("only passages at or above the floor, best first: %+v", res.Chunks)
	}
}

func TestFIX132_RerankTimeoutOrErrorFallsBackToFusedOrder(t *testing.T) {
	h, _, ids := newRAGHarness(t, DefaultRAGConfig())
	fused := NewRAGService(h.repo, h.models, DefaultRAGConfig())
	want, _ := fused.Search(context.Background(), h.tenant, h.kb.ID, "annual leave days", SearchHybrid, 0)

	rag := NewRAGService(h.repo, h.models, withCfg(func(c *RAGConfig) { c.RerankEnabled, c.RerankBudget = true, 150*time.Millisecond }))
	h.fake.RerankDelay = 2 * time.Second
	start := time.Now()
	res, err := rag.Search(context.Background(), h.tenant, h.kb.ID, "annual leave days", SearchHybridRerank, 0)
	if err != nil {
		t.Fatalf("a slow reranker must not fail the search: %v", err)
	}
	if took := time.Since(start); took > time.Second {
		t.Fatalf("the rerank budget (150 ms) must bound the search, took %v", took)
	}
	if res.RerankFallback != "timeout" || len(res.Chunks) != len(want.Chunks) || res.Chunks[0].DocumentID != ids["leave.txt"] {
		t.Fatalf("timeout → the fused order: fallback=%q %d vs %d", res.RerankFallback, len(res.Chunks), len(want.Chunks))
	}
	for i := range want.Chunks {
		if res.Chunks[i].ChunkID != want.Chunks[i].ChunkID || res.Chunks[i].Rerank != nil {
			t.Fatalf("fallback keeps the fused order at %d", i)
		}
	}
	h.fake.RerankDelay = 0
	h.fake.RerankFails = true
	res, err = rag.Search(context.Background(), h.tenant, h.kb.ID, "annual leave days", SearchHybridRerank, 0)
	if err != nil || res.RerankFallback != "error" || len(res.Chunks) != len(want.Chunks) {
		t.Fatalf("rerank error → fused order: %v %q", err, res.RerankFallback)
	}
}

func TestFIX132_SearchWithoutModelsFailsClearly(t *testing.T) {
	h, _, _ := newRAGHarness(t, DefaultRAGConfig())
	rag := NewRAGService(h.repo, h.models, DefaultRAGConfig())
	h.fake.SetStatus("loading")
	if _, err := rag.Search(context.Background(), h.tenant, h.kb.ID, "annual leave", "", 0); !IsKBModelsUnavailable(err) {
		t.Fatalf("models loading → unavailable error, got %v", err)
	}
	none := NewRAGService(h.repo, nil, DefaultRAGConfig())
	if _, err := none.Search(context.Background(), h.tenant, h.kb.ID, "x", "", 0); !IsKBModelsUnavailable(err) {
		t.Fatalf("no kb-models → not configured, got %v", err)
	}
}

func withCfg(f func(*RAGConfig)) RAGConfig {
	c := DefaultRAGConfig()
	f(&c)
	return c
}

// KB_MIN_SCORE (0.40): an off-topic question gets no passage at all; a real
// one keeps only passages whose fused score reaches the floor.
func TestFIX132_MinScoreKeepsOffTopicQuestionsOut(t *testing.T) {
	h, rag, ids := newRAGHarness(t, DefaultRAGConfig())
	ctx := context.Background()
	off, err := rag.Search(ctx, h.tenant, h.kb.ID, "zebra migration patterns", SearchHybrid, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(off.Chunks) != 0 || off.BelowFloor == 0 {
		t.Fatalf("off-topic → no passages (below floor %d), got %d", off.BelowFloor, len(off.Chunks))
	}
	on, err := rag.Search(ctx, h.tenant, h.kb.ID, "How many days of annual leave do employees get?", SearchHybrid, 0)
	if err != nil || len(on.Chunks) == 0 || on.Chunks[0].DocumentID != ids["leave.txt"] {
		t.Fatalf("a real question keeps its passage: %+v %v", on, err)
	}
	for _, c := range on.Chunks {
		if c.Score < 0.40 {
			t.Fatalf("a passage under the floor was kept: %v", c.Score)
		}
	}
	// The eval can lift the floor per search.
	zero := 0.0
	all, _ := rag.SearchWith(ctx, h.tenant, h.kb.ID, "zebra migration patterns", SearchHybrid, 0, SearchOptions{MinScore: &zero})
	if len(all.Chunks) != 4 {
		t.Fatalf("min_score 0 → every candidate, got %d", len(all.Chunks))
	}
	// The rerank fallback keeps the same floor.
	rr := NewRAGService(h.repo, h.models, withCfg(func(c *RAGConfig) { c.RerankEnabled = true }))
	h.fake.RerankFails = true
	fb, err := rr.Search(ctx, h.tenant, h.kb.ID, "zebra migration patterns", SearchHybridRerank, 0)
	if err != nil || fb.RerankFallback != "error" || len(fb.Chunks) != 0 {
		t.Fatalf("rerank fallback applies KB_MIN_SCORE: %+v %v", fb, err)
	}
}

// The old dense-only search with its 0.70 cut-off returned nothing for this
// Thai keyword question (evidence c5-retrieval-red-behaviour.txt); chat's
// default mode finds the passage.
func TestFIX132_ThaiKeywordQuestionFindsItsPassage(t *testing.T) {
	h, rag, ids := newRAGHarness(t, DefaultRAGConfig())
	res, err := rag.Search(context.Background(), h.tenant, h.kb.ID, "ราคาห้องพัก", "", 5)
	if err != nil {
		t.Fatal(err)
	}
	if res.Mode != SearchHybrid || len(res.Chunks) == 0 || res.Chunks[0].DocumentID != ids["hotel.txt"] {
		t.Fatalf("the hotel passage comes back first in chat's mode: %+v", res)
	}
}
