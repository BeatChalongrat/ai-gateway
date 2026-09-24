package service

// FIX132 B — background indexing: the upload returns at once, a worker
// indexes with visible progress, failures carry a closed-set code, kb-models
// outages are retried with backoff, and a delete / re-index / restart in the
// middle leaves no orphan chunks and no error rows.

import (
	"context"
	"net/http"
	"strconv"
	"testing"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func TestFIX132_UploadIsPendingAtOnceThenIndexedWithProgress(t *testing.T) {
	h := newKBHarness(t)
	d := h.upload(t, "handbook.txt", "text/plain", longEnglish(700)) // ~42 KB → ~43 chunks
	if d.Status != "pending" || d.ProgressTotal != 0 {
		t.Fatalf("the upload answers before indexing: %+v", d)
	}
	if n := h.fake.EmbedCalls.Load(); n != 0 {
		t.Fatalf("the upload request itself must not embed (got %d calls)", n)
	}
	if d.UploadedBy == nil || *d.UploadedBy != h.user {
		t.Fatalf("uploaded_by: %v", d.UploadedBy)
	}

	h.drain(t)
	got := h.doc(t, d.ID)
	if got.Status != "indexed" || got.ErrorCode != nil || got.IndexedAt == nil {
		t.Fatalf("want indexed, got %+v", got)
	}
	if got.ChunkCount < 30 || got.ProgressDone != got.ChunkCount || got.ProgressTotal != got.ChunkCount {
		t.Fatalf("progress %d/%d chunks %d", got.ProgressDone, got.ProgressTotal, got.ChunkCount)
	}
	if n := h.count(t, `SELECT count(*) FROM chat.knowledge_chunks WHERE document_id=$1 AND embedding IS NOT NULL AND sparse IS NOT NULL`, d.ID); n != got.ChunkCount {
		t.Fatalf("every chunk has dense + sparse: %d of %d", n, got.ChunkCount)
	}
	for _, n := range h.fake.BatchSizes {
		if n > KBEmbedBatchMax {
			t.Fatalf("batch of %d > %d", n, KBEmbedBatchMax)
		}
	}
	for _, p := range h.fake.Priorities {
		if p != "index" {
			t.Fatalf("indexing must be priority index, got %v", h.fake.Priorities)
		}
	}
	kb, _ := h.repo.GetKB(context.Background(), h.tenant, h.kb.ID)
	if kb.DocumentCount != 1 || kb.TotalChunks != got.ChunkCount {
		t.Fatalf("KB stats: %d docs %d chunks", kb.DocumentCount, kb.TotalChunks)
	}
}

func TestFIX132_ProgressIsVisibleWhileIndexing(t *testing.T) {
	h := newKBHarness(t)
	d := h.upload(t, "big.txt", "text/plain", longEnglish(600))
	h.fake.Block = make(chan struct{})
	h.fake.EmbedStarted = make(chan struct{}, 1)
	done := make(chan struct{})
	go func() { _, _ = h.w.RunOnce(context.Background()); close(done) }()
	<-h.fake.EmbedStarted
	h.fake.Block <- struct{}{} // let batch 1 through
	<-h.fake.EmbedStarted      // batch 2 is waiting
	mid := h.doc(t, d.ID)
	if mid.Status != "processing" || mid.ProgressDone != KBEmbedBatchMax || mid.ProgressTotal <= mid.ProgressDone || mid.ClaimedAt == nil {
		t.Fatalf("mid-index the row shows progress: status=%s %d/%d claimed=%v", mid.Status, mid.ProgressDone, mid.ProgressTotal, mid.ClaimedAt)
	}
	close(h.fake.Block)
	<-done
	if got := h.doc(t, d.ID); got.Status != "indexed" {
		t.Fatalf("status %s", got.Status)
	}
}

func TestFIX132_ExtractionFailuresCarryACode(t *testing.T) {
	h := newKBHarness(t)
	cases := map[string]string{
		"%ENCRYPTED pdf":   model.KBErrEncrypted,
		"%SCANNED pdf":     model.KBErrEmptyText,
		"%DAMAGED pdf":     model.KBErrExtractFailed,
		" \n\t \n\x00 \n ": model.KBErrEmptyText,
	}
	ids := map[string]string{}
	for body, code := range cases {
		d := h.upload(t, "f.pdf", "application/pdf", body)
		ids[d.ID.String()] = code
	}
	h.drain(t)
	docs, _ := h.repo.ListDocuments(context.Background(), h.tenant, h.kb.ID)
	for _, d := range docs {
		want := ids[d.ID.String()]
		if d.Status != "failed" || d.ErrorCode == nil || *d.ErrorCode != want || d.ErrorMessage == nil || *d.ErrorMessage == "" {
			t.Fatalf("want failed/%s with a message, got %s/%v/%v", want, d.Status, d.ErrorCode, d.ErrorMessage)
		}
	}
	if n := h.count(t, `SELECT count(*) FROM chat.knowledge_chunks WHERE knowledge_base_id=$1`, h.kb.ID); n != 0 {
		t.Fatalf("failed documents leave no chunks, got %d", n)
	}
}

func TestFIX132_ModelsDownIsRetriedWithBackoffThenFails(t *testing.T) {
	h := newKBHarness(t)
	d := h.upload(t, "a.txt", "text/plain", longEnglish(40))
	h.fake.Fail(1000, http.StatusBadGateway)
	for attempt := 1; attempt < KBIndexMaxAttempts; attempt++ {
		h.drain(t)
		got := h.doc(t, d.ID)
		if got.Status != "pending" || got.Attempts != attempt || got.NextAttemptAt == nil ||
			got.ErrorCode == nil || *got.ErrorCode != model.KBErrModelsUnavailable || got.ClaimedAt != nil {
			t.Fatalf("attempt %d: want pending/models_unavailable with a retry time, got %s attempts=%d next=%v code=%v",
				attempt, got.Status, got.Attempts, got.NextAttemptAt, got.ErrorCode)
		}
		// Measured on the database clock (the Docker VM's may differ from ours).
		var waitMS float64
		_ = h.pool.QueryRow(context.Background(), `SELECT EXTRACT(EPOCH FROM next_attempt_at - NOW()) * 1000 FROM chat.knowledge_documents WHERE id=$1`, d.ID).Scan(&waitMS)
		want := time.Duration(1<<(attempt-1)) * time.Second // base 1 s, doubled per attempt
		if wait := time.Duration(waitMS) * time.Millisecond; wait < want-500*time.Millisecond || wait > want+time.Second {
			t.Fatalf("attempt %d backoff %v, want ~%v", attempt, wait, want)
		}
		// Not due yet: the worker leaves it alone.
		if worked, _ := h.w.RunOnce(context.Background()); worked {
			t.Fatal("a document is not retried before next_attempt_at")
		}
		_, _ = h.pool.Exec(context.Background(), `UPDATE chat.knowledge_documents SET next_attempt_at = NOW() - interval '1 second' WHERE id=$1`, d.ID)
	}
	h.drain(t)
	got := h.doc(t, d.ID)
	if got.Status != "failed" || got.ErrorCode == nil || *got.ErrorCode != model.KBErrModelsUnavailable {
		t.Fatalf("after %d attempts: failed/models_unavailable, got %s/%v", KBIndexMaxAttempts, got.Status, got.ErrorCode)
	}
}

func TestFIX132_ModelsLoadingWaitsWithoutUsingAnAttempt(t *testing.T) {
	h := newKBHarness(t)
	d := h.upload(t, "a.txt", "text/plain", longEnglish(40))
	h.fake.SetStatus("loading")
	h.drain(t)
	got := h.doc(t, d.ID)
	if got.Status != "pending" || got.Attempts != 0 || got.NextAttemptAt == nil {
		t.Fatalf("loading → pending, no attempt used, retried later; got %s attempts=%d next=%v", got.Status, got.Attempts, got.NextAttemptAt)
	}
	h.fake.SetStatus("ready")
	_, _ = h.pool.Exec(context.Background(), `UPDATE chat.knowledge_documents SET next_attempt_at = NOW() WHERE id=$1`, d.ID)
	h.drain(t)
	got = h.doc(t, d.ID)
	if got.Status != "indexed" || got.ErrorCode != nil || got.ErrorMessage != nil || got.Attempts != 0 {
		t.Fatalf("ready again → indexed with the error cleared, got %+v", got)
	}
}

func TestFIX132_StaleProcessingGoesBackToPending(t *testing.T) {
	h := newKBHarness(t)
	ctx := context.Background()
	stale := h.upload(t, "stale.txt", "text/plain", "old claim")
	if c, err := h.repo.ClaimNextDocument(ctx); err != nil || c == nil || c.ID != stale.ID {
		t.Fatalf("claim: %v %v", c, err)
	}
	fresh := h.upload(t, "fresh.txt", "text/plain", "new claim")
	if c, _ := h.repo.ClaimNextDocument(ctx); c == nil || c.ID != fresh.ID {
		t.Fatal("second claim")
	}
	if c, _ := h.repo.ClaimNextDocument(ctx); c != nil {
		t.Fatal("a claimed document cannot be claimed twice")
	}
	// The process that held `stale` died 10 minutes ago.
	_, _ = h.pool.Exec(ctx, `UPDATE chat.knowledge_documents SET claimed_at = NOW() - interval '10 minutes' WHERE id=$1`, stale.ID)
	n, err := h.repo.ReleaseStaleClaims(ctx, 5*time.Minute)
	if err != nil || n != 1 {
		t.Fatalf("released %d (%v), want 1", n, err)
	}
	if got := h.doc(t, stale.ID); got.Status != "pending" || got.ClaimedAt != nil {
		t.Fatalf("stale → pending, got %s", got.Status)
	}
	if got := h.doc(t, fresh.ID); got.Status != "processing" {
		t.Fatalf("a live claim is kept, got %s", got.Status)
	}
	h.drain(t)
	if got := h.doc(t, stale.ID); got.Status != "indexed" {
		t.Fatalf("the released document is indexed afterwards, got %s", got.Status)
	}
	// The background sweeper does the same on its own.
	_, _ = h.pool.Exec(ctx, `UPDATE chat.knowledge_documents SET status='processing', claimed_at = NOW() - interval '10 minutes' WHERE id=$1`, stale.ID)
	bg, cancel := context.WithCancel(ctx)
	go h.w.StartBackground(bg)
	deadline := time.Now().Add(5 * time.Second)
	for h.doc(t, stale.ID).Status != "indexed" && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	if got := h.doc(t, stale.ID); got.Status != "indexed" {
		t.Fatalf("the sweeper + workers pick it up again, got %s", got.Status)
	}
}

// Delete while this process is indexing it: the run is cancelled, nothing
// more is written.
func TestFIX132_DeleteMidIndexStopsCleanly(t *testing.T) {
	h := newKBHarness(t)
	d := h.upload(t, "big.txt", "text/plain", longEnglish(600))
	h.fake.Block = make(chan struct{})
	h.fake.EmbedStarted = make(chan struct{}, 1)
	done := make(chan struct{})
	go func() { _, _ = h.w.RunOnce(context.Background()); close(done) }()
	<-h.fake.EmbedStarted
	h.fake.Block <- struct{}{} // batch 1 stored
	<-h.fake.EmbedStarted      // batch 2 in flight
	if n := h.count(t, `SELECT count(*) FROM chat.knowledge_chunks WHERE document_id=$1`, d.ID); n != KBEmbedBatchMax {
		t.Fatalf("batch 1 stored: %d chunks", n)
	}
	if err := h.svc.DeleteDocument(context.Background(), h.tenant, h.kb.ID, d.ID); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("the worker did not stop after the delete")
	}
	close(h.fake.Block)
	assertNoTrace(t, h)
}

// Delete while ANOTHER process is indexing it (no in-process cancel): its
// next fenced write matches no row and it stops.
func TestFIX132_DeleteMidIndexInAnotherProcessStopsCleanly(t *testing.T) {
	h := newKBHarness(t)
	other := NewKnowledgeService(h.repo, h.models, stubExtractor{h.store}, h.store, KnowledgeServiceKnobs{})
	otherWorker := NewKnowledgeWorker(other, KnowledgeWorkerConfig{})
	d := h.upload(t, "big.txt", "text/plain", longEnglish(600))
	h.fake.Block = make(chan struct{})
	h.fake.EmbedStarted = make(chan struct{}, 1)
	done := make(chan struct{})
	go func() { _, _ = otherWorker.RunOnce(context.Background()); close(done) }()
	<-h.fake.EmbedStarted
	h.fake.Block <- struct{}{}
	<-h.fake.EmbedStarted
	if err := h.svc.DeleteDocument(context.Background(), h.tenant, h.kb.ID, d.ID); err != nil {
		t.Fatal(err)
	}
	close(h.fake.Block) // batch 2 comes back after the row is gone
	<-done
	assertNoTrace(t, h)
}

func assertNoTrace(t *testing.T, h *kbHarness) {
	t.Helper()
	if n := h.count(t, `SELECT count(*) FROM chat.knowledge_chunks WHERE knowledge_base_id=$1`, h.kb.ID); n != 0 {
		t.Fatalf("orphan chunks: %d", n)
	}
	if n := h.count(t, `SELECT count(*) FROM chat.knowledge_documents WHERE knowledge_base_id=$1`, h.kb.ID); n != 0 {
		t.Fatalf("document rows left (error rows?): %d", n)
	}
	kb, _ := h.repo.GetKB(context.Background(), h.tenant, h.kb.ID)
	if kb.DocumentCount != 0 || kb.TotalChunks != 0 {
		t.Fatalf("KB stats after delete: %d docs, %d chunks", kb.DocumentCount, kb.TotalChunks)
	}
}

func TestFIX132_ReindexDeletesChunksAndRequeues(t *testing.T) {
	h := newKBHarness(t)
	d := h.upload(t, "a.txt", "text/plain", longEnglish(200))
	h.drain(t)
	first := h.doc(t, d.ID)
	re, err := h.svc.ReindexDocument(context.Background(), h.tenant, h.kb.ID, d.ID)
	if err != nil {
		t.Fatal(err)
	}
	if re.Status != "pending" || re.ChunkCount != 0 || re.IndexedAt != nil {
		t.Fatalf("reindex → pending with no chunks, got %+v", re)
	}
	if n := h.count(t, `SELECT count(*) FROM chat.knowledge_chunks WHERE document_id=$1`, d.ID); n != 0 {
		t.Fatalf("reindex deletes the chunks, %d left", n)
	}
	h.drain(t)
	again := h.doc(t, d.ID)
	if again.Status != "indexed" || again.ChunkCount != first.ChunkCount {
		t.Fatalf("indexed again: %s %d (was %d)", again.Status, again.ChunkCount, first.ChunkCount)
	}
	if n := h.count(t, `SELECT count(DISTINCT chunk_index) FROM chat.knowledge_chunks WHERE document_id=$1`, d.ID); n != first.ChunkCount {
		t.Fatalf("no duplicate chunks: %d distinct of %d", n, first.ChunkCount)
	}
}

// Re-index while another process is mid-document: that run stops at its
// fence; the fresh run indexes it once, with no duplicates.
func TestFIX132_ReindexMidIndexInAnotherProcess(t *testing.T) {
	h := newKBHarness(t)
	other := NewKnowledgeService(h.repo, h.models, stubExtractor{h.store}, h.store, KnowledgeServiceKnobs{})
	otherWorker := NewKnowledgeWorker(other, KnowledgeWorkerConfig{})
	d := h.upload(t, "big.txt", "text/plain", longEnglish(600))
	h.fake.Block = make(chan struct{})
	h.fake.EmbedStarted = make(chan struct{}, 1)
	done := make(chan struct{})
	go func() { _, _ = otherWorker.RunOnce(context.Background()); close(done) }()
	<-h.fake.EmbedStarted
	h.fake.Block <- struct{}{}
	<-h.fake.EmbedStarted
	if _, err := h.svc.ReindexDocument(context.Background(), h.tenant, h.kb.ID, d.ID); err != nil {
		t.Fatal(err)
	}
	close(h.fake.Block)
	<-done
	if n := h.count(t, `SELECT count(*) FROM chat.knowledge_chunks WHERE document_id=$1`, d.ID); n != 0 {
		t.Fatalf("the stale run left %d chunks", n)
	}
	h.fake.Block = nil
	h.drain(t)
	got := h.doc(t, d.ID)
	if got.Status != "indexed" || h.count(t, `SELECT count(*) FROM chat.knowledge_chunks WHERE document_id=$1`, d.ID) != got.ChunkCount {
		t.Fatalf("indexed once: %s, chunk_count %d", got.Status, got.ChunkCount)
	}
}

func TestFIX132_ShutdownMidIndexReleasesTheClaim(t *testing.T) {
	h := newKBHarness(t)
	d := h.upload(t, "big.txt", "text/plain", longEnglish(600))
	h.fake.Block = make(chan struct{})
	h.fake.EmbedStarted = make(chan struct{}, 1)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { _, _ = h.w.RunOnce(ctx); close(done) }()
	<-h.fake.EmbedStarted
	cancel() // SIGTERM
	<-done
	close(h.fake.Block)
	got := h.doc(t, d.ID)
	if got.Status != "pending" || got.ClaimedAt != nil || got.Attempts != 0 || got.ErrorCode != nil {
		t.Fatalf("shutdown → pending, no attempt used, no error; got %s claimed=%v attempts=%d code=%v", got.Status, got.ClaimedAt, got.Attempts, got.ErrorCode)
	}
	h.fake.Block = nil
	h.drain(t)
	if got := h.doc(t, d.ID); got.Status != "indexed" {
		t.Fatalf("the next process indexes it, got %s", got.Status)
	}
}

func TestFIX132_WorkersDrainTheQueueInTheBackground(t *testing.T) {
	h := newKBHarness(t)
	ids := []string{}
	for i := 0; i < 4; i++ {
		ids = append(ids, h.upload(t, "doc"+strconv.Itoa(i)+".txt", "text/plain", longEnglish(50+i*30)).ID.String())
	}
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() { h.w.StartBackground(ctx); close(stopped) }()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if h.count(t, `SELECT count(*) FROM chat.knowledge_documents WHERE knowledge_base_id=$1 AND status='indexed'`, h.kb.ID) == len(ids) {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	select {
	case <-stopped:
	case <-time.After(5 * time.Second):
		t.Fatal("StartBackground did not return after cancel")
	}
	if n := h.count(t, `SELECT count(*) FROM chat.knowledge_documents WHERE knowledge_base_id=$1 AND status='indexed'`, h.kb.ID); n != len(ids) {
		t.Fatalf("indexed %d of %d", n, len(ids))
	}
	_ = repository.ErrClaimLost
}
