package service

// FIX131 E1 — the interrupted-turn sweep.
//
// Before this file there was no sweep at all (two comments referred to an
// "orphan sweep" that did not exist). A turn whose process died before its
// finish callback — a restart, a crash, the kill at the end of a stop grace
// period — or whose finish deliberately wrote nothing (FIX125 M2: the
// conversation was deleted while it ran) kept status 'streaming' for good.
// Measured on live at FIX131: 187 such rows, the oldest from 2026-08-05.
//
// What the sweep does: every Interval (and at start-up) it lists assistant
// rows still 'streaming' that are older than MinAge, asks the run leases
// which of them some process is running, and settles the rest as
// interrupted (status 'error', terminal_cause 'interrupted', error_code
// GENERATION_INTERRUPTED — the ending the resume path already used). The
// partial answer, if any, stays on the row; the pages render the ending as
// "this answer did not finish" with Retry. The UPDATE is guarded, so a row
// a live finish callback settled in the meantime keeps its own ending.
//
// Ownership. A row is settled only when NO process holds its lease, seen
// on two observations at least Confirm apart. One observation is not
// enough: a Redis that restarted without persistence has lost every lease
// until the owners' next renewal (RunLeases.RenewEvery), and a sweep that
// ran in that window would close every live turn at once. Confirm is sized
// above the renewal period, so a live owner has always written its lease
// back by the second look. A lease check that fails (Redis down) settles
// nothing that pass.
//
// One summary line per pass, counts only — never a message id, never text.

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// Sweep defaults (overridable by config).
const (
	InterruptedSweepDefaultInterval = 60 * time.Second
	InterruptedSweepDefaultMinAge   = 2 * time.Minute
	interruptedSweepDefaultLimit    = 500
)

// interruptedTurnRepo is the slice of MessageRepo the sweep uses.
type interruptedTurnRepo interface {
	ListStreamingAssistantIDs(ctx context.Context, olderThan time.Time, limit int) ([]uuid.UUID, error)
	MarkInterruptedMany(ctx context.Context, ids []uuid.UUID) (repository.InterruptedCounts, error)
}

// InterruptedTurnSweep settles streaming rows no live process owns.
type InterruptedTurnSweep struct {
	repo   interruptedTurnRepo
	leases *RunLeases

	// Interval between passes; MinAge is how old a row must be before it
	// is even looked at; Confirm is the minimum gap between the two
	// lease-less observations that settle a row.
	Interval time.Duration
	MinAge   time.Duration
	Confirm  time.Duration
	Limit    int

	now func() time.Time

	mu       sync.Mutex
	suspects map[uuid.UUID]time.Time // first lease-less observation
}

// SweepReport is one pass, in counts.
type SweepReport struct {
	Candidates          int
	Leased              int
	Suspected           int
	Settled             int
	WithContent         int
	DeletedConversation int
	LeaseCheckFailed    bool
}

// NewInterruptedTurnSweep builds the sweep. Zero durations take the
// defaults; Confirm defaults to twice the lease renewal period (never less
// than 30 s).
func NewInterruptedTurnSweep(repo interruptedTurnRepo, leases *RunLeases, interval, minAge time.Duration) *InterruptedTurnSweep {
	if interval <= 0 {
		interval = InterruptedSweepDefaultInterval
	}
	if minAge <= 0 {
		minAge = InterruptedSweepDefaultMinAge
	}
	confirm := 2 * leases.RenewEvery()
	if confirm < 30*time.Second {
		confirm = 30 * time.Second
	}
	return &InterruptedTurnSweep{
		repo: repo, leases: leases,
		Interval: interval, MinAge: minAge, Confirm: confirm, Limit: interruptedSweepDefaultLimit,
		now:      time.Now,
		suspects: map[uuid.UUID]time.Time{},
	}
}

// SetClock is a test hook.
func (s *InterruptedTurnSweep) SetClock(now func() time.Time) { s.now = now }

// RunOnce is one pass. It never settles a row seen lease-less only once.
func (s *InterruptedTurnSweep) RunOnce(ctx context.Context) (SweepReport, error) {
	var rep SweepReport
	now := s.now()
	ids, err := s.repo.ListStreamingAssistantIDs(ctx, now.Add(-s.MinAge), s.Limit)
	if err != nil {
		return rep, err
	}
	rep.Candidates = len(ids)

	s.mu.Lock()
	defer s.mu.Unlock()
	// A row that is no longer a candidate (settled by its finish, or by
	// another instance's sweep) is no longer a suspect either.
	current := make(map[uuid.UUID]bool, len(ids))
	for _, id := range ids {
		current[id] = true
	}
	for id := range s.suspects {
		if !current[id] {
			delete(s.suspects, id)
		}
	}
	if len(ids) == 0 {
		return rep, nil
	}

	alive, err := s.leases.Alive(ctx, ids)
	if err != nil {
		// Cannot tell who owns what: settle nothing, keep the suspects
		// (their clocks keep running from the observation that was real).
		rep.LeaseCheckFailed = true
		return rep, nil
	}
	var settle []uuid.UUID
	for _, id := range ids {
		if alive[id] {
			rep.Leased++
			delete(s.suspects, id)
			continue
		}
		first, seen := s.suspects[id]
		if !seen {
			s.suspects[id] = now
			rep.Suspected++
			continue
		}
		if now.Sub(first) >= s.Confirm {
			settle = append(settle, id)
		} else {
			rep.Suspected++
		}
	}
	if len(settle) == 0 {
		return rep, nil
	}
	counts, err := s.repo.MarkInterruptedMany(ctx, settle)
	if err != nil {
		return rep, err
	}
	for _, id := range settle {
		delete(s.suspects, id)
	}
	rep.Settled = counts.Settled
	rep.WithContent = counts.WithContent
	rep.DeletedConversation = counts.DeletedConversation
	return rep, nil
}

func (s *InterruptedTurnSweep) runAndLog(ctx context.Context, pass string) {
	rep, err := s.RunOnce(ctx)
	if err != nil {
		if ctx.Err() == nil {
			slog.Warn("interrupted_turn_sweep failed", "pass", pass, "err", err.Error())
		}
		return
	}
	slog.Info("interrupted_turn_sweep",
		"pass", pass,
		"candidates", rep.Candidates,
		"leased", rep.Leased,
		"suspected", rep.Suspected,
		"settled", rep.Settled,
		"settled_with_content", rep.WithContent,
		"settled_deleted_conversation", rep.DeletedConversation,
		"lease_check_failed", rep.LeaseCheckFailed,
	)
}

// StartBackground runs a pass at start-up, a confirming pass Confirm later
// (so rows left by the previous process are settled within a minute of the
// start, not an Interval later), and then one every Interval, until ctx
// ends.
func (s *InterruptedTurnSweep) StartBackground(ctx context.Context) {
	s.runAndLog(ctx, "startup")
	select {
	case <-ctx.Done():
		return
	case <-time.After(s.Confirm):
	}
	s.runAndLog(ctx, "startup_confirm")
	t := time.NewTicker(s.Interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.runAndLog(ctx, "periodic")
		}
	}
}
