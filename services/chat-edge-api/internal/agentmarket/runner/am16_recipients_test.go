package runner

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM16 — nothing of a round goes to a recipient the admin did not name.
//
// "Documents may leave" said yes or no, not to whom: with it on, every model of the catalogue was open.
// The owner: the admin chooses which outside providers an agent may reach; "may leave" on with nothing
// ticked means nobody; nothing goes to a provider that is not ticked. The runner holds its half here —
// which model a round may use at all, and that every model call of a round tells the gateway the list —
// and the gateway refuses anything else (backend/tests/test_recipients.py).

// named gives m's catalogue a recipient for each of the outside models given and returns the list that
// names them all — for the tests from before AM16, in which "documents may leave" alone opened them.
func named(m *ModelClient, ids ...string) []string {
	out := []string{}
	for _, id := range ids {
		key := "route:" + id
		found := false
		for i := range m.catalog.models {
			if m.catalog.models[i].ID == id {
				m.catalog.models[i].Recipients = []string{key}
				found = true
			}
		}
		if !found {
			m.catalog.models = append(m.catalog.models, am.CatalogModel{ID: id, Recipients: []string{key}})
		}
		out = append(out, key)
	}
	m.catalog.fetched = time.Now()
	return out
}

func readSource(t *testing.T, file string) string {
	t.Helper()
	b, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestMayLeaveWithNobodyNamedReachesNobody(t *testing.T) {
	m := &ModelClient{BaseURL: "http://unused", APIKey: "k", Primary: inHouse, VisionModel: inHouse, SelfHosted: map[string]bool{inHouse: true}}
	m.catalog.models = []am.CatalogModel{
		{ID: inHouse, SelfHosted: true, Vision: true},
		{ID: "google/gemini", Vision: true, Recipients: []string{"openrouter:google"}},
		{ID: "anthropic/claude", Vision: true, Recipients: []string{"openrouter:anthropic", "openrouter:amazon"}},
		{ID: "openrouter/auto", Vision: true}, // nobody can be said to run it: it has no recipient
	}
	m.catalog.fetched = time.Now()
	for _, c := range []struct {
		what   string
		list   []string
		gemini bool
		claude bool
	}{
		{"nobody named", []string{}, false, false},
		{"a nil list is nobody too", nil, false, false},
		{"Google named", []string{"openrouter:google"}, true, false},
		{"Amazon named: the company that runs Claude", []string{"openrouter:amazon"}, false, true},
		{"the route alone names nobody", []string{"openrouter"}, false, false},
		{"a company alone names nobody", []string{"google", "amazon"}, false, false},
		{"another route's Google is another recipient", []string{"google"}, false, false},
	} {
		rc := &runCtx{r: &Runner{Model: m}, docsMayLeave: true, recipients: c.list}
		if got := rc.mayUse("google/gemini"); got != c.gemini {
			t.Errorf("%s: Gemini may be used = %v, want %v", c.what, got, c.gemini)
		}
		if got := rc.mayRead("google/gemini"); got != c.gemini {
			t.Errorf("%s: Gemini may read a page = %v, want %v", c.what, got, c.gemini)
		}
		if got := rc.mayUse("anthropic/claude"); got != c.claude {
			t.Errorf("%s: Claude may be used = %v, want %v", c.what, got, c.claude)
		}
		// a model nobody can be said to run is never reached, whatever is named
		if rc.mayUse("openrouter/auto") || rc.mayRead("openrouter/auto") || rc.mayUse("not-in-the-catalogue") {
			t.Errorf("%s: a model with no recipient was allowed", c.what)
		}
		// the in-house model is not a recipient: always
		if !rc.mayUse(inHouse) || !rc.mayRead(inHouse) {
			t.Errorf("%s: the in-house model was refused", c.what)
		}
	}
	// named, but the documents may not leave: the list opens nothing by itself
	rc := &runCtx{r: &Runner{Model: m}, docsMayLeave: false, recipients: []string{"openrouter:google"}}
	if rc.mayUse("google/gemini") || rc.mayRead("google/gemini") {
		t.Error("a named recipient was reached while the documents may not leave")
	}
}

// Every model call of a round tells the gateway who it may be sent to — and "nobody" is said as an empty
// list, never as nothing and never as null: the gateway reads an absent key as "did not ask".
func TestEveryModelCallOfARoundCarriesTheList(t *testing.T) {
	var bodies []string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		bodies = append(bodies, string(b))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"model":"` + inHouse + `","choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],"usage":{"total_tokens":3}}`))
	}))
	t.Cleanup(s.Close)
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: inHouse, Timeout: 5 * time.Second, HTTP: http.DefaultClient, MaxTokens: 50,
		SelfHosted: map[string]bool{inHouse: true}}
	ask := func(ctx context.Context) map[string]any {
		t.Helper()
		bodies = nil
		if _, err := m.send(ctx, inHouse, []chatMsg{{Role: "user", Content: "hi"}}, 0, 20); err != nil {
			t.Fatal(err)
		}
		if len(bodies) != 1 {
			t.Fatalf("%d requests", len(bodies))
		}
		var got map[string]any
		if err := json.Unmarshal([]byte(bodies[0]), &got); err != nil {
			t.Fatal(err)
		}
		return got
	}
	// nobody named: the list is there, and it is an empty list
	got := ask(withRecipients(context.Background(), nil))
	if !strings.Contains(bodies[0], `"metadata":{"allowed_recipients":[]}`) {
		t.Fatalf("nobody named is not said as an empty list: %s", bodies[0])
	}
	if meta, _ := got["metadata"].(map[string]any); meta == nil || meta["allowed_recipients"] == nil {
		t.Fatalf("nobody named reached the gateway as null or nothing: %v", got["metadata"])
	}
	// two named: both, as given
	got = ask(withRecipients(context.Background(), []string{"openrouter:google", "deepseek"}))
	list, _ := got["metadata"].(map[string]any)["allowed_recipients"].([]any)
	if len(list) != 2 || list[0] != "openrouter:google" || list[1] != "deepseek" {
		t.Fatalf("the list sent: %v", got["metadata"])
	}
	// a call of the runner's own, outside any round: it says nothing about recipients (unchanged)
	got = ask(context.Background())
	if _, has := got["metadata"]; has {
		t.Fatalf("a call outside a round carries a list: %v", got["metadata"])
	}
}

// The step's context and an answer's context are the two places a round's model calls are made from:
// both must put the round's list on the context, or a call would leave with no list at all.
func TestBothPlacesARoundCallsAModelFromCarryItsList(t *testing.T) {
	for file, want := range map[string]string{
		"pipeline.go": "withModelCalls(withRecipients(ctx, rc.recipients), !rc.docsMayLeave, rc.masked)",
		"jobs.go":     "withModelCalls(withRecipients(ctx, rc.recipients), !rc.docsMayLeave, rc.masked)",
	} {
		src := readSource(t, file)
		if strings.Count(src, "withModelCalls(") != 1 || !strings.Contains(src, want) {
			t.Errorf("%s: a round's model calls are not made under its list of recipients", file)
		}
	}
	if src := readSource(t, "model.go"); !strings.Contains(src, `body.Metadata = map[string]any{"allowed_recipients": named}`) {
		t.Error("model.go: the list is not put on the request")
	}
}
