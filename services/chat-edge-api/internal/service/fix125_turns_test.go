package service

// FIX125 — service-level pins, each on a private database (testdb.Fresh)
// against a scripted gateway.
//
//   M2  a turn whose conversation was deleted while it streamed writes
//       nothing into it: no content, no terminal, no finalize (message_count
//       stays put, so no title / state / preference work either); the
//       content UPDATE itself refuses a deleted conversation.
//   M4  the question of a failed (blank error / cancelled) turn is not sent
//       again with the next question; a failed turn that streamed text keeps
//       its question; Regenerate of the failed row still asks it.
//   M5  one turn per conversation: a second send / regenerate / edit while
//       one runs is ErrConversationBusy and writes nothing; the claim is
//       released by Finish, by a pre-stream error and after a cancel; a
//       second copy of the SAME idempotent request is FIX121's duplicate,
//       not "busy".

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// fix125Gateway answers every chat completion with one content chunk and a
// stop. With hold set, it waits after the first chunk until hold is closed
// or the request is cancelled. With fail set, it answers 500.
type fix125Gateway struct {
	mu        sync.Mutex
	bodies    []map[string]any
	hold      chan struct{}
	fail      bool
	cancelled chan struct{} // closed when a held request's context ends
	server    *httptest.Server
}

func newFix125Gateway(t *testing.T) *fix125Gateway {
	g := &fix125Gateway{cancelled: make(chan struct{})}
	var once sync.Once
	g.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		g.mu.Lock()
		g.bodies = append(g.bodies, body)
		hold, fail := g.hold, g.fail
		g.mu.Unlock()
		if fail {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"error":{"message":"boom"}}`))
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		fl, _ := w.(http.Flusher)
		_, _ = fmt.Fprintf(w, "data: %s\n\n", `{"choices":[{"delta":{"content":"ANSWER"},"finish_reason":null}]}`)
		if fl != nil {
			fl.Flush()
		}
		if hold != nil {
			select {
			case <-hold:
			case <-r.Context().Done():
				once.Do(func() { close(g.cancelled) })
				return
			case <-time.After(20 * time.Second):
				return
			}
		}
		_, _ = fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", `{"choices":[{"delta":{},"finish_reason":"stop"}]}`)
	}))
	t.Cleanup(g.server.Close)
	return g
}

func (g *fix125Gateway) setHold(h chan struct{}) { g.mu.Lock(); g.hold = h; g.mu.Unlock() }
func (g *fix125Gateway) setFail(f bool)          { g.mu.Lock(); g.fail = f; g.mu.Unlock() }
func (g *fix125Gateway) body(i int) map[string]any {
	g.mu.Lock()
	defer g.mu.Unlock()
	if i < 0 {
		i = len(g.bodies) + i
	}
	return g.bodies[i]
}
func (g *fix125Gateway) calls() int { g.mu.Lock(); defer g.mu.Unlock(); return len(g.bodies) }

type fix125Fixture struct {
	s      *MessageService
	cr     *repository.ConversationRepo
	mr     *repository.MessageRepo
	gw     *fix125Gateway
	tenant uuid.UUID
	user   uuid.UUID
	conv   *model.Conversation
	q      func(sql string, args ...any) []any
}

func newFix125Fixture(t *testing.T) *fix125Fixture {
	t.Helper()
	pool := testdb.Fresh(t)
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	gw := newFix125Gateway(t)
	s := NewMessageService(cr, mr, NewGatewayClient(gw.server.URL, "test-key", "", time.Minute, ""), nil, allowAllModels{}, 50)
	tenant, user := uuid.New(), uuid.New()
	conv, err := cr.Create(context.Background(), &model.Conversation{TenantID: tenant, UserID: user, ModelID: "m", Title: "FIX125"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	f := &fix125Fixture{s: s, cr: cr, mr: mr, gw: gw, tenant: tenant, user: user, conv: conv}
	f.q = func(sql string, args ...any) []any {
		t.Helper()
		row := pool.QueryRow(context.Background(), sql, args...)
		var a, b, c any
		if err := row.Scan(&a, &b, &c); err != nil {
			t.Fatalf("query %q: %v", sql, err)
		}
		return []any{a, b, c}
	}
	return f
}

func (f *fix125Fixture) in(content string) SendInput {
	return SendInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, Content: content, RequestID: uuid.NewString()}
}

func (f *fix125Fixture) addRow(t *testing.T, role, content, status string) *model.Message {
	t.Helper()
	m, err := f.mr.Create(context.Background(), &model.Message{
		ConversationID: f.conv.ID, TenantID: f.tenant, UserID: f.user,
		Role: role, ContentMasked: content, Status: status, IsActive: true,
	})
	if err != nil {
		t.Fatalf("seed %s row: %v", role, err)
	}
	time.Sleep(5 * time.Millisecond) // distinct created_at, stable order
	return m
}

func (f *fix125Fixture) countRows(t *testing.T, role string) int {
	t.Helper()
	r := f.q(`SELECT count(*), 0, 0 FROM chat.messages_masked WHERE conversation_id = $1 AND role = $2`, f.conv.ID, role)
	return int(r[0].(int64))
}

// userContents lists the user-role message texts of one captured request.
func userContents(body map[string]any) []string {
	var out []string
	msgs, _ := body["messages"].([]any)
	for _, raw := range msgs {
		m, _ := raw.(map[string]any)
		if m["role"] != "user" {
			continue
		}
		s, _ := m["content"].(string)
		out = append(out, s)
	}
	return out
}

// ---------------------------------------------------------------------------
// M2 — deleted while streaming
// ---------------------------------------------------------------------------

func TestFIX125_FinishAfterDeleteWritesNothing(t *testing.T) {
	f := newFix125Fixture(t)
	hold := make(chan struct{})
	f.gw.setHold(hold)
	ctx := context.Background()

	h, err := f.s.SendStream(ctx, f.in("อธิบายเรื่องนี้ให้หน่อย"))
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	var before int
	if r := f.q(`SELECT message_count, 0, 0 FROM chat.conversations WHERE id = $1`, f.conv.ID); r != nil {
		before = int(r[0].(int32))
	}
	if err := f.cr.SoftDelete(ctx, f.tenant, f.conv.ID); err != nil {
		t.Fatalf("soft delete: %v", err)
	}
	close(hold) // the stream completes normally AFTER the delete
	content, _ := drainHandle(h)
	if content != "ANSWER" {
		t.Fatalf("drained %q", content)
	}

	r := f.q(`SELECT content_masked, status, coalesce(terminal_cause, '') FROM chat.messages_masked WHERE id = $1`, h.AssistantID)
	if r[0].(string) != "" || r[1].(string) != "streaming" || r[2].(string) != "" {
		t.Fatalf("the answer was written into the deleted conversation: content=%q status=%q terminal=%q", r[0], r[1], r[2])
	}
	after := int(f.q(`SELECT message_count, 0, 0 FROM chat.conversations WHERE id = $1`, f.conv.ID)[0].(int32))
	if after != before {
		t.Fatalf("finalizeConversation ran on a deleted conversation: message_count %d -> %d", before, after)
	}
	if n := f.gw.calls(); n != 1 {
		t.Fatalf("the gateway was called %d times; nothing after the turn may call it", n)
	}
}

func TestFIX125_UpdateContentRefusesDeletedConversation(t *testing.T) {
	f := newFix125Fixture(t)
	ctx := context.Background()
	f.addRow(t, "user", "q", "completed")
	asst := f.addRow(t, "assistant", "", "streaming")
	if err := f.cr.SoftDelete(ctx, f.tenant, f.conv.ID); err != nil {
		t.Fatal(err)
	}
	if err := f.mr.UpdateContent(ctx, asst.ID, "late answer", "completed", nil, nil); err != nil {
		t.Fatalf("a refused write is not an error: %v", err)
	}
	r := f.q(`SELECT content_masked, status, 0 FROM chat.messages_masked WHERE id = $1`, asst.ID)
	if r[0].(string) != "" || r[1].(string) != "streaming" {
		t.Fatalf("UpdateContent wrote into a deleted conversation: %q / %q", r[0], r[1])
	}
}

// ---------------------------------------------------------------------------
// M4 — a failed turn's question is not re-asked
// ---------------------------------------------------------------------------

func TestFIX125_FailedBlankTurnQuestionNotResent(t *testing.T) {
	f := newFix125Fixture(t)
	f.addRow(t, "user", "Q0 completed", "completed")
	f.addRow(t, "assistant", "A0", "completed")
	f.addRow(t, "user", "Q1 failed", "completed")
	f.addRow(t, "assistant", "", "error")
	h, err := f.s.SendStream(context.Background(), f.in("Q2 new"))
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	drainHandle(h)
	got := userContents(f.gw.body(-1))
	if strings.Join(got, "|") != "Q0 completed|Q2 new" {
		t.Fatalf("user turns sent = %q; the failed question must not ride along with the new one", got)
	}
}

func TestFIX125_CancelledTurnWithTextKeepsQuestion(t *testing.T) {
	f := newFix125Fixture(t)
	f.addRow(t, "user", "Q1", "completed")
	f.addRow(t, "assistant", "partial answer the person saw", "cancelled")
	h, err := f.s.SendStream(context.Background(), f.in("Q2"))
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	drainHandle(h)
	if got := userContents(f.gw.body(-1)); strings.Join(got, "|") != "Q1|Q2" {
		t.Fatalf("user turns sent = %q; a turn that produced text keeps its question (FIX100)", got)
	}
}

func TestFIX125_RegenerateFailedRowStillAsksItsQuestion(t *testing.T) {
	f := newFix125Fixture(t)
	f.addRow(t, "user", "Q0", "completed")
	f.addRow(t, "assistant", "A0", "completed")
	f.addRow(t, "user", "Q1 failed", "completed")
	failed := f.addRow(t, "assistant", "", "error")
	h, err := f.s.Regenerate(context.Background(), RegenerateInput{
		TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, MessageID: failed.ID, RequestID: uuid.NewString(),
	})
	if err != nil {
		t.Fatalf("regenerate: %v", err)
	}
	drainHandle(h)
	got := userContents(f.gw.body(-1))
	if len(got) == 0 || got[len(got)-1] != "Q1 failed" {
		t.Fatalf("regenerate must ask the failed question again as the last user turn; sent %q", got)
	}
}

// ---------------------------------------------------------------------------
// M5 — one turn per conversation
// ---------------------------------------------------------------------------

func TestFIX125_SecondSendWhileStreamingIsBusy(t *testing.T) {
	f := newFix125Fixture(t)
	hold := make(chan struct{})
	f.gw.setHold(hold)
	ctx := context.Background()

	first, err := f.s.SendStream(ctx, f.in("first"))
	if err != nil {
		t.Fatalf("first send: %v", err)
	}
	if _, err := f.s.SendStream(ctx, f.in("second")); !errors.Is(err, ErrConversationBusy) {
		t.Fatalf("second send while the first streams: err=%v, want ErrConversationBusy", err)
	}
	if _, err := f.s.SendNonStream(ctx, f.in("second, non-stream")); !errors.Is(err, ErrConversationBusy) {
		t.Fatalf("non-stream send while the first streams: err=%v, want ErrConversationBusy", err)
	}
	if u := f.countRows(t, "user"); u != 1 {
		t.Fatalf("a refused send wrote a user row: %d user rows", u)
	}
	if n := f.gw.calls(); n != 1 {
		t.Fatalf("a refused send reached the gateway: %d calls", n)
	}

	close(hold)
	drainHandle(first)
	if f.s.TurnInFlight(f.conv.ID) {
		t.Fatal("Finish must release the conversation")
	}
	f.gw.setHold(nil)
	next, err := f.s.SendStream(ctx, f.in("after"))
	if err != nil {
		t.Fatalf("a send after the first turn finished: %v", err)
	}
	drainHandle(next)
	if u := f.countRows(t, "user"); u != 2 {
		t.Fatalf("user rows = %d, want 2", u)
	}
}

func TestFIX125_RegenerateAndEditWhileStreamingAreBusy(t *testing.T) {
	f := newFix125Fixture(t)
	u0 := f.addRow(t, "user", "Q0", "completed")
	a0 := f.addRow(t, "assistant", "A0", "completed")
	hold := make(chan struct{})
	f.gw.setHold(hold)
	ctx := context.Background()
	first, err := f.s.SendStream(ctx, f.in("Q1"))
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	_, err = f.s.Regenerate(ctx, RegenerateInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, MessageID: a0.ID})
	if !errors.Is(err, ErrConversationBusy) {
		t.Fatalf("regenerate while streaming: err=%v, want ErrConversationBusy", err)
	}
	if st := f.q(`SELECT status, 0, 0 FROM chat.messages_masked WHERE id = $1`, a0.ID)[0].(string); st != "completed" {
		t.Fatalf("a refused regenerate changed the row: status=%q", st)
	}
	_, err = f.s.EditMessage(ctx, EditInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, MessageID: u0.ID, NewContent: "Q0 edited"})
	if !errors.Is(err, ErrConversationBusy) {
		t.Fatalf("edit while streaming: err=%v, want ErrConversationBusy", err)
	}
	if u := f.countRows(t, "user"); u != 2 {
		t.Fatalf("a refused edit wrote a user row: %d", u)
	}
	close(hold)
	drainHandle(first)
	h, err := f.s.Regenerate(ctx, RegenerateInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, MessageID: a0.ID})
	if err != nil {
		t.Fatalf("regenerate after the turn ended: %v", err)
	}
	drainHandle(h)
	if f.s.TurnInFlight(f.conv.ID) {
		t.Fatal("regenerate's Finish must release the conversation")
	}
}

func TestFIX125_ClaimReleasedAfterPreStreamError(t *testing.T) {
	f := newFix125Fixture(t)
	f.gw.setFail(true)
	if _, err := f.s.SendStream(context.Background(), f.in("will fail")); err == nil {
		t.Fatal("expected the gateway failure to surface")
	}
	if f.s.TurnInFlight(f.conv.ID) {
		t.Fatal("a send that failed before streaming must release the conversation")
	}
	f.gw.setFail(false)
	h, err := f.s.SendStream(context.Background(), f.in("works now"))
	if err != nil {
		t.Fatalf("send after a failed one: %v", err)
	}
	drainHandle(h)
}

func TestFIX125_ClaimReleasedAfterCancel(t *testing.T) {
	f := newFix125Fixture(t)
	f.gw.setHold(make(chan struct{})) // never released: only the cancel ends it
	h, err := f.s.SendStream(context.Background(), f.in("stop me"))
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	if !f.s.CancelStream(f.conv.ID) {
		t.Fatal("no stream to cancel")
	}
	drainHandle(h)
	if f.s.TurnInFlight(f.conv.ID) {
		t.Fatal("a cancelled turn must release the conversation")
	}
	f.gw.setHold(nil)
	h2, err := f.s.SendStream(context.Background(), f.in("again"))
	if err != nil {
		t.Fatalf("send after stop: %v", err)
	}
	drainHandle(h2)
}

func TestFIX125_SameIdempotencyKeyIsDuplicateNotBusy(t *testing.T) {
	f := newFix125Fixture(t)
	// A copy of request "k" holds the conversation but has not inserted its
	// user row yet (the window FIX121's unique index used to decide).
	release, err := f.s.claimTurn(f.conv.ID, "k")
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	in := f.in("same request")
	in.IdempotencyKey = "k"
	if _, err := f.s.SendStream(context.Background(), in); !errors.Is(err, ErrSendDuplicate) {
		t.Fatalf("second copy of the same request: err=%v, want ErrSendDuplicate (FIX121 replay path)", err)
	}
	in.IdempotencyKey = "other"
	if _, err := f.s.SendStream(context.Background(), in); !errors.Is(err, ErrConversationBusy) {
		t.Fatalf("a different request: err=%v, want ErrConversationBusy", err)
	}
	if u := f.countRows(t, "user"); u != 0 {
		t.Fatalf("refused sends wrote %d user rows", u)
	}
}

// Pure builder pins for M4 (no database).
func TestFIX125_BuilderDropsQuestionOfBlankFailedTurn(t *testing.T) {
	for _, st := range []string{"error", "cancelled"} {
		history := []model.Message{
			fix100Msg("user", "U0", "completed", 1),
			fix100Msg("assistant", "A0", "completed", 1),
			fix100Msg("user", "U1", "completed", 2),
			fix100Msg("assistant", "", st, 2),
			fix100Msg("user", "U2", "completed", 3),
		}
		req := buildGatewayRequestWithImages("m", history, "sys", nil, nil, nil, false)
		if got := strings.Join(fix100Roles(req), ","); got != "system,user,assistant,user" {
			t.Fatalf("%s: roles = %s", st, got)
		}
		if last := req.Messages[len(req.Messages)-1].Content; last != "U2" {
			t.Fatalf("%s: last user turn = %#v, want only U2 (not U1 merged in)", st, last)
		}
	}
}

func TestFIX125_BuilderKeepsCurrentQuestionAndBlankCompleted(t *testing.T) {
	// The final user turn is never dropped, whatever follows it.
	history := []model.Message{
		fix100Msg("user", "U1", "completed", 1),
		fix100Msg("assistant", "", "error", 1),
	}
	req := buildGatewayRequestWithImages("m", history, "", nil, nil, nil, false)
	if len(req.Messages) != 1 || req.Messages[0].Content != "U1" {
		t.Fatalf("the current question must stay: %#v", req.Messages)
	}
	// A blank row marked completed is not an abandoned question (FIX100
	// behaviour unchanged: the two user turns merge).
	history = []model.Message{
		fix100Msg("user", "U1", "completed", 1),
		fix100Msg("assistant", "", "completed", 1),
		fix100Msg("user", "U2", "completed", 2),
	}
	req = buildGatewayRequestWithImages("m", history, "", nil, nil, nil, false)
	if len(req.Messages) != 1 || req.Messages[0].Content != "U1\n\nU2" {
		t.Fatalf("blank completed row: %#v", req.Messages)
	}
}
