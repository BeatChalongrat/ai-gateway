package handler

// FIX121 — the send path, end to end: real router, real auth, real
// MessageService over its own database (testdb.Fresh), a scripted gateway.
//
//   - C3: a send that arrives twice under one client-chosen X-Request-Id is
//     ONE turn — one user row, one answer row, one gateway call — and the
//     repeat is handed the first turn, marked Idempotent-Replayed. The same
//     id with different content is refused. No id, no key: two sends are two
//     turns, exactly as before.
//   - C5: the attempt log of a turn whose fallback walk was exhausted is on
//     the row and on the list wire after the stream is gone.
//
// These do not call probeEnabled: they are part of the ordinary lane.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// newFix121Stack is the probe stack with the router the server really runs:
// the Logging middleware in front (it is what honours or mints the request
// id — router.go installs it globally) and the list route beside the send.
func newFix121Stack(t *testing.T, gatewayURL string) *probeStack {
	t.Helper()
	pool := probePool(t)
	t.Cleanup(pool.Close)
	userID, tenantID := uuid.New(), uuid.New()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	gw := service.NewGatewayClient(gatewayURL, "probe-key", "", 0, "probe-secret")
	svc := service.NewMessageService(cr, mr, gw, nil, probeModels{}, 10)
	if rdb := probeRedis(t); rdb != nil {
		svc.ConfigureRunLedger(10*time.Minute, 8000, rdb)
	}
	h := NewMessageHandler(svc)
	prov := service.NewProvisionService(pool)
	r := chi.NewRouter()
	r.Use(middleware.Logging)
	r.Route("/api/v1", func(r chi.Router) {
		r.Use(middleware.RequireAuth(probePortalSecret, prov))
		r.Get("/conversations/{id}/messages", h.List)
		r.Post("/conversations/{id}/messages", h.Send)
		r.Post("/conversations/{id}/cancel", h.Cancel)
		r.Get("/conversations/{id}/messages/{messageID}/stream", h.ResumeStream)
	})
	srv := httptest.NewServer(r)
	t.Cleanup(srv.Close)
	tenantUUID, err := prov.EnsureUserExists(context.Background(), userID, "fix121-send@local", "owner", tenantID.String())
	if err != nil {
		t.Fatalf("provision: %v", err)
	}
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenantUUID, UserID: userID, ModelID: "gpt-4o-mini", Title: "FIX121 send"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	return &probeStack{pool: pool, svc: svc, srv: srv, token: mintProbeToken(userID, tenantID.String()), convID: conv.ID, userID: userID}
}

// countingGateway is newFakeGateway plus a count of completions asked for.
func countingGateway(t *testing.T, script *gwScript) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	inner := newFakeGateway(t, script)
	var calls atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/chat/completions") {
			calls.Add(1)
		}
		req, _ := http.NewRequestWithContext(r.Context(), r.Method, inner.URL+r.URL.Path, r.Body)
		req.Header = r.Header.Clone()
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			return
		}
		defer resp.Body.Close()
		for k, v := range resp.Header {
			w.Header()[k] = v
		}
		w.WriteHeader(resp.StatusCode)
		fl, _ := w.(http.Flusher)
		buf := make([]byte, 2048)
		for {
			n, err := resp.Body.Read(buf)
			if n > 0 {
				_, _ = w.Write(buf[:n])
				if fl != nil {
					fl.Flush()
				}
			}
			if err != nil {
				return
			}
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &calls
}

// postSend posts one message and reads the response to its end.
func postSend(t *testing.T, st *probeStack, body, requestID string) (int, http.Header, string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages", st.srv.URL, st.convID), strings.NewReader(body))
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+st.token)
	req.Header.Set("Content-Type", "application/json")
	if requestID != "" {
		req.Header.Set("X-Request-Id", requestID)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, resp.Header, string(raw)
}

func rowCounts(t *testing.T, st *probeStack) (users, assistants int) {
	t.Helper()
	err := st.pool.QueryRow(context.Background(), `
		SELECT count(*) FILTER (WHERE role = 'user'), count(*) FILTER (WHERE role = 'assistant')
		FROM chat.messages_masked WHERE conversation_id = $1`, st.convID).Scan(&users, &assistants)
	if err != nil {
		t.Fatalf("count rows: %v", err)
	}
	return users, assistants
}

// settle waits for the detached finish callback to write the answer row.
func settle(t *testing.T, st *probeStack, wantStatus string) {
	t.Helper()
	ok := waitFor(func() bool {
		var n int
		_ = st.pool.QueryRow(context.Background(), `
			SELECT count(*) FROM chat.messages_masked
			WHERE conversation_id = $1 AND role = 'assistant' AND status = $2`, st.convID, wantStatus).Scan(&n)
		return n >= 1
	}, 10*time.Second)
	if !ok {
		t.Fatalf("no assistant row reached status %q", wantStatus)
	}
}

const fix121Body = `{"content":"FIX121 one turn","stream":true}`

func TestFix121_SameRequestIDTwiceIsOneTurn(t *testing.T) {
	gw, calls := countingGateway(t, &gwScript{chunks: []string{contentChunk("ONE "), contentChunk("TWO")}})
	st := newFix121Stack(t, gw.URL+"/v1")
	const rid = "fix121-idem-0001"

	code, hdr, first := postSend(t, st, fix121Body, rid)
	if code != http.StatusOK {
		t.Fatalf("first send returned %d: %s", code, first)
	}
	if hdr.Get("Idempotent-Replayed") != "" {
		t.Fatalf("the first request must not be marked as a replay")
	}
	settle(t, st, "completed")

	code, hdr, second := postSend(t, st, fix121Body, rid)
	if code != http.StatusOK {
		t.Fatalf("repeat returned %d: %s", code, second)
	}
	if hdr.Get("Idempotent-Replayed") != "true" {
		t.Fatalf("the repeat must say Idempotent-Replayed: true, headers=%v", hdr)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("the gateway was asked %d times for one turn", got)
	}
	if u, a := rowCounts(t, st); u != 1 || a != 1 {
		t.Fatalf("one turn must be one user row and one answer row, got %d / %d", u, a)
	}
	// The repeat is handed the SAME answer, not an empty success.
	if got := concatDeltas(parseFrames(second)); got != "ONE TWO" {
		t.Fatalf("replayed content = %q, want the first turn's %q", got, "ONE TWO")
	}
	if last := lastEvent(parseFrames(second)); last != "message_complete" {
		t.Fatalf("replay must end on the first turn's terminal, got %q", last)
	}
	// Both responses name the same run.
	if !strings.Contains(first, rid) || !strings.Contains(second, rid) {
		t.Fatalf("both responses must carry run id %q", rid)
	}
}

func TestFix121_SameRequestIDWithDifferentContentIsRefused(t *testing.T) {
	gw, calls := countingGateway(t, &gwScript{chunks: []string{contentChunk("ok")}})
	st := newFix121Stack(t, gw.URL+"/v1")
	const rid = "fix121-idem-0002"

	if code, _, raw := postSend(t, st, fix121Body, rid); code != http.StatusOK {
		t.Fatalf("first send returned %d: %s", code, raw)
	}
	settle(t, st, "completed")

	code, _, raw := postSend(t, st, `{"content":"a DIFFERENT message","stream":true}`, rid)
	if code != http.StatusConflict || !strings.Contains(raw, CodeIdempotencyKeyReused) {
		t.Fatalf("want 409 %s, got %d: %s", CodeIdempotencyKeyReused, code, raw)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("a refused repeat must not reach the gateway (calls=%d)", got)
	}
	if u, a := rowCounts(t, st); u != 1 || a != 1 {
		t.Fatalf("a refused repeat must write nothing, got %d / %d", u, a)
	}
}

func TestFix121_NoSuppliedIDIsExactlyAsBefore(t *testing.T) {
	gw, calls := countingGateway(t, &gwScript{chunks: []string{contentChunk("ok")}})
	st := newFix121Stack(t, gw.URL+"/v1")

	for i := 0; i < 2; i++ {
		code, hdr, raw := postSend(t, st, fix121Body, "")
		if code != http.StatusOK {
			t.Fatalf("send %d returned %d: %s", i, code, raw)
		}
		if hdr.Get("Idempotent-Replayed") != "" {
			t.Fatalf("a send without a client id is never a replay")
		}
	}
	waitFor(func() bool { _, a := rowCounts(t, st); return a == 2 }, 10*time.Second)
	if got := calls.Load(); got != 2 {
		t.Fatalf("two sends without a key are two turns (gateway calls=%d)", got)
	}
	if u, a := rowCounts(t, st); u != 2 || a != 2 {
		t.Fatalf("want 2 user + 2 answer rows, got %d / %d", u, a)
	}
	// and their user rows keep run_id NULL, as every user row did before
	var withRun int
	_ = st.pool.QueryRow(context.Background(), `
		SELECT count(*) FROM chat.messages_masked
		WHERE conversation_id = $1 AND role = 'user' AND run_id IS NOT NULL`, st.convID).Scan(&withRun)
	if withRun != 0 {
		t.Fatalf("a minted id must not be written on the user row (found %d)", withRun)
	}
}

func TestFix121_RepeatWhileTheFirstIsStillStreamingAttachesToIt(t *testing.T) {
	hold := make(chan struct{})
	gw, calls := countingGateway(t, &gwScript{
		chunks: []string{contentChunk("PART-A "), contentChunk("PART-B")}, hold: hold, holdAfter: 0,
	})
	st := newFix121Stack(t, gw.URL+"/v1")
	const rid = "fix121-idem-0003"

	a, code := openSSEWithID(t, st, fix121Body, rid)
	if code != http.StatusOK {
		t.Fatalf("first send returned %d", code)
	}
	if !waitFor(func() bool { return strings.Contains(a.raw(), "PART-A") }, 10*time.Second) {
		t.Fatalf("first stream never produced its first chunk")
	}
	// The same request again, while the first is mid-generation.
	done := make(chan string, 1)
	go func() { _, _, raw := postSend(t, st, fix121Body, rid); done <- raw }()
	time.Sleep(300 * time.Millisecond)
	close(hold)
	second := <-done
	a.close()

	if got := calls.Load(); got != 1 {
		t.Fatalf("a repeat during the stream must not start a second generation (calls=%d)", got)
	}
	if got := concatDeltas(parseFrames(second)); got != "PART-A PART-B" {
		t.Fatalf("the repeat must see the whole first turn, got %q", got)
	}
	settle(t, st, "completed")
	if u, as := rowCounts(t, st); u != 1 || as != 1 {
		t.Fatalf("want 1 user + 1 answer row, got %d / %d", u, as)
	}
}

func openSSEWithID(t *testing.T, st *probeStack, body, requestID string) (*sseClient, int) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages", st.srv.URL, st.convID), strings.NewReader(body))
	if err != nil {
		cancel()
		t.Fatalf("build request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+st.token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Request-Id", requestID)
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

// ---------------------------------------------------------------------------
// C5 — the attempt log outlives the stream
// ---------------------------------------------------------------------------

func TestFix121_AttemptsOfAFailedTurnAreOnTheRowAndTheListWire(t *testing.T) {
	gw, _ := countingGateway(t, &gwScript{chunks: []string{
		typedChunk(map[string]any{"error": map[string]any{
			"code": "all_models_failed", "message": "every candidate failed",
			"attempts": []any{
				map[string]any{"provider": "sranai", "model": "qwen3.8-27b-fp8", "error_code": "provider_timeout", "error_message": "no first token", "latency_ms": 30000.0},
				map[string]any{"provider": "openai", "model": "gpt-5-mini", "error_code": "provider_no_credit", "junk": strings.Repeat("x", 5000)},
			},
		}}),
	}})
	st := newFix121Stack(t, gw.URL+"/v1")

	code, _, raw := postSend(t, st, fix121Body, "")
	if code != http.StatusOK {
		t.Fatalf("send returned %d: %s", code, raw)
	}
	if last := lastEvent(parseFrames(raw)); last != "error" {
		t.Fatalf("a failed turn ends on error, got %q", last)
	}
	settle(t, st, "error")

	// the row
	var stored []map[string]any
	ok := waitFor(func() bool {
		var b []byte
		_ = st.pool.QueryRow(context.Background(), `
			SELECT attempts FROM chat.messages_masked
			WHERE conversation_id = $1 AND role = 'assistant'`, st.convID).Scan(&b)
		return len(b) > 0 && json.Unmarshal(b, &stored) == nil && len(stored) == 2
	}, 10*time.Second)
	if !ok {
		t.Fatalf("the attempt log was not stored on the row (got %v)", stored)
	}
	if stored[0]["model"] != "qwen3.8-27b-fp8" || stored[0]["error_code"] != "provider_timeout" || stored[1]["model"] != "gpt-5-mini" {
		t.Fatalf("stored attempts lost their order or their fields: %v", stored)
	}
	if _, leaked := stored[1]["junk"]; leaked {
		t.Fatalf("only the closed set of keys is stored, got %v", stored[1])
	}

	// the list wire — what a reload, the desktop and another device read
	req, _ := http.NewRequest(http.MethodGet,
		fmt.Sprintf("%s/api/v1/conversations/%s/messages", st.srv.URL, st.convID), nil)
	req.Header.Set("Authorization", "Bearer "+st.token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	defer resp.Body.Close()
	var listed struct {
		Messages []struct {
			Role          string           `json:"role"`
			ErrorCode     string           `json:"error_code"`
			ErrorAttempts []map[string]any `json:"error_attempts"`
		} `json:"messages"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&listed); err != nil {
		t.Fatalf("decode list: %v", err)
	}
	found := false
	for _, m := range listed.Messages {
		if m.Role == "user" && m.ErrorAttempts != nil {
			t.Fatalf("a user row never carries attempts")
		}
		if m.Role == "assistant" {
			found = true
			if m.ErrorCode != "all_models_failed" || len(m.ErrorAttempts) != 2 || m.ErrorAttempts[1]["model"] != "gpt-5-mini" {
				t.Fatalf("list wire: error_code=%q attempts=%v", m.ErrorCode, m.ErrorAttempts)
			}
		}
	}
	if !found {
		t.Fatalf("no assistant row on the list wire")
	}
}

// ---------------------------------------------------------------------------
// C3 — a repeat of a turn that ENDED BADLY is answered with that ending.
// Found live (FIX121 side stack): the first send was refused before its
// stream opened; the repeat went through resume, which has no ledger for a
// run that never opened and said GENERATION_INTERRUPTED instead.
// ---------------------------------------------------------------------------

func TestFix121_RepeatOfARefusedSendGetsTheSameCodeNotInterrupted(t *testing.T) {
	var calls atomic.Int64
	gw := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/chat/completions") {
			calls.Add(1)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		fmt.Fprint(w, `{"error":{"code":"provider_unavailable","message":"No API key configured for provider 'google'.","retryable":false}}`)
	}))
	t.Cleanup(gw.Close)
	st := newFix121Stack(t, gw.URL+"/v1")
	const rid = "fix121-idem-0004"

	code, _, first := postSend(t, st, fix121Body, rid)
	if code == http.StatusOK || !strings.Contains(first, "provider_unavailable") {
		t.Fatalf("the first send must be refused with the gateway's code, got %d: %s", code, first)
	}
	settle(t, st, "error")

	code, hdr, second := postSend(t, st, fix121Body, rid)
	if code != http.StatusOK || hdr.Get("Idempotent-Replayed") != "true" {
		t.Fatalf("repeat: %d replayed=%q body=%s", code, hdr.Get("Idempotent-Replayed"), second)
	}
	fs := parseFrames(second)
	if last := lastEvent(fs); last != "error" {
		t.Fatalf("the repeat of a failed turn ends on error, got %q (%s)", last, second)
	}
	if strings.Contains(second, "GENERATION_INTERRUPTED") || !strings.Contains(second, `"code":"provider_unavailable"`) {
		t.Fatalf("the repeat must carry the first failure's own code: %s", second)
	}
	if !strings.Contains(second, rid) {
		t.Fatalf("the replay must carry the run id %q: %s", rid, second)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("a repeat must not reach the gateway again (calls=%d)", got)
	}
	if u, a := rowCounts(t, st); u != 1 || a != 1 {
		t.Fatalf("want 1 user + 1 answer row, got %d / %d", u, a)
	}
}

func TestFix121_RepeatOfAStoppedTurnSaysCancelled(t *testing.T) {
	hold := make(chan struct{})
	t.Cleanup(func() {
		select {
		case <-hold:
		default:
			close(hold)
		}
	})
	gw, calls := countingGateway(t, &gwScript{chunks: []string{contentChunk("HALF "), contentChunk("REST")}, hold: hold, holdAfter: 0})
	st := newFix121Stack(t, gw.URL+"/v1")
	const rid = "fix121-idem-0005"

	a, code := openSSEWithID(t, st, fix121Body, rid)
	if code != http.StatusOK {
		t.Fatalf("first send returned %d", code)
	}
	if !waitFor(func() bool { return strings.Contains(a.raw(), "HALF") }, 10*time.Second) {
		t.Fatalf("first chunk never arrived")
	}
	cancel, _ := http.NewRequest(http.MethodPost, fmt.Sprintf("%s/api/v1/conversations/%s/cancel", st.srv.URL, st.convID), nil)
	cancel.Header.Set("Authorization", "Bearer "+st.token)
	if resp, err := http.DefaultClient.Do(cancel); err != nil || resp.StatusCode != http.StatusOK {
		t.Fatalf("cancel: %v %v", resp, err)
	}
	settle(t, st, "cancelled")
	a.close()

	_, hdr, second := postSend(t, st, fix121Body, rid)
	if hdr.Get("Idempotent-Replayed") != "true" {
		t.Fatalf("not a replay: %s", second)
	}
	fs := parseFrames(second)
	if last := lastEvent(fs); last != "cancelled" {
		t.Fatalf("the repeat of a stopped turn ends on cancelled, got %q (%s)", last, second)
	}
	if got := concatDeltas(fs); got != "HALF " {
		t.Fatalf("the repeat shows what the stopped turn kept, got %q", got)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("calls=%d", got)
	}
}
