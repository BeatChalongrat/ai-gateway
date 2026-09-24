package main

// FIX131 E2 — what the process does between SIGTERM and exit.
//
// Kept out of main() so a test can drive the exact sequence with a fake
// signal: the Docker stop that used to lose every turn in flight (10 s
// default grace, a 30 s Shutdown that never waited for detached runs) is
// now:
//
//  1. refuse new turns (503 SERVER_DRAINING + Retry-After; nothing written)
//     while the listener stays open — reading chats and resuming a running
//     stream keep working;
//  2. stop the background loops (retention, sweeps, scheduler);
//  3. let the turns in flight finish, up to CHAT_EDGE_DRAIN_TIMEOUT;
//  4. end the rest as interrupted (row + live stream say
//     GENERATION_INTERRUPTED, text kept), settle any row left behind and
//     release every lease;
//  5. close the HTTP server, flush the run ledger, stop the lease renewal.
//
// A second signal during the drain exits at once.

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// turnDrainer is the slice of MessageService the shutdown drives.
type turnDrainer interface {
	BeginDrain()
	Drain(timeout, grace time.Duration) service.DrainReport
	InFlightTurns() int
	CloseRunLedger()
}

type shutdownPlan struct {
	svc          turnDrainer
	srv          *http.Server
	bgCancel     context.CancelFunc
	bgWG         *sync.WaitGroup
	leaseCancel  context.CancelFunc
	drainTimeout time.Duration
	drainGrace   time.Duration
	// httpTimeout bounds srv.Shutdown once the turns are done.
	httpTimeout time.Duration
	// exit is os.Exit in production (second signal); tests replace it.
	exit func(int)
}

// run blocks until the first signal on stop, then shuts down. It returns
// the drain report once the process may exit.
func (p shutdownPlan) run(stop <-chan os.Signal) service.DrainReport {
	<-stop
	slog.Info("shutdown signal received; draining turns",
		"in_flight_turns", p.svc.InFlightTurns(),
		"drain_timeout", p.drainTimeout.String(),
		"drain_grace", p.drainGrace.String(),
	)
	exit := p.exit
	if exit == nil {
		exit = os.Exit
	}
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-stop:
			slog.Warn("second shutdown signal: exiting without finishing the drain")
			exit(1)
		case <-done:
		}
	}()

	p.svc.BeginDrain()
	if p.bgCancel != nil {
		p.bgCancel()
	}
	rep := p.svc.Drain(p.drainTimeout, p.drainGrace)
	slog.Info("drain finished",
		"in_flight_at_start", rep.InFlightAtStart,
		"streaming_at_start", rep.StreamingAtStart,
		"interrupted", rep.Interrupted,
		"settled_directly", rep.SettledDirectly,
		"still_in_flight", rep.StillInFlight,
		"waited", rep.Waited.Round(time.Millisecond).String(),
	)
	if p.bgWG != nil {
		bgDone := make(chan struct{})
		go func() { p.bgWG.Wait(); close(bgDone) }()
		select {
		case <-bgDone:
		case <-time.After(5 * time.Second):
			slog.Warn("background loops did not stop within 5s; exiting anyway")
		}
	}

	httpTimeout := p.httpTimeout
	if httpTimeout <= 0 {
		httpTimeout = 10 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), httpTimeout)
	defer cancel()
	if err := p.srv.Shutdown(ctx); err != nil {
		slog.Error("http server shutdown error", "err", err)
	} else {
		// Every handler has returned, so nothing records frames any more:
		// flush the durable ledger (the interrupted runs' last frames).
		p.svc.CloseRunLedger()
	}
	if p.leaseCancel != nil {
		p.leaseCancel()
	}
	return rep
}
