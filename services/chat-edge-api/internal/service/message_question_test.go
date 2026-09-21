package service

// FIX74 — a web turn parks on a question and resumes when the user answers.
// Runs on a private database (testdb.Fresh) against a scripted gateway.
//
// Pins (each goes red if the behaviour is removed):
//   • a VALID ask_user_question ends the pump with a `question` update and a
//     `parked` terminal — no Done, no invented answer — and one pending row
//     holds the request as it stood (the assistant tool_calls message
//     included) and the text streamed so far;
//   • ResumeQuestion sends the answer as the tool result of the SAME call,
//     continues on the same assistant message, and the row ends completed
//     with prefix + continuation;
//   • two tabs: the second answer is ErrQuestionNotPending;
//   • expiry: an answer after the TTL is ErrQuestionExpired, the row is
//     settled cancelled, and the message list shows no card;
//   • a new message supersedes a pending question (cancelled, never
//     answered); Stop closes it the same way;
//   • the tool is offered ONLY to an attended streaming turn: not on the
//     non-stream path, not on a cloud scheduled run, not when the store is
//     unwired;
//   • the wait costs nothing: after the park there is no active stream
//     registration, and a turn parked on its last allowed round still gets
//     its answer round.

import (
	"context"
	"encoding/json"
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
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

const askArgs = `{"questions":[{"question":"Which format?","header":"Format","options":[{"label":"Summary","description":"Brief"},{"label":"Detailed","description":"Full"}]}]}`

// scriptedGateway answers request N with the N-th script: a tool call
// (ask) or plain content that echoes the last tool result it was sent.
type scriptedGateway struct {
	mu     sync.Mutex
	bodies []map[string]any
	server *httptest.Server
}

func newScriptedGateway(t *testing.T, script func(n int, body map[string]any) string) *scriptedGateway {
	g := &scriptedGateway{}
	g.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		g.mu.Lock()
		g.bodies = append(g.bodies, body)
		n := len(g.bodies)
		g.mu.Unlock()
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(script(n, body)))
	}))
	t.Cleanup(g.server.Close)
	return g
}

func sseAsk(callID string) string {
	return `data: {"choices":[{"delta":{"content":"Let me ask. "},"finish_reason":null}]}` + "\n\n" +
		`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"` + callID + `","type":"function","function":{"name":"ask_user_question","arguments":` + jsonString(askArgs) + `}}]},"finish_reason":null}]}` + "\n\n" +
		`data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}` + "\n\n" + "data: [DONE]\n\n"
}

func sseText(text string) string {
	return `data: {"choices":[{"delta":{"content":` + jsonString(text) + `},"finish_reason":null}]}` + "\n\n" +
		`data: {"choices":[{"delta":{},"finish_reason":"stop"}]}` + "\n\n" + "data: [DONE]\n\n"
}

func jsonString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

// lastToolResult returns the content of the last role=tool message in a request body.
func lastToolResult(body map[string]any) string {
	msgs, _ := body["messages"].([]any)
	for i := len(msgs) - 1; i >= 0; i-- {
		m, _ := msgs[i].(map[string]any)
		if m["role"] == "tool" {
			s, _ := m["content"].(string)
			return s
		}
	}
	return ""
}

type questionFixture struct {
	s      *MessageService
	pool   interface{ Close() }
	cr     *repository.ConversationRepo
	mr     *repository.MessageRepo
	pq     *repository.PendingQuestionRepo
	gw     *scriptedGateway
	tenant uuid.UUID
	user   uuid.UUID
	conv   *model.Conversation
	asst   *model.Message
}

func newQuestionFixture(t *testing.T, script func(n int, body map[string]any) string) *questionFixture {
	t.Helper()
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	pq := repository.NewPendingQuestionRepo(pool)
	gw := newScriptedGateway(t, script)
	s := NewMessageService(cr, mr, NewGatewayClient(gw.server.URL, "test-key", "", time.Minute, ""), nil, allowAllModels{}, 50)
	s.SetPendingQuestionStore(pq)
	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		testdeps.Missing(t, "conv create (schema?): %v", err)
	}
	if _, err := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: "user", ContentMasked: "make a logo", Status: "completed"}); err != nil {
		t.Fatal(err)
	}
	asst, err := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenantID, UserID: userID, Role: "assistant", Status: "streaming"})
	if err != nil {
		t.Fatal(err)
	}
	return &questionFixture{s: s, cr: cr, mr: mr, pq: pq, gw: gw, tenant: tenantID, user: userID, conv: conv, asst: asst}
}

// park runs the pump the way SendStream does and returns the updates it
// produced plus the pending row. It also does what the handler's finish
// would do for a parked terminal: persist the prefix as waiting_user.
func (f *questionFixture) park(t *testing.T, startIter int) ([]StreamUpdate, *model.PendingQuestion) {
	t.Helper()
	ctx := context.Background()
	req := GatewayChatRequest{Model: "m", Messages: []GatewayMessage{{Role: "system", Content: "sys"}, {Role: "user", Content: "make a logo"}}, Tools: []GatewayTool{askUserToolDef()}, ToolChoice: "auto"}
	callCtx := GatewayCallContext{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, RequestID: "req-park"}
	stream, err := f.s.gateway.SendChatCompletionStream(ctx, callCtx, req)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	out := make(chan StreamUpdate, 64)
	go f.s.pumpStreamWithTools(streamPumpInput{
		initialStream: stream, initialReq: req, callCtx: callCtx, streamCtx: ctx, toolsAttached: true,
		toolExecCtx: ToolExecContext{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, MessageID: f.asst.ID},
		out: out, startIter: startIter,
	})
	var updates []StreamUpdate
	var content strings.Builder
	for u := range out {
		updates = append(updates, u)
		content.WriteString(u.Delta)
	}
	row, err := f.pq.PendingForConversation(ctx, f.tenant, f.conv.ID)
	if err != nil {
		t.Fatalf("no pending row after the park: %v", err)
	}
	if err := f.mr.UpdateContent(ctx, f.asst.ID, content.String(), model.MessageStatusWaitingUser, nil, nil); err != nil {
		t.Fatal(err)
	}
	return updates, row
}

// drainHandle consumes a continuation the way the handler does and calls
// Finish with the drained text.
func drainHandle(h *StreamHandle) (string, string) {
	var content strings.Builder
	finish := ""
	for u := range h.Events {
		content.WriteString(u.Delta)
		if u.FinishReason != "" {
			finish = u.FinishReason
		}
	}
	h.Finish(content.String(), "", finish, nil)
	return content.String(), finish
}

func TestQuestion_ParkThenResume_AnswerIsTheToolResult(t *testing.T) {
	f := newQuestionFixture(t, func(n int, body map[string]any) string {
		if n == 1 {
			return sseAsk("call_ask")
		}
		return sseText("Thanks. You chose: " + lastToolResult(body))
	})
	ctx := context.Background()
	updates, row := f.park(t, 0)

	var sawQuestion, sawParked, sawDone bool
	for _, u := range updates {
		if u.Question != nil {
			sawQuestion = true
			if u.Question.PromptID != row.ID.String() || u.Question.ToolCallID != "call_ask" || u.Question.Questions[0].Header != "Format" {
				t.Fatalf("question frame: %+v", u.Question)
			}
		}
		sawParked = sawParked || u.Parked
		sawDone = sawDone || u.Done
	}
	if !sawQuestion || !sawParked || sawDone {
		t.Fatalf("park must emit question + parked and no Done: q=%v p=%v d=%v", sawQuestion, sawParked, sawDone)
	}
	if _, active := f.s.activeStreams.Load(f.conv.ID); active {
		t.Fatal("a parked turn must hold no active stream registration")
	}
	var state parkedTurn
	if err := json.Unmarshal(row.State, &state); err != nil {
		t.Fatal(err)
	}
	last := state.Request.Messages[len(state.Request.Messages)-1]
	if last.Role != "assistant" || len(last.ToolCalls) != 1 || last.ToolCalls[0].ID != "call_ask" || state.ContentPrefix != "Let me ask. " {
		t.Fatalf("parked request must end on the assistant tool_calls message and keep the prefix: %+v prefix=%q", last, state.ContentPrefix)
	}
	msg, _ := f.mr.GetByID(ctx, f.tenant, f.asst.ID)
	if msg.Status != model.MessageStatusWaitingUser {
		t.Fatalf("assistant row status = %q", msg.Status)
	}
	if pq, _ := f.s.PendingQuestionFor(ctx, f.tenant, f.user, f.conv.ID); pq == nil || pq.ID != row.ID {
		t.Fatal("the message list must find the pending card")
	}

	// The answer resumes the turn.
	h, err := f.s.ResumeQuestion(ctx, AnswerQuestionInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, PromptID: row.ID,
		Answer: QuestionAnswer{Outcome: "answered", Answers: map[string][]string{"Which format?": {"Detailed"}}}, RequestID: "req-resume"})
	if err != nil {
		t.Fatalf("resume: %v", err)
	}
	if h.AssistantID != f.asst.ID {
		t.Fatal("the continuation must run on the same assistant message")
	}
	content, _ := drainHandle(h)
	if content != `Thanks. You chose: "Which format?": Detailed` {
		t.Fatalf("continuation text: %q", content)
	}
	if got := lastToolResult(f.gw.bodies[1]); got != `"Which format?": Detailed` {
		t.Fatalf("the model must receive the answer as the tool result: %q", got)
	}
	toolMsgs := f.gw.bodies[1]["messages"].([]any)
	if tm := toolMsgs[len(toolMsgs)-1].(map[string]any); tm["tool_call_id"] != "call_ask" {
		t.Fatalf("tool result must answer the SAME call: %v", tm)
	}
	msg, _ = f.mr.GetByID(ctx, f.tenant, f.asst.ID)
	if msg.Status != "completed" || msg.ContentMasked != `Let me ask. Thanks. You chose: "Which format?": Detailed` {
		t.Fatalf("final row: status=%q content=%q", msg.Status, msg.ContentMasked)
	}
	after, _ := f.pq.GetByID(ctx, f.tenant, row.ID)
	if after.Status != model.PendingQuestionAnswered {
		t.Fatalf("pending row status = %q", after.Status)
	}
	// Two tabs: the second answer loses.
	if _, err := f.s.ResumeQuestion(ctx, AnswerQuestionInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, PromptID: row.ID,
		Answer: QuestionAnswer{Outcome: "skipped"}}); err != ErrQuestionNotPending {
		t.Fatalf("second answer must be refused: %v", err)
	}
}

func TestQuestion_SkipAndFreeTextReachTheModelVerbatim(t *testing.T) {
	f := newQuestionFixture(t, func(n int, body map[string]any) string {
		if n == 1 {
			return sseAsk("call_ask")
		}
		return sseText("ok")
	})
	ctx := context.Background()
	_, row := f.park(t, 0)
	resp := "make it a slide deck"
	h, err := f.s.ResumeQuestion(ctx, AnswerQuestionInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, PromptID: row.ID,
		Answer: QuestionAnswer{Outcome: "answered", Answers: map[string][]string{"Which format?": {"Summary"}}, Response: &resp}})
	if err != nil {
		t.Fatal(err)
	}
	drainHandle(h)
	if got := lastToolResult(f.gw.bodies[1]); got != `The user responded: "make it a slide deck"` {
		t.Fatalf("free text: %q", got)
	}
}

func TestQuestion_ExpiredIsRefusedAndSettled_NeverAnswered(t *testing.T) {
	f := newQuestionFixture(t, func(n int, body map[string]any) string {
		if n == 1 {
			return sseAsk("call_ask")
		}
		return sseText("should never run")
	})
	ctx := context.Background()
	_, row := f.park(t, 0)
	f.s.questionClock = func() time.Time { return time.Now().Add(pendingQuestionTTL + time.Hour) }
	if _, err := f.s.ResumeQuestion(ctx, AnswerQuestionInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, PromptID: row.ID,
		Answer: QuestionAnswer{Outcome: "answered", Answers: map[string][]string{"Which format?": {"Summary"}}}}); err != ErrQuestionExpired {
		t.Fatalf("expected ErrQuestionExpired, got %v", err)
	}
	after, _ := f.pq.GetByID(ctx, f.tenant, row.ID)
	msg, _ := f.mr.GetByID(ctx, f.tenant, f.asst.ID)
	if after.Status != model.PendingQuestionExpired || msg.Status != "cancelled" {
		t.Fatalf("expired: row=%q message=%q", after.Status, msg.Status)
	}
	if len(f.gw.bodies) != 1 {
		t.Fatal("an expired question must never reach the model as an answer")
	}
	if pq, _ := f.s.PendingQuestionFor(ctx, f.tenant, f.user, f.conv.ID); pq != nil {
		t.Fatal("no card after expiry")
	}
}

func TestQuestion_NewMessageSupersedes_StopCancels(t *testing.T) {
	f := newQuestionFixture(t, func(n int, body map[string]any) string { return sseAsk(fmt.Sprintf("call_%d", n)) })
	ctx := context.Background()
	_, row := f.park(t, 0)
	f.s.supersedePending(ctx, f.tenant, f.conv.ID)
	after, _ := f.pq.GetByID(ctx, f.tenant, row.ID)
	msg, _ := f.mr.GetByID(ctx, f.tenant, f.asst.ID)
	if after.Status != model.PendingQuestionSuperseded || msg.Status != "cancelled" {
		t.Fatalf("superseded: row=%q message=%q", after.Status, msg.Status)
	}
	// A second parked turn in the same conversation is possible again, and Stop closes it.
	asst2, _ := f.mr.Create(ctx, &model.Message{ConversationID: f.conv.ID, TenantID: f.tenant, UserID: f.user, Role: "assistant", Status: "streaming"})
	f.asst = asst2
	_, row2 := f.park(t, 0)
	if !f.s.CancelPendingQuestion(ctx, f.tenant, f.user, f.conv.ID) {
		t.Fatal("Stop must close the pending question")
	}
	after2, _ := f.pq.GetByID(ctx, f.tenant, row2.ID)
	if after2.Status != model.PendingQuestionCancelled {
		t.Fatalf("cancelled: %q", after2.Status)
	}
	if len(f.gw.bodies) != 2 {
		t.Fatal("neither supersede nor Stop may answer the model")
	}
}

func TestQuestion_LastAllowedRoundStillGetsItsAnswerRound(t *testing.T) {
	f := newQuestionFixture(t, func(n int, body map[string]any) string {
		if n == 1 {
			return sseAsk("call_ask")
		}
		return sseText("answer round ran")
	})
	ctx := context.Background()
	_, row := f.park(t, maxToolIterations-1)
	h, err := f.s.ResumeQuestion(ctx, AnswerQuestionInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, PromptID: row.ID,
		Answer: QuestionAnswer{Outcome: "skipped"}})
	if err != nil {
		t.Fatal(err)
	}
	content, _ := drainHandle(h)
	if content != "answer round ran" || len(f.gw.bodies) != 2 {
		t.Fatalf("the continuation must read its stream even at the iteration cap: %q requests=%d", content, len(f.gw.bodies))
	}
}

func TestQuestion_OfferedOnlyToAttendedStreamingTurns(t *testing.T) {
	s := &MessageService{toolExec: &ToolExecutor{}}
	conv := &model.Conversation{ID: uuid.New()}
	has := func(req GatewayChatRequest) bool {
		for _, d := range req.Tools {
			if d.Function.Name == AskUserTool {
				return true
			}
		}
		return false
	}
	var req GatewayChatRequest
	s.attachTools(context.Background(), &req, conv, nil, true)
	if has(req) {
		t.Fatal("with no store wired the tool must not be offered")
	}
	s.pendingQuestions = repository.NewPendingQuestionRepo(nil)
	req = GatewayChatRequest{}
	s.attachTools(context.Background(), &req, conv, nil, true)
	if !has(req) {
		t.Fatal("an attended streaming turn must be offered the tool")
	}
	req = GatewayChatRequest{}
	s.attachTools(context.Background(), &req, conv, nil, false)
	if has(req) {
		t.Fatal("the non-stream path must not be offered the tool (nobody can be shown a card)")
	}
	req = GatewayChatRequest{}
	s.attachTools(context.Background(), &req, conv, model.CloudAllowedToolNames(), true)
	if has(req) {
		t.Fatal("a cloud scheduled run must not be offered the tool")
	}
	if model.IsCloudToolAllowed(AskUserTool) {
		t.Fatal("ask_user_question must be withheld from cloud runs")
	}
}

func TestQuestion_InvalidAskIsAToolErrorNotACard(t *testing.T) {
	f := newQuestionFixture(t, func(n int, body map[string]any) string {
		if n == 1 {
			bad := `{"questions":[{"question":"Which?","header":"Which","options":[{"label":"A"},{"label":"Other"}]}]}`
			return `data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_bad","type":"function","function":{"name":"ask_user_question","arguments":` + jsonString(bad) + `}}]},"finish_reason":null}]}` + "\n\n" +
				`data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}` + "\n\n" + "data: [DONE]\n\n"
		}
		return sseText("re-asked properly: " + lastToolResult(body))
	})
	f.s.toolExec = &ToolExecutor{}
	ctx := context.Background()
	req := GatewayChatRequest{Model: "m", Messages: []GatewayMessage{{Role: "user", Content: "x"}}, Tools: []GatewayTool{askUserToolDef()}}
	callCtx := GatewayCallContext{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID}
	stream, _ := f.s.gateway.SendChatCompletionStream(ctx, callCtx, req)
	out := make(chan StreamUpdate, 64)
	go f.s.pumpStreamWithTools(streamPumpInput{initialStream: stream, initialReq: req, callCtx: callCtx, streamCtx: ctx, toolsAttached: true,
		toolExecCtx: ToolExecContext{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, MessageID: f.asst.ID}, out: out})
	var content strings.Builder
	for u := range out {
		if u.Question != nil || u.Parked {
			t.Fatal("an invalid question must not become a card")
		}
		content.WriteString(u.Delta)
	}
	if !strings.Contains(content.String(), "do not offer an 'Other' choice") {
		t.Fatalf("the model must get the validation error as the tool result: %q", content.String())
	}
	if _, err := f.pq.PendingForConversation(ctx, f.tenant, f.conv.ID); err == nil {
		t.Fatal("nothing may be parked for an invalid question")
	}
}
