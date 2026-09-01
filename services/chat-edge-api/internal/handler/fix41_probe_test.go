package handler

// FIX41 Part A/G — the stream-durability probe harness.
//
// WHY IT LOOKS LIKE THIS. The probes in the FIX41 brief (P1..P4) are
// browser-shaped ("close the tab mid-stream", "restart chat-edge",
// "press cancel"). Running them against the deployed stack on this box
// is impossible: every provider key in .env is the literal placeholder
// (OPENAI_API_KEY=sk-...), so the gateway answers
// "No fallback chain registered" for every model and no generation ever
// starts (the same wall FIX33 §6.2 hit). Rather than skip the
// measurement, the probe rebuilds the SAME code path in-process:
//
//   * a real chi router with the real middleware.RequireAuth and a
//     real minted portal JWT,
//   * the real MessageHandler / MessageService / StreamBroker over the
//     real postgres-webchat database,
//   * a FAKE GATEWAY (httptest) that scripts the upstream SSE frames,
//     so a stall / a mid-stream provider error / a slow tail are
//     deterministic instead of hoping a provider misbehaves on cue.
//
// Every HTTP hop the browser makes is a real HTTP hop here; the only
// substitution is who answers /chat/completions. What each probe
// simulates:
//
//   P1  close the tab mid-stream, reopen within 5s  → disconnect the
//       SSE reader, then GET the resume endpoint.
//   P2  `docker compose restart chat-edge-api`      → throw away the
//       whole MessageService + broker (that is precisely what a
//       container restart destroys: in-process memory) and rebuild it
//       over the SAME database, then resume.
//   P3  press Stop mid-stream                       → POST /cancel
//       while a SECOND viewer is attached, and record that viewer's
//       last frame. (The tab that pressed Stop learns nothing from the
//       wire — it aborted its own fetch — so the only client that can
//       be *told* is another attached viewer, which is exactly the
//       resume path.)
//   P4  provider errors mid-stream                  → the fake gateway
//       emits an {"error":...} frame, then closes.
//
// Raw frames from every probe are written to $FIX41_PROBE_OUT as JSON.
//
// Opt-in: FIX41_PROBE=1. Skips cleanly when unset or the DB is down.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

const probePortalSecret = "fix41-probe-portal-secret-not-a-real-key"

// ---------------------------------------------------------------------------
// captured frames
// ---------------------------------------------------------------------------

// probeFrame is one SSE frame as the CLIENT saw it. RunID/Seq are read
// out of the payload when present (they are absent before Part C — that
// absence is the Part A finding).
type probeFrame struct {
	Event string          `json:"event"`
	RunID string          `json:"run_id,omitempty"`
	Seq   int64           `json:"seq,omitempty"`
	Data  json.RawMessage `json:"data"`
}

func parseFrames(raw string) []probeFrame {
	var out []probeFrame
	for _, block := range strings.Split(raw, "\n\n") {
		block = strings.TrimSpace(block)
		if block == "" {
			continue
		}
		var ev string
		var data string
		for _, line := range strings.Split(block, "\n") {
			switch {
			case strings.HasPrefix(line, "event: "):
				ev = strings.TrimPrefix(line, "event: ")
			case strings.HasPrefix(line, "data: "):
				data = strings.TrimPrefix(line, "data: ")
			}
		}
		if data == "" {
			continue // heartbeat / comment frame — never an event
		}
		f := probeFrame{Event: ev, Data: json.RawMessage(data)}
		var env struct {
			RunID string `json:"run_id"`
			Seq   int64  `json:"seq"`
		}
		if json.Unmarshal([]byte(data), &env) == nil {
			f.RunID, f.Seq = env.RunID, env.Seq
		}
		out = append(out, f)
	}
	return out
}

func eventNames(fs []probeFrame) []string {
	out := make([]string, 0, len(fs))
	for _, f := range fs {
		out = append(out, f.Event)
	}
	return out
}

// concatDeltas rebuilds the text the client would have rendered.
func concatDeltas(fs []probeFrame) string {
	var b strings.Builder
	for _, f := range fs {
		if f.Event != "content_delta" {
			continue
		}
		var d struct {
			Delta string `json:"delta"`
		}
		if json.Unmarshal(f.Data, &d) == nil {
			b.WriteString(d.Delta)
		}
	}
	return b.String()
}

// ---------------------------------------------------------------------------
// fake gateway
// ---------------------------------------------------------------------------

// gwScript describes what the fake gateway streams back.
type gwScript struct {
	// chunks are raw `data: ` payloads, emitted in order.
	chunks []string
	// gap is the pause between chunks (lets the probe disconnect
	// mid-stream deterministically).
	gap time.Duration
	// hold, when non-nil, is closed by the probe to release the tail of
	// the stream — used to hold a generation open while a second client
	// attaches / a cancel lands.
	hold <-chan struct{}
	// holdAfter is the chunk index after which the stream waits on hold.
	holdAfter int
}

func newFakeGateway(t *testing.T, script *gwScript) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fl, _ := w.(http.Flusher)
		for i, c := range script.chunks {
			select {
			case <-r.Context().Done():
				return
			default:
			}
			fmt.Fprintf(w, "data: %s\n\n", c)
			if fl != nil {
				fl.Flush()
			}
			if script.hold != nil && i == script.holdAfter {
				select {
				case <-script.hold:
				case <-r.Context().Done():
					return
				case <-time.After(20 * time.Second):
					return
				}
			}
			if script.gap > 0 {
				select {
				case <-time.After(script.gap):
				case <-r.Context().Done():
					return
				}
			}
		}
		fmt.Fprint(w, "data: [DONE]\n\n")
		if fl != nil {
			fl.Flush()
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func contentChunk(s string) string {
	b, _ := json.Marshal(map[string]any{
		"id": "probe", "object": "chat.completion.chunk",
		"choices": []any{map[string]any{"index": 0, "delta": map[string]any{"content": s}}},
	})
	return string(b)
}

func typedChunk(m map[string]any) string {
	b, _ := json.Marshal(m)
	return string(b)
}

// ---------------------------------------------------------------------------
// stack under probe
// ---------------------------------------------------------------------------

type probeStack struct {
	pool   *pgxpool.Pool
	svc    *service.MessageService
	srv    *httptest.Server
	token  string
	convID uuid.UUID
	userID uuid.UUID
}

type probeModels struct{}

func (probeModels) ModelAllowed(string) bool { return true }
func (probeModels) ModelIDs() []string       { return []string{"gpt-4o-mini"} }

func probePool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"
	}
	pool, err := pgxpool.New(context.Background(), dsn)
	if err != nil {
		t.Skipf("FIX41 probe: no pool: %v", err)
	}
	if err := pool.Ping(context.Background()); err != nil {
		pool.Close()
		t.Skipf("FIX41 probe: DB unreachable: %v", err)
	}
	return pool
}

func mintProbeToken(userID uuid.UUID, tenantID string) string {
	claims := jwt.MapClaims{
		"sub": userID.String(), "email": "fix41-probe@local",
		"type": "portal", "token_use": "access",
		"tenant_id": tenantID, "tenant_role": "owner", "token_version": 0,
		"iat": time.Now().Unix(), "exp": time.Now().Add(time.Hour).Unix(),
	}
	s, _ := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(probePortalSecret))
	return s
}

// bringUp builds a fresh chat-edge "process": a new MessageService (so a
// new, EMPTY StreamBroker), a new handler, a new HTTP server. Called
// twice by P2 over the same pool — that is the restart.
func bringUp(t *testing.T, pool *pgxpool.Pool, gatewayURL string) (*service.MessageService, *httptest.Server) {
	t.Helper()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	gw := service.NewGatewayClient(gatewayURL, "probe-key", "", 0, "probe-secret")
	svc := service.NewMessageService(cr, mr, gw, nil, probeModels{}, 10)
	h := NewMessageHandler(svc)
	prov := service.NewProvisionService(pool)

	r := chi.NewRouter()
	r.Route("/api/v1", func(r chi.Router) {
		r.Use(middleware.RequireAuth(probePortalSecret, prov))
		r.Post("/conversations/{id}/messages", h.Send)
		r.Post("/conversations/{id}/cancel", h.Cancel)
		r.Get("/conversations/{id}/messages/{messageID}/stream", h.ResumeStream)
	})
	srv := httptest.NewServer(r)
	t.Cleanup(srv.Close)
	return svc, srv
}

func newProbeStack(t *testing.T, gatewayURL string) *probeStack {
	t.Helper()
	pool := probePool(t)
	t.Cleanup(pool.Close)

	userID := uuid.New()
	tenantID := uuid.New()
	token := mintProbeToken(userID, tenantID.String())

	svc, srv := bringUp(t, pool, gatewayURL)

	// Provision the identity rows the same way a first request does, then
	// create the conversation under the tenant UUID the provisioner
	// actually minted.
	prov := service.NewProvisionService(pool)
	tenantUUID, err := prov.EnsureUserExists(context.Background(), userID, "fix41-probe@local", "owner", tenantID.String())
	if err != nil {
		t.Skipf("FIX41 probe: provision failed: %v", err)
	}
	cr := repository.NewConversationRepo(pool)
	conv, err := cr.Create(context.Background(), &model.Conversation{
		TenantID: tenantUUID, UserID: userID, ModelID: "gpt-4o-mini",
		Title: "FIX41 probe",
	})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	return &probeStack{pool: pool, svc: svc, srv: srv, token: token, convID: conv.ID, userID: userID}
}

// ---------------------------------------------------------------------------
// SSE client
// ---------------------------------------------------------------------------

// sseClient reads an SSE response incrementally so the probe can drop
// the connection at an exact point in the stream.
type sseClient struct {
	mu     sync.Mutex
	buf    bytes.Buffer
	cancel context.CancelFunc
	done   chan struct{}
}

func (c *sseClient) raw() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.buf.String()
}

func (c *sseClient) frames() []probeFrame { return parseFrames(c.raw()) }

// countEvent returns how many frames of a given event name arrived.
func (c *sseClient) countEvent(name string) int {
	n := 0
	for _, f := range c.frames() {
		if f.Event == name {
			n++
		}
	}
	return n
}

func (c *sseClient) close() {
	c.cancel()
	<-c.done
}

func openSSE(t *testing.T, method, url, token, body string) (*sseClient, int) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	var rdr *strings.Reader
	if body != "" {
		rdr = strings.NewReader(body)
	}
	var req *http.Request
	var err error
	if rdr != nil {
		req, err = http.NewRequestWithContext(ctx, method, url, rdr)
	} else {
		req, err = http.NewRequestWithContext(ctx, method, url, nil)
	}
	if err != nil {
		cancel()
		t.Fatalf("build request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		cancel()
		t.Fatalf("open stream: %v", err)
	}
	c := &sseClient{cancel: cancel, done: make(chan struct{})}
	go func() {
		defer close(c.done)
		defer resp.Body.Close()
		p := make([]byte, 4096)
		for {
			n, err := resp.Body.Read(p)
			if n > 0 {
				c.mu.Lock()
				c.buf.Write(p[:n])
				c.mu.Unlock()
			}
			if err != nil {
				return
			}
		}
	}()
	return c, resp.StatusCode
}

// waitFor polls until cond or the deadline; returns whether it fired.
func waitFor(cond func() bool, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return cond()
}

// ---------------------------------------------------------------------------
// results
// ---------------------------------------------------------------------------

type probeResult struct {
	Probe    string                  `json:"probe"`
	Question string                  `json:"question"`
	Verdict  string                  `json:"verdict"`
	Notes    []string                `json:"notes,omitempty"`
	Streams  map[string][]probeFrame `json:"streams"`
	Metrics  map[string]any          `json:"metrics,omitempty"`
}

var (
	probeResultsMu sync.Mutex
	probeResults   = map[string]probeResult{}
)

func record(r probeResult) {
	probeResultsMu.Lock()
	defer probeResultsMu.Unlock()
	probeResults[r.Probe] = r
}

func probeEnabled(t *testing.T) {
	t.Helper()
	if os.Getenv("FIX41_PROBE") != "1" {
		t.Skip("set FIX41_PROBE=1 to run the FIX41 stream-durability probes")
	}
}

// TestZZZFix41ProbeFlush writes every recorded probe out. Named so `go
// test` (which runs tests in source order within a file, and files in
// name order) runs it last.
func TestZZZFix41ProbeFlush(t *testing.T) {
	probeEnabled(t)
	out := os.Getenv("FIX41_PROBE_OUT")
	if out == "" {
		t.Skip("FIX41_PROBE_OUT unset — nothing written")
	}
	probeResultsMu.Lock()
	defer probeResultsMu.Unlock()
	if len(probeResults) == 0 {
		t.Skip("no probe results recorded in this run")
	}
	if err := os.MkdirAll(out, 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", out, err)
	}
	for name, r := range probeResults {
		b, _ := json.MarshalIndent(r, "", "  ")
		p := filepath.Join(out, name+".json")
		if err := os.WriteFile(p, b, 0o644); err != nil {
			t.Fatalf("write %s: %v", p, err)
		}
		t.Logf("wrote %s (verdict: %s)", p, r.Verdict)
	}
}

// ---------------------------------------------------------------------------
// capWriter — an http.ResponseWriter + Flusher that keeps every byte, so a
// probe can read the frame log while the drain is still writing.
// ---------------------------------------------------------------------------

type capWriter struct {
	mu  sync.Mutex
	buf bytes.Buffer
	hdr http.Header
}

func (c *capWriter) Header() http.Header {
	if c.hdr == nil {
		c.hdr = http.Header{}
	}
	return c.hdr
}

func (c *capWriter) Write(p []byte) (int, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.buf.Write(p)
}

func (c *capWriter) WriteHeader(int) {}
func (c *capWriter) Flush()          {}

func (c *capWriter) body() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.buf.String()
}
