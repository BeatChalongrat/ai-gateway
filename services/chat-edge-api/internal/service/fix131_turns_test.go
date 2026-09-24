package service

// FIX131 — turns that never finish, and a restart that loses none.
//
//   E1  A streaming row is settled as interrupted (error / interrupted /
//       GENERATION_INTERRUPTED, partial text kept) only when NO process
//       holds its run lease, seen on two looks at least Confirm apart; a
//       Redis that lost its keys, a lease check that fails, a live turn of
//       this process or another one, a parked (waiting_user) turn, a fresh
//       row and a finished row are never touched. The live shapes of the
//       187 stuck rows are seeded (throwaway-tenant fixtures with text, the
//       owner's empty rows in deleted conversations).
//   E1  A running turn holds its lease until its row is written; a turn
//       whose conversation was deleted while it ran (FIX125 M2 writes
//       nothing) is left for the sweep, which settles it.
//   E1  The resume path does not settle a row another process leases.
//   E2  While draining, every turn path refuses with ErrServerDraining and
//       writes nothing; a turn in flight finishes normally within the drain;
//       a turn past the deadline ends as interrupted with its text kept; the
//       claim and the lease are released either way.

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func memLeases() (*RunLeases, *MemoryLeaseStore) {
	st := NewMemoryLeaseStore()
	return NewRunLeases(st, "this-instance", 45*time.Second, 15*time.Second), st
}

// fakeSweepRepo is the database half of the sweep, in memory.
type fakeSweepRepo struct {
	mu       sync.Mutex
	rows     map[uuid.UUID]string // id → status
	settled  []uuid.UUID
	listErr  error
	listedAt []time.Time
}

func (f *fakeSweepRepo) ListStreamingAssistantIDs(_ context.Context, olderThan time.Time, _ int) ([]uuid.UUID, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.listedAt = append(f.listedAt, olderThan)
	if f.listErr != nil {
		return nil, f.listErr
	}
	var out []uuid.UUID
	for id, st := range f.rows {
		if st == "streaming" {
			out = append(out, id)
		}
	}
	return out, nil
}

func (f *fakeSweepRepo) MarkInterruptedMany(_ context.Context, ids []uuid.UUID) (repository.InterruptedCounts, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var c repository.InterruptedCounts
	for _, id := range ids {
		if f.rows[id] == "streaming" {
			f.rows[id] = "error"
			f.settled = append(f.settled, id)
			c.Settled++
			c.IDs = append(c.IDs, id)
		}
	}
	return c, nil
}

func (f *fakeSweepRepo) status(id uuid.UUID) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.rows[id]
}

type testClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *testClock) now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *testClock) add(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// ---------------------------------------------------------------------------
// E1 — the sweep's rules (no database)
// ---------------------------------------------------------------------------

func TestFIX131_SweepSettlesAnUnleasedRowOnlyOnTheSecondLook(t *testing.T) {
	leases, store := memLeases()
	orphan, live, foreign := uuid.New(), uuid.New(), uuid.New()
	repo := &fakeSweepRepo{rows: map[uuid.UUID]string{orphan: "streaming", live: "streaming", foreign: "streaming"}}
	clock := &testClock{t: time.Date(2026, 9, 24, 12, 0, 0, 0, time.UTC)}
	store.SetClock(clock.now)
	leases.Acquire(live)                                   // this process runs it
	store.PutForeign(foreign, "other-instance", time.Hour) // another process runs it
	sw := NewInterruptedTurnSweep(repo, leases, time.Minute, 2*time.Minute)
	sw.SetClock(clock.now)

	rep, err := sw.RunOnce(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if rep.Settled != 0 || repo.status(orphan) != "streaming" {
		t.Fatalf("one lease-less look must not settle anything: %+v", rep)
	}
	if rep.Candidates != 3 || rep.Leased != 2 || rep.Suspected != 1 {
		t.Fatalf("first look: %+v", rep)
	}
	clock.add(sw.Confirm - time.Second)
	if rep, _ = sw.RunOnce(context.Background()); rep.Settled != 0 {
		t.Fatalf("a second look sooner than Confirm (%s) settled a row: %+v", sw.Confirm, rep)
	}
	clock.add(2 * time.Second)
	rep, _ = sw.RunOnce(context.Background())
	if rep.Settled != 1 || repo.status(orphan) != "error" {
		t.Fatalf("the unleased row was not settled on the confirming look: %+v", rep)
	}
	if repo.status(live) != "streaming" || repo.status(foreign) != "streaming" {
		t.Fatalf("a leased row was settled: live=%s foreign=%s", repo.status(live), repo.status(foreign))
	}
	if cutoff := repo.listedAt[len(repo.listedAt)-1]; !cutoff.Equal(clock.now().Add(-2 * time.Minute)) {
		t.Fatalf("candidates must be older than MinAge: cutoff %s", cutoff)
	}
}

func TestFIX131_SweepNeverClosesALiveTurnAcrossARedisFlush(t *testing.T) {
	st := NewMemoryLeaseStore()
	clock := &testClock{t: time.Date(2026, 9, 24, 12, 0, 0, 0, time.UTC)}
	st.SetClock(clock.now)
	// The owner is ANOTHER process (its local set is not consulted here).
	owner := NewRunLeases(st, "owner-instance", 45*time.Second, 15*time.Second)
	sweeper := NewRunLeases(st, "sweeper-instance", 45*time.Second, 15*time.Second)
	id := uuid.New()
	owner.Acquire(id)
	repo := &fakeSweepRepo{rows: map[uuid.UUID]string{id: "streaming"}}
	sw := NewInterruptedTurnSweep(repo, sweeper, time.Minute, 2*time.Minute)
	sw.SetClock(clock.now)

	st.Flush() // Redis restarted without persistence: every lease is gone
	if rep, _ := sw.RunOnce(context.Background()); rep.Suspected != 1 || rep.Settled != 0 {
		t.Fatalf("right after the flush: %+v", rep)
	}
	clock.add(owner.RenewEvery())
	if err := owner.RenewOnce(context.Background()); err != nil { // the owner's next renewal
		t.Fatal(err)
	}
	clock.add(sw.Confirm)
	rep, _ := sw.RunOnce(context.Background())
	if rep.Settled != 0 || repo.status(id) != "streaming" {
		t.Fatalf("a live turn was closed after a Redis flush: %+v", rep)
	}
	if rep.Leased != 1 {
		t.Fatalf("the renewed lease was not seen: %+v", rep)
	}
}

func TestFIX131_SweepSettlesNothingWhenLeasesCannotBeChecked(t *testing.T) {
	leases, store := memLeases()
	clock := &testClock{t: time.Now()}
	store.SetClock(clock.now)
	id := uuid.New()
	repo := &fakeSweepRepo{rows: map[uuid.UUID]string{id: "streaming"}}
	sw := NewInterruptedTurnSweep(repo, leases, time.Minute, 2*time.Minute)
	sw.SetClock(clock.now)
	store.Err = errors.New("redis: connection refused")
	for i := 0; i < 3; i++ {
		rep, err := sw.RunOnce(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if !rep.LeaseCheckFailed || rep.Settled != 0 {
			t.Fatalf("pass %d with Redis down: %+v", i, rep)
		}
		clock.add(time.Hour)
	}
	if repo.status(id) != "streaming" {
		t.Fatal("a row was settled while no lease could be checked")
	}
}

// ---------------------------------------------------------------------------
// E1 — against a real database: the live shapes
// ---------------------------------------------------------------------------

func TestFIX131_SweepSettlesTheLiveShapesAndNothingElse(t *testing.T) {
	f := newFix125Fixture(t)
	ctx := context.Background()
	leases, store := memLeases()
	f.s.SetRunLeases(leases)

	type seed struct {
		name, status, content string
		age                   time.Duration
		deleted, leased       bool
		foreignLease          bool
	}
	seeds := []seed{
		// 176 throwaway-tenant fixtures (Aug 5 → Sep 14): partial text.
		{name: "fixture_with_text", status: "streaming", content: "เริ่มตอบแล้วพัง", age: 40 * 24 * time.Hour},
		// the owner's rows: empty, conversation deleted while streaming.
		{name: "owner_deleted_empty", status: "streaming", content: "", age: 2 * 24 * time.Hour, deleted: true},
		// must NOT be touched:
		{name: "fresh_streaming", status: "streaming", content: "", age: 30 * time.Second},
		{name: "leased_here", status: "streaming", content: "กำลังตอบ", age: time.Hour, leased: true},
		{name: "leased_elsewhere", status: "streaming", content: "", age: time.Hour, foreignLease: true},
		{name: "parked_question", status: model.MessageStatusWaitingUser, content: "", age: time.Hour},
		{name: "completed", status: "completed", content: "done", age: time.Hour},
		{name: "errored", status: "error", content: "", age: time.Hour},
	}
	ids := map[string]uuid.UUID{}
	for _, sd := range seeds {
		conv, err := f.cr.Create(ctx, &model.Conversation{TenantID: uuid.New(), UserID: uuid.New(), ModelID: "m", Title: sd.name})
		if err != nil {
			t.Fatal(err)
		}
		m, err := f.mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: conv.TenantID, UserID: conv.UserID,
			Role: "assistant", ContentMasked: sd.content, Status: sd.status, IsActive: true})
		if err != nil {
			t.Fatal(err)
		}
		f.q(`WITH u AS (UPDATE chat.messages_masked SET created_at = now() - make_interval(secs => $2) WHERE id = $1 RETURNING 1) SELECT count(*), 0, 0 FROM u`,
			m.ID, sd.age.Seconds())
		if sd.deleted {
			if err := f.cr.SoftDelete(ctx, conv.TenantID, conv.ID); err != nil {
				t.Fatal(err)
			}
		}
		if sd.leased {
			leases.Acquire(m.ID)
		}
		if sd.foreignLease {
			store.PutForeign(m.ID, "other-instance", time.Hour)
		}
		ids[sd.name] = m.ID
	}

	clock := &testClock{t: time.Now()}
	sw := NewInterruptedTurnSweep(f.mr, leases, time.Minute, 2*time.Minute)
	sw.SetClock(clock.now)
	rep, err := sw.RunOnce(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Settled != 0 {
		t.Fatalf("the first look settled rows: %+v", rep)
	}
	clock.add(sw.Confirm)
	rep, err = sw.RunOnce(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Settled != 2 || rep.WithContent != 1 || rep.DeletedConversation != 1 {
		t.Fatalf("confirming look: %+v, want 2 settled (1 with text, 1 in a deleted conversation)", rep)
	}

	read := func(id uuid.UUID) (content, status, cause, code string) {
		r := f.q(`SELECT content_masked, status, coalesce(terminal_cause,'') || '|' || coalesce(error_code,'') FROM chat.messages_masked WHERE id = $1`, id)
		parts := strings.SplitN(r[2].(string), "|", 2)
		return r[0].(string), r[1].(string), parts[0], parts[1]
	}
	for _, name := range []string{"fixture_with_text", "owner_deleted_empty"} {
		content, status, cause, code := read(ids[name])
		if status != "error" || cause != TerminalCauseInterrupted || code != CodeGenerationInterrupted {
			t.Fatalf("%s: status=%s cause=%s code=%s, want error/interrupted/GENERATION_INTERRUPTED", name, status, cause, code)
		}
		if name == "fixture_with_text" && content != "เริ่มตอบแล้วพัง" {
			t.Fatalf("the partial text was not kept: %q", content)
		}
	}
	want := map[string]string{
		"fresh_streaming": "streaming", "leased_here": "streaming", "leased_elsewhere": "streaming",
		"parked_question": model.MessageStatusWaitingUser, "completed": "completed", "errored": "error",
	}
	for name, st := range want {
		content, status, cause, _ := read(ids[name])
		if status != st || cause != "" {
			t.Fatalf("%s was touched: status=%s cause=%q", name, status, cause)
		}
		if name == "leased_here" && content != "กำลังตอบ" {
			t.Fatalf("%s content changed: %q", name, content)
		}
	}
	// A third pass has nothing left to do.
	clock.add(time.Hour)
	if rep, _ = sw.RunOnce(ctx); rep.Settled != 0 {
		t.Fatalf("a later pass settled more rows: %+v", rep)
	}
}

// ---------------------------------------------------------------------------
// E1 — a running turn and its lease
// ---------------------------------------------------------------------------

func TestFIX131_ARunningTurnHoldsItsLeaseUntilItsRowIsWritten(t *testing.T) {
	f := newFix125Fixture(t)
	leases, _ := memLeases()
	f.s.SetRunLeases(leases)
	hold := make(chan struct{})
	f.gw.setHold(hold)
	ctx := context.Background()

	h, err := f.s.SendStream(ctx, f.in("FIX131 lease"))
	if err != nil {
		t.Fatal(err)
	}
	if !leases.HeldLocally(h.AssistantID) {
		t.Fatal("a streaming row must hold its lease while its turn runs")
	}
	// Age the row far past MinAge: the lease alone keeps the sweep away.
	f.q(`WITH u AS (UPDATE chat.messages_masked SET created_at = now() - interval '3 hours' WHERE id = $1 RETURNING 1) SELECT count(*), 0, 0 FROM u`, h.AssistantID)
	clock := &testClock{t: time.Now()}
	sw := NewInterruptedTurnSweep(f.mr, leases, time.Minute, 2*time.Minute)
	sw.SetClock(clock.now)
	for i := 0; i < 3; i++ {
		if rep, _ := sw.RunOnce(ctx); rep.Settled != 0 || rep.Leased != 1 {
			t.Fatalf("pass %d touched a running turn: %+v", i, rep)
		}
		clock.add(time.Hour)
	}
	close(hold)
	drainHandle(h)
	if leases.HeldLocally(h.AssistantID) || leases.Count() != 0 {
		t.Fatal("the lease must be released once the row holds its ending")
	}
	if r := f.q(`SELECT status, content_masked, 0 FROM chat.messages_masked WHERE id = $1`, h.AssistantID); r[0].(string) != "completed" || r[1].(string) != "ANSWER" {
		t.Fatalf("row after finish: %v", r)
	}
}

func TestFIX131_ATurnWhoseConversationWasDeletedIsSettledByTheSweep(t *testing.T) {
	f := newFix125Fixture(t)
	leases, _ := memLeases()
	f.s.SetRunLeases(leases)
	hold := make(chan struct{})
	f.gw.setHold(hold)
	ctx := context.Background()

	h, err := f.s.SendStream(ctx, f.in("FIX131 deleted while streaming"))
	if err != nil {
		t.Fatal(err)
	}
	if err := f.cr.SoftDelete(ctx, f.tenant, f.conv.ID); err != nil {
		t.Fatal(err)
	}
	close(hold)
	drainHandle(h)
	// FIX125 M2 — the finish wrote nothing into the deleted conversation.
	if r := f.q(`SELECT status, content_masked, 0 FROM chat.messages_masked WHERE id = $1`, h.AssistantID); r[0].(string) != "streaming" || r[1].(string) != "" {
		t.Fatalf("FIX125 M2 changed: %v", r)
	}
	if leases.Count() != 0 {
		t.Fatal("the lease must be released even when the finish wrote nothing")
	}
	f.q(`WITH u AS (UPDATE chat.messages_masked SET created_at = now() - interval '10 minutes' WHERE id = $1 RETURNING 1) SELECT count(*), 0, 0 FROM u`, h.AssistantID)
	clock := &testClock{t: time.Now()}
	sw := NewInterruptedTurnSweep(f.mr, leases, time.Minute, 2*time.Minute)
	sw.SetClock(clock.now)
	_, _ = sw.RunOnce(ctx)
	clock.add(sw.Confirm)
	rep, _ := sw.RunOnce(ctx)
	if rep.Settled != 1 || rep.DeletedConversation != 1 {
		t.Fatalf("sweep: %+v", rep)
	}
	r := f.q(`SELECT status, coalesce(terminal_cause,''), content_masked FROM chat.messages_masked WHERE id = $1`, h.AssistantID)
	if r[0].(string) != "error" || r[1].(string) != TerminalCauseInterrupted || r[2].(string) != "" {
		t.Fatalf("row after the sweep: %v", r)
	}
}

// ---------------------------------------------------------------------------
// E1 — the resume path respects another process's lease
// ---------------------------------------------------------------------------

func TestFIX131_ResumeDoesNotSettleARowAnotherProcessLeases(t *testing.T) {
	f := newFix125Fixture(t)
	leases, store := memLeases()
	f.s.SetRunLeases(leases)
	ctx := context.Background()
	f.addRow(t, "user", "q", "completed")
	asst := f.addRow(t, "assistant", "ครึ่งหนึ่ง", "streaming")
	f.q(`WITH u AS (UPDATE chat.messages_masked SET created_at = now() - interval '1 minute' WHERE id = $1 RETURNING 1) SELECT count(*), 0, 0 FROM u`, asst.ID)

	store.PutForeign(asst.ID, "other-instance", time.Hour)
	if _, err := f.s.ResumeAttach(ctx, f.tenant, f.user, f.conv.ID, asst.ID); !errors.Is(err, ErrResumeUnavailable) {
		t.Fatalf("resume of a row another process runs: err=%v, want ErrResumeUnavailable", err)
	}
	if r := f.q(`SELECT status, 0, 0 FROM chat.messages_masked WHERE id = $1`, asst.ID); r[0].(string) != "streaming" {
		t.Fatalf("the resume settled a leased row: %v", r[0])
	}

	store.Err = errors.New("redis down")
	if _, err := f.s.ResumeAttach(ctx, f.tenant, f.user, f.conv.ID, asst.ID); !errors.Is(err, ErrResumeUnavailable) {
		t.Fatalf("resume with the lease check failing: err=%v, want ErrResumeUnavailable (unknown is alive)", err)
	}
	store.Err = nil

	store.Flush() // the other process is gone and its lease expired
	att, err := f.s.ResumeAttach(ctx, f.tenant, f.user, f.conv.ID, asst.ID)
	if err != nil {
		t.Fatalf("resume of an orphan: %v", err)
	}
	att.Cancel()
	r := f.q(`SELECT status, coalesce(terminal_cause,''), content_masked FROM chat.messages_masked WHERE id = $1`, asst.ID)
	if r[0].(string) != "error" || r[1].(string) != TerminalCauseInterrupted || r[2].(string) != "ครึ่งหนึ่ง" {
		t.Fatalf("orphan after resume: %v", r)
	}
}

// ---------------------------------------------------------------------------
// E2 — the drain
// ---------------------------------------------------------------------------

// drainWithErr drains a handle the way the HTTP handler does: text kept, a
// failed stream finished as an error.
func drainWithErr(h *StreamHandle) (content string, err error) {
	var b strings.Builder
	finish := ""
	for u := range h.Events {
		b.WriteString(u.Delta)
		if u.FinishReason != "" {
			finish = u.FinishReason
		}
		if u.Err != nil {
			err = u.Err
		}
	}
	if err != nil && finish == "" {
		finish = "error"
	}
	h.FinishWith(b.String(), "", finish, nil, TerminalInfo{})
	return b.String(), err
}

func TestFIX131_DrainingRefusesEveryNewTurnAndWritesNothing(t *testing.T) {
	f := newFix125Fixture(t)
	leases, _ := memLeases()
	f.s.SetRunLeases(leases)
	ctx := context.Background()
	f.addRow(t, "user", "q", "completed")
	asst := f.addRow(t, "assistant", "a", "completed")
	f.s.BeginDrain()

	if _, err := f.s.SendStream(ctx, f.in("new")); !errors.Is(err, ErrServerDraining) {
		t.Fatalf("send while draining: %v", err)
	}
	if _, err := f.s.SendNonStream(ctx, f.in("new, non-stream")); !errors.Is(err, ErrServerDraining) {
		t.Fatalf("non-stream send while draining: %v", err)
	}
	if _, err := f.s.Regenerate(ctx, RegenerateInput{TenantID: f.tenant, UserID: f.user, ConversationID: f.conv.ID, MessageID: asst.ID, RequestID: uuid.NewString()}); !errors.Is(err, ErrServerDraining) {
		t.Fatalf("regenerate while draining: %v", err)
	}
	if u := f.countRows(t, "user"); u != 1 {
		t.Fatalf("a refused turn wrote a user row: %d", u)
	}
	if a := f.countRows(t, "assistant"); a != 1 {
		t.Fatalf("a refused turn wrote an assistant row: %d", a)
	}
	if r := f.q(`SELECT status, 0, 0 FROM chat.messages_masked WHERE id = $1`, asst.ID); r[0].(string) != "completed" {
		t.Fatalf("a refused regenerate retired the old answer: %v", r[0])
	}
	if n := f.gw.calls(); n != 0 {
		t.Fatalf("a refused turn reached the gateway: %d calls", n)
	}
}

func TestFIX131_DrainLetsATurnInFlightFinish(t *testing.T) {
	f := newFix125Fixture(t)
	leases, _ := memLeases()
	f.s.SetRunLeases(leases)
	hold := make(chan struct{})
	f.gw.setHold(hold)
	ctx := context.Background()
	h, err := f.s.SendStream(ctx, f.in("FIX131 finish during drain"))
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan string, 1)
	go func() { c, _ := drainWithErr(h); done <- c }()

	f.s.BeginDrain()
	time.AfterFunc(300*time.Millisecond, func() { close(hold) })
	rep := f.s.Drain(10*time.Second, 2*time.Second)
	if rep.InFlightAtStart != 1 || rep.Interrupted != 0 || rep.StillInFlight != 0 {
		t.Fatalf("drain report: %+v", rep)
	}
	if c := <-done; c != "ANSWER" {
		t.Fatalf("content %q", c)
	}
	if r := f.q(`SELECT status, coalesce(terminal_cause,''), 0 FROM chat.messages_masked WHERE id = $1`, h.AssistantID); r[0].(string) != "completed" || r[1].(string) != TerminalCauseCompleted {
		t.Fatalf("a turn that finished within the drain: %v", r)
	}
	if leases.Count() != 0 || f.s.InFlightTurns() != 0 {
		t.Fatalf("leases=%d in-flight=%d after the drain", leases.Count(), f.s.InFlightTurns())
	}
}

func TestFIX131_DrainInterruptsATurnPastTheDeadlineAndKeepsItsText(t *testing.T) {
	f := newFix125Fixture(t)
	leases, _ := memLeases()
	f.s.SetRunLeases(leases)
	hold := make(chan struct{}) // never released: the provider is still writing
	f.gw.setHold(hold)
	t.Cleanup(func() { close(hold) })
	ctx := context.Background()
	h, err := f.s.SendStream(ctx, f.in("FIX131 cut at the deadline"))
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan string, 1)
	go func() { c, _ := drainWithErr(h); done <- c }()
	time.Sleep(200 * time.Millisecond) // the first chunk has streamed

	f.s.BeginDrain()
	rep := f.s.Drain(300*time.Millisecond, 5*time.Second)
	if rep.Interrupted != 1 || rep.StillInFlight != 0 {
		t.Fatalf("drain report: %+v", rep)
	}
	if c := <-done; c != "ANSWER" {
		t.Fatalf("the partial text was lost: %q", c)
	}
	if !h.Interrupted() || h.UserCancelled() {
		t.Fatalf("interrupted=%v userCancelled=%v", h.Interrupted(), h.UserCancelled())
	}
	r := f.q(`SELECT status, coalesce(terminal_cause,'') || '|' || coalesce(error_code,''), content_masked FROM chat.messages_masked WHERE id = $1`, h.AssistantID)
	if r[0].(string) != "error" || r[1].(string) != "interrupted|GENERATION_INTERRUPTED" || r[2].(string) != "ANSWER" {
		t.Fatalf("row of a turn cut by the drain: %v", r)
	}
	if leases.Count() != 0 || f.s.InFlightTurns() != 0 {
		t.Fatalf("leases=%d in-flight=%d after the drain", leases.Count(), f.s.InFlightTurns())
	}
}

func TestFIX131_DrainSettlesARowWhoseFinishNeverRan(t *testing.T) {
	f := newFix125Fixture(t)
	leases, _ := memLeases()
	f.s.SetRunLeases(leases)
	hold := make(chan struct{})
	f.gw.setHold(hold)
	t.Cleanup(func() { close(hold) })
	ctx := context.Background()
	// A handle nobody drains: its finish never runs (a handler stuck on a
	// dead write, a tool that ignores cancellation).
	h, err := f.s.SendStream(ctx, f.in("FIX131 stuck"))
	if err != nil {
		t.Fatal(err)
	}
	f.s.BeginDrain()
	rep := f.s.Drain(100*time.Millisecond, 300*time.Millisecond)
	if rep.SettledDirectly != 1 {
		t.Fatalf("drain report: %+v", rep)
	}
	r := f.q(`SELECT status, coalesce(terminal_cause,''), 0 FROM chat.messages_masked WHERE id = $1`, h.AssistantID)
	if r[0].(string) != "error" || r[1].(string) != TerminalCauseInterrupted {
		t.Fatalf("a row the drain could not see finish: %v", r)
	}
	if leases.Count() != 0 {
		t.Fatalf("leases left held: %d", leases.Count())
	}
}
