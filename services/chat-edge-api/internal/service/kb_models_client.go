package service

// FIX132 B — the knowledge base's model client.
//
// Every model the knowledge base needs runs in the kb-models service on this
// machine (services/kb-models): bge-m3 for the dense vector and its sparse
// keyword weights, bge-reranker-v2-m3 for reranking, PyThaiNLP for Thai word
// segmentation. chat-edge calls it directly on the compose network.
//
// Why there is no DLP step here, when the old path had one: until FIX132 the
// knowledge base embedded through the gateway's /v1/embeddings route, which
// ran the raw-egress gate (credential block + per-tenant PII policy) because
// the text then left the machine for a hosted provider. Nothing leaves the
// machine now: the text goes from chat-edge to a container beside it and the
// answer comes back. There is no third party to protect it from, and a
// masked embedding would be useless anyway. The no-egress property is enforced here, not
// assumed:
//
//   - the base URL must name a compose service, loopback, an RFC 1918
//     address or host.docker.internal (ValidateKBModelsURL), and
//   - the dialer refuses to connect to any address that is not loopback or
//     private, whatever DNS says the name resolves to, and never uses an
//     HTTP proxy from the environment.
//
// Endpoints (services/kb-models/app/main.py):
//
//	GET  /health         {status: loading|ready|failed, error, models{…}}
//	POST /v1/embeddings  {input:[≤64], return_sparse, priority} → dense + sparse
//	POST /v1/rerank      {query, documents:[≤200], priority} → relevance scores
//	POST /v1/segment     {texts:[≤64]} → space-joined Thai words
//
// kb-models runs one request at a time; priority "query" goes ahead of
// "index" waiters, so a chat turn never waits behind a big upload.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"syscall"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// KBModelsDefaultURL is where kb-models answers on the compose network.
const KBModelsDefaultURL = "http://kb-models:8090"

const (
	// KBEmbedDim is bge-m3's dense dimension — the knowledge_chunks.embedding
	// column (migration 049). A vector of any other size is refused here so a
	// wrong model can never write a row the column would reject mid-document.
	KBEmbedDim = 1024
	// KBSparseDim is bge-m3's vocabulary size (XLM-R, 250,002 token ids) —
	// the knowledge_chunks.sparse sparsevec dimension.
	KBSparseDim = 250002
	// KBEmbedBatchMax is how many texts one embeddings call carries. kb-models
	// accepts 64; 16 keeps one call short, so a chat-time query waiting behind
	// an indexing batch waits seconds, not a minute.
	KBEmbedBatchMax = 16
	// kbSegmentBatchMax / kbRerankMaxDocs are kb-models' own limits.
	kbSegmentBatchMax = 64
	kbRerankMaxDocs   = 200
)

// Per-call time limits. Indexing is generous (a batch of 16 passages on CPU,
// possibly queued behind a query); a query is short because a person waits.
var (
	kbTimeoutEmbedIndex = 180 * time.Second
	kbTimeoutEmbedQuery = 20 * time.Second
	kbTimeoutRerank     = 30 * time.Second
	kbTimeoutSegment    = 30 * time.Second
	kbTimeoutHealth     = 3 * time.Second
)

// KBPriority is kb-models' queue class.
type KBPriority string

const (
	KBPriorityQuery KBPriority = "query"
	KBPriorityIndex KBPriority = "index"
)

// SparseVec is bge-m3's lexical weights for one text: token ids (0-based,
// ascending) and their weights. The lexical score of a passage for a query is
// the inner product over shared ids.
type SparseVec = model.SparseVector

// KBEmbedding is one text's embeddings.
type KBEmbedding struct {
	Dense     []float32
	Sparse    *SparseVec
	Tokens    int
	Truncated bool
}

// KBModelsHealth is GET /health.
type KBModelsHealth struct {
	Status string `json:"status"`
	Error  string `json:"error"`
	Models struct {
		Embed struct {
			Repo      string `json:"repo"`
			Revision  string `json:"revision"`
			Dim       int    `json:"dim"`
			MaxTokens int    `json:"max_tokens"`
		} `json:"embed"`
		Rerank struct {
			Repo     string `json:"repo"`
			Revision string `json:"revision"`
			Enabled  bool   `json:"enabled"`
		} `json:"rerank"`
	} `json:"models"`
	Waiting int `json:"waiting"`
}

// ErrKBModelsNotConfigured — no usable KB_MODELS_URL (unset, or refused by
// the guard). The knowledge base reports itself unavailable.
var ErrKBModelsNotConfigured = errors.New("kb-models: not configured")

// ErrKBModelsLoading — kb-models is up but its models are still loading
// (the first start reads ~3 GB of weights). Retried without counting as an
// attempt.
var ErrKBModelsLoading = errors.New("kb-models: models loading")

// KBModelsError is a failed call. Status 0 means no HTTP answer at all
// (connection refused, timeout, reset).
type KBModelsError struct {
	Op     string
	Status int
	Body   string
	Err    error
}

func (e *KBModelsError) Error() string {
	if e.Status == 0 {
		return fmt.Sprintf("kb-models %s: %v", e.Op, e.Err)
	}
	return fmt.Sprintf("kb-models %s: status %d: %s", e.Op, e.Status, strings.TrimSpace(e.Body))
}

func (e *KBModelsError) Unwrap() error { return e.Err }

// IsKBModelsLoading reports a "models are still loading" answer.
func IsKBModelsLoading(err error) bool {
	if errors.Is(err, ErrKBModelsLoading) {
		return true
	}
	var ke *KBModelsError
	return errors.As(err, &ke) && ke.Status == http.StatusServiceUnavailable &&
		strings.Contains(strings.ToLower(ke.Body), "loading")
}

// IsKBModelsUnavailable reports an outcome that says kb-models cannot serve
// right now (not reachable, timed out, 5xx, 429, loading, not configured) —
// as opposed to a refusal of THIS input (4xx), which says nothing about it.
func IsKBModelsUnavailable(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, ErrKBModelsNotConfigured) || IsKBModelsLoading(err) {
		return true
	}
	var ke *KBModelsError
	if errors.As(err, &ke) {
		return ke.Status == 0 || ke.Status >= 500 || ke.Status == http.StatusTooManyRequests
	}
	return false
}

// KBModelsClient talks to kb-models. A nil client is valid and answers every
// call with ErrKBModelsNotConfigured.
type KBModelsClient struct {
	base    string
	http    *http.Client
	observe func(error)
}

// SetObserver is told every embeddings call's outcome (nil = worked), so the
// knowledge availability verdict follows what really happened (FIX130 C2).
func (c *KBModelsClient) SetObserver(fn func(error)) {
	if c != nil {
		c.observe = fn
	}
}

// BaseURL is the validated base URL ("" on a nil client).
func (c *KBModelsClient) BaseURL() string {
	if c == nil {
		return ""
	}
	return c.base
}

// NewKBModelsClient validates rawURL (empty = KBModelsDefaultURL) and builds
// a client whose connections can only reach loopback or private addresses.
func NewKBModelsClient(rawURL string) (*KBModelsClient, error) {
	if strings.TrimSpace(rawURL) == "" {
		rawURL = KBModelsDefaultURL
	}
	u, err := ValidateKBModelsURL(rawURL)
	if err != nil {
		return nil, err
	}
	dialer := &net.Dialer{Timeout: 5 * time.Second, Control: internalOnlyDial}
	transport := &http.Transport{
		Proxy:               nil, // never through an HTTP proxy from the environment
		DialContext:         dialer.DialContext,
		MaxIdleConns:        8,
		MaxIdleConnsPerHost: 8,
		IdleConnTimeout:     90 * time.Second,
	}
	return &KBModelsClient{
		base: strings.TrimRight(u.String(), "/"),
		http: &http.Client{Transport: transport},
	}, nil
}

// composeServiceName is a single DNS label, the shape of a compose service
// name. A dotted name could be anywhere on the internet, so it is refused.
var composeServiceName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$`)

// ValidateKBModelsURL accepts http(s) URLs whose host is a compose service
// name, localhost, host.docker.internal, a loopback address or an RFC 1918
// address — places on this machine or its private network. Anything else is
// refused: document text must never be sent off the host.
func ValidateKBModelsURL(raw string) (*url.URL, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return nil, fmt.Errorf("KB_MODELS_URL: %w", err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return nil, fmt.Errorf("KB_MODELS_URL: scheme %q is not http or https", u.Scheme)
	}
	if u.User != nil {
		return nil, errors.New("KB_MODELS_URL: credentials in the URL are not allowed")
	}
	host := u.Hostname()
	if host == "" {
		return nil, errors.New("KB_MODELS_URL: no host")
	}
	if ip := net.ParseIP(host); ip != nil {
		if !internalIP(ip) {
			return nil, fmt.Errorf("KB_MODELS_URL: %s is not a loopback or private (RFC 1918) address", host)
		}
		return u, nil
	}
	switch strings.ToLower(host) {
	case "localhost", "host.docker.internal":
		return u, nil
	}
	if composeServiceName.MatchString(host) {
		return u, nil
	}
	return nil, fmt.Errorf("KB_MODELS_URL: host %q is not a compose service name, loopback, RFC 1918 address or host.docker.internal", host)
}

// internalIP: loopback (v4/v6) or IPv4 RFC 1918.
func internalIP(ip net.IP) bool {
	if ip.IsLoopback() {
		return true
	}
	return ip.To4() != nil && ip.IsPrivate()
}

// internalOnlyDial runs after DNS resolution, on the address actually being
// connected to: a service name that resolves to a public address is refused.
func internalOnlyDial(_, address string, _ syscall.RawConn) error {
	host, _, err := net.SplitHostPort(address)
	if err != nil {
		return err
	}
	ip := net.ParseIP(host)
	if ip == nil || !internalIP(ip) {
		return fmt.Errorf("kb-models: refusing to connect to %s (not loopback or private)", address)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

func (c *KBModelsClient) do(ctx context.Context, op, method, path string, in, out any, limit time.Duration) error {
	if c == nil {
		return ErrKBModelsNotConfigured
	}
	cctx, cancel := context.WithTimeout(ctx, limit)
	defer cancel()
	var body io.Reader
	if in != nil {
		buf, err := json.Marshal(in)
		if err != nil {
			return fmt.Errorf("kb-models %s: marshal: %w", op, err)
		}
		body = bytes.NewReader(buf)
	}
	req, err := http.NewRequestWithContext(cctx, method, c.base+path, body)
	if err != nil {
		return fmt.Errorf("kb-models %s: %w", op, err)
	}
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		if ctx.Err() != nil { // the caller gave up — not a verdict on kb-models
			return ctx.Err()
		}
		return &KBModelsError{Op: op, Err: err}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		return &KBModelsError{Op: op, Status: resp.StatusCode, Body: string(b)}
	}
	if out == nil {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return &KBModelsError{Op: op, Status: resp.StatusCode, Body: "undecodable answer", Err: err}
	}
	return nil
}

type kbEmbedResponse struct {
	Data []struct {
		Index     int       `json:"index"`
		Embedding []float32 `json:"embedding"`
		Tokens    int       `json:"tokens"`
		Truncated bool      `json:"truncated"`
		Sparse    *struct {
			Indices []int     `json:"indices"`
			Values  []float32 `json:"values"`
		} `json:"sparse"`
	} `json:"data"`
}

// Embed returns one embedding per text, in order. More than KBEmbedBatchMax
// texts are sent in several calls. withSparse asks for bge-m3's lexical
// weights too (same forward pass, no extra model run).
func (c *KBModelsClient) Embed(ctx context.Context, texts []string, withSparse bool, prio KBPriority) ([]KBEmbedding, error) {
	out, err := c.embed(ctx, texts, withSparse, prio)
	if c != nil && c.observe != nil {
		c.observe(err)
	}
	return out, err
}

func (c *KBModelsClient) embed(ctx context.Context, texts []string, withSparse bool, prio KBPriority) ([]KBEmbedding, error) {
	if c == nil {
		return nil, ErrKBModelsNotConfigured
	}
	if len(texts) == 0 {
		return nil, nil
	}
	for i, t := range texts {
		if strings.TrimSpace(t) == "" {
			return nil, fmt.Errorf("kb-models embeddings: input %d is empty", i)
		}
	}
	limit := kbTimeoutEmbedIndex
	if prio == KBPriorityQuery {
		limit = kbTimeoutEmbedQuery
	}
	out := make([]KBEmbedding, 0, len(texts))
	for start := 0; start < len(texts); start += KBEmbedBatchMax {
		end := min(start+KBEmbedBatchMax, len(texts))
		batch := texts[start:end]
		var resp kbEmbedResponse
		if err := c.do(ctx, "embeddings", http.MethodPost, "/v1/embeddings", map[string]any{
			"input":         batch,
			"return_sparse": withSparse,
			"priority":      string(prio),
		}, &resp, limit); err != nil {
			return nil, err
		}
		if len(resp.Data) != len(batch) {
			return nil, &KBModelsError{Op: "embeddings", Status: 200, Body: fmt.Sprintf("%d embeddings for %d inputs", len(resp.Data), len(batch))}
		}
		got := make([]KBEmbedding, len(batch))
		seen := make([]bool, len(batch))
		for _, d := range resp.Data {
			if d.Index < 0 || d.Index >= len(batch) || seen[d.Index] {
				return nil, &KBModelsError{Op: "embeddings", Status: 200, Body: fmt.Sprintf("bad index %d", d.Index)}
			}
			if len(d.Embedding) != KBEmbedDim {
				return nil, &KBModelsError{Op: "embeddings", Status: 200, Body: fmt.Sprintf("dimension %d, want %d", len(d.Embedding), KBEmbedDim)}
			}
			seen[d.Index] = true
			e := KBEmbedding{Dense: d.Embedding, Tokens: d.Tokens, Truncated: d.Truncated}
			if withSparse {
				sv := &SparseVec{}
				if d.Sparse != nil {
					if len(d.Sparse.Indices) != len(d.Sparse.Values) {
						return nil, &KBModelsError{Op: "embeddings", Status: 200, Body: "sparse indices/values differ in length"}
					}
					for k, id := range d.Sparse.Indices {
						if id < 0 || id >= KBSparseDim {
							return nil, &KBModelsError{Op: "embeddings", Status: 200, Body: fmt.Sprintf("sparse id %d out of range", id)}
						}
						if k > 0 && id <= d.Sparse.Indices[k-1] {
							return nil, &KBModelsError{Op: "embeddings", Status: 200, Body: "sparse ids not ascending"}
						}
					}
					sv.Indices, sv.Values = d.Sparse.Indices, d.Sparse.Values
				}
				e.Sparse = sv
			}
			got[d.Index] = e
		}
		out = append(out, got...)
	}
	return out, nil
}

// Rerank scores every document against the query (0..1, higher = more
// relevant) and returns the scores in the documents' order.
func (c *KBModelsClient) Rerank(ctx context.Context, query string, docs []string) ([]float64, error) {
	if c == nil {
		return nil, ErrKBModelsNotConfigured
	}
	if len(docs) == 0 {
		return nil, nil
	}
	if len(docs) > kbRerankMaxDocs {
		docs = docs[:kbRerankMaxDocs]
	}
	var resp struct {
		Results []struct {
			Index int     `json:"index"`
			Score float64 `json:"relevance_score"`
		} `json:"results"`
	}
	if err := c.do(ctx, "rerank", http.MethodPost, "/v1/rerank", map[string]any{
		"query":     query,
		"documents": docs,
		"priority":  string(KBPriorityQuery),
	}, &resp, kbTimeoutRerank); err != nil {
		return nil, err
	}
	scores := make([]float64, len(docs))
	seen := 0
	for _, r := range resp.Results {
		if r.Index < 0 || r.Index >= len(docs) {
			return nil, &KBModelsError{Op: "rerank", Status: 200, Body: fmt.Sprintf("bad index %d", r.Index)}
		}
		scores[r.Index] = r.Score
		seen++
	}
	if seen != len(docs) {
		return nil, &KBModelsError{Op: "rerank", Status: 200, Body: fmt.Sprintf("%d scores for %d documents", seen, len(docs))}
	}
	return scores, nil
}

// Segment returns each text with its Thai words separated by single spaces
// (PyThaiNLP newmm). Latin text passes through as its own words.
func (c *KBModelsClient) Segment(ctx context.Context, texts []string) ([]string, error) {
	if c == nil {
		return nil, ErrKBModelsNotConfigured
	}
	out := make([]string, 0, len(texts))
	for start := 0; start < len(texts); start += kbSegmentBatchMax {
		end := min(start+kbSegmentBatchMax, len(texts))
		var resp struct {
			Texts []string `json:"texts"`
		}
		if err := c.do(ctx, "segment", http.MethodPost, "/v1/segment", map[string]any{
			"texts": texts[start:end],
		}, &resp, kbTimeoutSegment); err != nil {
			return nil, err
		}
		if len(resp.Texts) != end-start {
			return nil, &KBModelsError{Op: "segment", Status: 200, Body: fmt.Sprintf("%d texts for %d inputs", len(resp.Texts), end-start)}
		}
		out = append(out, resp.Texts...)
	}
	return out, nil
}

// Health reads GET /health. A reachable service that is still loading
// returns its health and ErrKBModelsLoading; one that failed to load returns
// a *KBModelsError with status 503.
func (c *KBModelsClient) Health(ctx context.Context) (*KBModelsHealth, error) {
	var h KBModelsHealth
	if err := c.do(ctx, "health", http.MethodGet, "/health", nil, &h, kbTimeoutHealth); err != nil {
		return nil, err
	}
	switch h.Status {
	case "ready":
		return &h, nil
	case "loading":
		return &h, ErrKBModelsLoading
	default:
		return &h, &KBModelsError{Op: "health", Status: http.StatusServiceUnavailable, Body: "models " + h.Status + ": " + h.Error}
	}
}

// Probe is the availability check: nil when kb-models is ready.
func (c *KBModelsClient) Probe(ctx context.Context) error {
	_, err := c.Health(ctx)
	return err
}
