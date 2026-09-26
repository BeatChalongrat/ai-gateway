package runner

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// Runner claims runs and executes them. Its concurrency (Slots) is its own
// queue lane: chat never waits for an agent slot, and an agent run never
// takes a chat slot.
type Runner struct {
	Store  *agentmarket.Store
	Files  storage.Backend
	Exec   *Executor
	Model  *ModelClient
	Engine *Engine
	// Converter is the doc-converter service (AM2): a PDF is the finished
	// .docx converted by LibreOffice; nil or unconfigured means no PDF.
	Converter     *Converter
	Log           *slog.Logger
	ID            string
	Version       string
	SandboxImage  string
	Slots         int
	Lease         time.Duration
	Poll          time.Duration
	CreditsPerUSD float64
	// SandboxCreditsPerSecond charges compute time against the install budget.
	SandboxCreditsPerSecond float64
	// SandboxReady is false when the sandbox could not start: installs are
	// refused (no capabilities) and runs fail with a clear reason.
	SandboxReady bool
	// SandboxCheck, when set, is retried every SandboxRecheck (default 15 s)
	// while the sandbox is not ready, so a runner that started before Docker
	// had finished booting (AM2: the image check timed out right after a
	// Docker restart) recovers by itself instead of refusing every run until
	// someone restarts it.
	SandboxCheck   func(context.Context) error
	SandboxRecheck time.Duration
	Now            func() time.Time
	// Retention (AM2): 0 means the default (48 h, 7 days, 30 days).
	StagedTTL, ScratchTTL, RemovedTTL time.Duration
	// Reaper removes the sandbox containers a killed runner left behind; nil
	// means the default one built from Exec's sandbox (always on).
	Reaper *Reaper

	engines   engineCache
	running   atomic.Int32
	started   time.Time
	instance  string
	sandboxOK atomic.Bool
}

// Sandbox reports whether the compute sandbox is usable now: ready at start,
// or found ready by a later SandboxCheck.
func (r *Runner) Sandbox() bool { return r.SandboxReady || r.sandboxOK.Load() }

// watchSandbox retries SandboxCheck until it passes, then records the sandbox
// as ready and heartbeats so the new capabilities are visible at once.
func (r *Runner) watchSandbox(ctx context.Context, onReady func()) {
	if r.Sandbox() || r.SandboxCheck == nil {
		return
	}
	every := r.SandboxRecheck
	if every <= 0 {
		every = 15 * time.Second
	}
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		if err := r.SandboxCheck(ctx); err != nil {
			continue
		}
		r.sandboxOK.Store(true)
		r.Log.Info("sandbox available again — installs and runs accepted")
		if onReady != nil {
			onReady()
		}
		return
	}
}

// Register stores this runner's engine (runs pinned to it load it later)
// and writes every package that is a new version to agent_definitions
// (engines.go: a released version is never overwritten).
func (r *Runner) Register(ctx context.Context) error {
	if err := r.storeEngine(ctx); err != nil {
		r.Log.Warn("engine not stored — runs pinned to this engine need this runner", "err", err)
	}
	return r.register(ctx)
}

// Capabilities is what this runner can bind abstract capabilities to.
func (r *Runner) Capabilities() map[string]agentmarket.Binding {
	caps := map[string]agentmarket.Binding{}
	eng := r.Engine.Checksum[:12]
	if r.Sandbox() {
		img := r.SandboxImage
		caps["spreadsheet.read.xlsx"] = agentmarket.Binding{Capability: "spreadsheet.read.xlsx", Impl: "sandbox:openpyxl", Version: img}
		caps["compute.python.decimal"] = agentmarket.Binding{Capability: "compute.python.decimal", Impl: "sandbox:python3.12+decimal", Version: img}
		caps["document.read.pdf"] = agentmarket.Binding{Capability: "document.read.pdf", Impl: "sandbox:pdfplumber", Version: img}
		caps["deliverable.report.docx"] = agentmarket.Binding{Capability: "deliverable.report.docx", Impl: "engine:report_docx+docx_generator.py", Version: eng}
		caps["deliverable.deck.pptx"] = agentmarket.Binding{Capability: "deliverable.deck.pptx", Impl: "engine:deck_pptx+pptx_generator.py", Version: eng}
		caps["deliverable.table.xlsx"] = agentmarket.Binding{Capability: "deliverable.table.xlsx", Impl: "engine:table_xlsx", Version: eng}
		// AM2: invoices and receipts read into fields with boxes and confidence
		// (text layer, e-Tax XML, Tesseract tha+eng on page images).
		caps["document.extract.e2"] = agentmarket.Binding{Capability: "document.extract.e2", Impl: "engine:e2+tesseract", Version: eng + "|" + img}
	}
	// A PDF is the finished report converted by LibreOffice (doc-converter).
	if r.Converter.Available() {
		caps["deliverable.pdf"] = agentmarket.Binding{Capability: "deliverable.pdf", Impl: "doc-converter:libreoffice", Version: eng}
	}
	// A vision model reads scans and photos — only one that runs in the
	// organisation (documents never go to an outside provider).
	if v := r.Model.VisionModel; v != "" && r.Model.SelfHosted[v] {
		vd, _ := json.Marshal(map[string]any{"model": v, "self_hosted": true})
		caps["model.vision"] = agentmarket.Binding{Capability: "model.vision", Impl: "gateway", Version: v, Detail: vd}
	}
	// A model is optional at run time (rules write the narrative when none
	// answers), but the capability says which models are bound.
	detail, _ := json.Marshal(map[string]any{"primary": r.Model.Primary, "fallback": r.Model.Fallback,
		"self_hosted": r.Model.SelfHosted})
	caps["model.text"] = agentmarket.Binding{Capability: "model.text", Impl: "gateway", Version: r.Model.Primary + "|" + r.Model.Fallback, Detail: detail}
	return caps
}

// housekeeping is the retention pass (AM2, agentmarket/retention.go): at
// start, then every hour — uploads never attached after StagedTTL (48 h), a
// failed or cancelled run's scratch after ScratchTTL (7 days), a removed
// install after RemovedTTL (30 days, like a deleted chat), and the expired
// rows of the duplicate index.
func (r *Runner) housekeeping(ctx context.Context) {
	staged, scratch, removed := orDefault(r.StagedTTL, 48*time.Hour), orDefault(r.ScratchTTL, 7*24*time.Hour),
		orDefault(r.RemovedTTL, 30*24*time.Hour)
	t := time.NewTicker(time.Hour)
	defer t.Stop()
	for {
		if c, err := r.Store.Retention(ctx, r.Files, staged, scratch, removed); err != nil {
			if ctx.Err() == nil {
				r.Log.Warn("retention pass failed", "err", err)
			}
		} else if c != (agentmarket.RetentionCounts{}) {
			r.Log.Info("retention pass", "staged_files", c.StagedFiles, "scratch_runs", c.ScratchRuns,
				"scratch_objects", c.ScratchObjects, "removed_installs", c.RemovedInstalls, "removed_objects", c.RemovedObjects,
				"index_rows", c.IndexRows)
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

func orDefault(d, def time.Duration) time.Duration {
	if d > 0 {
		return d
	}
	return def
}

func (r *Runner) heartbeat(ctx context.Context) {
	caps, _ := json.Marshal(r.Capabilities())
	if err := r.Store.Heartbeat(ctx, r.ID, r.Version, r.started, caps, int(r.running.Load()), r.Slots); err != nil {
		r.Log.Warn("heartbeat failed", "err", err)
	}
}

// Start runs until ctx ends. It returns after in-flight runs have stopped
// (their leases are left to expire, so a restarted runner resumes them).
func (r *Runner) Start(ctx context.Context) error {
	r.started = time.Now()
	r.instance = uuid.NewString()
	if r.Now == nil {
		r.Now = time.Now
	}
	if err := r.Register(ctx); err != nil {
		return err
	}
	r.heartbeat(ctx)
	r.startReaper(ctx)
	go r.housekeeping(ctx)
	go r.watchSandbox(ctx, func() { r.heartbeat(ctx) })
	go func() {
		t := time.NewTicker(10 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				r.heartbeat(ctx)
			}
		}
	}()
	slots := make(chan struct{}, r.Slots)
	done := make(chan struct{})
	var inflight atomic.Int32
	for {
		select {
		case <-ctx.Done():
			for inflight.Load() > 0 {
				time.Sleep(100 * time.Millisecond)
			}
			close(done)
			return nil
		case slots <- struct{}{}:
		}
		run, err := r.Store.ClaimRun(ctx, r.ID, r.Lease)
		if err != nil {
			<-slots
			if !errors.Is(err, agentmarket.ErrNotFound) && ctx.Err() == nil {
				r.Log.Warn("claim failed", "err", err)
			}
			select {
			case <-ctx.Done():
			case <-time.After(r.Poll):
			}
			continue
		}
		inflight.Add(1)
		r.running.Add(1)
		go func(run agentmarket.Run) {
			defer func() {
				<-slots
				inflight.Add(-1)
				r.running.Add(-1)
				if p := recover(); p != nil {
					r.Log.Error("run panicked", "run", run.ID, "panic", p)
					_ = r.Store.Finish(context.Background(), run.ID, r.ID, agentmarket.RunFailed, "internal_error")
				}
			}()
			r.execute(ctx, run)
		}(run)
	}
}

// startReaper removes what a killed runner left behind before the first
// claim, then keeps sweeping (AM1c).
func (r *Runner) startReaper(ctx context.Context) {
	if r.Exec == nil || r.Exec.SB == nil {
		return
	}
	if r.Reaper == nil {
		r.Reaper = NewReaper(r.Exec.SB, r.Exec.Limits.Wall, r.Log)
	}
	me := Identity{Runner: r.ID, Instance: r.instance}
	if n := r.Reaper.Sweep(ctx, me, r.Exec.InFlight); n > 0 {
		r.Log.Info("sandbox containers left by a previous runner removed", "count", n)
	}
	if r.Reaper.Every <= 0 {
		return
	}
	go func() {
		t := time.NewTicker(r.Reaper.Every)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				r.Reaper.Sweep(ctx, me, r.Exec.InFlight)
			}
		}
	}()
}

// execute holds the lease while the run works, and cancels the work the
// moment a cancel is requested or the lease is lost.
func (r *Runner) execute(parent context.Context, run agentmarket.Run) {
	parent = withSandboxLabels(parent, map[string]string{LabelRun: run.ID.String(), LabelRunner: r.ID, LabelInstance: r.instance})
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	var cancelled atomic.Bool
	go func() {
		t := time.NewTicker(r.Lease / 3)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				ok, cancelReq, err := r.Store.RenewLease(context.Background(), run.ID, r.ID, r.Lease)
				if err == nil && (!ok || cancelReq) {
					cancelled.Store(cancelReq)
					cancel()
					return
				}
			}
		}
	}()
	log := r.Log.With("run", run.ID, "kind", run.Kind, "attempt", run.Attempts)
	log.Info("run claimed")
	var err error
	switch run.Kind {
	case agentmarket.KindRun, agentmarket.KindSelfTest:
		err = r.pipeline(ctx, run, log)
	case agentmarket.KindRender:
		err = r.renderJob(ctx, run, log)
	case agentmarket.KindReply:
		err = r.replyJob(ctx, run, log)
	case agentmarket.KindMappingPreview:
		err = r.previewJob(ctx, run, log)
	default:
		err = r.Store.Finish(context.Background(), run.ID, r.ID, agentmarket.RunFailed, "unknown_kind")
	}
	if err != nil && ctx.Err() != nil {
		// Cancelled (or the lease went away): record cancel if it was asked for.
		if cancelled.Load() {
			bg := context.Background()
			_ = r.Store.Finish(bg, run.ID, r.ID, agentmarket.RunCancelled, "cancelled_by_user")
			r.syncProgress(bg, run.ID)
		}
		log.Info("run stopped", "err", err)
		return
	}
	if err != nil {
		log.Error("run failed", "err", err)
	}
}
