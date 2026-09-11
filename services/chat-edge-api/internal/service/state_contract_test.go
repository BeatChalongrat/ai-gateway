// FIX106 — the summary is ONE contract across two trees.
//
// The system prompt was already byte-pinned (the desktop compares
// compaction-prompt.ts with prompt_compaction_extraction.txt). The rest of the
// request was not, and it had drifted: only chat-edge stated the
// conversation's language, chat-edge cut each message at 500 bytes where the
// desktop cut at 500 characters, and the existing state was written in a
// different key order with different escaping. The rendered
// <conversation_state> block — what the model actually reads instead of the
// folded messages — was pinned by nothing.
//
// testdata/state_contract.json holds inputs and the exact outputs. This test
// checks chat-edge against it; apps/desktop/test/state-contract.test.ts checks
// the desktop against the same file. Regenerate from Go with
// UPDATE_STATE_CONTRACT=1, then the desktop test says whether the desktop
// still agrees.
package service

import (
	"bytes"
	"encoding/json"
	"os"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

const stateContractPath = "testdata/state_contract.json"

type contractState struct {
	CurrentGoal         *string         `json:"current_goal"`
	StructuredSummary   json.RawMessage `json:"structured_summary,omitempty"`
	ConfirmedDecisions  json.RawMessage `json:"confirmed_decisions,omitempty"`
	ActiveArtifacts     json.RawMessage `json:"active_artifacts,omitempty"`
	OpenTasks           json.RawMessage `json:"open_tasks,omitempty"`
	RejectedApproaches  json.RawMessage `json:"rejected_approaches,omitempty"`
	UnresolvedQuestions json.RawMessage `json:"unresolved_questions,omitempty"`
}

func (c *contractState) toModel() *model.ConversationState {
	if c == nil {
		return nil
	}
	return &model.ConversationState{
		CurrentGoal:         c.CurrentGoal,
		StructuredSummary:   c.StructuredSummary,
		ConfirmedDecisions:  c.ConfirmedDecisions,
		ActiveArtifacts:     c.ActiveArtifacts,
		OpenTasks:           c.OpenTasks,
		RejectedApproaches:  c.RejectedApproaches,
		UnresolvedQuestions: c.UnresolvedQuestions,
	}
}

type stateContract struct {
	Bodies []struct {
		Name         string         `json:"name"`
		Instructions string         `json:"instructions"`
		Existing     *contractState `json:"existing"`
		Messages     []struct {
			Role    string `json:"role"`
			Content string `json:"content"`
		} `json:"messages"`
		Body string `json:"body"`
	} `json:"bodies"`
	Renders []struct {
		Name  string        `json:"name"`
		State contractState `json:"state"`
		Block string        `json:"block"`
	} `json:"renders"`
	// FIX107 Part B1 — the <context_note> appended when older messages are
	// left out, from the same asset on both sides.
	Notes []struct {
		Name     string `json:"name"`
		Missing  int    `json:"missing"`
		Kept     int    `json:"kept"`
		HeadKept bool   `json:"head_kept"`
		Status   string `json:"status"`
		Note     string `json:"note"`
	} `json:"notes"`
}

func TestFIX106_StateContract(t *testing.T) {
	raw, err := os.ReadFile(stateContractPath)
	if err != nil {
		t.Fatal(err)
	}
	var c stateContract
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	update := os.Getenv("UPDATE_STATE_CONTRACT") == "1"
	svc := &ConversationStateService{}

	for i := range c.Bodies {
		b := &c.Bodies[i]
		msgs := make([]model.Message, 0, len(b.Messages))
		for _, m := range b.Messages {
			msgs = append(msgs, model.Message{Role: m.Role, ContentMasked: m.Content})
		}
		got := svc.buildExtractionPrompt(b.Existing.toModel(), msgs, b.Instructions)
		switch {
		case update:
			b.Body = got
		case b.Body == "":
			t.Fatalf("%s: no expected body recorded — run with UPDATE_STATE_CONTRACT=1", b.Name)
		case got != b.Body:
			t.Errorf("%s: the extraction body changed:\n--- got ---\n%s\n--- want ---\n%s", b.Name, got, b.Body)
		}
	}
	for i := range c.Renders {
		r := &c.Renders[i]
		got := renderStateBlock(r.State.toModel())
		switch {
		case update:
			r.Block = got
		case r.Block == "":
			t.Fatalf("%s: no expected block recorded — run with UPDATE_STATE_CONTRACT=1", r.Name)
		case got != r.Block:
			t.Errorf("%s: the rendered state block changed:\n--- got ---\n%s\n--- want ---\n%s", r.Name, got, r.Block)
		}
	}
	for i := range c.Notes {
		n := &c.Notes[i]
		headKept := 0
		if n.HeadKept {
			headKept = 2
		}
		got := compactionContextNote(n.Missing, n.Kept, headKept, n.Status)
		switch {
		case update:
			n.Note = got
		case n.Note == "":
			t.Fatalf("%s: no expected note recorded — run with UPDATE_STATE_CONTRACT=1", n.Name)
		case got != n.Note:
			t.Errorf("%s: the context note changed:\n--- got ---\n%s\n--- want ---\n%s", n.Name, got, n.Note)
		}
	}

	if update {
		var buf bytes.Buffer
		enc := json.NewEncoder(&buf)
		enc.SetEscapeHTML(false)
		enc.SetIndent("", "  ")
		if err := enc.Encode(c); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(stateContractPath, buf.Bytes(), 0o644); err != nil {
			t.Fatal(err)
		}
		t.Log("state contract regenerated — now run the desktop's state-contract.test.ts")
	}
}
