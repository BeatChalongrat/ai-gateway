// Package kbfake is a deterministic stand-in for the kb-models service
// (services/kb-models) for tests: same endpoints, same shapes, no model.
//
//   - /v1/segment splits Thai with a greedy longest match over Words (plus
//     whitespace), so a test knows every true word boundary.
//   - /v1/embeddings gives each word a fixed dimension (FNV hash): the dense
//     vector is the L2-normalised word count vector, so cosine similarity is
//     word overlap; the sparse vector is the same words as token ids with
//     weight = count.
//   - /v1/rerank scores a document by the share of the query's words it
//     contains.
//
// Knobs let a test make it load, fail, block or count calls. Only tests
// import this package.
package kbfake

import (
	"encoding/json"
	"hash/fnv"
	"math"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"unicode"
	"unicode/utf8"
)

const (
	Dim       = 1024
	SparseDim = 250002
)

// Server is the fake.
type Server struct {
	*httptest.Server

	mu sync.Mutex
	// Words is the Thai dictionary /v1/segment matches (longest first).
	Words []string
	// Status is /health's status: "ready" (default), "loading", "failed".
	Status string
	// FailNext makes the next N model calls (embeddings/rerank/segment)
	// answer FailStatus (default 503 "models loading" when Status is
	// loading, else 500).
	FailNext   int
	FailStatus int
	// RerankFails makes every /v1/rerank call answer 500.
	RerankFails bool
	// Block, when non-nil, makes every /v1/embeddings call with priority
	// "index" wait until it is closed (or a value is received).
	Block chan struct{}

	EmbedCalls   atomic.Int64
	RerankCalls  atomic.Int64
	SegmentCalls atomic.Int64
	HealthCalls  atomic.Int64
	// Priorities records every embeddings call's priority, in order.
	Priorities []string
	// BatchSizes records every embeddings call's input count.
	BatchSizes []int
	// EmbedStarted receives one value when an index embeddings call starts
	// (non-blocking send; buffered by the caller of New if wanted).
	EmbedStarted chan struct{}
}

// New starts a ready fake with the given Thai dictionary.
func New(words ...string) *Server {
	s := &Server{Words: append([]string(nil), words...), Status: "ready"}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.health)
	mux.HandleFunc("/v1/embeddings", s.embeddings)
	mux.HandleFunc("/v1/rerank", s.rerank)
	mux.HandleFunc("/v1/segment", s.segment)
	s.Server = httptest.NewServer(mux)
	return s
}

// SetStatus changes /health's status (and whether model calls answer).
func (s *Server) SetStatus(st string) {
	s.mu.Lock()
	s.Status = st
	s.mu.Unlock()
}

// Fail makes the next n model calls fail with status.
func (s *Server) Fail(n, status int) {
	s.mu.Lock()
	s.FailNext, s.FailStatus = n, status
	s.mu.Unlock()
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	s.HealthCalls.Add(1)
	s.mu.Lock()
	st := s.Status
	s.mu.Unlock()
	writeJSON(w, 200, map[string]any{
		"status": st, "error": nil, "waiting": 0,
		"models": map[string]any{
			"embed":  map[string]any{"repo": "BAAI/bge-m3", "revision": "fake", "dim": Dim, "max_tokens": 1024},
			"rerank": map[string]any{"repo": "BAAI/bge-reranker-v2-m3", "revision": "fake", "enabled": true},
		},
	})
}

// gate answers the "not ready" / injected-failure cases; true = handled.
func (s *Server) gate(w http.ResponseWriter) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.Status != "ready" {
		writeJSON(w, 503, map[string]any{"detail": "models " + s.Status})
		return true
	}
	if s.FailNext > 0 {
		s.FailNext--
		st := s.FailStatus
		if st == 0 {
			st = 500
		}
		writeJSON(w, st, map[string]any{"detail": "injected failure"})
		return true
	}
	return false
}

func (s *Server) embeddings(w http.ResponseWriter, r *http.Request) {
	s.EmbedCalls.Add(1)
	var req struct {
		Input        any    `json:"input"`
		ReturnSparse bool   `json:"return_sparse"`
		Priority     string `json:"priority"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	var texts []string
	switch v := req.Input.(type) {
	case string:
		texts = []string{v}
	case []any:
		for _, x := range v {
			str, _ := x.(string)
			texts = append(texts, str)
		}
	}
	s.mu.Lock()
	s.Priorities = append(s.Priorities, req.Priority)
	s.BatchSizes = append(s.BatchSizes, len(texts))
	block, started := s.Block, s.EmbedStarted
	s.mu.Unlock()
	if req.Priority == "index" {
		if started != nil {
			select {
			case started <- struct{}{}:
			default:
			}
		}
		if block != nil {
			select {
			case <-block:
			case <-r.Context().Done():
				return
			}
		}
	}
	if s.gate(w) {
		return
	}
	if len(texts) == 0 || len(texts) > 64 {
		writeJSON(w, 400, map[string]any{"detail": "input must have 1..64 items"})
		return
	}
	data := make([]map[string]any, 0, len(texts))
	for i, t := range texts {
		words := s.WordsOf(t)
		item := map[string]any{"object": "embedding", "index": i, "embedding": Dense(words), "tokens": len(words), "truncated": false}
		if req.ReturnSparse {
			idx, val := Sparse(words)
			item["sparse"] = map[string]any{"indices": idx, "values": val}
		}
		data = append(data, item)
	}
	writeJSON(w, 200, map[string]any{"object": "list", "data": data})
}

func (s *Server) rerank(w http.ResponseWriter, r *http.Request) {
	s.RerankCalls.Add(1)
	var req struct {
		Query     string   `json:"query"`
		Documents []string `json:"documents"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	s.mu.Lock()
	fails := s.RerankFails
	s.mu.Unlock()
	if fails {
		writeJSON(w, 500, map[string]any{"detail": "rerank broke"})
		return
	}
	if s.gate(w) {
		return
	}
	q := uniq(s.WordsOf(req.Query))
	type res struct {
		Index int     `json:"index"`
		Score float64 `json:"relevance_score"`
	}
	out := make([]res, 0, len(req.Documents))
	for i, d := range req.Documents {
		have := map[string]bool{}
		for _, w := range s.WordsOf(d) {
			have[w] = true
		}
		hit := 0
		for _, w := range q {
			if have[w] {
				hit++
			}
		}
		score := 0.0
		if len(q) > 0 {
			score = float64(hit) / float64(len(q))
		}
		out = append(out, res{Index: i, Score: score})
	}
	sort.SliceStable(out, func(a, b int) bool { return out[a].Score > out[b].Score })
	writeJSON(w, 200, map[string]any{"results": out})
}

func (s *Server) segment(w http.ResponseWriter, r *http.Request) {
	s.SegmentCalls.Add(1)
	var req struct {
		Texts []string `json:"texts"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	if s.gate(w) {
		return
	}
	out := make([]string, 0, len(req.Texts))
	for _, t := range req.Texts {
		out = append(out, strings.Join(s.WordsOf(t), " "))
	}
	writeJSON(w, 200, map[string]any{"texts": out})
}

// WordsOf segments text the way the fake's /v1/segment does: whitespace
// separates; inside a run, Thai is matched greedily against Words (longest
// first); anything not in the dictionary becomes a one-rune word (Thai) or
// runs of letters/digits (Latin), lower-cased.
func (s *Server) WordsOf(text string) []string {
	s.mu.Lock()
	dict := append([]string(nil), s.Words...)
	s.mu.Unlock()
	sort.SliceStable(dict, func(a, b int) bool { return utf8.RuneCountInString(dict[a]) > utf8.RuneCountInString(dict[b]) })
	var out []string
	for _, run := range strings.Fields(text) {
		for len(run) > 0 {
			matched := false
			for _, d := range dict {
				if d != "" && strings.HasPrefix(run, d) {
					out = append(out, d)
					run = run[len(d):]
					matched = true
					break
				}
			}
			if matched {
				continue
			}
			r, size := utf8.DecodeRuneInString(run)
			if isThai(r) {
				out = append(out, run[:size])
				run = run[size:]
				continue
			}
			if unicode.IsLetter(r) || unicode.IsDigit(r) {
				end := size
				for end < len(run) {
					r2, s2 := utf8.DecodeRuneInString(run[end:])
					if !(unicode.IsLetter(r2) || unicode.IsDigit(r2)) || isThai(r2) {
						break
					}
					end += s2
				}
				out = append(out, strings.ToLower(run[:end]))
				run = run[end:]
				continue
			}
			run = run[size:] // punctuation: dropped
		}
	}
	return out
}

func isThai(r rune) bool { return r >= 0x0E00 && r <= 0x0E7F }

func hashOf(w string, mod int) int {
	h := fnv.New32a()
	_, _ = h.Write([]byte(w))
	return int(h.Sum32() % uint32(mod))
}

// Dense is the fake's dense vector for a word list.
func Dense(words []string) []float32 {
	v := make([]float64, Dim)
	for _, w := range words {
		v[hashOf(w, Dim)]++
	}
	if len(words) == 0 {
		v[0] = 1
	}
	var n float64
	for _, x := range v {
		n += x * x
	}
	n = math.Sqrt(n)
	out := make([]float32, Dim)
	for i, x := range v {
		out[i] = float32(x / n)
	}
	return out
}

// Sparse is the fake's sparse vector (ascending ids, weight = count).
func Sparse(words []string) ([]int, []float32) {
	m := map[int]float32{}
	for _, w := range words {
		m[hashOf(w, SparseDim-1)]++
	}
	ids := make([]int, 0, len(m))
	for id := range m {
		ids = append(ids, id)
	}
	sort.Ints(ids)
	vals := make([]float32, len(ids))
	for i, id := range ids {
		vals[i] = m[id]
	}
	return ids, vals
}

func uniq(ws []string) []string {
	seen := map[string]bool{}
	out := ws[:0:0]
	for _, w := range ws {
		if !seen[w] {
			seen[w] = true
			out = append(out, w)
		}
	}
	return out
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
