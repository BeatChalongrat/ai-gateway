package service

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
)

// FIX117 — compaction progress in raw counts. The reports are what a
// client turns into "62%": they must never go backwards, never say
// "everything" before the fold is really done, never invent a total the
// server could not count, and never be able to break the fold itself.

func progressRun(t *testing.T, msgs *fakeMessages, replies []string, batch int, upto *uuid.UUID, sink func(ExtractionProgress)) (reached bool, err error) {
	t.Helper()
	svc := newConversationStateServiceWith(&fakeStates{}, msgs, fakeConvs{}, &fakeGateway{replies: replies})
	_, reached, err = svc.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), upto, ExtractionOptions{BatchLimit: batch, OnProgress: sink})
	return reached, err
}

func TestFIX117_ProgressCountsFoldedOverTotal_NeverBackwards_NeverFullBeforeDone(t *testing.T) {
	// 12 messages, batches of 5, up to the 11th: three gateway calls.
	msgs := &fakeMessages{all: history(12)}
	upto := msgs.all[10].ID
	var got []ExtractionProgress
	reached, err := progressRun(t, msgs, []string{goalJSON("a"), goalJSON("b"), goalJSON("c")}, 5, &upto, func(p ExtractionProgress) { got = append(got, p) })
	if err != nil || !reached {
		t.Fatalf("reached=%v err=%v", reached, err)
	}
	if len(got) < 3 {
		t.Fatalf("expected a report before the first batch and after each batch, got %d: %+v", len(got), got)
	}
	first := got[0]
	if first.Folded != 0 || !first.TotalKnown || first.Total != 11 || first.Done {
		t.Fatalf("first report must give the denominator up front (0 of 11, not done): %+v", first)
	}
	for i := 1; i < len(got); i++ {
		if got[i].Folded < got[i-1].Folded {
			t.Fatalf("progress went backwards at %d: %+v", i, got)
		}
		if got[i].Total != 11 || !got[i].TotalKnown {
			t.Fatalf("the total changed mid-run: %+v", got)
		}
	}
	for _, p := range got[:len(got)-1] {
		if p.Done {
			t.Fatalf("done reported before the last report: %+v", got)
		}
		if p.Folded >= p.Total {
			t.Fatalf("100%% reported before the fold finished: %+v", got)
		}
	}
	last := got[len(got)-1]
	if !last.Done || !last.Reached || last.Folded != last.Total {
		t.Fatalf("the last report must be done, reached and complete: %+v", last)
	}
	// The middle reports are the batches: 5 then 10 of 11.
	if got[1].Folded != 5 || got[2].Folded != 10 {
		t.Fatalf("expected 5 then 10 folded after the first two batches: %+v", got)
	}
}

func TestFIX117_ProgressTotalUnknownWhenTheCountFails_NoInventedDenominator(t *testing.T) {
	msgs := &fakeMessages{all: history(7), countErr: errors.New("count unavailable")}
	upto := msgs.all[6].ID
	var got []ExtractionProgress
	reached, err := progressRun(t, msgs, []string{goalJSON("a"), goalJSON("b")}, 5, &upto, func(p ExtractionProgress) { got = append(got, p) })
	if err != nil || !reached {
		t.Fatalf("the fold itself must not depend on the count: reached=%v err=%v", reached, err)
	}
	if len(got) == 0 {
		t.Fatal("reports must still flow without a denominator")
	}
	for _, p := range got {
		if p.TotalKnown || p.Total != 0 {
			t.Fatalf("a failed count must be reported as unknown, never as a number: %+v", p)
		}
	}
	if got[len(got)-1].Folded != 7 || !got[len(got)-1].Done {
		t.Fatalf("folded counts still advance without a total: %+v", got)
	}
}

func TestFIX117_AFailedBatchEndsTheReportsAsDoneButNotReached_NotFull(t *testing.T) {
	msgs := &fakeMessages{all: history(12)}
	upto := msgs.all[10].ID
	var got []ExtractionProgress
	reached, err := progressRun(t, msgs, []string{goalJSON("a"), "not json at all"}, 5, &upto, func(p ExtractionProgress) { got = append(got, p) })
	if err != nil || reached {
		t.Fatalf("a failed second batch is not coverage: reached=%v err=%v", reached, err)
	}
	last := got[len(got)-1]
	if !last.Done || last.Reached || last.Folded != 5 || last.Total != 11 {
		t.Fatalf("the last report must say done, not reached, 5 of 11 — not 100%%: %+v", last)
	}
}

func TestFIX117_APanickingSinkNeverBreaksTheFold(t *testing.T) {
	msgs := &fakeMessages{all: history(12)}
	upto := msgs.all[10].ID
	calls := 0
	reached, err := progressRun(t, msgs, []string{goalJSON("a"), goalJSON("b"), goalJSON("c")}, 5, &upto, func(ExtractionProgress) { calls++; panic("frame could not be written") })
	if err != nil || !reached {
		t.Fatalf("the sink failing must not change the fold: reached=%v err=%v", reached, err)
	}
	if calls < 3 {
		t.Fatalf("reports must keep being attempted after a failure, got %d", calls)
	}
}

func TestFIX117_NoSinkMeansNoCountAndNoChangeInBehaviour(t *testing.T) {
	// The count query is only paid for when someone listens.
	msgs := &fakeMessages{all: history(12), countErr: errors.New("must not be called")}
	upto := msgs.all[10].ID
	reached, err := progressRun(t, msgs, []string{goalJSON("a"), goalJSON("b"), goalJSON("c")}, 5, &upto, nil)
	if err != nil || !reached {
		t.Fatalf("reached=%v err=%v", reached, err)
	}
}

func TestFIX117_NothingToFoldIsDoneAtOnce(t *testing.T) {
	// The anchor already covers everything: one report, done, 0 of 0.
	msgs := &fakeMessages{all: history(3)}
	states := &fakeStates{}
	svc := newConversationStateServiceWith(states, msgs, fakeConvs{}, &fakeGateway{replies: []string{goalJSON("a")}})
	last := msgs.all[2].ID
	if _, _, err := svc.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), &last, ExtractionOptions{}); err != nil {
		t.Fatal(err)
	}
	var got []ExtractionProgress
	_, reached, err := svc.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), &last, ExtractionOptions{OnProgress: func(p ExtractionProgress) { got = append(got, p) }})
	if err != nil || !reached {
		t.Fatalf("reached=%v err=%v", reached, err)
	}
	if len(got) == 0 || !got[len(got)-1].Done || got[len(got)-1].Folded != 0 {
		t.Fatalf("expected a done report with nothing folded: %+v", got)
	}
}
