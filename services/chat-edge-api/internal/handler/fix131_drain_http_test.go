package handler

// FIX131 E2 — the HTTP contract of a graceful restart, end to end: real
// router + auth, real MessageService over its own database (testdb.Fresh),
// a scripted gateway held open mid-stream, and the service's drain driven
// the way main.go drives it on SIGTERM.
//
//   - While draining, a new send / regenerate is 503 SERVER_DRAINING with
//     Retry-After and writes nothing; reading the chat still works.
//   - A turn cut at the drain deadline ENDS its live stream on
//     `error` GENERATION_INTERRUPTED (cause interrupted, retryable), the
//     text it streamed stays on the row, and the row says the same.
//   - A turn that finishes inside the drain ends normally.

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

func newFix131Stack(t *testing.T, gatewayURL string) *probeStack {
	t.Helper()
	pool := probePool(t)
	t.Cleanup(pool.Close)
	userID, tenantID := uuid.New(), uuid.New()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	gw := service.NewGatewayClient(gatewayURL, "probe-key", "", 0, "probe-secret")
	svc := service.NewMessageService(cr, mr, gw, nil, probeModels{}, 10)
	svc.SetRunLeases(service.NewRunLeases(service.NewMemoryLeaseStore(), "fix131-test", 45*time.Second, 15*time.Second))
	h := NewMessageHandler(svc)
	prov := service.NewProvisionService(pool)
	r := chi.NewRouter()
	r.Use(middleware.Logging)
	r.Route("/api/v1", func(r chi.Router) {
		r.Use(middleware.RequireAuth(probePortalSecret, prov, nil))
		r.Get("/conversations/{id}/messages", h.List)
		r.Post("/conversations/{id}/messages", h.Send)
		r.Post("/conversations/{id}/messages/{messageID}/regenerate", h.Regenerate)
		r.Get("/conversations/{id}/messages/{messageID}/stream", h.ResumeStream)
	})
	srv := httptest.NewServer(r)
	t.Cleanup(srv.Close)
	tenantUUID, err := prov.EnsureUserExists(context.Background(), userID, "fix131@local", "owner", tenantID.String())
	if err != nil {
		t.Fatalf("provision: %v", err)
	}
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenantUUID, UserID: userID, ModelID: "gpt-4o-mini", Title: "FIX131"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	return &probeStack{pool: pool, svc: svc, srv: srv, token: mintProbeToken(userID, tenantID.String()), convID: conv.ID, userID: userID}
}

func authedDo(t *testing.T, st *probeStack, method, path string) (int, http.Header, string) {
	t.Helper()
	req, _ := http.NewRequest(method, st.srv.URL+path, nil)
	req.Header.Set("Authorization", "Bearer "+st.token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	var b [1 << 16]byte
	n, _ := resp.Body.Read(b[:])
	return resp.StatusCode, resp.Header, string(b[:n])
}

func TestFIX131_NewTurnsWhileDrainingAre503WithRetryAfter(t *testing.T) {
	gw := newFakeGateway(t, &gwScript{chunks: []string{contentChunk("A")}})
	st := newFix131Stack(t, gw.URL+"/v1")
	// One finished turn first, so there is something to regenerate.
	if code, _, raw := postSend(t, st, `{"content":"FIX131 first","stream":true}`, ""); code != http.StatusOK {
		t.Fatalf("first send: %d %s", code, raw)
	}
	settle(t, st, "completed")
	var asstID uuid.UUID
	if err := st.pool.QueryRow(context.Background(), `SELECT id FROM chat.messages_masked WHERE conversation_id = $1 AND role = 'assistant'`, st.convID).Scan(&asstID); err != nil {
		t.Fatal(err)
	}

	st.svc.BeginDrain()
	code, hdr, raw := postSend(t, st, `{"content":"FIX131 during the restart","stream":true}`, "fix131-drain-0001")
	if code != http.StatusServiceUnavailable || errorCode(raw) != CodeServerDraining || hdr.Get("Retry-After") != "5" {
		t.Fatalf("send while draining: %d Retry-After=%q %s, want 503 %s Retry-After 5", code, hdr.Get("Retry-After"), raw, CodeServerDraining)
	}
	code, _, raw = postSend(t, st, `{"content":"FIX131 non-stream during the restart"}`, "")
	if code != http.StatusServiceUnavailable || errorCode(raw) != CodeServerDraining {
		t.Fatalf("non-stream send while draining: %d %s", code, raw)
	}
	code, hdr, raw = authedDo(t, st, http.MethodPost, fmt.Sprintf("/api/v1/conversations/%s/messages/%s/regenerate", st.convID, asstID))
	if code != http.StatusServiceUnavailable || errorCode(raw) != CodeServerDraining || hdr.Get("Retry-After") == "" {
		t.Fatalf("regenerate while draining: %d %s", code, raw)
	}
	if u, a := rowCounts(t, st); u != 1 || a != 1 {
		t.Fatalf("a refused turn wrote rows: %d user / %d assistant, want 1 / 1", u, a)
	}
	// Reading the chat is not a new turn: it keeps working through the drain.
	if code, _, raw := authedDo(t, st, http.MethodGet, fmt.Sprintf("/api/v1/conversations/%s/messages", st.convID)); code != http.StatusOK {
		t.Fatalf("list while draining: %d %s", code, raw)
	}
}

func TestFIX131_ARunCutAtTheDrainDeadlineEndsInterruptedOnTheWireAndTheRow(t *testing.T) {
	hold := make(chan struct{})
	var once sync.Once
	t.Cleanup(func() { once.Do(func() { close(hold) }) })
	gw := newFakeGateway(t, &gwScript{chunks: []string{contentChunk("PART "), contentChunk("REST")}, hold: hold, holdAfter: 0})
	st := newFix131Stack(t, gw.URL+"/v1")

	res := sendAsync(t, st, `{"content":"FIX131 long answer","stream":true}`, "")
	waitStreaming(t, st)
	time.Sleep(200 * time.Millisecond) // "PART " has gone out

	st.svc.BeginDrain()
	rep := st.svc.Drain(300*time.Millisecond, 5*time.Second)
	if rep.Interrupted != 1 || rep.StillInFlight != 0 {
		t.Fatalf("drain report: %+v", rep)
	}
	var r sendResult
	select {
	case r = <-res:
	case <-time.After(10 * time.Second):
		t.Fatal("the cut run's response never ended")
	}
	frames := parseFrames(r.body)
	if len(frames) == 0 {
		t.Fatalf("no frames: %q", r.body)
	}
	last := frames[len(frames)-1]
	var term struct {
		Code      string `json:"code"`
		Cause     string `json:"cause"`
		Retryable bool   `json:"retryable"`
		FinalSeq  int64  `json:"final_seq"`
	}
	_ = json.Unmarshal([]byte(last.Data), &term)
	if last.Event != "error" || term.Code != "GENERATION_INTERRUPTED" || term.Cause != "interrupted" || !term.Retryable || term.FinalSeq == 0 {
		t.Fatalf("last frame = %s %s, want error GENERATION_INTERRUPTED cause interrupted retryable with final_seq", last.Event, last.Data)
	}
	if got := concatDeltas(frames); got != "PART " {
		t.Fatalf("streamed text = %q", got)
	}
	var content, status, cause, code string
	if err := st.pool.QueryRow(context.Background(), `
		SELECT content_masked, status, coalesce(terminal_cause,''), coalesce(error_code,'')
		FROM chat.messages_masked WHERE conversation_id = $1 AND role = 'assistant'`, st.convID).Scan(&content, &status, &cause, &code); err != nil {
		t.Fatal(err)
	}
	if content != "PART " || status != "error" || cause != "interrupted" || code != "GENERATION_INTERRUPTED" {
		t.Fatalf("row = %q %s %s %s, want the partial text kept and error/interrupted/GENERATION_INTERRUPTED", content, status, cause, code)
	}
	if n := st.svc.RunLeases().Count(); n != 0 {
		t.Fatalf("%d lease(s) left held after the drain", n)
	}
}

func TestFIX131_ARunThatFinishesInsideTheDrainEndsNormally(t *testing.T) {
	hold := make(chan struct{})
	gw := newFakeGateway(t, &gwScript{chunks: []string{contentChunk("ONE "), contentChunk("TWO")}, hold: hold, holdAfter: 0})
	st := newFix131Stack(t, gw.URL+"/v1")
	res := sendAsync(t, st, `{"content":"FIX131 short answer","stream":true}`, "")
	waitStreaming(t, st)

	st.svc.BeginDrain()
	time.AfterFunc(300*time.Millisecond, func() { close(hold) })
	rep := st.svc.Drain(10*time.Second, 2*time.Second)
	if rep.InFlightAtStart != 1 || rep.Interrupted != 0 {
		t.Fatalf("drain report: %+v", rep)
	}
	r := <-res
	frames := parseFrames(r.body)
	if last := frames[len(frames)-1]; last.Event != "message_complete" {
		t.Fatalf("last frame = %s %s", last.Event, last.Data)
	}
	if got := concatDeltas(frames); got != "ONE TWO" {
		t.Fatalf("text = %q", got)
	}
	settle(t, st, "completed")
}
