package runner

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A package's prompt is two messages' parts. The rules are the system
// message; the shape of the answer goes with the question, at the end of the
// user message — the platform removes from an answer whatever repeats the
// system message, and the shape is what an answer is meant to repeat
// (backend/tests/test_am12_prompt_echo.py holds the prompts to this with the
// guard itself).
func TestAM12APromptIsSplitIntoItsRulesAndWhatGoesWithTheQuestion(t *testing.T) {
	system, ask := splitPrompt([]byte("rules, line one\r\n\r\n- a rule\r\n\r\n=== with the question ===\r\nAnswer in this shape\r\n\r\n{\"open\": []}\r\n"))
	if system != "rules, line one\n\n- a rule" {
		t.Fatalf("the rules: %q", system)
	}
	if ask != "\n\nAnswer in this shape\n\n{\"open\": []}" {
		t.Fatalf("what goes with the question: %q", ask)
	}
	// a prompt with no marker is all rules, and nothing is added to the question
	if system, ask := splitPrompt([]byte("only rules\n")); system != "only rules" || ask != "" {
		t.Fatalf("no marker: %q %q", system, ask)
	}
	// the marker is a line of its own: the same words inside a sentence split nothing
	if _, ask := splitPrompt([]byte("the line === with the question === is the marker\n")); ask != "" {
		t.Fatalf("words inside a sentence split the prompt: %q", ask)
	}
	// a marker with nothing under it adds nothing
	if system, ask := splitPrompt([]byte("rules\n=== with the question ===\n\n")); system != "rules" || ask != "" {
		t.Fatalf("an empty second part: %q %q", system, ask)
	}
}

func TestAM12EveryPromptOfAPackageOnTheWebKeepsItsShapeOutOfTheSystemMessage(t *testing.T) {
	manifests, _ := filepath.Glob(filepath.Join("..", "..", "..", "..", "..", "backend", "agent_market", "packages", "*", "agent.json"))
	if len(manifests) < 6 {
		t.Fatalf("packages found: %d", len(manifests))
	}
	checked := 0
	for _, file := range manifests {
		raw, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		var m struct {
			Web *struct {
				Prompts map[string]string `json:"prompts"`
			} `json:"web"`
		}
		if err := json.Unmarshal(raw, &m); err != nil {
			t.Fatal(err)
		}
		if m.Web == nil {
			continue
		}
		for name, rel := range m.Web.Prompts {
			text, err := os.ReadFile(filepath.Join(filepath.Dir(file), filepath.FromSlash(rel)))
			if err != nil {
				t.Fatal(err)
			}
			system, ask := splitPrompt(text)
			if strings.Contains(system, "{") {
				t.Errorf("%s %s: JSON among the rules — the system message must not print the shape of the answer", filepath.Base(filepath.Dir(file)), name)
			}
			i := strings.Index(ask, "{")
			var shape map[string]json.RawMessage
			if i < 0 || json.Unmarshal([]byte(ask[i:]), &shape) != nil || len(shape) == 0 {
				t.Errorf("%s %s: what goes with the question does not end in the answer's shape: %q", filepath.Base(filepath.Dir(file)), name, ask)
			}
			checked++
		}
	}
	if checked < 4 {
		t.Fatalf("prompts checked: %d", checked)
	}
}

// A page no model read because the gateway's content guardrail refused the question for every model is
// told apart from a page no model answered about: the package says which (found on the test stack: the
// gateway refuses the page that carries instructions to its reader, 403 guardrail_blocked).
func TestAM12AQuestionEveryModelWasRefusedIsKnownAsRefused(t *testing.T) {
	blocked := `gateway 403: {"error":{"code":"guardrail_blocked","message":"Request blocked by content guardrail","details":{"rule":"x"},"retryable":false}}`
	cases := []struct {
		name  string
		tried []string
		want  bool
	}{
		{"every model was refused", []string{"model-a: " + blocked, "model-b: " + blocked}, true},
		{"one model was refused, the other did not answer", []string{"model-a: " + blocked, "model-b: gateway 502: upstream"}, false},
		{"an answer that was not JSON", []string{"model-a: ไม่ใช่ JSON object เดียว", "model-a: ไม่ใช่ JSON object เดียว"}, false},
		{"another refusal of the gateway's", []string{`model-a: gateway 403: {"error":{"code":"model_not_allowed"}}`}, false},
		{"nothing was tried", nil, false},
		{"the words in a model's own answer are not the gateway's code", []string{"model-a: guardrail_blocked is not JSON"}, false},
	}
	for _, c := range cases {
		if got := refusedByGuardrail(c.tried); got != c.want {
			t.Errorf("%s: %v", c.name, got)
		}
	}
}
