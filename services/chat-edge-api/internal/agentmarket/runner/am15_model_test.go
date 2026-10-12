package runner

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// AM15 — a model that cannot be reached is not a model that gave no usable answer. The first has a run
// wait (ErrModelUnreachable); the second has the caller's rules stand in for it, as always (ErrNoModel).
// Each row is what a gateway may send back for one call; `away` is which of the two it must be.

type am15Reply struct {
	status int
	body   string        // sent as is when not 200
	answer string        // the content of a 200
	model  string        // the model a 200 names ("" = the one asked)
	finish string        // finish_reason of a 200 ("" = stop)
	cached bool          // a 200 the gateway says it took from its cache
	wait   time.Duration // before answering
}

func am15Gateway(t *testing.T, reply func(model string, n int) am15Reply) *httptest.Server {
	t.Helper()
	counts := map[string]int{}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Model    string `json:"model"`
			Messages []struct {
				Content any `json:"content"`
			} `json:"messages"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		counts[req.Model]++
		if am15Said != nil && len(req.Messages) > 0 {
			if s, ok := req.Messages[len(req.Messages)-1].Content.(string); ok {
				am15Said(s)
			}
		}
		rp := reply(req.Model, counts[req.Model])
		if rp.wait > 0 {
			select {
			case <-time.After(rp.wait):
			case <-r.Context().Done():
				return
			}
		}
		if rp.status != http.StatusOK {
			w.WriteHeader(rp.status)
			_, _ = w.Write([]byte(rp.body))
			return
		}
		model, finish := rp.model, rp.finish
		if model == "" {
			model = req.Model
		}
		if finish == "" {
			finish = "stop"
		}
		b, _ := json.Marshal(map[string]any{"model": model, "cached": rp.cached, "choices": []any{map[string]any{
			"message": map[string]any{"content": rp.answer}, "finish_reason": finish}}, "usage": map[string]any{"total_tokens": 10}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	t.Cleanup(func() { am15Said = nil })
	return s
}

// am15Said, when set, is told the words of each call's last message.
var am15Said func(string)

func am15Client(url string) *ModelClient {
	return &ModelClient{BaseURL: url, APIKey: "k", Primary: "qwen", Fallback: "deepseek", SelfHosted: map[string]bool{"qwen": true},
		Timeout: 400 * time.Millisecond, HTTP: http.DefaultClient}
}

func TestAModelThatCannotBeReachedIsNotAModelThatGaveNoUsableAnswer(t *testing.T) {
	notJSON := func(string) error { return errors.New("not what was asked for") }
	cases := []struct {
		name     string
		reply    am15Reply
		validate func(string) error
		away     bool
	}{
		// what the test stack's gateway said on 12 Oct 2026 with the in-house model taken away
		{"the gateway gave up waiting for the provider", am15Reply{status: 504, body: `{"error":{"code":"provider_timeout","message":"Provider 'sranai' gave no answer for 'qwen3.8-27b-fp8' within 30 s."}}`}, nil, true},
		{"the provider is away", am15Reply{status: 503, body: `{"error":{"code":"provider_unavailable"}}`}, nil, true},
		{"every model the gateway tried failed", am15Reply{status: 502, body: `{"error":{"code":"all_models_failed"}}`}, nil, true},
		{"a gateway error with no body", am15Reply{status: 500}, nil, true},
		{"the gateway will not let the call through now", am15Reply{status: 429, body: `{"error":{"code":"rate_limited"}}`}, nil, true},
		{"nothing within the call's own time", am15Reply{status: 200, answer: "late", wait: 2 * time.Second}, nil, true},
		{"another model answered in its place", am15Reply{status: 200, answer: `{"ok":true}`, model: "deepseek-v4-flash"}, nil, true},
		// the model was reached: its answer could not be used
		{"its thinking took the whole answer (the gateway's refusal of an empty answer)", am15Reply{status: 502, body: `{"error":{"code":"output_limit_reached"}}`}, nil, false},
		{"the gateway's refusal of an empty completion", am15Reply{status: 502, body: `{"error":{"code":"empty_completion"}}`}, nil, false},
		{"an empty answer", am15Reply{status: 200, answer: "   "}, nil, false},
		{"an answer cut off at the output limit", am15Reply{status: 200, answer: "half an ans", finish: "length"}, nil, false},
		{"an answer the caller's check refuses, twice", am15Reply{status: 200, answer: "prose"}, notJSON, false},
		// the request itself was refused: nothing to wait for
		{"the guardrail refused the request", am15Reply{status: 400, body: `{"error":{"code":"guardrail_blocked"}}`}, nil, false},
		{"the key was refused", am15Reply{status: 401, body: `{"error":{"code":"invalid_api_key"}}`}, nil, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := am15Gateway(t, func(string, int) am15Reply { return tc.reply })
			m := am15Client(s.URL)
			// an agent whose documents may not leave: the in-house model and no other
			_, tried, err := m.CompleteWith(context.Background(), []string{"qwen"}, "s", "u", tc.validate)
			if !errors.Is(err, ErrNoModel) {
				t.Fatalf("no answer is always ErrNoModel to a caller that asks nothing more: %v (tried %v)", err, tried)
			}
			if got := errors.Is(err, ErrModelUnreachable); got != tc.away {
				t.Fatalf("unreachable = %v, want %v (tried %v)", got, tc.away, tried)
			}
			// the scan's reader asks one model, with no other to fall back to
			_, verr := m.Vision(context.Background(), "qwen", "read", [][]byte{[]byte("jpg")}, tc.validate)
			if got := errors.Is(verr, ErrModelUnreachable); verr == nil || got != tc.away {
				t.Fatalf("vision: unreachable = %v, want %v (%v)", got, tc.away, verr)
			}
		})
	}
}

func TestNoGatewayAtAllIsAModelThatCannotBeReached(t *testing.T) {
	s := am15Gateway(t, func(string, int) am15Reply { return am15Reply{status: 200, answer: "x"} })
	url := s.URL
	s.Close() // nothing listens there now
	_, _, err := am15Client(url).CompleteWith(context.Background(), []string{"qwen"}, "s", "u", nil)
	if !errors.Is(err, ErrModelUnreachable) {
		t.Fatalf("a refused connection: %v", err)
	}
}

func TestAModelThatMayStandInStillDoes(t *testing.T) {
	// documents may leave: the in-house model is away and the outside one answers — the answer is used
	s := am15Gateway(t, func(model string, _ int) am15Reply {
		if model == "qwen" {
			return am15Reply{status: 504, body: `{"error":{"code":"provider_timeout"}}`}
		}
		return am15Reply{status: 200, answer: `{"ok":true}`}
	})
	m := am15Client(s.URL)
	c, tried, err := m.CompleteWith(context.Background(), []string{"qwen", "deepseek"}, "s", "u", nil)
	if err != nil || c.Model != "deepseek" || len(tried) != 1 {
		t.Fatalf("the model that may stand in: %+v %v %v", c, tried, err)
	}
	// away and then an answer that cannot be used: a model was reached — rules, not a wait
	s2 := am15Gateway(t, func(model string, _ int) am15Reply {
		if model == "qwen" {
			return am15Reply{status: 504, body: `{"error":{"code":"provider_timeout"}}`}
		}
		return am15Reply{status: 200, answer: "  "}
	})
	_, _, err = am15Client(s2.URL).CompleteWith(context.Background(), []string{"qwen", "deepseek"}, "s", "u", nil)
	if !errors.Is(err, ErrNoModel) || errors.Is(err, ErrModelUnreachable) {
		t.Fatalf("one away, one reached with nothing usable: %v", err)
	}
	// every one of them away: nothing was reached
	s3 := am15Gateway(t, func(string, int) am15Reply {
		return am15Reply{status: 503, body: `{"error":{"code":"provider_unavailable"}}`}
	})
	_, _, err = am15Client(s3.URL).CompleteWith(context.Background(), []string{"qwen", "deepseek"}, "s", "u", nil)
	if !errors.Is(err, ErrModelUnreachable) {
		t.Fatalf("both away: %v", err)
	}
}

func TestARunnerThatIsStoppingHasNotFoundItsModelAway(t *testing.T) {
	// the run's own context ends while the call waits (a shutdown, a lost lease): the run is left as it
	// is for the next runner — it must not be read as "the model is away" and parked with a notice
	s := am15Gateway(t, func(string, int) am15Reply { return am15Reply{status: 200, answer: "late", wait: 3 * time.Second} })
	m := am15Client(s.URL)
	m.Timeout = 5 * time.Second
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	_, err := m.send(ctx, "qwen", []chatMsg{{Role: "user", Content: "u"}}, 0, 16)
	if err == nil || errors.Is(err, ErrModelUnreachable) {
		t.Fatalf("a stopped run: %v", err)
	}
}

func TestTheModelIsAskedAWordToKnowItIsBack(t *testing.T) {
	away := true
	asked := 0
	s := am15Gateway(t, func(string, int) am15Reply {
		asked++
		if away {
			return am15Reply{status: 504, body: `{"error":{"code":"provider_timeout"}}`}
		}
		return am15Reply{status: 200, answer: "  "} // an empty answer is still the model being there
	})
	m := am15Client(s.URL)
	if m.Reachable(context.Background()) {
		t.Fatal("the model is away: not reachable")
	}
	away = false
	if !m.Reachable(context.Background()) {
		t.Fatal("the model answers again — an empty answer too is the model being there")
	}
	if asked != 2 {
		t.Fatalf("one word each time: %d calls", asked)
	}
	if (&ModelClient{}).Reachable(context.Background()) {
		t.Fatal("no model configured is not a model that can be reached")
	}
}

// The runner's /model page: who answers its model calls — asked now, of the model, in words no call
// before it used. /healthz said "model: true" through the hours the in-house model answered nobody.
func TestTheRunnersWordSaysWhoAnsweredAndIsNeverTheCachesAnswer(t *testing.T) {
	reply := am15Reply{status: 200, answer: "ok"}
	var said []string
	s := am15Gateway(t, func(string, int) am15Reply { return reply })
	am15Said = func(words string) { said = append(said, words) }
	m := am15Client(s.URL)
	ctx := context.Background()

	w := m.word(ctx)
	if !w.Answered || w.Served != "qwen" || w.Asked != "qwen" || w.Error != "" || w.At.IsZero() {
		t.Fatalf("the model answers: %+v", w)
	}
	// the gateway keeps an answer for an hour under the same words: each word is asked in new ones
	_ = m.word(ctx)
	if len(said) != 2 || said[0] == said[1] {
		t.Fatalf("two words, never the same sentence twice: %q", said)
	}
	reply = am15Reply{status: 200, answer: "ok", cached: true}
	if w = m.word(ctx); w.Answered || w.Error == "" {
		t.Fatalf("an answer out of the cache is nobody answering now: %+v", w)
	}
	reply = am15Reply{status: 504, body: `{"error":{"code":"provider_timeout","message":"Provider 'sranai' gave no answer for 'qwen' within 30 s."}}`}
	if w = m.word(ctx); w.Answered || w.Served != "" || !strings.Contains(w.Error, "provider_timeout") {
		t.Fatalf("the model is away, said in the gateway's own words: %+v", w)
	}
	reply = am15Reply{status: 200, answer: "ok", model: "deepseek-v4-flash"}
	if w = m.word(ctx); w.Answered || w.Served != "deepseek-v4-flash" {
		t.Fatalf("another model answered in its place: it is named, and the model asked did not answer: %+v", w)
	}
	reply = am15Reply{status: 200, answer: "  "}
	if w = m.word(ctx); !w.Answered || w.Served != "qwen" || w.Error == "" {
		t.Fatalf("an empty answer is the model asked being there: %+v", w)
	}

	// whoever asks from outside is given the last word while it is young: a caller that loops asks the model nothing more
	reply = am15Reply{status: 200, answer: "ok"}
	before := len(said)
	first := m.WordWithin(ctx, time.Minute)
	if again := m.WordWithin(ctx, time.Minute); again.At != first.At || len(said) != before+1 {
		t.Fatalf("one word within the minute: %d call(s)", len(said)-before)
	}
	if _ = m.WordWithin(ctx, 0); len(said) != before+2 {
		t.Fatalf("an old word is asked again: %d call(s)", len(said)-before)
	}
}
