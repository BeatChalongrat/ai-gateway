package runner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// gatewayThatSays answers every call as the model asked for, and says about masking what it is told to
// (an empty string: says nothing, as a gateway older than the rule does).
func gatewayThatSays(t *testing.T, masking func(r *http.Request) string, got *[]http.Header) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*got = append(*got, r.Header.Clone())
		var req struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		if m := masking(r); m != "" {
			w.Header().Set(MaskingHeader, m)
		}
		b, _ := json.Marshal(map[string]any{"model": req.Model, "choices": []any{map[string]any{
			"message": map[string]any{"content": `{"ok":true}`}, "finish_reason": "stop"}}, "usage": map[string]any{"total_tokens": 10}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	return s
}

// AM13b: the header that says "this data may not leave" is sent only when BOTH are true — the round's
// egress is off and the model called is in-house. Everything else is sent as before, to be masked.
func TestEgressClosedIsSaidOnlyForAnInHouseModelOfARoundWhoseDataMayNotLeave(t *testing.T) {
	var got []http.Header
	s := gatewayThatSays(t, func(*http.Request) string { return "" }, &got)
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "qwen3.8-27b-fp8", Fallback: "deepseek-v4-flash",
		SelfHosted: map[string]bool{"qwen3.8-27b-fp8": true}, Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	closed, _ := withModelCalls(context.Background(), true)
	open, _ := withModelCalls(context.Background(), false)
	for _, c := range []struct {
		what  string
		ctx   context.Context
		model string
		want  string
	}{
		{"egress off · in-house model", closed, "qwen3.8-27b-fp8", EgressClosed},
		{"egress off · a model outside", closed, "deepseek-v4-flash", ""},
		{"egress on · in-house model", open, "qwen3.8-27b-fp8", ""},
		{"egress on · a model outside", open, "deepseek-v4-flash", ""},
		{"a call nobody marked", context.Background(), "qwen3.8-27b-fp8", ""},
	} {
		got = got[:0]
		if _, _, err := m.CompleteWith(c.ctx, []string{c.model}, "s", "u", nil); err != nil {
			t.Fatalf("%s: %v", c.what, err)
		}
		if len(got) != 1 || got[0].Get(EgressHeader) != c.want {
			t.Fatalf("%s: %s = %q, want %q", c.what, EgressHeader, got[0].Get(EgressHeader), c.want)
		}
	}
	// and a page image (Vision) is a model call like any other
	got = got[:0]
	if _, err := m.Vision(closed, "qwen3.8-27b-fp8", "read", [][]byte{{0xff, 0xd8}}, nil); err != nil {
		t.Fatal(err)
	}
	if got[0].Get(EgressHeader) != EgressClosed {
		t.Fatalf("vision: %q", got[0].Get(EgressHeader))
	}
}

// AM13b: what a round knows about itself is what the gateway said it DID — never what was asked. A
// gateway that says nothing (an older one, or one that does not know this runner) has masked.
func TestWhatTheGatewayDidIsWhatIsNoted(t *testing.T) {
	var got []http.Header
	say := ""
	s := gatewayThatSays(t, func(*http.Request) string { return say }, &got)
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "qwen3.8-27b-fp8", SelfHosted: map[string]bool{"qwen3.8-27b-fp8": true},
		Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	ask := func(ctx context.Context) {
		t.Helper()
		if _, _, err := m.CompleteWith(ctx, []string{"qwen3.8-27b-fp8"}, "s", "u", nil); err != nil {
			t.Fatal(err)
		}
	}
	ctx, calls := withModelCalls(context.Background(), true)
	if calls.masking() != "" {
		t.Fatal("no call yet")
	}
	say = "off"
	ask(ctx)
	ask(ctx)
	if calls.masking() != am.MaskingOff {
		t.Fatalf("two calls sent as typed: %q", calls.masking())
	}
	say = "on"
	ask(ctx)
	if calls.masking() != am.MaskingMixed {
		t.Fatalf("one of three was masked: %q", calls.masking())
	}
	// the runner asked (egress off, in-house) and the gateway did not say it complied: masked
	for _, silent := range []string{"", "on", "OFF ", "yes"} {
		ctx2, calls2 := withModelCalls(context.Background(), true)
		say = silent
		ask(ctx2)
		if calls2.masking() != am.MaskingOn {
			t.Fatalf("gateway said %q: noted %q, want masked", silent, calls2.masking())
		}
	}
	// a call that failed is not a call the gateway answered
	down := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, "x", http.StatusBadGateway) }))
	t.Cleanup(down.Close)
	m.BaseURL = down.URL
	ctx3, calls3 := withModelCalls(context.Background(), true)
	if _, _, err := m.CompleteWith(ctx3, []string{"qwen3.8-27b-fp8"}, "s", "u", nil); err == nil || calls3.masking() != "" {
		t.Fatalf("a failed call: err=%v noted %q", err, calls3.masking())
	}
}

func TestMaskingIsWrittenBesideAStepsOutputAndNowhereElse(t *testing.T) {
	out := stampMasking(json.RawMessage(`{"narrative":{"a":1},"model":"qwen"}`), am.MaskingOff)
	var v struct {
		Masking   string          `json:"masking"`
		Model     string          `json:"model"`
		Narrative json.RawMessage `json:"narrative"`
	}
	if err := json.Unmarshal(out, &v); err != nil || v.Masking != am.MaskingOff || v.Model != "qwen" || string(v.Narrative) != `{"a":1}` {
		t.Fatalf("%s %v", out, err)
	}
	for _, same := range []string{`[1,2]`, `"text"`, `null`, `not json`} {
		if got := stampMasking(json.RawMessage(same), am.MaskingOff); string(got) != same {
			t.Fatalf("%s became %s", same, got)
		}
	}
	if got := stampMasking(json.RawMessage(`{"a":1}`), ""); string(got) != `{"a":1}` {
		t.Fatalf("a step that called no model: %s", got)
	}
}

// AM13b: there is one door to the model client that knows whether a round's data may leave — rc.step
// (and askModel, which answers outside a step). A call made with a context that never went through
// either would be masked — safe, but the round's card could then not say what was done. So: no file of
// the runner calls the model client with a context of its own making.
func TestEveryModelCallIsMadeInsideAStepOrThroughAskModel(t *testing.T) {
	call := regexp.MustCompile(`\.Model\.(CompleteWith|Vision|Complete)\(([^,)]*)`)
	files, _ := filepath.Glob("*.go")
	seen := 0
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range call.FindAllStringSubmatch(string(src), -1) {
			seen++
			if arg := strings.TrimSpace(m[2]); arg != "ctx" {
				t.Errorf("%s: %s(%s …) — a model call takes the context it was given (the step's), not one of its own", f, m[1], arg)
			}
		}
	}
	if seen < 6 {
		t.Fatalf("only %d model calls found: the pattern no longer reads the runner", seen)
	}
}
