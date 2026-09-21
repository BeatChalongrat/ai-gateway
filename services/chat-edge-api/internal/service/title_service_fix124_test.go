package service

import (
	"context"
	"encoding/json"
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
)

// FIX122 + FIX124 — who names a chat, when, and with what.
//
// FIX124 changed the rule from "after turn one" to "after the first turn that
// was answered", and the title model from "the serving model or nothing" to
// "the serving model, then any model that works".

const titleQ = "ช่วยสรุปรายงานประจำไตรมาสให้หน่อย"

func titleConv(title, modelID string, memory bool) *model.Conversation {
	return &model.Conversation{Title: title, ModelID: modelID, MemoryEnabled: memory}
}

func titleUserRow(text string) *model.Message {
	return &model.Message{Role: "user", ContentMasked: text, Status: "completed"}
}

func titleAnswerRow(modelID string, fallbackFrom *string) *model.Message {
	return &model.Message{Role: "assistant", ContentMasked: "คำตอบ", Status: "completed", ModelID: &modelID, FallbackFrom: fallbackFrom}
}

func TestPlanTitle(t *testing.T) {
	long := strings.Repeat("ก", 150)
	luna := "gpt-5.6-luna"
	cases := []struct {
		name string
		conv *model.Conversation
		seed *repository.TitleSeed
		want titlePlan
	}{
		{"new chat asks the model that answered", titleConv("New conversation", "auto", true),
			&repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)},
			titlePlan{action: "ask", model: "qwen3.8-27b-fp8"}},
		{"the first-words placeholder is still a machine title", titleConv(makeTitleFrom(long), "auto", true),
			&repository.TitleSeed{FirstUser: titleUserRow(long), Question: titleUserRow(long), Answer: titleAnswerRow("deepseek-v4-flash", nil)},
			titlePlan{action: "ask", model: "deepseek-v4-flash"}},
		// FIX124 — the owner's 66b0122a: two failed turns, the third answered.
		{"fail, fail, success: the third turn names the chat", titleConv("New conversation", "auto", true),
			&repository.TitleSeed{FirstUser: titleUserRow("ไง"), Question: titleUserRow("ลองอีกที"), Answer: titleAnswerRow("deepseek-v4-pro", nil)},
			titlePlan{action: "ask", model: "deepseek-v4-pro"}},
		{"a title the person typed is kept", titleConv("[NIGHT] two-tabs", "auto", true),
			&repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)},
			titlePlan{action: "keep", reason: "title_already_set"}},
		{"a title a model already wrote is kept: one name per chat", titleConv("สรุปรายงานไตรมาส", "auto", true),
			&repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)},
			titlePlan{action: "keep", reason: "title_already_set"}},
		{"temporary chat is sent nowhere", titleConv("New conversation", "auto", false),
			&repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)},
			titlePlan{action: "keep", reason: "temporary_chat"}},
		{"nothing answered yet: the first words, no model", titleConv("New conversation", "auto", true),
			&repository.TitleSeed{FirstUser: titleUserRow(titleQ)},
			titlePlan{action: "fallback", reason: "no_turn_answered"}},
		{"no messages", titleConv("New conversation", "auto", true), &repository.TitleSeed{}, titlePlan{action: "keep", reason: "no_messages"}},
		{"the row carries the provider's served name: the pick is the catalogue id", titleConv("New conversation", "deepseek-v4-flash", true),
			&repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("deepseek-flash", nil)},
			titlePlan{action: "ask", model: "deepseek-flash", requested: "deepseek-v4-flash"}},
		{"after a fallback the pick is not who answered", titleConv("New conversation", luna, true),
			&repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", &luna)},
			titlePlan{action: "ask", model: "qwen3.8-27b-fp8"}},
	}
	for _, c := range cases {
		if got := planTitle(c.conv, c.seed); got != c.want {
			t.Errorf("%s: got %+v, want %+v", c.name, got, c.want)
		}
	}
}

// The request is small, and it no longer draws a provider boundary of its
// own: the gateway applies the tenant's. Decoded from the JSON on the wire.
func TestBuildTitleRequest_Wire(t *testing.T) {
	raw, err := json.Marshal(buildTitleRequest("qwen3.8-27b-fp8", "ถามเรื่องภาษี", "ตอบเรื่องภาษี"))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["model"] != "qwen3.8-27b-fp8" || got["max_tokens"] != float64(titleMaxTokens) {
		t.Errorf("model=%v max_tokens=%v", got["model"], got["max_tokens"])
	}
	for _, absent := range []string{"tools", "tool_choice", "response_format", "routing_hints"} {
		if _, ok := got[absent]; ok {
			t.Errorf("%s must not be sent", absent)
		}
	}
	if mode := got["reasoning"].(map[string]any)["mode"]; mode != "off" {
		t.Errorf("reasoning.mode = %v", mode)
	}
	meta := got["metadata"].(map[string]any)
	if meta["purpose"] != titlePurpose {
		t.Errorf("metadata.purpose = %v", meta["purpose"])
	}
	// FIX122 learnt that a list naming no provider re-routes the PRIMARY;
	// FIX124 sends no list at all — the tenant's own is the gateway's to apply.
	if _, ok := meta["allowed_providers"]; ok {
		t.Errorf("allowed_providers must not be sent: %v", meta["allowed_providers"])
	}
	if len(got["messages"].([]any)) != 2 || len(raw) > 1400 {
		t.Errorf("messages=%d bytes=%d; the request is meant to be small", len(got["messages"].([]any)), len(raw))
	}
	for _, id := range []string{"gpt-4o-mini", "gpt-5.6-luna", "glm-5.3-flash"} {
		if strings.Contains(string(raw), id) {
			t.Errorf("request names %s", id)
		}
	}
}

// ── Generate, end to end against fakes ─────────────────────────────────

type fakeTitleConvs struct {
	conv   *model.Conversation
	writes []string
	// renameBeforeWrite simulates the person renaming while the model writes.
	renameBeforeWrite string
}

func (f *fakeTitleConvs) GetByID(_ context.Context, _, _ uuid.UUID) (*model.Conversation, error) {
	c := *f.conv
	return &c, nil
}

func (f *fakeTitleConvs) UpdateTitleIfCurrent(_ context.Context, _, _ uuid.UUID, current, title string) (bool, error) {
	if f.renameBeforeWrite != "" {
		f.conv.Title = f.renameBeforeWrite
	}
	if f.conv.Title != current {
		return false, nil
	}
	f.conv.Title = title
	f.writes = append(f.writes, title)
	return true, nil
}

type fakeTitleMsgs struct{ seed *repository.TitleSeed }

func (f *fakeTitleMsgs) GetTitleSeed(context.Context, uuid.UUID, uuid.UUID) (*repository.TitleSeed, error) {
	return f.seed, nil
}

// titleGateway answers GET /models with a two-model catalogue and each
// completion by the rule for the model it names; it records the models asked.
type titleGateway struct {
	mu    sync.Mutex
	asked []string
	rule  map[string]func(w http.ResponseWriter)
}

func titleOK(served, content string) func(http.ResponseWriter) {
	return func(w http.ResponseWriter) {
		b, _ := json.Marshal(map[string]any{"model": served, "choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": content}}},
			"usage": map[string]any{"prompt_tokens": 80, "completion_tokens": 9}})
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(b)
	}
}

func titleDown(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusBadGateway)
	_, _ = io.WriteString(w, `{"error":{"code":"all_models_failed","message":"down"}}`)
}

func newTitleService(t *testing.T, conv *model.Conversation, seed *repository.TitleSeed, rule map[string]func(http.ResponseWriter)) (*TitleService, *fakeTitleConvs, *titleGateway) {
	t.Helper()
	gw := &titleGateway{rule: rule}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"data":[{"id":"qwen3.8-27b-fp8","provider":"sranai"},{"id":"deepseek-v4-flash","provider":"deepseek"}]}`)
			return
		}
		var req struct {
			Model string `json:"model"`
		}
		b, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(b, &req)
		gw.mu.Lock()
		gw.asked = append(gw.asked, req.Model)
		gw.mu.Unlock()
		if h, ok := gw.rule[req.Model]; ok {
			h(w)
			return
		}
		titleDown(w)
	}))
	t.Cleanup(srv.Close)
	convs := &fakeTitleConvs{conv: conv}
	conv.UserID = titleUser
	return &TitleService{gateway: NewGatewayClient(srv.URL, "test-key", "", time.Minute, ""), conversations: convs, messages: &fakeTitleMsgs{seed: seed}}, convs, gw
}

var titleUser = uuid.New()

func generate(t *testing.T, s *TitleService) TitleResult {
	t.Helper()
	res, err := s.Generate(context.Background(), uuid.New(), titleUser, uuid.New())
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	return res
}

// fail → fail → success: one title call, after the success, and never again.
func TestGenerate_NamesAfterTheFirstAnsweredTurn(t *testing.T) {
	seed := &repository.TitleSeed{FirstUser: titleUserRow("ไง"), Question: titleUserRow("ช่วยอธิบายภาษีมูลค่าเพิ่ม"), Answer: titleAnswerRow("deepseek-v4-flash", nil)}
	s, convs, gw := newTitleService(t, titleConv("New conversation", "deepseek-v4-flash", true), seed,
		map[string]func(http.ResponseWriter){"deepseek-v4-flash": titleOK("deepseek-v4-flash", "ภาษีมูลค่าเพิ่มคืออะไร")})

	res := generate(t, s)
	if res.Title != "ภาษีมูลค่าเพิ่มคืออะไร" || res.Source != TitleSourceModel || res.FallbackUsed {
		t.Fatalf("got %+v", res)
	}
	if len(gw.asked) != 1 || gw.asked[0] != "deepseek-v4-flash" || len(convs.writes) != 1 {
		t.Fatalf("asked=%v writes=%v", gw.asked, convs.writes)
	}
	// a later turn asks again: the title is no longer a machine title
	res = generate(t, s)
	if res.Source != TitleSourceKept || res.Reason != "title_already_set" || len(gw.asked) != 1 {
		t.Fatalf("second Generate: %+v asked=%v", res, gw.asked)
	}
}

// While nothing has been answered the chat carries the first words — never
// "New conversation" — and no model is asked.
func TestGenerate_NothingAnsweredYetUsesTheFirstWords(t *testing.T) {
	s, convs, gw := newTitleService(t, titleConv("New conversation", "auto", true), &repository.TitleSeed{FirstUser: titleUserRow(titleQ)}, nil)
	res := generate(t, s)
	if res.Title != titleQ || res.Source != TitleSourceFallback || len(gw.asked) != 0 || convs.conv.Title != titleQ {
		t.Fatalf("got %+v asked=%v title=%q", res, gw.asked, convs.conv.Title)
	}
}

// The model that answered cannot name it → any model that works does, and
// the result says so.
func TestGenerate_TitleModelDeadAnotherModelNamesIt(t *testing.T) {
	seed := &repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)}
	s, convs, gw := newTitleService(t, titleConv(titleQ, "qwen3.8-27b-fp8", true), seed,
		map[string]func(http.ResponseWriter){AutoModelID: titleOK("deepseek-v4-flash", "สรุปรายงานไตรมาส")})
	res := generate(t, s)
	if res.Title != "สรุปรายงานไตรมาส" || res.Source != TitleSourceModel || !res.FallbackUsed || res.Model != "deepseek-v4-flash" {
		t.Fatalf("got %+v", res)
	}
	if strings.Join(gw.asked, ",") != "qwen3.8-27b-fp8,"+AutoModelID || convs.conv.Title != "สรุปรายงานไตรมาส" {
		t.Fatalf("asked=%v title=%q", gw.asked, convs.conv.Title)
	}
}

// The router's pick is asked for the model NEAREST the one that answered
// (the gateway's rescue order), so a small model's chat is not named by a
// large one: the second request carries metadata.closest_to.
func TestGenerate_SecondAskNamesTheModelToStayCloseTo(t *testing.T) {
	var bodies []map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			_, _ = io.WriteString(w, `{"data":[{"id":"qwen3.8-27b-fp8","provider":"sranai"}]}`)
			return
		}
		var req map[string]any
		b, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(b, &req)
		bodies = append(bodies, req)
		if req["model"] == AutoModelID {
			titleOK("deepseek-v4-flash", "สรุปรายงานไตรมาส")(w)
			return
		}
		titleDown(w)
	}))
	t.Cleanup(srv.Close)
	conv := titleConv("New conversation", "qwen3.8-27b-fp8", true)
	conv.UserID = titleUser
	seed := &repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)}
	s := &TitleService{gateway: NewGatewayClient(srv.URL, "test-key", "", time.Minute, ""), conversations: &fakeTitleConvs{conv: conv}, messages: &fakeTitleMsgs{seed: seed}}
	if res := generate(t, s); res.Source != TitleSourceModel || !res.FallbackUsed {
		t.Fatalf("got %+v", res)
	}
	if len(bodies) != 2 {
		t.Fatalf("calls = %d", len(bodies))
	}
	if _, ok := bodies[0]["metadata"].(map[string]any)["closest_to"]; ok {
		t.Error("the first ask names its model; it has nothing to be close to")
	}
	if got := bodies[1]["metadata"].(map[string]any)["closest_to"]; got != "qwen3.8-27b-fp8" {
		t.Errorf("second ask closest_to = %v", got)
	}
}

// A served name the catalogue does not list (and no pick to resolve it
// through) goes straight to the router — it is not a reason to give up.
func TestGenerate_UnknownServedModelGoesToTheRouter(t *testing.T) {
	seed := &repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("vendor/some-served-name-0731", nil)}
	s, _, gw := newTitleService(t, titleConv("New conversation", "auto", true), seed,
		map[string]func(http.ResponseWriter){AutoModelID: titleOK("deepseek-v4-flash", "สรุปรายงานไตรมาส")})
	res := generate(t, s)
	if res.Source != TitleSourceModel || !res.FallbackUsed || strings.Join(gw.asked, ",") != AutoModelID {
		t.Fatalf("got %+v asked=%v", res, gw.asked)
	}
}

// Every model dead → the first words, not an error and not "New conversation".
func TestGenerate_EveryModelDeadLeavesTheFirstWords(t *testing.T) {
	seed := &repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)}
	s, convs, gw := newTitleService(t, titleConv("New conversation", "qwen3.8-27b-fp8", true), seed, nil)
	res := generate(t, s)
	if res.Title != titleQ || res.Source != TitleSourceFallback || res.Reason != "model_call_failed" || !res.FallbackUsed {
		t.Fatalf("got %+v", res)
	}
	if len(gw.asked) != 2 || convs.conv.Title != titleQ {
		t.Fatalf("asked=%v title=%q", gw.asked, convs.conv.Title)
	}
}

func TestGenerate_RenameAndTemporaryAskNothing(t *testing.T) {
	seed := &repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)}
	for name, conv := range map[string]*model.Conversation{
		"rename":    titleConv("ชื่อที่ตั้งเอง", "auto", true),
		"temporary": titleConv("New conversation", "auto", false),
	} {
		s, convs, gw := newTitleService(t, conv, seed, map[string]func(http.ResponseWriter){"qwen3.8-27b-fp8": titleOK("qwen3.8-27b-fp8", "x")})
		before := conv.Title
		res := generate(t, s)
		if res.Source != TitleSourceKept || len(gw.asked) != 0 || len(convs.writes) != 0 || convs.conv.Title != before {
			t.Errorf("%s: %+v asked=%v writes=%v", name, res, gw.asked, convs.writes)
		}
	}
}

// Renamed while the model was writing: the person's name stands.
func TestGenerate_RenamedMeanwhile(t *testing.T) {
	seed := &repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("qwen3.8-27b-fp8", nil)}
	s, convs, _ := newTitleService(t, titleConv("New conversation", "auto", true), seed,
		map[string]func(http.ResponseWriter){"qwen3.8-27b-fp8": titleOK("qwen3.8-27b-fp8", "ชื่อจากโมเดล")})
	convs.renameBeforeWrite = "ชื่อที่ตั้งเอง"
	res := generate(t, s)
	if res.Title != "ชื่อที่ตั้งเอง" || res.Reason != "renamed_meanwhile" || len(convs.writes) != 0 {
		t.Fatalf("got %+v writes=%v", res, convs.writes)
	}
}

// The served name resolves through the person's pick (FIX122), and the
// completion's quirks are still cleaned.
func TestGenerate_ServedNameResolvesThroughThePick(t *testing.T) {
	seed := &repository.TitleSeed{FirstUser: titleUserRow(titleQ), Question: titleUserRow(titleQ), Answer: titleAnswerRow("deepseek-flash", nil)}
	s, _, gw := newTitleService(t, titleConv("New conversation", "deepseek-v4-flash", true), seed,
		map[string]func(http.ResponseWriter){"deepseek-v4-flash": titleOK("deepseek-flash", "\"สรุปรายงานประจำไตรมาส\".")})
	res := generate(t, s)
	if res.Title != "สรุปรายงานประจำไตรมาส" || res.FallbackUsed || strings.Join(gw.asked, ",") != "deepseek-v4-flash" {
		t.Fatalf("got %+v asked=%v", res, gw.asked)
	}
}
