// FIX102 Part C — nothing is left out silently.
//
// The trim proceeds when the summary cannot be written (the stored history
// is untouched and the next turn retries), but the model and the user are
// told, and a successful summary is no longer reported as incomplete.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// ── coverage: what "reached" means ───────────────────────────────────────

func TestFIX102_CoverageIsReachedWhenTheBatchRunsPastTheDroppedMessage(t *testing.T) {
	// The probe's shape: one batch folds everything after the anchor, so the
	// anchor lands on the newest message, well past the last dropped one.
	msgs := &fakeMessages{all: history(24)}
	svc := newConversationStateServiceWith(&fakeStates{}, msgs, fakeConvs{}, &fakeGateway{replies: []string{goalJSON("g")}})
	upto := msgs.all[9].ID
	st, reached, err := svc.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), &upto, ExtractionOptions{})
	if err != nil || st == nil {
		t.Fatalf("err=%v st=%v", err, st)
	}
	if *st.SummarizedUpToMessageID == upto {
		t.Fatal("test setup: the anchor should have run past the dropped message")
	}
	if !reached {
		t.Fatal("a successful extraction that covers the dropped message was reported incomplete — the log line FIX101 read as a credit failure")
	}
}

func TestFIX102_AFailedCallIsNeverCoverage(t *testing.T) {
	msgs := &fakeMessages{all: history(24)}
	goal := "older"
	anchor := msgs.all[3].ID
	st := &fakeStates{row: &model.ConversationState{CurrentGoal: &goal, SummarizedUpToMessageID: &anchor}}
	svc := newConversationStateServiceWith(st, msgs, fakeConvs{}, &fakeGateway{err: errors.New("insufficient_credits")})
	upto := msgs.all[9].ID
	_, reached, err := svc.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), &upto, ExtractionOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if reached {
		t.Fatal("a failed extraction reported coverage")
	}
	// Non-JSON is a failure too.
	svc2 := newConversationStateServiceWith(st, msgs, fakeConvs{}, &fakeGateway{replies: []string{"Sure, here's a summary!"}})
	if _, reached, _ := svc2.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), &upto, ExtractionOptions{}); reached {
		t.Fatal("a chatty non-JSON reply reported coverage")
	}
}

func TestFIX102_NothingAfterTheAnchorIsCovered(t *testing.T) {
	msgs := &fakeMessages{all: history(6)}
	goal := "g"
	anchor := msgs.all[5].ID
	st := &fakeStates{row: &model.ConversationState{CurrentGoal: &goal, SummarizedUpToMessageID: &anchor}}
	gw := &fakeGateway{}
	svc := newConversationStateServiceWith(st, msgs, fakeConvs{}, gw)
	upto := msgs.all[2].ID
	if _, reached, _ := svc.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), &upto, ExtractionOptions{}); !reached || len(gw.calls) != 0 {
		t.Fatalf("reached=%v calls=%d", reached, len(gw.calls))
	}
}

// ── the compiler: status, note, marker, notice ───────────────────────────

func compileWith(t *testing.T, gw *fakeGateway, existing *model.ConversationState) (*CompiledContext, []model.Message) {
	t.Helper()
	h := seedBig(300, 2000) // far over any budget: the middle is dropped
	msgs := &fakeMessages{all: h}
	st := &fakeStates{row: existing}
	svc := newConversationStateServiceWith(st, msgs, fakeConvs{}, gw)
	c := NewContextCompiler(svc)
	cv := conv()
	out, err := c.Compile(context.Background(), CompileInputs{
		Conv: cv, History: h,
		BuildPrefix: func() (string, []ContextSource) { return "S", nil },
		BuildState: func() (string, []ContextSource) {
			return svc.BuildStateContextWithSources(context.Background(), cv.ID, uuid.Nil, uuid.Nil)
		},
	})
	if err != nil || !out.Compacted {
		t.Fatalf("setup: err=%v compacted=%v", err, out.Compacted)
	}
	return out, h
}

func markerOf(t *testing.T, out *CompiledContext) (string, map[string]any) {
	t.Helper()
	for _, s := range out.Sources {
		if s.SourceType == model.SourceTypeCompactedHistory {
			var meta map[string]any
			if err := json.Unmarshal(s.Metadata, &meta); err != nil {
				t.Fatalf("marker metadata: %v", err)
			}
			return s.DisplayLabel, meta
		}
	}
	t.Fatal("no compaction marker")
	return "", nil
}

func TestFIX102_SummaryWrittenIsCompleteAndSaysNothingExtra(t *testing.T) {
	out, _ := compileWith(t, &fakeGateway{replies: []string{goalJSON("plan"), goalJSON("plan"), goalJSON("plan"), goalJSON("plan")}}, nil)
	if out.SummaryStatus != SummaryComplete {
		t.Fatalf("status %q", out.SummaryStatus)
	}
	if out.LossNotice() != nil {
		t.Fatal("a summarized trim is not a loss")
	}
	label, meta := markerOf(t, out)
	if !strings.Contains(label, "summarized") || meta["summary"] != SummaryComplete || meta["code"] != "conversation_compacted" {
		t.Fatalf("label %q meta %v", label, meta)
	}
}

func TestFIX102_NoSummaryCouldBeWrittenIsSaidToTheModelAndTheUser(t *testing.T) {
	out, _ := compileWith(t, &fakeGateway{err: errors.New("insufficient_credits")}, nil)
	if out.SummaryStatus != SummaryFailed {
		t.Fatalf("status %q", out.SummaryStatus)
	}
	if !out.Compacted || out.StateInjected {
		t.Fatal("the trim proceeds, with nothing injected")
	}
	if !strings.Contains(out.SystemPrefix, "it could not be written this turn") {
		t.Fatal("the model must be told there is no summary because it failed")
	}
	n := out.LossNotice()
	if n == nil || n.Summary != SummaryFailed || n.Dropped != out.Dropped || n.Total != 300 || n.Sent != out.MessagesSent {
		t.Fatalf("notice %+v (dropped %d sent %d)", n, out.Dropped, out.MessagesSent)
	}
	label, meta := markerOf(t, out)
	if strings.Contains(label, " summarized") || meta["summary"] != SummaryFailed || int(meta["missing"].(float64)) != out.Dropped {
		t.Fatalf("label %q meta %v", label, meta)
	}
}

func TestFIX102_AnOlderSummaryWithAFailedRefreshIsPartialNotComplete(t *testing.T) {
	h := seedBig(300, 2000)
	goal := "an older goal"
	anchor := h[5].ID
	existing := &model.ConversationState{CurrentGoal: &goal, SummarizedUpToMessageID: &anchor, StructuredSummary: []byte(`{"key_context":"old"}`)}
	out, _ := compileWith(t, &fakeGateway{err: errors.New("provider_timeout")}, existing)
	if out.SummaryStatus != SummaryPartial {
		t.Fatalf("status %q", out.SummaryStatus)
	}
	if !out.StateInjected {
		t.Fatal("the older summary still rides along")
	}
	if strings.Contains(out.SystemPrefix, "A summary of the older part is in") || !strings.Contains(out.SystemPrefix, "does not cover the most recent") {
		t.Fatal("the note must not claim the summary covers everything left out")
	}
	n := out.LossNotice()
	if n == nil || n.Summary != SummaryPartial || n.Sent != out.MessagesSent-2 {
		t.Fatalf("notice %+v", n)
	}
}

func TestFIX102_MemoryOffAndDryRunAreNotLossNotices(t *testing.T) {
	c := NewContextCompiler(nil)
	cv := conv()
	cv.MemoryEnabled = false
	out, _ := c.Compile(context.Background(), CompileInputs{Conv: cv, History: seedBig(300, 2000), BuildPrefix: func() (string, []ContextSource) { return "S", nil }})
	if out.SummaryStatus != SummaryOff || out.LossNotice() != nil {
		t.Fatalf("memory off: %q, notice %v", out.SummaryStatus, out.LossNotice())
	}

	h := seedBig(300, 2000)
	svc := newConversationStateServiceWith(&fakeStates{}, &fakeMessages{all: h}, fakeConvs{}, &fakeGateway{})
	dry, _ := NewContextCompiler(svc).Compile(context.Background(), CompileInputs{Conv: conv(), History: h, DryRun: true, BuildPrefix: func() (string, []ContextSource) { return "S", nil }})
	if dry.SummaryStatus != SummaryPending || dry.LossNotice() != nil || dry.ExtractionTriggered {
		t.Fatalf("dry run: %q, notice %v", dry.SummaryStatus, dry.LossNotice())
	}
}
