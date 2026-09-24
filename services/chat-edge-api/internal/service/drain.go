package service

// FIX131 E2 — a restart that does not lose the turns in flight.
//
// Before: SIGTERM → srv.Shutdown(30 s) → exit. Docker's default stop grace
// is 10 s, so the kill came first; a turn still streaming died with the
// process and its row said 'streaming' for good (3 rows per restart in the
// NIGHT3 chaos run). The HTTP listener also closed at once, so a send
// during the stop was a connection error the page could not tell from a
// dead network.
//
// Now (main.go drives it):
//
//  1. BeginDrain — new turns (send, regenerate, edit, a question's answer)
//     are refused BEFORE anything is written, with ErrServerDraining; the
//     handler answers 503 SERVER_DRAINING + Retry-After and the web page
//     re-sends the same request (same idempotency key) once the new
//     process is up. Everything else — reading chats, resuming a stream
//     that is running — keeps working through the drain.
//  2. Drain(timeout) — the turns in flight get up to CHAT_EDGE_DRAIN_TIMEOUT
//     (default 60 s) to finish normally.
//  3. What is still running then is interrupted: its upstream call is
//     cancelled and it ends as an INTERRUPTED turn — the row says error /
//     interrupted / GENERATION_INTERRUPTED, the live stream's last frame
//     says the same, the partial text is kept. A row whose finish did not
//     run within the grace is settled directly, and every lease is
//     released, so the next process's sweep has nothing to guess about.
//
// docker-compose.webchat.yml sets stop_grace_period above drain + grace.
//
// Vendor practice: a deploy stops routing new requests to the old worker,
// lets in-flight responses finish within a bound, and marks the rest
// incomplete; the client retries a refused request with backoff.

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/google/uuid"
)

// ErrServerDraining — the process is shutting down and takes no new turn.
// Nothing was written. The handler answers 503 SERVER_DRAINING.
var ErrServerDraining = errors.New("message: the server is restarting and takes no new turns")

// drainPoll is how often Drain looks at the in-flight count. A var so the
// tests can shorten it.
var drainPoll = 50 * time.Millisecond

// SetRunLeases wires the run leases (FIX131 E1). Nil keeps the pre-FIX131
// behaviour: no lease is held and every broker miss is settled by age.
func (s *MessageService) SetRunLeases(l *RunLeases) { s.leases = l }

// RunLeases is the wired lease set (nil when none).
func (s *MessageService) RunLeases() *RunLeases { return s.leases }

// leaseRun takes the lease for a streaming row; the returned release is
// idempotent and nil-safe.
func (s *MessageService) leaseRun(msgID uuid.UUID) func() {
	if s.leases == nil {
		return func() {}
	}
	return s.leases.Acquire(msgID)
}

// leasedElsewhere reports whether a streaming row this process does not
// run may still be running somewhere: a live lease, or a lease check that
// failed (unknown is treated as alive — never close what might be live).
func (s *MessageService) leasedElsewhere(ctx context.Context, msgID uuid.UUID) bool {
	if s.leases == nil {
		return false
	}
	alive, err := s.leases.Alive(ctx, []uuid.UUID{msgID})
	if err != nil {
		slog.Warn("resume: lease check failed; not settling the row", "message_id", msgID.String(), "err", err.Error())
		return true
	}
	return alive[msgID]
}

// BeginDrain refuses every new turn from now on.
func (s *MessageService) BeginDrain() { s.draining.Store(true) }

// Draining reports whether BeginDrain was called.
func (s *MessageService) Draining() bool { return s.draining.Load() }

// InFlightTurns is the number of turns holding a conversation right now.
func (s *MessageService) InFlightTurns() int {
	n := 0
	s.turns.Range(func(_, _ any) bool { n++; return true })
	return n
}

// DrainReport is what a Drain did, in counts.
type DrainReport struct {
	InFlightAtStart  int
	StreamingAtStart int
	// Interrupted is the number of running generations cut at the deadline.
	Interrupted int
	// SettledDirectly — rows still unsettled after the grace, settled here.
	SettledDirectly int
	// StillInFlight — turns that had not returned when Drain gave up.
	StillInFlight int
	Waited        time.Duration
}

// Drain waits up to timeout for the turns in flight to end, interrupts the
// ones still running, waits up to grace for them to write their ending,
// settles any row left unsettled, and releases every lease. BeginDrain must
// have been called first (otherwise new turns keep arriving).
func (s *MessageService) Drain(timeout, grace time.Duration) DrainReport {
	start := time.Now()
	rep := DrainReport{InFlightAtStart: s.InFlightTurns(), StreamingAtStart: s.leases.Count()}
	deadline := start.Add(timeout)
	for s.InFlightTurns() > 0 && time.Now().Before(deadline) {
		time.Sleep(drainPoll)
	}
	if s.InFlightTurns() == 0 && s.leases.Count() == 0 {
		rep.Waited = time.Since(start)
		return rep
	}

	// The deadline passed. Cut what is still generating; each one ends as
	// an interrupted turn through its own finish (StreamHandle.FinishWith).
	s.activeStreams.Range(func(_, v any) bool {
		reg, ok := v.(*streamRegistration)
		if !ok {
			return true
		}
		reg.markInterrupted()
		reg.cancel()
		rep.Interrupted++
		return true
	})
	graceEnd := time.Now().Add(grace)
	for (s.InFlightTurns() > 0 || s.leases.Count() > 0) && time.Now().Before(graceEnd) {
		time.Sleep(drainPoll)
	}

	// A row whose finish did not run in time: settle it here, guarded, so
	// it never outlives this process as 'streaming'.
	if held := s.leases.Held(); len(held) > 0 && s.messages != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		counts, err := s.messages.MarkInterruptedMany(ctx, held)
		cancel()
		if err != nil {
			slog.Warn("drain: could not settle rows still streaming; the next start-up sweep will", "rows", len(held), "err", err.Error())
		}
		rep.SettledDirectly = counts.Settled
	}
	s.leases.ReleaseAll()
	rep.StillInFlight = s.InFlightTurns()
	rep.Waited = time.Since(start)
	return rep
}

// CloseRunLedger flushes the durable run-ledger mirror (the interrupted
// runs' terminal frames included) so a resume after the restart replays
// them. Safe to call when no mirror is wired.
func (s *MessageService) CloseRunLedger() {
	if s.broker == nil {
		return
	}
	s.broker.mu.Lock()
	st := s.broker.store
	s.broker.mu.Unlock()
	if st != nil {
		st.Close()
	}
}

// markInterrupted / isInterrupted (FIX131 E2) — the drain cut this run at
// its deadline. Distinct from a person's Stop: the ending is
// interrupted, not stopped_by_user.
func (r *streamRegistration) markInterrupted() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.interrupted = true
}

func (r *streamRegistration) isInterrupted() bool {
	if r == nil {
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.interrupted
}

// interruptedEnding is how a run the drain cut ends, on the row and on the
// stream: an error, cause interrupted, code GENERATION_INTERRUPTED — the
// same ending the sweep and the resume path write for a run whose process
// died.
func interruptedEnding() TerminalInfo {
	return TerminalInfo{Cause: TerminalCauseInterrupted, ErrorCode: CodeGenerationInterrupted}
}
