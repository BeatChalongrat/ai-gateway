package service

// FIX126 O1 — background calls use the configured background model (with the
// conversation's model as fallback) and wait while the conversation has a
// turn in flight.

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/config"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestFIX126_BackgroundModelsResolution(t *testing.T) {
	cases := []struct{ summary, background, wantSum, wantExt string }{
		{"", DefaultBackgroundTaskModel, DefaultBackgroundTaskModel, DefaultBackgroundTaskModel},
		{"summary-model", DefaultBackgroundTaskModel, "summary-model", DefaultBackgroundTaskModel},
		{"", "off", "", ""},
		{"summary-model", "none", "summary-model", ""},
	}
	for _, c := range cases {
		s, e := BackgroundModels(c.summary, c.background)
		if s != c.wantSum || e != c.wantExt {
			t.Fatalf("BackgroundModels(%q,%q) = (%q,%q), want (%q,%q)", c.summary, c.background, s, e, c.wantSum, c.wantExt)
		}
	}
	// The service default and the config default are the same model.
	if f, ok := reflect.TypeOf(config.Config{}).FieldByName("BackgroundTaskModel"); !ok ||
		f.Tag.Get("envDefault") != DefaultBackgroundTaskModel {
		t.Fatalf("config envDefault %q != service default %q", f.Tag.Get("envDefault"), DefaultBackgroundTaskModel)
	}
	// The summariser tries the background model, then the chat model.
	st := &ConversationStateService{}
	sum, _ := BackgroundModels("", DefaultBackgroundTaskModel)
	st.SetExtractionModel(sum)
	if got := st.extractionModels("", "qwen3.8-27b-fp8"); len(got) != 2 || got[0] != DefaultBackgroundTaskModel || got[1] != "qwen3.8-27b-fp8" {
		t.Fatalf("summariser order = %v", got)
	}
}

// modelGateway answers non-stream completions; a model listed in refuse gets
// a 400, every other model "[]". It records the models asked for.
type modelGateway struct {
	mu     sync.Mutex
	models []string
	refuse map[string]bool
	srv    *httptest.Server
}

func newModelGateway(t *testing.T, refuse ...string) *modelGateway {
	g := &modelGateway{refuse: map[string]bool{}}
	for _, m := range refuse {
		g.refuse[m] = true
	}
	g.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body struct {
			Model string `json:"model"`
		}
		_ = json.Unmarshal(raw, &body)
		g.mu.Lock()
		g.models = append(g.models, body.Model)
		g.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if g.refuse[body.Model] {
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"error":{"code":"model_not_served","message":"no"}}`))
			return
		}
		_, _ = fmt.Fprint(w, `{"id":"x","choices":[{"index":0,"message":{"role":"assistant","content":"[]"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}`)
	}))
	t.Cleanup(g.srv.Close)
	return g
}

func (g *modelGateway) asked() []string {
	g.mu.Lock()
	defer g.mu.Unlock()
	return append([]string(nil), g.models...)
}

type fix126BgFixture struct {
	cr     *repository.ConversationRepo
	mr     *repository.MessageRepo
	states *repository.ConversationStateRepo
	mem    *repository.MemoryRepo
	conv   *model.Conversation
	tenant uuid.UUID
	user   uuid.UUID
}

func newFix126BgFixture(t *testing.T, rows int) *fix126BgFixture {
	t.Helper()
	pool := testdb.Fresh(t)
	ctx := context.Background()
	f := &fix126BgFixture{
		cr: repository.NewConversationRepo(pool), mr: repository.NewMessageRepo(pool),
		states: repository.NewConversationStateRepo(pool), mem: repository.NewMemoryRepo(pool),
		tenant: uuid.New(), user: uuid.New(),
	}
	conv, err := f.cr.Create(ctx, &model.Conversation{TenantID: f.tenant, UserID: f.user, ModelID: "qwen3.8-27b-fp8", Title: "FIX126", MemoryEnabled: true})
	if err != nil {
		t.Fatalf("conv: %v", err)
	}
	f.conv = conv
	for i := 0; i < rows; i++ {
		role := "user"
		if i%2 == 1 {
			role = "assistant"
		}
		if _, err := f.mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: f.tenant, UserID: f.user,
			Role: role, ContentMasked: fmt.Sprintf("ข้อความ %d", i), Status: "completed", IsActive: true}); err != nil {
			t.Fatalf("message: %v", err)
		}
		time.Sleep(2 * time.Millisecond)
	}
	return f
}

func TestFIX126_PreferenceExtractorUsesTheBackgroundModelThenFallsBack(t *testing.T) {
	f := newFix126BgFixture(t, memoryExtractFirstAt+2)
	ctx := context.Background()
	run := func(refuse ...string) []string {
		gw := newModelGateway(t, refuse...)
		e := NewPreferenceExtractor(NewMemoryService(f.mem), f.mem, f.mr, f.cr,
			NewGatewayClient(gw.srv.URL, "k", "", time.Minute, ""))
		_, bg := BackgroundModels("", DefaultBackgroundTaskModel)
		e.SetExtractionModel(bg)
		if _, err := e.ExtractPreferences(ctx, f.conv.ID, f.user, f.tenant); err != nil {
			t.Fatalf("extract: %v", err)
		}
		return gw.asked()
	}
	if got := run(); len(got) != 1 || got[0] != DefaultBackgroundTaskModel {
		t.Fatalf("the extractor asked %v, want only %s", got, DefaultBackgroundTaskModel)
	}
	if got := run(DefaultBackgroundTaskModel); len(got) != 2 || got[0] != DefaultBackgroundTaskModel || got[1] != "qwen3.8-27b-fp8" {
		t.Fatalf("background model unavailable: asked %v, want [%s qwen3.8-27b-fp8]", got, DefaultBackgroundTaskModel)
	}
}

func TestFIX126_BackgroundTaskWaitsForTheTurnInFlight(t *testing.T) {
	s := &MessageService{bgWaitPoll: 5 * time.Millisecond, bgWaitMax: 5 * time.Second}
	convID := uuid.New()
	release, err := s.claimTurn(convID, "")
	if err != nil {
		t.Fatalf("claim: %v", err)
	}
	ran := make(chan time.Time, 1)
	s.runWhenTurnIdle("fix126-test", convID, func(context.Context) { ran <- time.Now() })

	select {
	case <-ran:
		t.Fatal("the background task ran while the conversation had a turn in flight")
	case <-time.After(300 * time.Millisecond):
	}
	released := time.Now()
	release()
	select {
	case at := <-ran:
		if at.Before(released) {
			t.Fatal("ran before the turn was released")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the background task never ran after the turn ended")
	}

	// A conversation that stays busy past the wait: the task is skipped.
	busy := uuid.New()
	if _, err := s.claimTurn(busy, ""); err != nil {
		t.Fatalf("claim: %v", err)
	}
	s.bgWaitMax = 50 * time.Millisecond
	skipped := make(chan struct{}, 1)
	s.runWhenTurnIdle("fix126-test", busy, func(context.Context) { skipped <- struct{}{} })
	select {
	case <-skipped:
		t.Fatal("a task ran on a conversation that never became free")
	case <-time.After(300 * time.Millisecond):
	}
}

// The real trigger: state extraction kicked while the turn still holds the
// conversation reaches the gateway only after the turn is released, and asks
// the background model.
func TestFIX126_StateExtractionIsDeferredUntilTheTurnEnds(t *testing.T) {
	f := newFix126BgFixture(t, firstExtractionAtMessages)
	ctx := context.Background()
	gw := newModelGateway(t)
	s := NewMessageService(f.cr, f.mr, NewGatewayClient(gw.srv.URL, "k", "", time.Minute, ""), nil, allowAllModels{}, 50)
	s.bgWaitPoll = 5 * time.Millisecond
	st := NewConversationStateService(f.states, f.mr, f.cr, NewGatewayClient(gw.srv.URL, "k", "", time.Minute, ""))
	sum, _ := BackgroundModels("", DefaultBackgroundTaskModel)
	st.SetExtractionModel(sum)
	s.SetConversationStateService(st)

	release, err := s.claimTurn(f.conv.ID, "")
	if err != nil {
		t.Fatalf("claim: %v", err)
	}
	s.maybeUpdateConversationState(ctx, f.conv)
	time.Sleep(300 * time.Millisecond)
	if n := len(gw.asked()); n != 0 {
		t.Fatalf("state extraction called the gateway %d times while the turn was in flight", n)
	}
	release()
	if !waitUntil(3*time.Second, func() bool { return len(gw.asked()) > 0 }) {
		t.Fatal("state extraction never ran after the turn ended")
	}
	if got := gw.asked()[0]; got != DefaultBackgroundTaskModel {
		t.Fatalf("state extraction asked %q, want the background model", got)
	}
	// Let the extraction goroutine finish before the database is dropped.
	time.Sleep(200 * time.Millisecond)
}

func waitUntil(d time.Duration, cond func() bool) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return cond()
}
