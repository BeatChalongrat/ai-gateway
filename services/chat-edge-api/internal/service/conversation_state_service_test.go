// FIX33 §6.1 — the extraction flow against in-memory fakes: batching
// from the OLDEST message, a failed / non-JSON call keeps the existing
// state and does not advance the anchor, the call ships no tools and
// says so, instructions are folded in and persisted.
package service

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type fakeStates struct {
	row *model.ConversationState
}

func (f *fakeStates) Get(_ context.Context, _, _, _ uuid.UUID) (*model.ConversationState, error) {
	if f.row == nil {
		return nil, nil
	}
	cp := *f.row
	return &cp, nil
}
func (f *fakeStates) Upsert(_ context.Context, s *model.ConversationState) error {
	cp := *s
	f.row = &cp
	return nil
}

type fakeMessages struct {
	all      []model.Message
	countErr error
}

func (f *fakeMessages) ListAfter(_ context.Context, _, _, after uuid.UUID, limit int) ([]model.Message, error) {
	start := 0
	if after != uuid.Nil {
		for i := range f.all {
			if f.all[i].ID == after {
				start = i + 1
				break
			}
		}
	}
	end := start + limit
	if end > len(f.all) {
		end = len(f.all)
	}
	if start >= len(f.all) {
		return nil, nil
	}
	return f.all[start:end], nil
}
func (f *fakeMessages) CountMessages(_ context.Context, _, _ uuid.UUID) (int, error) {
	return len(f.all), nil
}

// countErr (FIX117) makes CountAfterUpTo fail, so a test can prove the
// progress reports say "unknown" instead of inventing a total.
func (f *fakeMessages) CountAfterUpTo(_ context.Context, _, _, after uuid.UUID, upto *uuid.UUID) (int, error) {
	if f.countErr != nil {
		return 0, f.countErr
	}
	start := 0
	if after != uuid.Nil {
		for i := range f.all {
			if f.all[i].ID == after {
				start = i + 1
				break
			}
		}
	}
	end := len(f.all)
	if upto != nil {
		end = -1
		for i := range f.all {
			if f.all[i].ID == *upto {
				end = i + 1
				break
			}
		}
		if end < 0 {
			return 0, errors.New("upto not in conversation")
		}
	}
	if end < start {
		return 0, nil
	}
	return end - start, nil
}

type fakeConvs struct{}

func (fakeConvs) GetByID(_ context.Context, _, id uuid.UUID) (*model.Conversation, error) {
	return &model.Conversation{ID: id, ModelID: "gpt-4o-mini", MemoryEnabled: true}, nil
}

type fakeGateway struct {
	calls   []GatewayChatRequest
	replies []string // consumed in order; "" = error
	err     error
}

func (g *fakeGateway) SendChatCompletion(_ context.Context, _ GatewayCallContext, req GatewayChatRequest) (*GatewayChatResponse, error) {
	g.calls = append(g.calls, req)
	if g.err != nil {
		return nil, g.err
	}
	reply := ""
	if len(g.replies) > 0 {
		reply = g.replies[0]
		g.replies = g.replies[1:]
	}
	if reply == "" {
		return nil, errors.New("gateway down")
	}
	return &GatewayChatResponse{
		Choices: []GatewayChatChoice{{Message: GatewayMessage{Role: "assistant", Content: reply}}},
		Usage:   &GatewayChatUsage{PromptTokens: 100, CompletionTokens: 20},
	}, nil
}

type fakeUsage struct{ rows []CompactionUsage }

func (u *fakeUsage) RecordCompactionUsage(_ context.Context, r CompactionUsage) error {
	u.rows = append(u.rows, r)
	return nil
}

func history(n int) []model.Message {
	out := make([]model.Message, 0, n)
	for i := 0; i < n; i++ {
		role := "user"
		if i%2 == 1 {
			role = "assistant"
		}
		out = append(out, model.Message{ID: uuid.New(), Role: role, ContentMasked: fmt.Sprintf("m%d", i)})
	}
	return out
}

func goalJSON(goal string) string {
	return `{"current_goal":"` + goal + `","structured_summary":{"key_context":"k"},"confirmed_decisions":[],"active_artifacts":[],"open_tasks":[],"rejected_approaches":[],"unresolved_questions":[]}`
}

func TestExtraction_StartsFromOldestAndBatches(t *testing.T) {
	msgs := &fakeMessages{all: history(250)}
	gw := &fakeGateway{replies: []string{goalJSON("g1"), goalJSON("g2"), goalJSON("g3")}}
	st := &fakeStates{}
	svc := newConversationStateServiceWith(st, msgs, fakeConvs{}, gw)
	usage := &fakeUsage{}
	svc.SetCompactionUsageRecorder(usage)

	last := msgs.all[249].ID
	out, reached, err := svc.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), &last, ExtractionOptions{Trigger: "auto"})
	if err != nil {
		t.Fatal(err)
	}
	if !reached {
		t.Fatal("coverage must reach the requested message")
	}
	if len(gw.calls) != 3 { // 120 + 120 + 10
		t.Fatalf("expected 3 batched calls, got %d", len(gw.calls))
	}
	// The first call folded the OLDEST messages (m0…), not the newest.
	first := gw.calls[0].Messages[1].Content.(string)
	if !strings.Contains(first, "[user] m0\n") || strings.Contains(first, "[user] m248\n") {
		t.Fatal("the first batch must start at the oldest message")
	}
	if out.SummarizedUpToMessageID == nil || *out.SummarizedUpToMessageID != last {
		t.Fatal("anchor must land on the newest message")
	}
	if len(usage.rows) != 3 || usage.rows[0].Trigger != "auto" || usage.rows[0].PromptTokens != 100 {
		t.Fatalf("every extraction call must be billed separately: %+v", usage.rows)
	}
}

func TestExtraction_CallShipsNoToolsAndSaysSo(t *testing.T) {
	msgs := &fakeMessages{all: history(4)}
	gw := &fakeGateway{replies: []string{goalJSON("g")}}
	svc := newConversationStateServiceWith(&fakeStates{}, msgs, fakeConvs{}, gw)
	if _, _, err := svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{}); err != nil {
		t.Fatal(err)
	}
	req := gw.calls[0]
	if len(req.Tools) != 0 || req.ToolChoice != nil {
		t.Fatal("the extraction call must carry no tools")
	}
	sys := req.Messages[0].Content.(string)
	if !strings.Contains(sys, "Do NOT call any tool") {
		t.Fatal("the system prompt must forbid tool calls in words")
	}
	if sys != ExtractionSystemPrompt() {
		t.Fatal("the system prompt must be the shared asset, byte for byte")
	}
}

func TestExtraction_FailureKeepsExistingStateAndAnchor(t *testing.T) {
	msgs := &fakeMessages{all: history(10)}
	goal := "keep me"
	anchor := msgs.all[3].ID
	existing := &model.ConversationState{CurrentGoal: &goal, SummarizedUpToMessageID: &anchor, StructuredSummary: []byte(`{"key_context":"old"}`)}
	st := &fakeStates{row: existing}

	// (a) gateway error
	gw := &fakeGateway{err: errors.New("boom")}
	svc := newConversationStateServiceWith(st, msgs, fakeConvs{}, gw)
	out, folded, err := svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{})
	if err != nil || folded != 0 {
		t.Fatalf("a failed call is not an error and folds nothing: err=%v folded=%d", err, folded)
	}
	if out == nil || *out.CurrentGoal != "keep me" || *out.SummarizedUpToMessageID != anchor {
		t.Fatal("existing state must be returned untouched")
	}
	if st.row.SummarizedUpToMessageID == nil || *st.row.SummarizedUpToMessageID != anchor {
		t.Fatal("the stored anchor must not move")
	}

	// (b) the model chatted / called a tool instead of JSON
	gw2 := &fakeGateway{replies: []string{"Sure! Here is a summary of the conversation..."}}
	svc2 := newConversationStateServiceWith(st, msgs, fakeConvs{}, gw2)
	out2, folded2, _ := svc2.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{})
	if folded2 != 0 || *out2.CurrentGoal != "keep me" || *st.row.SummarizedUpToMessageID != anchor {
		t.Fatal("a non-JSON reply must keep the state and not advance the anchor")
	}
	if !strings.Contains(string(st.row.StructuredSummary), "old") {
		t.Fatal("the existing summary must survive a failed pass")
	}
}

func TestExtraction_InstructionsFoldedAndPersisted(t *testing.T) {
	msgs := &fakeMessages{all: history(4)}
	gw := &fakeGateway{replies: []string{goalJSON("g"), goalJSON("g2")}}
	st := &fakeStates{}
	svc := newConversationStateServiceWith(st, msgs, fakeConvs{}, gw)
	out, _, err := svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{Instructions: "keep the code snippets", Trigger: "manual"})
	if err != nil {
		t.Fatal(err)
	}
	body := gw.calls[0].Messages[1].Content.(string)
	if !strings.Contains(body, "USER INSTRUCTIONS") || !strings.Contains(body, "keep the code snippets") {
		t.Fatal("instructions must reach the prompt")
	}
	if out.CompactionInstructions != "keep the code snippets" {
		t.Fatal("instructions must persist on the row")
	}
	// A later pass with NO instructions re-uses the stored ones.
	msgs.all = append(msgs.all, history(2)...)
	if _, _, err := svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(gw.calls[1].Messages[1].Content.(string), "keep the code snippets") {
		t.Fatal("stored instructions must apply to later passes")
	}
}

func TestManualBoundary_SetAndRenderedNoteContainsHonesty(t *testing.T) {
	st := &fakeStates{}
	svc := newConversationStateServiceWith(st, &fakeMessages{all: history(2)}, fakeConvs{}, &fakeGateway{})
	b := uuid.New()
	row, err := svc.SetManualCompactBoundary(context.Background(), uuid.New(), uuid.New(), uuid.New(), b, "drop the debugging")
	if err != nil || row.ManualCompactBoundaryMessageID == nil || *row.ManualCompactBoundaryMessageID != b {
		t.Fatal("boundary must be recorded")
	}
	if row.CompactionInstructions != "drop the debugging" {
		t.Fatal("instructions must be recorded with the boundary")
	}
	goal := "g"
	st.row.CurrentGoal = &goal
	text, _ := svc.BuildStateContextWithSources(context.Background(), uuid.New(), uuid.New(), uuid.New())
	if !strings.Contains(text, StateHonestyNote) {
		t.Fatal("the rendered state block must carry the honesty note")
	}
}
