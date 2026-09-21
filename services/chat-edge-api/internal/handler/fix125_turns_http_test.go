package handler

// FIX125 — the HTTP contract of two chat-edge fixes, end to end: real
// router + auth, real MessageService over its own database (testdb.Fresh),
// a scripted gateway held open mid-stream.
//
//   M2  DELETE of a conversation whose answer is streaming stops the stream
//       (the canceller is called, the send response ends without the
//       gateway being released) and nothing is written into the deleted
//       conversation afterwards.
//   M5  a second send into a conversation that is answering is 409
//       CONVERSATION_BUSY and writes no user row; the same X-Request-Id
//       sent again is still FIX121's replay (not busy); once the first turn
//       ends a new send works.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// recordingCanceller is the real MessageService's Stop, with a record of
// which conversations DELETE asked it to stop.
type recordingCanceller struct {
	inner StreamCanceller
	mu    sync.Mutex
	calls []uuid.UUID
	hits  []bool
}

func (c *recordingCanceller) CancelStream(id uuid.UUID) bool {
	ok := c.inner.CancelStream(id)
	c.mu.Lock()
	c.calls = append(c.calls, id)
	c.hits = append(c.hits, ok)
	c.mu.Unlock()
	return ok
}

type fix125Stack struct {
	*probeStack
	canceller *recordingCanceller
}

func newFix125Stack(t *testing.T, gatewayURL string) *fix125Stack {
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
	ch := NewConversationHandler(service.NewConversationService(cr, probeModels{}))
	ch.SetMessageService(svc)
	rec := &recordingCanceller{inner: svc}
	ch.SetStreamCanceller(rec)
	prov := service.NewProvisionService(pool)
	r := chi.NewRouter()
	r.Use(middleware.Logging)
	r.Route("/api/v1", func(r chi.Router) {
		r.Use(middleware.RequireAuth(probePortalSecret, prov))
		r.Delete("/conversations/{id}", ch.Delete)
		r.Get("/conversations/{id}/messages", h.List)
		r.Post("/conversations/{id}/messages", h.Send)
		r.Post("/conversations/{id}/cancel", h.Cancel)
		r.Get("/conversations/{id}/messages/{messageID}/stream", h.ResumeStream)
	})
	srv := httptest.NewServer(r)
	t.Cleanup(srv.Close)
	tenantUUID, err := prov.EnsureUserExists(context.Background(), userID, "fix125@local", "owner", tenantID.String())
	if err != nil {
		t.Fatalf("provision: %v", err)
	}
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenantUUID, UserID: userID, ModelID: "gpt-4o-mini", Title: "FIX125"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	return &fix125Stack{
		probeStack: &probeStack{pool: pool, svc: svc, srv: srv, token: mintProbeToken(userID, tenantID.String()), convID: conv.ID, userID: userID},
		canceller:  rec,
	}
}

type sendResult struct {
	code int
	hdr  http.Header
	body string
}

// sendAsync posts one send and delivers its whole response on the channel.
func sendAsync(t *testing.T, st *probeStack, body, requestID string) <-chan sendResult {
	out := make(chan sendResult, 1)
	go func() {
		code, hdr, raw := postSend(t, st, body, requestID)
		out <- sendResult{code, hdr, raw}
	}()
	return out
}

// waitStreaming waits until the conversation has an assistant row that is
// still streaming (the first turn is in flight).
func waitStreaming(t *testing.T, st *probeStack) {
	t.Helper()
	ok := waitFor(func() bool {
		var n int
		_ = st.pool.QueryRow(context.Background(), `
			SELECT count(*) FROM chat.messages_masked
			WHERE conversation_id = $1 AND role = 'assistant' AND status = 'streaming'`, st.convID).Scan(&n)
		return n >= 1
	}, 10*time.Second)
	if !ok {
		t.Fatal("the first turn never started streaming")
	}
}

func errorCode(body string) string {
	var env struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	_ = json.Unmarshal([]byte(body), &env)
	return env.Error.Code
}

func TestFIX125_DeleteWhileStreamingStopsTheTurn(t *testing.T) {
	hold := make(chan struct{})
	var releaseOnce sync.Once
	release := func() { releaseOnce.Do(func() { close(hold) }) }
	t.Cleanup(release)
	gw := newFakeGateway(t, &gwScript{chunks: []string{contentChunk("PART "), contentChunk("REST")}, hold: hold, holdAfter: 0})
	st := newFix125Stack(t, gw.URL+"/v1")

	first := sendAsync(t, st.probeStack, `{"content":"FIX125 delete me","stream":true}`, "")
	waitStreaming(t, st.probeStack)

	req, _ := http.NewRequest(http.MethodDelete, fmt.Sprintf("%s/api/v1/conversations/%s", st.srv.URL, st.convID), nil)
	req.Header.Set("Authorization", "Bearer "+st.token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("delete: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("delete returned %d", resp.StatusCode)
	}

	// The gateway is NOT released: only the delete can end this turn.
	select {
	case <-first:
	case <-time.After(5 * time.Second):
		t.Fatal("the turn kept streaming after its conversation was deleted")
	}
	st.canceller.mu.Lock()
	calls, hits := append([]uuid.UUID(nil), st.canceller.calls...), append([]bool(nil), st.canceller.hits...)
	st.canceller.mu.Unlock()
	if len(calls) != 1 || calls[0] != st.convID || !hits[0] {
		t.Fatalf("DELETE must stop the running stream exactly like Stop: calls=%v hits=%v", calls, hits)
	}

	// Nothing lands in the deleted conversation after the stop.
	time.Sleep(300 * time.Millisecond)
	var content, status string
	var count int
	if err := st.pool.QueryRow(context.Background(), `
		SELECT m.content_masked, m.status, c.message_count
		FROM chat.messages_masked m JOIN chat.conversations c ON c.id = m.conversation_id
		WHERE m.conversation_id = $1 AND m.role = 'assistant'`, st.convID).Scan(&content, &status, &count); err != nil {
		t.Fatalf("read row: %v", err)
	}
	if content != "" || status != "streaming" || count != 0 {
		t.Fatalf("the deleted conversation was written to: content=%q status=%q message_count=%d", content, status, count)
	}
}

func TestFIX125_SecondSendIsConversationBusy(t *testing.T) {
	hold := make(chan struct{})
	var releaseOnce sync.Once
	release := func() { releaseOnce.Do(func() { close(hold) }) }
	t.Cleanup(release)
	gw := newFakeGateway(t, &gwScript{chunks: []string{contentChunk("ONE "), contentChunk("TWO")}, hold: hold, holdAfter: 0})
	st := newFix125Stack(t, gw.URL+"/v1")
	const rid = "fix125-busy-0001"
	const body = `{"content":"FIX125 first","stream":true}`

	first := sendAsync(t, st.probeStack, body, rid)
	waitStreaming(t, st.probeStack)

	// Another tab, another message: refused, nothing written.
	code, _, raw := postSend(t, st.probeStack, `{"content":"FIX125 second tab","stream":true}`, "")
	if code != http.StatusConflict || errorCode(raw) != CodeConversationBusy {
		t.Fatalf("second send while answering: %d %s, want 409 %s", code, raw, CodeConversationBusy)
	}
	code, _, raw = postSend(t, st.probeStack, `{"content":"FIX125 second tab, non-stream"}`, "")
	if code != http.StatusConflict || errorCode(raw) != CodeConversationBusy {
		t.Fatalf("non-stream second send: %d %s, want 409 %s", code, raw, CodeConversationBusy)
	}
	if u, _ := rowCounts(t, st.probeStack); u != 1 {
		t.Fatalf("a refused send wrote a user row: %d user rows", u)
	}

	// The SAME request again (FIX121) is the replay of the first turn, not busy.
	replay := sendAsync(t, st.probeStack, body, rid)
	time.Sleep(200 * time.Millisecond)
	release()
	r1 := <-first
	r2 := <-replay
	if r1.code != http.StatusOK {
		t.Fatalf("first send: %d %s", r1.code, r1.body)
	}
	if r2.code != http.StatusOK || r2.hdr.Get("Idempotent-Replayed") != "true" {
		t.Fatalf("same X-Request-Id while answering must still replay: %d %v %s", r2.code, r2.hdr, r2.body)
	}
	if got := concatDeltas(parseFrames(r2.body)); got != "ONE TWO" {
		t.Fatalf("replay content = %q", got)
	}
	settle(t, st.probeStack, "completed")

	// The first turn is over: a new send works.
	code, _, raw = postSend(t, st.probeStack, `{"content":"FIX125 after","stream":true}`, "")
	if code != http.StatusOK {
		t.Fatalf("send after the first turn ended: %d %s", code, raw)
	}
	if u, a := rowCounts(t, st.probeStack); u != 2 || a != 2 {
		t.Fatalf("rows = %d user / %d assistant, want 2 / 2", u, a)
	}
}
