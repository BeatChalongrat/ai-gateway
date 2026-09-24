package service

// FIX132 B — the knowledge base's background indexer.
//
// KB_INDEX_WORKERS goroutines (default 2) each loop: claim the oldest pending
// document (FOR UPDATE SKIP LOCKED — several chat-edge processes can run
// this safely), extract its text, chunk it, embed the chunks in batches of
// at most 16 with kb-models (dense + sparse in one call, priority "index" so
// chat-time questions go first), and store each batch with its progress. Every write is fenced on the claim
// (repository/knowledge_index_repo.go), so a document deleted or re-indexed
// meanwhile just stops: nothing is written, no error row appears, no chunk
// is left behind.
//
// Failures:
//   - a password-protected PDF → failed / encrypted; nothing extractable
//     (a scan without text, an empty file) → failed / empty_text; any other
//     extraction failure → failed / extract_failed;
//   - kb-models loading its weights → back to pending, retried in 30 s, no
//     attempt counted;
//   - kb-models unreachable / 5xx / timing out → back to pending with
//     backoff (30 s, 1, 2, 4 min …), failed / models_unavailable after
//     KBIndexMaxAttempts attempts;
//   - this process shutting down → the claim is released (pending, no
//     attempt counted) so the next process starts on it at once;
//   - a process that died mid-document leaves a 'processing' row whose claim
//     is no longer renewed; after staleClaim (5 min) the sweeper sends it
//     back to pending.

import (
	"context"
	"errors"
	"log/slog"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// KnowledgeWorkerConfig tunes the indexer; zero values take the defaults.
type KnowledgeWorkerConfig struct {
	Workers      int           // KB_INDEX_WORKERS, default 2 (1..4)
	PollInterval time.Duration // idle poll, default 3 s (an upload wakes it at once)
	StaleClaim   time.Duration // default 5 min
	SweepEvery   time.Duration // stale-claim sweep, default 1 min
	MaxAttempts  int           // default KBIndexMaxAttempts
	BackoffBase  time.Duration // default 30 s, doubled per attempt, capped at 10 min
	LoadingDelay time.Duration // default 30 s
}

// KBIndexMaxAttempts is how many times a document is tried while kb-models
// is unavailable before it is marked failed / models_unavailable.
const KBIndexMaxAttempts = 5

func (c KnowledgeWorkerConfig) withDefaults() KnowledgeWorkerConfig {
	if c.Workers <= 0 {
		c.Workers = 2
	}
	if c.Workers > 4 {
		c.Workers = 4
	}
	if c.PollInterval <= 0 {
		c.PollInterval = 3 * time.Second
	}
	if c.StaleClaim <= 0 {
		c.StaleClaim = 5 * time.Minute
	}
	if c.SweepEvery <= 0 {
		c.SweepEvery = time.Minute
	}
	if c.MaxAttempts <= 0 {
		c.MaxAttempts = KBIndexMaxAttempts
	}
	if c.BackoffBase <= 0 {
		c.BackoffBase = 30 * time.Second
	}
	if c.LoadingDelay <= 0 {
		c.LoadingDelay = 30 * time.Second
	}
	return c
}

// KnowledgeWorker indexes pending knowledge documents in the background.
type KnowledgeWorker struct {
	svc  *KnowledgeService
	cfg  KnowledgeWorkerConfig
	wake chan struct{}

	mu     sync.Mutex
	active map[uuid.UUID]context.CancelFunc
}

// NewKnowledgeWorker builds the indexer and wires it into svc (uploads wake
// it; deletes and re-indexes stop its work on that document).
func NewKnowledgeWorker(svc *KnowledgeService, cfg KnowledgeWorkerConfig) *KnowledgeWorker {
	w := &KnowledgeWorker{
		svc:    svc,
		cfg:    cfg.withDefaults(),
		wake:   make(chan struct{}, 1),
		active: map[uuid.UUID]context.CancelFunc{},
	}
	svc.SetIndexer(w)
	return w
}

// Workers is the number of indexing goroutines.
func (w *KnowledgeWorker) Workers() int { return w.cfg.Workers }

// Notify wakes an idle worker (non-blocking).
func (w *KnowledgeWorker) Notify() {
	select {
	case w.wake <- struct{}{}:
	default:
	}
}

// Cancel stops this process's work on docID, if any. The run writes
// nothing more; the caller is deleting or resetting the document.
func (w *KnowledgeWorker) Cancel(docID uuid.UUID) {
	w.mu.Lock()
	cancel := w.active[docID]
	w.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}

// StartBackground runs the workers and the stale-claim sweeper until ctx is
// cancelled, then waits for them (an in-flight document's claim is released
// on the way out).
func (w *KnowledgeWorker) StartBackground(ctx context.Context) {
	var wg sync.WaitGroup
	for i := 0; i < w.cfg.Workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			w.loop(ctx)
		}()
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		w.sweepLoop(ctx)
	}()
	wg.Wait()
}

func (w *KnowledgeWorker) loop(ctx context.Context) {
	for ctx.Err() == nil {
		worked, err := w.RunOnce(ctx)
		if err != nil && ctx.Err() == nil {
			slog.Warn("kb index: claim failed", "err", err)
		}
		if worked {
			continue
		}
		t := time.NewTimer(w.cfg.PollInterval)
		select {
		case <-ctx.Done():
		case <-w.wake:
		case <-t.C:
		}
		t.Stop()
	}
}

func (w *KnowledgeWorker) sweepLoop(ctx context.Context) {
	for {
		if n, err := w.svc.repo.ReleaseStaleClaims(ctx, w.cfg.StaleClaim); err != nil {
			if ctx.Err() == nil {
				slog.Warn("kb index: stale-claim sweep failed", "err", err)
			}
		} else if n > 0 {
			slog.Info("kb index: stale claims released", "documents", n)
			w.Notify()
		}
		t := time.NewTimer(w.cfg.SweepEvery)
		select {
		case <-ctx.Done():
			t.Stop()
			return
		case <-t.C:
		}
	}
}

// RunOnce claims and indexes one document; false when the queue is empty.
func (w *KnowledgeWorker) RunOnce(ctx context.Context) (bool, error) {
	doc, err := w.svc.repo.ClaimNextDocument(ctx)
	if err != nil || doc == nil {
		return false, err
	}
	docCtx, cancel := context.WithCancel(ctx)
	w.mu.Lock()
	w.active[doc.ID] = cancel
	w.mu.Unlock()
	defer func() {
		w.mu.Lock()
		delete(w.active, doc.ID)
		w.mu.Unlock()
		cancel()
	}()
	w.index(ctx, docCtx, doc)
	return true, nil
}

// indexRun carries one claimed document through the pipeline.
type indexRun struct {
	w      *KnowledgeWorker
	ctx    context.Context // the worker's: cancelled on shutdown
	docCtx context.Context // also cancelled when the document is deleted / reset
	doc    *model.KnowledgeDocument
	claim  time.Time
	log    *slog.Logger
}

// stopped reports whether the run must end without writing its outcome:
// shutdown (the claim is released for the next process) or the document
// was deleted / reset here (its row no longer expects this run).
func (r *indexRun) stopped() bool {
	if r.ctx.Err() != nil {
		rctx, cancel := context.WithTimeout(context.WithoutCancel(r.ctx), 5*time.Second)
		defer cancel()
		if err := r.w.svc.repo.ReleaseClaim(rctx, r.doc.ID, r.claim); err != nil && !errors.Is(err, repository.ErrClaimLost) {
			r.log.Warn("kb index: release on shutdown failed", "err", err)
		} else {
			r.log.Info("kb index: released on shutdown")
		}
		return true
	}
	if r.docCtx.Err() != nil {
		r.log.Info("kb index: stopped (document deleted or re-indexed)")
		return true
	}
	return false
}

// lost handles a fenced write that matched no row.
func (r *indexRun) lost(err error) bool {
	if errors.Is(err, repository.ErrClaimLost) {
		r.log.Info("kb index: stopped (document deleted, re-indexed or reclaimed)")
		return true
	}
	return false
}

func (r *indexRun) fail(code, message string) {
	if r.stopped() {
		return
	}
	if err := r.w.svc.repo.FailDocument(r.ctx, r.doc.ID, r.claim, code, message); err != nil {
		if !r.lost(err) {
			r.log.Warn("kb index: could not record failure", "code", code, "err", err)
		}
		return
	}
	r.log.Info("kb index: failed", "code", code, "reason", message)
	_ = r.w.svc.repo.RecomputeKBStats(r.ctx, r.doc.KnowledgeBaseID)
}

// retry puts the document back in the queue (kb-models could not serve it).
func (r *indexRun) retry(err error) {
	if r.stopped() {
		return
	}
	cfg := r.w.cfg
	if IsKBModelsLoading(err) {
		if e := r.w.svc.repo.RetryLater(r.ctx, r.doc.ID, r.claim, cfg.LoadingDelay, false,
			model.KBErrModelsUnavailable, "The knowledge base models are still loading; indexing starts when they are ready."); e != nil && !r.lost(e) {
			r.log.Warn("kb index: could not requeue", "err", e)
		}
		r.log.Info("kb index: models loading, requeued", "delay", cfg.LoadingDelay.String())
		return
	}
	attempts := r.doc.Attempts + 1
	if attempts >= cfg.MaxAttempts {
		r.fail(model.KBErrModelsUnavailable, "The knowledge base models were unavailable after "+strconv.Itoa(attempts)+" attempts: "+short(err))
		return
	}
	delay := cfg.BackoffBase << (attempts - 1)
	if delay > 10*time.Minute {
		delay = 10 * time.Minute
	}
	if e := r.w.svc.repo.RetryLater(r.ctx, r.doc.ID, r.claim, delay, true,
		model.KBErrModelsUnavailable, "The knowledge base models are unavailable; retrying automatically ("+short(err)+")."); e != nil && !r.lost(e) {
		r.log.Warn("kb index: could not requeue", "err", e)
	}
	r.log.Info("kb index: models unavailable, requeued", "attempt", attempts, "delay", delay.String(), "err", err)
}

// modelError routes a kb-models error: interruption, retry, or failure.
func (r *indexRun) modelError(err error) {
	if r.stopped() {
		return
	}
	if IsKBModelsUnavailable(err) {
		r.retry(err)
		return
	}
	r.fail(model.KBErrInternal, "Indexing failed: "+short(err))
}

func (w *KnowledgeWorker) index(ctx, docCtx context.Context, doc *model.KnowledgeDocument) {
	r := &indexRun{
		w: w, ctx: ctx, docCtx: docCtx, doc: doc, claim: *doc.ClaimedAt,
		log: slog.With("kb_id", doc.KnowledgeBaseID.String(), "doc_id", doc.ID.String()),
	}
	svc := w.svc
	started := time.Now()

	kb, err := svc.repo.GetKB(ctx, doc.TenantID, doc.KnowledgeBaseID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			r.log.Info("kb index: knowledge base gone")
			return
		}
		r.retry(&KBModelsError{Op: "load knowledge base", Err: err})
		return
	}
	if svc.models == nil {
		r.fail(model.KBErrModelsUnavailable, "The knowledge base models are not configured (KB_MODELS_URL).")
		return
	}

	// 1. Text.
	text, reason, ok := svc.extractor.ExtractKnowledgeText(docCtx, doc.StoragePath, doc.ContentType)
	if r.stopped() {
		return
	}
	if !ok {
		switch reason {
		case UnreadableEncrypted:
			r.fail(model.KBErrEncrypted, "The file is password-protected. Remove the password and upload it again.")
		case UnreadableScanned:
			r.fail(model.KBErrEmptyText, "No text could be read from this file (a scan with no recognisable text).")
		case "unsupported":
			r.fail(model.KBErrUnsupportedType, "This file type cannot be indexed.")
		default:
			r.fail(model.KBErrExtractFailed, "The file could not be read (damaged, or an unsupported variant of the format).")
		}
		return
	}
	text = NormalizeKBText(text)
	if strings.TrimSpace(text) == "" {
		r.fail(model.KBErrEmptyText, "No text could be read from this file (it is empty, or a scan with no text layer).")
		return
	}
	if r.claim, err = svc.repo.Heartbeat(ctx, doc.ID, r.claim); err != nil {
		if !r.lost(err) && !r.stopped() {
			r.retry(&KBModelsError{Op: "heartbeat", Err: err})
		}
		return
	}

	// 2. Chunks (Thai runs longer than a chunk are split between words by
	// kb-models /v1/segment).
	size, overlap := EffectiveChunking(kb.ChunkSize, kb.ChunkOverlap, svc.knobs.ChunkSize, svc.knobs.ChunkOverlap)
	chunks, err := ChunkTextRunes(docCtx, text, size, overlap, svc.models)
	if err != nil {
		r.modelError(err)
		return
	}
	if len(chunks) == 0 {
		r.fail(model.KBErrEmptyText, "No text could be read from this file.")
		return
	}
	chars := utf8.RuneCountInString(text)
	if r.claim, err = svc.repo.SetProgressTotal(ctx, doc.ID, r.claim, len(chunks), chars); err != nil {
		if !r.lost(err) && !r.stopped() {
			r.retry(&KBModelsError{Op: "progress", Err: err})
		}
		return
	}

	// 3. Embed + store, one batch at a time.
	batch := svc.knobs.EmbedBatchSize
	for i := 0; i < len(chunks); i += batch {
		end := min(i+batch, len(chunks))
		part := chunks[i:end]
		embs, err := svc.models.Embed(docCtx, part, true, KBPriorityIndex)
		if err != nil {
			r.modelError(err)
			return
		}
		rows := make([]model.KnowledgeChunk, len(part))
		for j, c := range part {
			rows[j] = model.KnowledgeChunk{
				DocumentID:      doc.ID,
				KnowledgeBaseID: doc.KnowledgeBaseID,
				TenantID:        doc.TenantID,
				Content:         c,
				Embedding:       embs[j].Dense,
				Sparse:          embs[j].Sparse,
				ChunkIndex:      i + j,
				Metadata:        map[string]any{"filename": doc.OriginalFilename},
			}
		}
		if r.stopped() {
			return
		}
		if r.claim, err = svc.repo.CommitIndexBatch(ctx, doc.ID, r.claim, rows, end, i == 0); err != nil {
			if !r.lost(err) && !r.stopped() {
				r.retry(&KBModelsError{Op: "store chunks", Err: err})
			}
			return
		}
	}

	if err := svc.repo.FinishIndexed(ctx, doc.ID, r.claim, len(chunks), chars); err != nil {
		if !r.lost(err) && !r.stopped() {
			r.log.Warn("kb index: could not mark indexed", "err", err)
		}
		return
	}
	_ = svc.repo.RecomputeKBStats(ctx, doc.KnowledgeBaseID)
	r.log.Info("kb index: indexed", "chunks", len(chunks), "chars", chars,
		"ms", time.Since(started).Milliseconds())
}

// short is an error's first line, capped, for error_message.
func short(err error) string {
	s := err.Error()
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	if utf8.RuneCountInString(s) > 200 {
		s = string([]rune(s)[:200]) + "…"
	}
	return s
}
