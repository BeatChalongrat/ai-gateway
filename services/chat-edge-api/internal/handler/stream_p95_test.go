package handler

// FIX41 G1 criterion 9 — does a normal turn get slower?
//
// SELF-CONTAINED ON PURPOSE. This file uses only exported API plus the
// real chi router, so the identical file compiles and runs against the
// PRE-FIX41 tree in a git worktree. That is what makes the before/after
// comparison a measurement rather than two numbers taken from two
// different programs.
//
//   FIX41_P95=1 FIX41_P95_OUT=<file.json> \
//     go test ./internal/handler/ -run TestFix41TurnP95 -count=1 -v
//
// A turn here is a full HTTP round trip: POST the message, read the SSE
// to its terminal frame. The upstream is a scripted fake gateway with no
// artificial delay, so what is being measured is chat-edge's own work —
// which is where a ledger write would show up if it cost anything.

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

const (
	p95Secret = "fix41-p95-portal-secret-not-a-real-key"
	p95Turns  = 40
	p95Deltas = 200
)

type p95Models struct{}

func (p95Models) ModelAllowed(string) bool { return true }
func (p95Models) ModelIDs() []string       { return []string{"gpt-4o-mini"} }

func TestFix41TurnP95(t *testing.T) {
	if os.Getenv("FIX41_P95") != "1" {
		t.Skip("set FIX41_P95=1 to measure per-turn latency")
	}
	ctx := context.Background()
	// Own database per test, dropped when the test ends — no
	// cleanup code, so none can silently fail (internal/testdb).
	pool := testdb.Fresh(t)

	// Fake gateway: a fixed-size answer, no artificial pauses.
	gw := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fl, _ := w.(http.Flusher)
		chunk, _ := json.Marshal(map[string]any{
			"id": "p95", "object": "chat.completion.chunk",
			"choices": []any{map[string]any{"index": 0, "delta": map[string]any{"content": "ข้อความสั้น ๆ "}}},
		})
		for i := 0; i < p95Deltas; i++ {
			fmt.Fprintf(w, "data: %s\n\n", chunk)
			if fl != nil {
				fl.Flush()
			}
		}
		fmt.Fprint(w, "data: [DONE]\n\n")
		if fl != nil {
			fl.Flush()
		}
	}))
	defer gw.Close()

	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	client := service.NewGatewayClient(gw.URL+"/v1", "k", "", 0, "s")
	svc := service.NewMessageService(cr, mr, client, nil, p95Models{}, 10)
	configureLedgerIfPresent(t, svc)

	h := NewMessageHandler(svc)
	prov := service.NewProvisionService(pool)
	r := chi.NewRouter()
	r.Route("/api/v1", func(r chi.Router) {
		r.Use(middleware.RequireAuth(p95Secret, prov))
		r.Post("/conversations/{id}/messages", h.Send)
	})
	srv := httptest.NewServer(r)
	defer srv.Close()

	userID, tenantID := uuid.New(), uuid.New()
	claims := jwt.MapClaims{
		"sub": userID.String(), "email": "p95@local", "type": "portal", "token_use": "access",
		"tenant_id": tenantID.String(), "tenant_role": "owner",
		"iat": time.Now().Unix(), "exp": time.Now().Add(time.Hour).Unix(),
	}
	token, _ := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(p95Secret))
	tenantUUID, err := prov.EnsureUserExists(ctx, userID, "p95@local", "owner", tenantID.String())
	if err != nil {
		t.Skipf("provision failed: %v", err)
	}
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantUUID, UserID: userID, ModelID: "gpt-4o-mini", Title: "p95"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	url := fmt.Sprintf("%s/api/v1/conversations/%s/messages", srv.URL, conv.ID)

	turn := func() (time.Duration, int, error) {
		start := time.Now()
		req, _ := http.NewRequest(http.MethodPost, url, strings.NewReader(`{"content":"p95 probe","stream":true}`))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return 0, 0, err
		}
		defer resp.Body.Close()
		frames := 0
		sc := bufio.NewScanner(resp.Body)
		sc.Buffer(make([]byte, 1<<20), 1<<20)
		for sc.Scan() {
			if strings.HasPrefix(sc.Text(), "event: ") {
				frames++
			}
		}
		return time.Since(start), frames, nil
	}

	// Warm the pools so the first connection's cost is not read as latency.
	for i := 0; i < 3; i++ {
		if _, _, err := turn(); err != nil {
			t.Fatalf("warmup turn: %v", err)
		}
	}

	var samples []time.Duration
	frames := 0
	for i := 0; i < p95Turns; i++ {
		d, f, err := turn()
		if err != nil {
			t.Fatalf("turn %d: %v", i, err)
		}
		samples = append(samples, d)
		frames = f
	}
	sort.Slice(samples, func(i, j int) bool { return samples[i] < samples[j] })
	pick := func(p float64) float64 {
		return float64(samples[int(float64(len(samples)-1)*p)].Nanoseconds()) / 1e6
	}
	var total time.Duration
	for _, s := range samples {
		total += s
	}
	out := map[string]any{
		"turns":           p95Turns,
		"deltas_per_turn": p95Deltas,
		"frames_observed": frames,
		"p50_ms":          pick(0.50),
		"p95_ms":          pick(0.95),
		"p99_ms":          pick(0.99),
		"mean_ms":         float64(total.Nanoseconds()) / 1e6 / float64(len(samples)),
		"min_ms":          float64(samples[0].Nanoseconds()) / 1e6,
		"max_ms":          float64(samples[len(samples)-1].Nanoseconds()) / 1e6,
	}
	b, _ := json.MarshalIndent(out, "", "  ")
	t.Logf("turn latency over %d turns (%d frames each): %s", p95Turns, frames, b)
	if p := os.Getenv("FIX41_P95_OUT"); p != "" {
		_ = os.MkdirAll(filepath.Dir(p), 0o755)
		if err := os.WriteFile(p, b, 0o644); err != nil {
			t.Fatalf("write %s: %v", p, err)
		}
		t.Logf("wrote %s", p)
	}
}
