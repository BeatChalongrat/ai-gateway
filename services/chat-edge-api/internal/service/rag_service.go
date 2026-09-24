// RAGService — query-time retrieval for the knowledge base.
//
// FIX132 B. A question is embedded ONCE by kb-models (bge-m3, dense +
// sparse, priority "query" so it goes ahead of indexing). Postgres then
// scores the knowledge base's indexed chunks two ways, exactly (no ANN index
// at pilot scale):
//
//   - dense:  cosine similarity of the bge-m3 vectors;
//   - sparse: bge-m3's own lexical weights (Σ q_w·d_w over shared tokens) —
//     the keyword signal, which needs no Thai word segmentation.
//
// The candidates (top KB_CANDIDATES of each arm) are fused BY SCORE:
// score = cosine + KB_SPARSE_WEIGHT × lexical. On the FIX132 eval set (36
// Thai/English questions) that missed the right passage at rank 1 for 19 %
// of questions against 42 % for equal-weight Reciprocal Rank Fusion — bge-m3
// was trained to combine its own dense and sparse scores this way
// (BGE-M3 paper, s_dense + w·s_lex), so this follows the model rather than
// the rank-only RRF that Azure / Elastic use for unrelated retrievers.
//
// A passage is kept only if its fused score is at least KB_MIN_SCORE (0.40),
// up to KB_TOP_K (20). Measured: the lowest top score of a real question was
// 0.51, off-topic questions topped out at 0.32–0.50 (median 0.38), so 0.40
// loses no real question and keeps most off-topic questions from pulling in
// twenty unrelated passages. When nothing passes, no passage is given to the
// model at all. (This replaces the old 0.70 cosine cut-off, which was tuned to
// OpenAI's embedding scale and applied to the dense score alone.)
//
// Optionally (KB_RERANK_ENABLED) the fused top KB_RERANK_CANDIDATES go to
// bge-reranker-v2-m3 — what Anthropic's Contextual Retrieval and most vendor
// pipelines add last — within KB_RERANK_BUDGET_MS, keeping passages whose
// relevance is at least KB_RERANK_FLOOR (0.01; off-topic questions scored at
// most 0.0056); on a timeout or error the fused order (and KB_MIN_SCORE)
// stands and the turn is not failed. Off by default: measured it improved
// rank-1 misses from 19 % to 8 % but cost 21.7 s per question (p50) on this
// PC's CPU. Contextual chunk headers (Anthropic's LLM-per-chunk step) are not
// used: hybrid retrieval already had the answer in the top 20 for every
// question of the eval set.
//
// The kept passages become the context block (BuildContext), capped by
// KB_CONTEXT_MAX_CHARS and by a quarter of the model's context window.
package service

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// SearchMode selects the retrieval layers (the search endpoint lets the
// eval compare them through the real code path).
type SearchMode string

const (
	SearchDense        SearchMode = "dense"
	SearchSparse       SearchMode = "sparse"
	SearchHybrid       SearchMode = "hybrid"
	SearchHybridRerank SearchMode = "hybrid_rerank"
)

// ParseSearchMode accepts "" (→ the default) or one of the four modes.
func ParseSearchMode(s string) (SearchMode, bool) {
	switch m := SearchMode(strings.TrimSpace(strings.ToLower(s))); m {
	case "":
		return "", true
	case SearchDense, SearchSparse, SearchHybrid, SearchHybridRerank:
		return m, true
	}
	return "", false
}

// RAGConfig — the retrieval knobs (env names in brackets).
type RAGConfig struct {
	SparseWeight     float64       // KB_SPARSE_WEIGHT, 0.3; 0 = dense only
	Candidates       int           // KB_CANDIDATES, 50 per arm
	RerankEnabled    bool          // KB_RERANK_ENABLED, false
	RerankCandidates int           // KB_RERANK_CANDIDATES, 40
	RerankBudget     time.Duration // KB_RERANK_BUDGET_MS, 4000 ms
	RerankFloor      float64       // KB_RERANK_FLOOR, 0.01
	MinScore         float64       // KB_MIN_SCORE, 0.40 (fused score, no reranker)
	TopK             int           // KB_TOP_K, 20
	ContextMaxChars  int           // KB_CONTEXT_MAX_CHARS, 24000
}

// DefaultRAGConfig is the measured default (FIX132 eval).
func DefaultRAGConfig() RAGConfig {
	return RAGConfig{
		SparseWeight:     0.3,
		Candidates:       50,
		RerankEnabled:    false,
		RerankCandidates: 40,
		RerankBudget:     4 * time.Second,
		RerankFloor:      0.01,
		MinScore:         0.40,
		TopK:             20,
		ContextMaxChars:  24000,
	}
}

func (c RAGConfig) withDefaults() RAGConfig {
	d := DefaultRAGConfig()
	if c.SparseWeight < 0 {
		c.SparseWeight = 0
	}
	if c.Candidates <= 0 {
		c.Candidates = d.Candidates
	}
	if c.Candidates > 500 {
		c.Candidates = 500
	}
	if c.RerankCandidates <= 0 {
		c.RerankCandidates = d.RerankCandidates
	}
	if c.RerankCandidates > kbRerankMaxDocs {
		c.RerankCandidates = kbRerankMaxDocs
	}
	if c.RerankBudget <= 0 {
		c.RerankBudget = d.RerankBudget
	}
	if c.TopK <= 0 {
		c.TopK = d.TopK
	}
	if c.ContextMaxChars <= 0 {
		c.ContextMaxChars = d.ContextMaxChars
	}
	return c
}

// RetrievedChunk is one retrieval hit — for the search endpoint and the
// context block.
type RetrievedChunk struct {
	ChunkID    uuid.UUID
	Content    string
	Score      float64  // the mode's score (fused / cosine / lexical)
	Rerank     *float64 // reranker relevance 0..1, when reranked
	DocumentID uuid.UUID
	Filename   string
	ChunkIndex int
	Cosine     float64
	Lexical    float64
}

// SearchTiming is the search's cost in milliseconds.
type SearchTiming struct {
	Embed  int64 `json:"embed"`
	SQL    int64 `json:"sql"`
	Rerank int64 `json:"rerank"`
	Total  int64 `json:"total"`
}

// SearchResult is one search.
type SearchResult struct {
	Mode   SearchMode
	Chunks []RetrievedChunk
	Timing SearchTiming
	// RerankFallback says why a hybrid_rerank search kept the fused order:
	// "" (it reranked, or no rerank was asked), "timeout", "error".
	RerankFallback string
	// BelowFloor is how many fused candidates KB_MIN_SCORE (or the rerank
	// floor) left out.
	BelowFloor int
}

// SearchOptions are per-search overrides (the eval endpoint); nil fields
// take the config.
type SearchOptions struct {
	MinScore *float64
}

type RAGService struct {
	repo   *repository.KnowledgeRepo
	models *KBModelsClient
	cfg    RAGConfig
}

func NewRAGService(repo *repository.KnowledgeRepo, models *KBModelsClient, cfg RAGConfig) *RAGService {
	return &RAGService{repo: repo, models: models, cfg: cfg.withDefaults()}
}

// Config is the effective retrieval config.
func (s *RAGService) Config() RAGConfig { return s.cfg }

// Enabled reports whether retrieval can run (kb-models configured).
func (s *RAGService) Enabled() bool { return s != nil && s.models != nil }

// DefaultMode is what chat uses: hybrid, reranked when the reranker is on.
func (s *RAGService) DefaultMode() SearchMode {
	if s.cfg.RerankEnabled {
		return SearchHybridRerank
	}
	return SearchHybrid
}

// Search retrieves from one knowledge base. The caller has already checked
// that the asker may read it. mode "" = DefaultMode; topK ≤ 0 = KB_TOP_K.
func (s *RAGService) Search(ctx context.Context, tenantID, kbID uuid.UUID, query string, mode SearchMode, topK int) (*SearchResult, error) {
	return s.SearchWith(ctx, tenantID, kbID, query, mode, topK, SearchOptions{})
}

// SearchWith is Search with per-search overrides.
func (s *RAGService) SearchWith(ctx context.Context, tenantID, kbID uuid.UUID, query string, mode SearchMode, topK int, opt SearchOptions) (*SearchResult, error) {
	start := time.Now()
	minScore := s.cfg.MinScore
	if opt.MinScore != nil {
		minScore = *opt.MinScore
	}
	if !s.Enabled() {
		return nil, ErrKBModelsNotConfigured
	}
	if mode == "" {
		mode = s.DefaultMode()
	}
	if topK <= 0 {
		topK = s.cfg.TopK
	}
	if topK > 200 {
		topK = 200
	}
	query = strings.TrimSpace(query)
	if query == "" {
		return nil, errors.New("rag: empty query")
	}
	res := &SearchResult{Mode: mode}

	// One embeddings call: dense + sparse together.
	t0 := time.Now()
	embs, err := s.models.Embed(ctx, []string{query}, true, KBPriorityQuery)
	res.Timing.Embed = time.Since(t0).Milliseconds()
	if err != nil {
		return nil, err
	}
	q := embs[0]

	useDense := mode != SearchSparse
	useSparse := mode == SearchSparse || ((mode == SearchHybrid || mode == SearchHybridRerank) && s.cfg.SparseWeight > 0)
	t1 := time.Now()
	cands, err := s.repo.SearchCandidates(ctx, tenantID, kbID, q.Dense, q.Sparse, s.cfg.Candidates, useDense, useSparse)
	res.Timing.SQL = time.Since(t1).Milliseconds()
	if err != nil {
		return nil, err
	}

	hits := make([]RetrievedChunk, 0, len(cands))
	for _, c := range cands {
		h := RetrievedChunk{
			ChunkID: c.ChunkID, Content: c.Content, DocumentID: c.DocumentID,
			Filename: c.Filename, ChunkIndex: c.ChunkIndex, Cosine: c.Cosine, Lexical: c.Lexical,
		}
		switch mode {
		case SearchDense:
			if c.DenseRank == 0 {
				continue
			}
			h.Score = c.Cosine
		case SearchSparse:
			if c.SparseRank == 0 {
				continue
			}
			h.Score = c.Lexical
		default:
			h.Score = c.Cosine + s.cfg.SparseWeight*c.Lexical
		}
		hits = append(hits, h)
	}
	sort.SliceStable(hits, func(i, j int) bool {
		if hits[i].Score != hits[j].Score {
			return hits[i].Score > hits[j].Score
		}
		return hits[i].ChunkID.String() < hits[j].ChunkID.String()
	})

	// The fused-score floor: hybrid, and hybrid_rerank when the reranker did
	// not answer. (dense / sparse are single-arm debugging views: no floor.)
	floor := func(in []RetrievedChunk) []RetrievedChunk {
		out := in[:0:0]
		for _, h := range in {
			if h.Score >= minScore {
				out = append(out, h)
			}
		}
		res.BelowFloor += len(in) - len(out)
		return out
	}
	switch mode {
	case SearchHybrid:
		hits = floor(hits)
	case SearchHybridRerank:
		if len(hits) > 0 {
			var reranked []RetrievedChunk
			n := min(len(hits), s.cfg.RerankCandidates)
			reranked, res.RerankFallback, res.Timing.Rerank = s.rerank(ctx, query, hits)
			if res.RerankFallback != "" {
				hits = floor(hits)
			} else {
				res.BelowFloor += n - len(reranked)
				hits = reranked
			}
		}
	}
	if len(hits) > topK {
		hits = hits[:topK]
	}
	res.Chunks = hits
	res.Timing.Total = time.Since(start).Milliseconds()
	return res, nil
}

// rerank scores the fused top RerankCandidates with the cross-encoder within
// the time budget and keeps those at or above the floor, best first. On a
// timeout or error the fused order stands (logged; the turn goes on).
func (s *RAGService) rerank(ctx context.Context, query string, hits []RetrievedChunk) ([]RetrievedChunk, string, int64) {
	n := min(len(hits), s.cfg.RerankCandidates)
	docs := make([]string, n)
	for i := 0; i < n; i++ {
		docs[i] = hits[i].Content
	}
	rctx, cancel := context.WithTimeout(ctx, s.cfg.RerankBudget)
	defer cancel()
	t := time.Now()
	scores, err := s.models.Rerank(rctx, query, docs)
	ms := time.Since(t).Milliseconds()
	if err != nil {
		why := "error"
		if rctx.Err() != nil && ctx.Err() == nil {
			why = "timeout"
		}
		slog.Warn("kb rerank skipped; using the fused order", "reason", why, "candidates", n, "ms", ms, "err", err)
		return hits, why, ms
	}
	out := make([]RetrievedChunk, 0, n)
	for i := 0; i < n; i++ {
		sc := scores[i]
		if sc < s.cfg.RerankFloor {
			continue
		}
		h := hits[i]
		h.Rerank = &sc
		out = append(out, h)
	}
	sort.SliceStable(out, func(i, j int) bool { return *out[i].Rerank > *out[j].Rerank })
	return out, "", ms
}

// QueryKnowledgeBase is Search in the default mode, returning the chunks.
func (s *RAGService) QueryKnowledgeBase(ctx context.Context, tenantID, kbID uuid.UUID, query string, topK int) ([]RetrievedChunk, error) {
	res, err := s.Search(ctx, tenantID, kbID, query, "", topK)
	if err != nil {
		return nil, err
	}
	return res.Chunks, nil
}

// BuildContext is the RAGReader entry point: search the KB, format the
// block. Errors are swallowed (returns "") so a retrieval glitch never
// blocks the reply.
func (s *RAGService) BuildContext(ctx context.Context, tenantID, userID, kbID uuid.UUID, query string) string {
	_ = userID
	chunks, err := s.QueryKnowledgeBase(ctx, tenantID, kbID, query, 0)
	if err != nil {
		return ""
	}
	return BuildRAGContext(chunks)
}

// BuildRAGContext formats retrieved chunks into a markdown block
// suitable for prepending to a system prompt. Citation format is
// fixed at “[Source: filename]“ so the message bubble's parser
// can pick it up reliably.
//
// Empty input → empty string so the message service can safely
// concat the result regardless of whether retrieval found anything.
func BuildRAGContext(chunks []RetrievedChunk) string {
	if len(chunks) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteString("## Retrieved Knowledge Base Context\n\n")
	b.WriteString("Use the following information to answer the user's question. ")
	b.WriteString("Cite your sources using [Source: filename] format. ")
	b.WriteString("If the knowledge base doesn't contain relevant information ")
	b.WriteString("for the user's question, say so explicitly rather than guessing.\n\n")
	for i, c := range chunks {
		b.WriteString("---\n")
		fmt.Fprintf(&b, "[Source: %s, section %d]\n", c.Filename, c.ChunkIndex+1)
		b.WriteString(strings.TrimSpace(c.Content))
		b.WriteString("\n")
		if i == len(chunks)-1 {
			b.WriteString("---\n\n")
		}
	}
	return b.String()
}
