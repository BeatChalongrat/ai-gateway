package main

// FIX131 E2 — the SIGTERM path, as main() runs it (shutdownPlan.run), with
// a real MessageService over its own database (testdb.Fresh), a real HTTP
// server on a loopback port, a scripted gateway that never finishes its
// answer, and a fake signal.
//
// Before FIX131 the same signal closed the listener at once (a send during
// the stop was a connection error), Shutdown waited 30 s on a handler that
// Docker killed at 10 s, and the turn's row stayed 'streaming'.

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/handler"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

type allowModels struct{}

func (allowModels) ModelAllowed(string) bool { return true }
func (allowModels) ModelIDs() []string       { return []string{"m"} }

// heldGateway streams one chunk and then waits until released (or the
// request is cancelled): a model still writing its answer.
func heldGateway(t *testing.T, release <-chan struct{}) *httptest.Server {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fl, _ := w.(http.Flusher)
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"ครึ่งแรก \"},\"finish_reason\":null}]}\n\n")
		fl.Flush()
		select {
		case <-release:
		case <-r.Context().Done():
			return
		case <-time.After(30 * time.Second):
			return
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"จบ\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n")
		fl.Flush()
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestFIX131_SIGTERMDrainsRefusesNewTurnsAndEndsTheRestInterrupted(t *testing.T) {
	pool := testdb.Fresh(t)
	release := make(chan struct{})
	var once sync.Once
	t.Cleanup(func() { once.Do(func() { close(release) }) })
	gw := heldGateway(t, release)

	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	svc := service.NewMessageService(cr, mr, service.NewGatewayClient(gw.URL, "k", "", time.Minute, ""), nil, allowModels{}, 10)
	leases := service.NewRunLeases(service.NewMemoryLeaseStore(), "sigterm-test", 45*time.Second, 15*time.Second)
	svc.SetRunLeases(leases)
	h := handler.NewMessageHandler(svc)

	tenant, user := uuid.New(), uuid.New()
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX131 SIGTERM"})
	if err != nil {
		t.Fatal(err)
	}
	other, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX131 other"})
	if err != nil {
		t.Fatal(err)
	}
	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			next.ServeHTTP(w, req.WithContext(middleware.WithCaller(req.Context(), middleware.Caller{UserID: user, TenantUUID: tenant, TenantID: tenant.String()})))
		})
	})
	r.Post("/conversations/{id}/messages", h.Send)
	r.Get("/conversations/{id}/messages", h.List)
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: r, ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = srv.Serve(ln) }()
	base := "http://" + ln.Addr().String()

	post := func(convID uuid.UUID, text string) (int, http.Header, string, error) {
		resp, err := http.Post(fmt.Sprintf("%s/conversations/%s/messages", base, convID), "application/json",
			strings.NewReader(fmt.Sprintf(`{"content":%q,"stream":true}`, text)))
		if err != nil {
			return 0, nil, "", err
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, resp.Header, string(raw), nil
	}

	// A turn in flight: the model is still writing.
	type result struct {
		code int
		body string
	}
	first := make(chan result, 1)
	go func() {
		code, _, body, err := post(conv.ID, "FIX131 long answer")
		if err != nil {
			body = err.Error()
		}
		first <- result{code, body}
	}()
	streaming := func() int {
		var n int
		_ = pool.QueryRow(context.Background(), `SELECT count(*) FROM chat.messages_masked WHERE conversation_id = $1 AND role = 'assistant' AND status = 'streaming'`, conv.ID).Scan(&n)
		return n
	}
	deadline := time.Now().Add(10 * time.Second)
	for streaming() == 0 && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if streaming() != 1 {
		t.Fatal("the first turn never started")
	}
	time.Sleep(200 * time.Millisecond) // its first chunk is out

	stop := make(chan os.Signal, 2)
	exited := make(chan int, 1)
	bgCtx, bgCancel := context.WithCancel(context.Background())
	var bgWG sync.WaitGroup
	bgWG.Add(1)
	go func() { defer bgWG.Done(); <-bgCtx.Done() }() // a background loop that stops when told
	leaseCtx, leaseCancel := context.WithCancel(context.Background())
	go leases.Run(leaseCtx)
	plan := shutdownPlan{
		svc: svc, srv: srv, bgCancel: bgCancel, bgWG: &bgWG, leaseCancel: leaseCancel,
		drainTimeout: 1500 * time.Millisecond, drainGrace: 5 * time.Second, httpTimeout: 5 * time.Second,
		exit: func(code int) { exited <- code },
	}
	reportCh := make(chan service.DrainReport, 1)
	go func() { reportCh <- plan.run(stop) }()
	stop <- syscall.SIGTERM

	// During the drain: the listener is still open, a new turn is refused
	// with a clear 503 and nothing is written.
	time.Sleep(200 * time.Millisecond)
	code, hdr, body, err := post(other.ID, "FIX131 sent during the restart")
	if err != nil {
		t.Fatalf("a send during the drain could not reach the server: %v", err)
	}
	if code != http.StatusServiceUnavailable || !strings.Contains(body, handler.CodeServerDraining) || hdr.Get("Retry-After") == "" {
		t.Fatalf("send during the drain: %d Retry-After=%q %s", code, hdr.Get("Retry-After"), body)
	}
	var n int
	_ = pool.QueryRow(context.Background(), `SELECT count(*) FROM chat.messages_masked WHERE conversation_id = $1`, other.ID).Scan(&n)
	if n != 0 {
		t.Fatalf("a refused send wrote %d row(s)", n)
	}

	var rep service.DrainReport
	select {
	case rep = <-reportCh:
	case <-time.After(20 * time.Second):
		t.Fatal("the shutdown never finished")
	}
	if rep.InFlightAtStart != 1 || rep.Interrupted != 1 || rep.StillInFlight != 0 {
		t.Fatalf("drain report: %+v", rep)
	}
	select {
	case code := <-exited:
		t.Fatalf("one signal must not exit early (exit %d)", code)
	default:
	}

	f := <-first
	if f.code != http.StatusOK || !strings.Contains(f.body, "event: error") || !strings.Contains(f.body, `"code":"GENERATION_INTERRUPTED"`) || !strings.Contains(f.body, "ครึ่งแรก") {
		t.Fatalf("the turn in flight must end on error GENERATION_INTERRUPTED with its text: %d %s", f.code, f.body)
	}
	var content, status, cause string
	if err := pool.QueryRow(context.Background(), `
		SELECT content_masked, status, coalesce(terminal_cause,'') FROM chat.messages_masked
		WHERE conversation_id = $1 AND role = 'assistant'`, conv.ID).Scan(&content, &status, &cause); err != nil {
		t.Fatal(err)
	}
	if status != "error" || cause != "interrupted" || content != "ครึ่งแรก " {
		t.Fatalf("row after the restart: %q %s %s", content, status, cause)
	}
	if leases.Count() != 0 {
		t.Fatalf("%d lease(s) left held", leases.Count())
	}
	// The server is closed now.
	if _, _, _, err := post(other.ID, "after"); err == nil {
		t.Fatal("the server still answered after the shutdown")
	}
}
