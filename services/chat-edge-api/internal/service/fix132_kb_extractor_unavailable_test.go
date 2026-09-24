package service

import (
	"bytes"
	"context"
	"testing"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// FIX132 B — when the file reader itself cannot run (the extraction sandbox is
// off or unreachable), a knowledge-base document is NOT reported as a damaged
// file: it waits and retries like a model outage, and only after the retries
// fails as extract_unavailable, which the page words as "the file reader is not
// available right now", not "your file is damaged".
func TestFIX132_ExtractorUnavailableIsRetriedNotBlamedOnTheFile(t *testing.T) {
	h := newKBHarness(t)
	d := h.upload(t, "policy.pdf", "application/pdf", "%UNAVAILABLE pdf")
	for attempt := 1; attempt < KBIndexMaxAttempts; attempt++ {
		h.drain(t)
		got := h.doc(t, d.ID)
		if got.Status != "pending" || got.Attempts != attempt || got.ErrorCode == nil || *got.ErrorCode != "extract_unavailable" {
			t.Fatalf("attempt %d: want pending/extract_unavailable, got %s attempts=%d code=%v", attempt, got.Status, got.Attempts, got.ErrorCode)
		}
		_, _ = h.pool.Exec(context.Background(), `UPDATE chat.knowledge_documents SET next_attempt_at = NOW() - interval '1 second' WHERE id=$1`, d.ID)
	}
	h.drain(t)
	got := h.doc(t, d.ID)
	if got.Status != "failed" || got.ErrorCode == nil || *got.ErrorCode != "extract_unavailable" {
		t.Fatalf("after %d attempts: want failed/extract_unavailable, got %s/%v", KBIndexMaxAttempts, got.Status, got.ErrorCode)
	}
}

// With no sandbox configured, reading a PDF says the reader is unavailable —
// not the "unknown" reason that the knowledge base words as a damaged file.
func TestFIX132_NoSandboxMeansReaderUnavailable(t *testing.T) {
	store, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	key := "knowledge/t/kb/doc_a.pdf"
	body := []byte("%PDF-1.4 fake")
	if err := store.Upload(ctx, key, bytes.NewReader(body), "application/pdf", int64(len(body))); err != nil {
		t.Fatal(err)
	}
	fs := NewFileService(nil, nil, store, Knobs{})
	if _, reason, ok := fs.ExtractKnowledgeText(ctx, key, "application/pdf"); ok || reason != "unavailable" {
		t.Fatalf("no sandbox: want (false, unavailable), got (%v, %q)", ok, reason)
	}
}
