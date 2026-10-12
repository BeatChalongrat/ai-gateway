package runner

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM16 — what a run says on the face of its card is what happened: something of it was sent to a model
// outside the organisation as it was typed. Found on the test stack on 12 Oct 2026 before anything was
// changed: with an agent's documents allowed out, a scanned tax invoice went to an outside model as an
// image — the gateway masks text, never an image — and the run's card said "masked".

const (
	inHouse = "qwen3.8-27b-fp8"
	outside = "google/gemini-2.5-flash"
)

// modelGateway answers as the model named in answers[asked] (default: the model asked for), with the
// status and the masking word it is told to give.
type modelGateway struct {
	status  int
	masking string
	answers map[string]string
}

func (g *modelGateway) serve(t *testing.T) *ModelClient {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		if g.status >= 400 {
			http.Error(w, `{"error":{"code":"x"}}`, g.status)
			return
		}
		if g.masking != "" {
			w.Header().Set(MaskingHeader, g.masking)
		}
		model := req.Model
		if other := g.answers[req.Model]; other != "" {
			model = other
		}
		b, _ := json.Marshal(map[string]any{"model": model, "choices": []any{map[string]any{
			"message": map[string]any{"content": `{"ok":true}`}, "finish_reason": "stop"}}, "usage": map[string]any{"total_tokens": 10}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	return &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: inHouse, Fallback: outside, VisionModel: inHouse,
		SelfHosted: map[string]bool{inHouse: true}, Timeout: 5 * time.Second, HTTP: http.DefaultClient}
}

func TestWhatLeftTheOrganisationAsTypedIsNoted(t *testing.T) {
	text := func(m *ModelClient, ctx context.Context, model string) error {
		_, _, err := m.CompleteWith(ctx, []string{model}, "s", "u", nil)
		return err
	}
	page := func(m *ModelClient, ctx context.Context, model string) error {
		_, err := m.Vision(ctx, model, "read", [][]byte{{0xff, 0xd8}}, nil)
		return err
	}
	for _, c := range []struct {
		what    string
		g       modelGateway
		masked  bool
		call    func(*ModelClient, context.Context, string) error
		model   string
		wantErr bool
		want    bool
	}{
		// text
		{"text to an outside model, sent as typed", modelGateway{masking: "off"}, false, text, outside, false, true},
		{"text to an outside model, masked by the gateway", modelGateway{masking: "on"}, false, text, outside, false, false},
		{"text to an outside model, a gateway that says nothing has masked", modelGateway{}, false, text, outside, false, false},
		{"text to the in-house model, as typed: nothing left", modelGateway{masking: "off"}, false, text, inHouse, false, false},
		// a page: an image is never masked, whatever the gateway says about the text beside it
		{"a page to an outside model while the gateway says masked", modelGateway{masking: "on"}, true, page, outside, false, true},
		{"a page to an outside model, a gateway that says nothing", modelGateway{}, false, page, outside, false, true},
		{"a page to the in-house model", modelGateway{masking: "off"}, false, page, inHouse, false, false},
		// a call that failed after the provider may have been sent it
		{"a page to an outside model, no answer in time (504)", modelGateway{status: 504}, false, page, outside, true, true},
		{"unmasked text to an outside model, 502", modelGateway{status: 502}, false, text, outside, true, true},
		{"masked text to an outside model, 502: nothing as typed was in it", modelGateway{status: 502}, true, text, outside, true, false},
		// … and one the gateway refused before any provider saw it
		{"a page to an outside model, refused by policy (403)", modelGateway{status: 403}, false, page, outside, true, false},
		{"a page to an outside model, model not there (404)", modelGateway{status: 404}, false, page, outside, true, false},
		// another model answered: that model is the one that was sent it
		{"the in-house model asked, an outside one answered as typed", modelGateway{masking: "off", answers: map[string]string{inHouse: outside}}, false, text, inHouse, true, true},
		{"an outside model asked for a page, the in-house one answered", modelGateway{masking: "on", answers: map[string]string{outside: inHouse}}, false, page, outside, true, false},
		{"the same model under its provider's prefix is the model asked", modelGateway{masking: "off", answers: map[string]string{inHouse: "sranai/" + inHouse}}, false, text, inHouse, false, false},
	} {
		g := c.g
		m := g.serve(t)
		ctx, calls := withModelCalls(context.Background(), false, c.masked)
		err := c.call(m, ctx, c.model)
		if (err != nil) != c.wantErr {
			t.Fatalf("%s: err = %v", c.what, err)
		}
		if calls.wentOut() != c.want {
			t.Errorf("%s: noted as sent out unmasked = %v, want %v", c.what, calls.wentOut(), c.want)
		}
	}
	// a call nobody marked notes nothing and does not fall over
	g := modelGateway{masking: "off"}
	if err := page(g.serve(t), context.Background(), outside); err != nil {
		t.Fatal(err)
	}
}

// The stamp is written beside a step's output — also beside a step that failed, whose output is
// otherwise empty — and a run is read from every step, whatever became of it.
func TestWhatWasSentOutIsWrittenBesideAStepWhateverBecameOfIt(t *testing.T) {
	if got := string(stampSentOut(json.RawMessage(`{"a":1}`), true)); got != `{"a":1,"sent_out_unmasked":true}` {
		t.Fatalf("beside an output: %s", got)
	}
	if got := string(stampSentOut(nil, true)); got != `{"sent_out_unmasked":true}` {
		t.Fatalf("beside a failed step's empty output: %s", got)
	}
	if got := stampSentOut(nil, false); got != nil {
		t.Fatalf("nothing was sent: %s", got)
	}
	if got := string(stampSentOut(json.RawMessage(`{"a":1}`), false)); got != `{"a":1}` {
		t.Fatalf("nothing was sent: %s", got)
	}
	if !sentOutStamped(json.RawMessage(`{"sent_out_unmasked":true}`)) || sentOutStamped(json.RawMessage(`{}`)) || sentOutStamped(nil) {
		t.Fatal("reading the stamp back")
	}
	steps := []am.Step{
		{Name: "parse:a.pdf", Status: "succeeded", Output: json.RawMessage(`{"rows":3}`)},
		{Name: "vision:a.pdf", Status: "failed", Output: json.RawMessage(`{"sent_out_unmasked":true}`)},
	}
	if !sentOutOf(steps) {
		t.Fatal("a failed step that sent its page out is not counted")
	}
	if sentOutOf(steps[:1]) {
		t.Fatal("a run that sent nothing out says it did")
	}
	// and the progress card of that run carries it — the card a run has whatever it comes to
	if p := BuildProgress(am.Run{Kind: am.KindRun, Status: am.RunFailed}, steps, time.Now()); p.SentOutUnmasked {
		t.Fatal("BuildProgress sets it itself: syncProgress is where every kind of progress card gets it")
	}
}

// While the admin's masking switch for an agent is on, the image of a page is read inside the
// organisation only — an image cannot be masked — whatever the other switch says. Text may still go out
// (masked). With masking off, both follow the leave switch.
func TestAPageIsReadInsideWhileTheAgentIsMasked(t *testing.T) {
	m := &ModelClient{BaseURL: "http://unused", APIKey: "k", Primary: inHouse, VisionModel: inHouse, SelfHosted: map[string]bool{inHouse: true}}
	for _, c := range []struct {
		leave, masked      bool
		wantText, wantPage bool
	}{
		{false, false, false, false},
		{false, true, false, false},
		{true, false, true, true},
		{true, true, true, false},
	} {
		// (the outside model's recipient is named throughout: what this holds is the two switches — who
		// may be reached at all is am16_recipients_test.go)
		rc := &runCtx{r: &Runner{Model: m}, docsMayLeave: c.leave, masked: c.masked, recipients: named(m, outside)}
		if got := rc.mayUse(outside); got != c.wantText {
			t.Errorf("leave=%v masked=%v: text to an outside model allowed = %v, want %v", c.leave, c.masked, got, c.wantText)
		}
		if got := rc.mayRead(outside); got != c.wantPage {
			t.Errorf("leave=%v masked=%v: a page to an outside model allowed = %v, want %v", c.leave, c.masked, got, c.wantPage)
		}
		if !rc.mayUse(inHouse) || !rc.mayRead(inHouse) {
			t.Errorf("leave=%v masked=%v: the in-house model must always be allowed", c.leave, c.masked)
		}
	}
}

// An answer from another model is refused as before (AM15) — and what that other model was sent is
// still noted: the refusal does not undo the sending.
func TestAnAnswerFromAnotherModelIsRefusedButWhatItWasSentIsStillNoted(t *testing.T) {
	g := modelGateway{masking: "off", answers: map[string]string{inHouse: outside}}
	m := g.serve(t)
	ctx, calls := withModelCalls(context.Background(), false, false)
	_, _, err := m.CompleteWith(ctx, []string{inHouse}, "s", "u", nil)
	if !errors.Is(err, ErrModelUnreachable) {
		t.Fatalf("an answer from another model must read as the model asked being away: %v", err)
	}
	if !calls.wentOut() {
		t.Fatal("the text went to an outside model as typed and nothing says so")
	}
}
