package service

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// FIX122 Part B — who names a chat, when, and with what.

func titleConv(title string, memory bool) *model.Conversation {
	return &model.Conversation{Title: title, ModelID: "auto", MemoryEnabled: memory}
}

func explicitConv(modelID string) *model.Conversation {
	return &model.Conversation{Title: "New conversation", ModelID: modelID, MemoryEnabled: true}
}

func fellBack(userText, served, from string) []model.Message {
	h := firstTurn(userText, "completed", strp(served))
	h[1].FallbackFrom = strp(from)
	return h
}

func firstTurn(userText, asstStatus string, asstModel *string) []model.Message {
	return []model.Message{
		{Role: "user", ContentMasked: userText, Status: "completed"},
		{Role: "assistant", ContentMasked: "คำตอบ", Status: asstStatus, ModelID: asstModel},
	}
}

func TestPlanTitle(t *testing.T) {
	const q = "ช่วยสรุปรายงานประจำไตรมาสให้หน่อย"
	long := strings.Repeat("ก", 150)
	cases := []struct {
		name    string
		conv    *model.Conversation
		history []model.Message
		want    titlePlan
	}{
		{"new chat asks the model that answered", titleConv("New conversation", true), firstTurn(q, "completed", strp("qwen3.8-27b-fp8")),
			titlePlan{action: "ask", model: "qwen3.8-27b-fp8"}},
		{"the first-message truncation is still a machine title", titleConv(makeTitleFrom(long), true), firstTurn(long, "completed", strp("deepseek-v4-flash")),
			titlePlan{action: "ask", model: "deepseek-v4-flash"}},
		{"auto is named by the served model, not the sentinel", titleConv(q, true), firstTurn(q, "completed", strp("deepseek-v4-pro")),
			titlePlan{action: "ask", model: "deepseek-v4-pro"}},
		{"a title the person typed is kept", titleConv("[NIGHT] two-tabs", true), firstTurn(q, "completed", strp("qwen3.8-27b-fp8")),
			titlePlan{action: "keep", reason: "title_already_set"}},
		{"a title from an earlier run is kept", titleConv("สรุปรายงานไตรมาส", true), firstTurn(q, "completed", strp("qwen3.8-27b-fp8")),
			titlePlan{action: "keep", reason: "title_already_set"}},
		{"temporary chat is sent nowhere", titleConv("New conversation", false), firstTurn(q, "completed", strp("qwen3.8-27b-fp8")),
			titlePlan{action: "keep", reason: "temporary_chat"}},
		{"nobody answered: first words, no model", titleConv("New conversation", true), firstTurn(q, "error", strp("qwen3.8-27b-fp8")),
			titlePlan{action: "fallback", reason: "first_turn_unanswered"}},
		{"only the user row exists", titleConv("New conversation", true), []model.Message{{Role: "user", ContentMasked: q, Status: "completed"}},
			titlePlan{action: "fallback", reason: "first_turn_unanswered"}},
		{"answer with no model on the row", titleConv("New conversation", true), firstTurn(q, "completed", nil),
			titlePlan{action: "fallback", reason: "served_model_unknown"}},
		{"second turn is too late", titleConv("New conversation", true),
			append(firstTurn(q, "completed", strp("qwen3.8-27b-fp8")), model.Message{Role: "user", ContentMasked: "ต่อ", Status: "completed"}),
			titlePlan{action: "keep", reason: "not_first_turn"}},
		{"no messages", titleConv("New conversation", true), nil, titlePlan{action: "keep", reason: "no_messages"}},
		{"the row carries the provider's served name: the pick is the catalogue id", explicitConv("deepseek-v4-flash"), firstTurn(q, "completed", strp("deepseek-flash")),
			titlePlan{action: "ask", model: "deepseek-flash", requested: "deepseek-v4-flash"}},
		{"after a fallback the pick is not who answered", explicitConv("gpt-5.6-luna"), fellBack(q, "qwen3.8-27b-fp8", "gpt-5.6-luna"),
			titlePlan{action: "ask", model: "qwen3.8-27b-fp8"}},
	}
	for _, c := range cases {
		if got := planTitle(c.conv, c.history); got != c.want {
			t.Errorf("%s: got %+v, want %+v", c.name, got, c.want)
		}
	}
}

// An empty assistant row is "nobody answered" even when it says completed:
// firstUserAndAssistant skips rows with no text.
func TestPlanTitle_EmptyAnswerIsNoAnswer(t *testing.T) {
	h := []model.Message{
		{Role: "user", ContentMasked: "สวัสดี", Status: "completed"},
		{Role: "assistant", ContentMasked: "  ", Status: "completed", ModelID: strp("qwen3.8-27b-fp8")},
	}
	if got := planTitle(titleConv("New conversation", true), h); got.action != "fallback" {
		t.Fatalf("got %+v, want a fallback", got)
	}
}

// The request is small and stays home. Decoded from the JSON that goes on
// the wire, so a renamed struct tag fails here too.
func TestBuildTitleRequest_Wire(t *testing.T) {
	raw, err := json.Marshal(buildTitleRequest("qwen3.8-27b-fp8", "sranai", "ถามเรื่องภาษี", "ตอบเรื่องภาษี"))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["model"] != "qwen3.8-27b-fp8" {
		t.Errorf("model = %v", got["model"])
	}
	if got["max_tokens"] != float64(titleMaxTokens) {
		t.Errorf("max_tokens = %v", got["max_tokens"])
	}
	for _, absent := range []string{"tools", "tool_choice", "response_format", "routing_hints"} {
		if _, ok := got[absent]; ok {
			t.Errorf("%s must not be sent", absent)
		}
	}
	if mode := got["reasoning"].(map[string]any)["mode"]; mode != "off" {
		t.Errorf("reasoning.mode = %v", mode)
	}
	// the boundary is the serving provider and nothing else — never a list
	// that names no provider (the gateway then routes the PRIMARY elsewhere)
	allowed := got["metadata"].(map[string]any)["allowed_providers"].([]any)
	if len(allowed) != 1 || allowed[0] != "sranai" {
		t.Errorf("allowed_providers = %v", allowed)
	}
	msgs := got["messages"].([]any)
	if len(msgs) != 2 {
		t.Fatalf("messages = %d, want 2", len(msgs))
	}
	if n := len(raw); n > 1400 {
		t.Errorf("request is %d bytes; it is meant to be small", n)
	}
}

// The three models the old path ended up on are named nowhere in the title
// service, and neither is the one it started from.
func TestTitleService_NoHardCodedModel(t *testing.T) {
	raw, _ := json.Marshal(buildTitleRequest("m", "p", "u", "a"))
	for _, id := range []string{"gpt-4o-mini", "gpt-5.6-luna", "deepseek-v4-flash", "glm-5.3-flash"} {
		if strings.Contains(string(raw), id) {
			t.Errorf("request names %s", id)
		}
	}
}

func titleFakeGateway(t *testing.T, status int, body string, seen *[]map[string]any) *GatewayClient {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet { // the catalogue
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"data":[{"id":"qwen3.8-27b-fp8","provider":"sranai"},{"id":"deepseek-v4-flash","provider":"deepseek"}]}`)
			return
		}
		b, _ := io.ReadAll(r.Body)
		var req map[string]any
		_ = json.Unmarshal(b, &req)
		*seen = append(*seen, req)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}))
	t.Cleanup(srv.Close)
	return NewGatewayClient(srv.URL, "test-key", "", time.Minute, "")
}

func TestAskModel(t *testing.T) {
	user := &model.Message{Role: "user", ContentMasked: "ช่วยสรุปรายงานประจำไตรมาสให้หน่อย"}
	asst := &model.Message{Role: "assistant", ContentMasked: "ได้ครับ", ModelID: strp("qwen3.8-27b-fp8")}
	ok := func(content string) string {
		b, _ := json.Marshal(map[string]any{"model": "qwen3.8-27b-fp8", "choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": content}}},
			"usage": map[string]any{"prompt_tokens": 80, "completion_tokens": 9}})
		return string(b)
	}
	cases := []struct {
		name      string
		status    int
		body      string
		wantTitle string
		wantWhy   string
	}{
		{"free text", 200, ok("\"สรุปรายงานประจำไตรมาส\"."), "สรุปรายงานประจำไตรมาส", ""},
		{"a model that still answers in the old envelope", 200, ok(`{"title":"สรุปรายงาน"}`), "สรุปรายงาน", ""},
		{"thinking ate the budget", 200, ok(""), "", "model_returned_nothing"},
		{"model down", 503, `{"error":{"code":"provider_unavailable","message":"down"}}`, "", "model_call_failed"},
		{"no fallback candidate", 502, `{"error":{"code":"no_fallback_candidate","message":"x"}}`, "", "model_call_failed"},
	}
	for _, c := range cases {
		var seen []map[string]any
		s := &TitleService{gateway: titleFakeGateway(t, c.status, c.body, &seen)}
		title, why := s.askModel(context.Background(), uuid.New(), uuid.New(), uuid.New(), titlePlan{action: "ask", model: "qwen3.8-27b-fp8"}, user, asst)
		if title != c.wantTitle || why != c.wantWhy {
			t.Errorf("%s: got (%q, %q), want (%q, %q)", c.name, title, why, c.wantTitle, c.wantWhy)
		}
		// One call, to the model that answered — a failure is not retried
		// on another model.
		if len(seen) != 1 || seen[0]["model"] != "qwen3.8-27b-fp8" {
			t.Errorf("%s: gateway saw %d call(s): %v", c.name, len(seen), seen)
			continue
		}
		if allowed := seen[0]["metadata"].(map[string]any)["allowed_providers"].([]any); len(allowed) != 1 || allowed[0] != "sranai" {
			t.Errorf("%s: allowed_providers = %v, want the serving provider only", c.name, allowed)
		}
	}
}

// The served name is not in the catalogue but the person's pick is: the pick
// is asked, bounded by ITS provider.
func TestAskModel_ServedNameResolvesThroughThePick(t *testing.T) {
	var seen []map[string]any
	body, _ := json.Marshal(map[string]any{"model": "deepseek-flash", "choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "ท้องฟ้าสีฟ้า"}}}})
	s := &TitleService{gateway: titleFakeGateway(t, 200, string(body), &seen)}
	user := &model.Message{Role: "user", ContentMasked: "ทำไมท้องฟ้าเป็นสีฟ้า"}
	title, _ := s.askModel(context.Background(), uuid.New(), uuid.New(), uuid.New(), titlePlan{action: "ask", model: "deepseek-flash", requested: "deepseek-v4-flash"}, user, nil)
	if title != "ท้องฟ้าสีฟ้า" || len(seen) != 1 || seen[0]["model"] != "deepseek-v4-flash" {
		t.Fatalf("title=%q calls=%v", title, seen)
	}
	if allowed := seen[0]["metadata"].(map[string]any)["allowed_providers"].([]any); len(allowed) != 1 || allowed[0] != "deepseek" {
		t.Errorf("allowed_providers = %v", allowed)
	}
}

// A served model the catalogue does not list has no provider to bound the
// call with — and an unbounded call is the old behaviour. No call at all.
func TestAskModel_UnknownModelMakesNoCall(t *testing.T) {
	var seen []map[string]any
	s := &TitleService{gateway: titleFakeGateway(t, 200, `{}`, &seen)}
	user := &model.Message{Role: "user", ContentMasked: "สวัสดี"}
	title, why := s.askModel(context.Background(), uuid.New(), uuid.New(), uuid.New(), titlePlan{action: "ask", model: "vendor/some-served-name-0731"}, user, nil)
	if title != "" || why != "served_model_not_in_catalogue" {
		t.Errorf("got (%q, %q)", title, why)
	}
	if len(seen) != 0 {
		t.Errorf("gateway saw %d completion call(s), want none", len(seen))
	}
}
