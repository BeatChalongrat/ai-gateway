package service

// FIX130 A1 — things get learned automatically.
//
// NIGHT2: 20-message chats produced zero memories. The extractor capped its
// call at 800 output tokens while the background model spent ~1.8k thinking,
// the JSON parse returned nil, and the zero was never logged. These tests
// drive the real extractor + trigger against a fake gateway and a throwaway
// database (testdb).

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// extractGateway is a fake gateway for the extraction call: it records every
// request body and answers with reply(); hold, when non-nil, blocks each call
// until it is closed (to observe "in flight").
type extractGateway struct {
	mu     sync.Mutex
	bodies []map[string]any
	reply  func() string
	hold   chan struct{}
	srv    *httptest.Server
}

func newExtractGateway(t *testing.T, reply func() string) *extractGateway {
	g := &extractGateway{reply: reply}
	g.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		g.mu.Lock()
		g.bodies = append(g.bodies, body)
		hold := g.hold
		g.mu.Unlock()
		if hold != nil {
			<-hold
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, g.reply())
	}))
	t.Cleanup(g.srv.Close)
	return g
}

func (g *extractGateway) calls() []map[string]any {
	g.mu.Lock()
	defer g.mu.Unlock()
	return append([]map[string]any(nil), g.bodies...)
}

// completion renders an OpenAI-shaped non-stream answer.
func completion(content, finish string, completionTokens, reasoningTokens int) string {
	b, _ := json.Marshal(map[string]any{
		"id":      "x",
		"model":   DefaultBackgroundTaskModel,
		"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": content}, "finish_reason": finish}},
		"usage":   map[string]any{"prompt_tokens": 900, "completion_tokens": completionTokens, "reasoning_tokens": reasoningTokens},
	})
	return string(b)
}

type fix130Fixture struct {
	pool   *pgxpool.Pool
	cr     *repository.ConversationRepo
	mr     *repository.MessageRepo
	mem    *repository.MemoryRepo
	memSvc *MemoryService
	tenant uuid.UUID
	user   uuid.UUID
}

func newFix130Fixture(t *testing.T) *fix130Fixture {
	t.Helper()
	pool := testdb.Fresh(t)
	mem := repository.NewMemoryRepo(pool)
	return &fix130Fixture{
		pool: pool,
		cr:   repository.NewConversationRepo(pool), mr: repository.NewMessageRepo(pool),
		mem: mem, memSvc: NewMemoryService(mem),
		tenant: uuid.New(), user: uuid.New(),
	}
}

// chat creates a conversation holding the given turns (alternating user /
// assistant, user first). memoryOn / temporary shape the switches.
func (f *fix130Fixture) chat(t *testing.T, temporary, memoryOn bool, turns ...string) *model.Conversation {
	t.Helper()
	ctx := context.Background()
	conv, err := f.cr.Create(ctx, &model.Conversation{TenantID: f.tenant, UserID: f.user, ModelID: "qwen3.8-27b-fp8", IsTemporary: temporary})
	if err != nil {
		t.Fatalf("conv: %v", err)
	}
	if conv.MemoryEnabled != memoryOn {
		if err := f.cr.UpdateMemoryEnabled(ctx, f.tenant, conv.ID, memoryOn); err != nil {
			t.Fatalf("memory switch: %v", err)
		}
		conv.MemoryEnabled = memoryOn
	}
	f.add(t, conv, turns...)
	return conv
}

func (f *fix130Fixture) add(t *testing.T, conv *model.Conversation, turns ...string) {
	t.Helper()
	for i, text := range turns {
		role := "user"
		if i%2 == 1 {
			role = "assistant"
		}
		if _, err := f.mr.Create(context.Background(), &model.Message{ConversationID: conv.ID, TenantID: f.tenant, UserID: f.user,
			Role: role, ContentMasked: text, Status: "completed", IsActive: true}); err != nil {
			t.Fatalf("message: %v", err)
		}
		time.Sleep(2 * time.Millisecond)
	}
}

func (f *fix130Fixture) extractor(gw *extractGateway) *PreferenceExtractor {
	e := NewPreferenceExtractor(f.memSvc, f.mem, f.mr, f.cr, NewGatewayClient(gw.srv.URL, "k", "", time.Minute, ""))
	e.SetExtractionModel(DefaultBackgroundTaskModel)
	return e
}

func (f *fix130Fixture) rows(t *testing.T, status string) []model.Memory {
	t.Helper()
	rows, err := f.mem.ListByUser(context.Background(), f.user, f.tenant, repository.MemoryListFilters{Status: status, Limit: 100})
	if err != nil {
		t.Fatalf("list %s: %v", status, err)
	}
	return rows
}

// eightTurns is long enough for the pre-FIX130 extractor to call the model
// at all (its floor was 8 messages), so the tests below measure the call,
// not the floor.
var eightTurns = []string{
	"ช่วยสรุปรายงานนี้ให้หน่อย ตอบสั้นๆ นะ", "สรุป: …",
	"ขอสั้นกว่านี้อีก", "สั้นลง: …",
	"โอเค ต่อไปช่วยเขียนอีเมลถึงลูกค้า สั้นๆ", "อีเมล: …",
	"ขอบคุณ ตอบสั้นแบบนี้ดีแล้ว", "ยินดีครับ",
}

const preferenceArray = `[{"type":"preference","content":"ชอบคำตอบสั้นๆ","category":"response_style","confidence":0.9,"evidence":"ตอบสั้นๆ นะ"}]`

func TestFIX130_ExtractionTurnsThinkingOffAndLeavesRoomToAnswer(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true, eightTurns...)
	gw := newExtractGateway(t, func() string { return completion(preferenceArray, "stop", 120, 0) })
	if _, err := f.extractor(gw).ExtractPreferences(context.Background(), conv.ID, f.user, f.tenant); err != nil {
		t.Fatalf("extract: %v", err)
	}
	calls := gw.calls()
	if len(calls) == 0 {
		t.Fatal("the extractor never called the model")
	}
	body := calls[0]
	r, _ := body["reasoning"].(map[string]any)
	if r == nil || r["mode"] != "off" {
		t.Errorf("the extraction call must ask for no thinking; reasoning=%v", body["reasoning"])
	}
	// deepseek-v4-flash reasons although its catalogue row says it does not,
	// and "off" sends nothing to a strategy-none row — the ceiling is what
	// guarantees room for the answer after the thinking.
	if mt, _ := body["max_tokens"].(float64); mt < 4096 {
		t.Errorf("max_tokens = %v; a model that thinks anyway (~1.8k tokens measured) must still have room to answer", body["max_tokens"])
	}
}

func TestFIX130_ExtractionReadsAnArrayWrappedInProse(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true, eightTurns...)
	wrapped := "Sure! Here is what I found:\n```json\n" + preferenceArray + "\n```\nHope this helps."
	gw := newExtractGateway(t, func() string { return completion(wrapped, "stop", 150, 0) })
	n, err := f.extractor(gw).ExtractPreferences(context.Background(), conv.ID, f.user, f.tenant)
	if err != nil {
		t.Fatalf("extract: %v", err)
	}
	if n != 1 || len(f.rows(t, model.MemoryStatusPendingConfirmation)) != 1 {
		t.Fatalf("an array inside prose and a fence must still be read: created=%d", n)
	}
}

func TestFIX130_FactFromATwoTurnChatBecomesAPendingThaiSuggestion(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true,
		"สวัสดีครับ ผมชื่อเล่นว่าภูผา ทำงานเป็นวิศวกรโยธาอยู่ที่เชียงใหม่", "ยินดีที่ได้รู้จักครับคุณภูผา",
		"ช่วยแนะนำร้านกาแฟแถวนิมมานหน่อย", "ร้านแนะนำ: …",
	)
	fact := `[{"type":"fact","content":"ชื่อเล่นว่าภูผา เป็นวิศวกรโยธา อยู่เชียงใหม่","category":"identity","confidence":0.92,"evidence":"ผมชื่อเล่นว่าภูผา ทำงานเป็นวิศวกรโยธาอยู่ที่เชียงใหม่"}]`
	gw := newExtractGateway(t, func() string { return completion(fact, "stop", 200, 0) })
	n, err := f.extractor(gw).ExtractPreferences(context.Background(), conv.ID, f.user, f.tenant)
	if err != nil {
		t.Fatalf("extract: %v", err)
	}
	if n != 1 {
		t.Fatalf("a fact stated in a two-turn chat must become a suggestion; created=%d calls=%d", n, len(gw.calls()))
	}
	// The prompt asks for facts about the user, in the user's language.
	prompt := fmt.Sprint(gw.calls()[0]["messages"])
	for _, want := range []string{"ผมชื่อเล่นว่าภูผา", "same language"} {
		if !strings.Contains(prompt, want) {
			t.Errorf("extraction prompt lacks %q", want)
		}
	}
	rows := f.rows(t, model.MemoryStatusPendingConfirmation)
	if len(rows) != 1 {
		t.Fatalf("pending rows = %d", len(rows))
	}
	m := rows[0]
	if m.MemoryType != model.MemoryTypeExplicitFact || m.SourceType != model.MemorySourceAssistantInferred ||
		m.SourceConversationID == nil || *m.SourceConversationID != conv.ID || !strings.Contains(m.EvidenceExcerpt, "ภูผา") {
		t.Fatalf("suggestion shape: type=%s source=%s conv=%v evidence=%q", m.MemoryType, m.SourceType, m.SourceConversationID, m.EvidenceExcerpt)
	}
	// Accepted → injected as a known fact in the next turn.
	if _, err := f.memSvc.SetConfirmed(context.Background(), m.ID, f.user, f.tenant, true); err != nil {
		t.Fatalf("confirm: %v", err)
	}
	block, srcs := f.memSvc.BuildMemoryContextWithSources(context.Background(), f.user, f.tenant, nil)
	if !strings.Contains(block, "<known_facts>") || !strings.Contains(block, "ภูผา") || len(srcs) != 1 {
		t.Fatalf("an accepted fact must be injected under known_facts:\n%s", block)
	}
}

// lockedWriter is a goroutine-safe log sink.
type lockedWriter struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (w *lockedWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.buf.Write(p)
}

func (w *lockedWriter) String() string {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.buf.String()
}

func TestFIX130_EveryExtractionRunIsLogged(t *testing.T) {
	sink := &lockedWriter{}
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(sink, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(prev) })

	f := newFix130Fixture(t)
	conv := f.chat(t, false, true, eightTurns...)
	// The NIGHT2 shape: the whole budget went to thinking, no answer.
	gw := newExtractGateway(t, func() string { return completion("", "length", 4096, 4096) })
	if _, err := f.extractor(gw).ExtractPreferences(context.Background(), conv.ID, f.user, f.tenant); err != nil {
		t.Fatalf("extract: %v", err)
	}
	var line map[string]any
	for _, l := range strings.Split(sink.String(), "\n") {
		if strings.Contains(l, `"msg":"memory_extraction"`) && strings.Contains(l, conv.ID.String()) {
			_ = json.Unmarshal([]byte(l), &line)
		}
	}
	if line == nil {
		t.Fatalf("no memory_extraction log line for the run; log:\n%s", sink.String())
	}
	if line["outcome"] != "budget_exhausted" || line["finish_reason"] != "length" {
		t.Errorf("outcome=%v finish_reason=%v, want budget_exhausted/length", line["outcome"], line["finish_reason"])
	}
	for _, k := range []string{"model", "suggestions", "created", "elapsed_ms", "completion_tokens", "reasoning_tokens"} {
		if _, ok := line[k]; !ok {
			t.Errorf("log line lacks %q: %v", k, line)
		}
	}
	// Counts only — never the user's words.
	if strings.Contains(sink.String(), "ตอบสั้นๆ") {
		t.Error("the extraction log carries user text")
	}
}

// The trigger: a two-turn chat is enough, one run per new stretch of
// messages, one run in flight per conversation.
func TestFIX130_ExtractionTriggerRunsAfterTwoTurnsOnceAndOneAtATime(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true, "ผมชื่อบีท ทำงานที่ GB Tech", "สวัสดีครับคุณบีท", "ช่วยเขียนอีเมลสั้นๆ", "อีเมล: …")
	gw := newExtractGateway(t, func() string { return completion("[]", "stop", 5, 0) })
	gw.hold = make(chan struct{})
	s := NewMessageService(f.cr, f.mr, nil, nil, allowAllModels{}, 50)
	s.bgWaitPoll, s.bgWaitMax = 5*time.Millisecond, 5*time.Second
	s.SetPreferenceExtractor(f.extractor(gw))

	ctx := context.Background()
	s.maybeExtractPreferences(ctx, conv)
	s.maybeExtractPreferences(ctx, conv) // the same turn finishing twice / a second replica
	waitFor(t, func() bool { return len(gw.calls()) >= 1 }, "the extraction call after two user turns")
	time.Sleep(150 * time.Millisecond)
	if n := len(gw.calls()); n != 1 {
		t.Fatalf("%d extraction calls in flight for one conversation, want 1", n)
	}
	close(gw.hold)
	time.Sleep(150 * time.Millisecond)

	// Nothing new since that run → no second run over the same messages.
	s.maybeExtractPreferences(ctx, conv)
	time.Sleep(200 * time.Millisecond)
	if n := len(gw.calls()); n != 1 {
		t.Fatalf("re-ran over the same messages: %d calls", n)
	}
	// Two more turns → one more run.
	f.add(t, conv, "อีกเรื่อง ผมแพ้กุ้งนะ", "รับทราบครับ", "แนะนำเมนูเย็นนี้หน่อย", "เมนู: …")
	s.maybeExtractPreferences(ctx, conv)
	waitFor(t, func() bool { return len(gw.calls()) == 2 }, "the run after two more turns")
}

func TestFIX130_TemporaryChatIsNeverExtracted(t *testing.T) {
	f := newFix130Fixture(t)
	// A temporary chat whose memory flag was switched on before the server
	// refused that: still sealed.
	turns := append(append([]string{}, eightTurns...), eightTurns...)
	turns = append(turns, eightTurns[:4]...)
	conv := f.chat(t, true, true, turns...)
	gw := newExtractGateway(t, func() string { return completion(preferenceArray, "stop", 50, 0) })
	s := NewMessageService(f.cr, f.mr, NewGatewayClient(gw.srv.URL, "k", "", time.Minute, ""), nil, allowAllModels{}, 50)
	s.bgWaitPoll, s.bgWaitMax = 5*time.Millisecond, 5*time.Second
	s.SetPreferenceExtractor(f.extractor(gw))
	s.maybeExtractPreferences(context.Background(), conv)
	time.Sleep(400 * time.Millisecond)
	if n := len(gw.calls()); n != 0 {
		t.Fatalf("a temporary chat reached the extractor (%d calls)", n)
	}
	if rows := f.rows(t, model.MemoryStatusPendingConfirmation); len(rows) != 0 {
		t.Fatalf("a temporary chat produced %d suggestions", len(rows))
	}
}

// The per-conversation state extractor is a writer too: a temporary chat
// keeps no summary, whatever its memory flag says.
func TestFIX130_TemporaryChatKeepsNoConversationState(t *testing.T) {
	f := newFix130Fixture(t)
	turns := append(append([]string{}, eightTurns...), eightTurns[:4]...)
	conv := f.chat(t, true, true, turns...)
	gw := newExtractGateway(t, func() string { return completion(`{"goal":"x"}`, "stop", 20, 0) })
	gc := NewGatewayClient(gw.srv.URL, "k", "", time.Minute, "")
	s := NewMessageService(f.cr, f.mr, gc, nil, allowAllModels{}, 50)
	s.bgWaitPoll, s.bgWaitMax = 5*time.Millisecond, 5*time.Second
	s.SetConversationStateService(NewConversationStateService(repository.NewConversationStateRepo(f.pool), f.mr, f.cr, gc))
	s.maybeUpdateConversationState(context.Background(), conv)
	time.Sleep(400 * time.Millisecond)
	if n := len(gw.calls()); n != 0 {
		t.Fatalf("a temporary chat ran the conversation-state extractor (%d calls)", n)
	}
}

func TestFIX130_RejectedSuggestionIsNotSuggestedAgain(t *testing.T) {
	f := newFix130Fixture(t)
	ctx := context.Background()
	conv := f.chat(t, false, true, "hi", "hello")
	suggest := func() (*model.Memory, error) {
		cid := conv.ID
		return f.memSvc.CreateMemory(ctx, CreateMemoryInput{
			TenantID: f.tenant, UserID: f.user, ScopeType: model.MemoryScopeGlobalUser,
			MemoryType: model.MemoryTypeInferredPreference, Content: "Prefers short answers",
			SourceType: model.MemorySourceAssistantInferred, SourceConversationID: &cid,
			StatusOverride: model.MemoryStatusPendingConfirmation, ActorType: "system",
		})
	}
	m, err := suggest()
	if err != nil {
		t.Fatalf("first suggestion: %v", err)
	}
	// The page's "ไม่ใช่" button.
	if err := f.memSvc.DeleteMemory(ctx, m.ID, f.user, f.tenant); err != nil {
		t.Fatalf("reject: %v", err)
	}
	if got := f.rows(t, model.MemoryStatusPendingConfirmation); len(got) != 0 {
		t.Fatalf("a rejected suggestion is still pending: %v", got)
	}
	if again, err := suggest(); err == nil {
		t.Fatalf("a suggestion the user rejected was created again (%s)", again.ID)
	}
}

func waitFor(t *testing.T, cond func() bool, what string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}
