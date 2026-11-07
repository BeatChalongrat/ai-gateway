package service

// FIX132 B — a knowledge-base test harness: a private pgvector database
// (testdb.Fresh), local blob storage, kb-models faked with httptest
// (deterministic vectors), and the real KnowledgeService + KnowledgeWorker.

import (
	"bytes"
	"context"
	"io"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/kbfake"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

// stubExtractor reads the stored blob; a body starting with one of the
// markers below fails the way the real extractor would.
type stubExtractor struct{ store storage.Backend }

func (e stubExtractor) ExtractKnowledgeText(ctx context.Context, key, _ string) (string, string, bool) {
	body, _, _, err := e.store.Download(ctx, key)
	if err != nil {
		return "", UnreadableUnknown, false
	}
	defer body.Close()
	b, _ := io.ReadAll(body)
	s := string(b)
	for marker, reason := range map[string]string{
		"%ENCRYPTED": UnreadableEncrypted, "%SCANNED": UnreadableScanned, "%DAMAGED": UnreadableDamaged,
		"%UNAVAILABLE": "unavailable", // the extractor itself could not run (sandbox off / unreachable)
	} {
		if strings.HasPrefix(s, marker) {
			return "", reason, false
		}
	}
	return s, "", true
}

type kbMemFile struct{ *bytes.Reader }

func (kbMemFile) Close() error { return nil }

type kbHarness struct {
	pool   *pgxpool.Pool
	repo   *repository.KnowledgeRepo
	fake   *kbfake.Server
	models *KBModelsClient
	store  storage.Backend
	svc    *KnowledgeService
	w      *KnowledgeWorker
	tenant uuid.UUID
	user   uuid.UUID
	kb     *model.KnowledgeBase
}

func newKBHarness(t *testing.T) *kbHarness {
	t.Helper()
	pool := testdb.Fresh(t)
	fake := kbfake.New(thaiWords...)
	t.Cleanup(fake.Close)
	models, err := NewKBModelsClient(fake.URL)
	if err != nil {
		t.Fatal(err)
	}
	store, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	h := &kbHarness{pool: pool, repo: repository.NewKnowledgeRepo(pool), fake: fake, models: models, store: store,
		tenant: uuid.New(), user: uuid.New()}
	h.svc = NewKnowledgeService(h.repo, models, stubExtractor{store}, store, KnowledgeServiceKnobs{})
	h.w = NewKnowledgeWorker(h.svc, KnowledgeWorkerConfig{Workers: 2, PollInterval: 20 * time.Millisecond,
		BackoffBase: time.Second, LoadingDelay: time.Second, SweepEvery: 50 * time.Millisecond})
	h.kb, err = h.svc.CreateKB(context.Background(), CreateKBInput{TenantID: h.tenant, UserID: h.user, Name: "คู่มือพนักงาน"})
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func (h *kbHarness) upload(t *testing.T, name, ct, body string) *model.KnowledgeDocument {
	t.Helper()
	d, err := h.svc.UploadDocument(context.Background(), UploadDocumentInput{
		TenantID: h.tenant, UserID: h.user, KnowledgeBaseID: h.kb.ID,
		OriginalName: name, ContentType: ct, Size: int64(len(body)),
		Body: kbMemFile{bytes.NewReader([]byte(body))},
	})
	if err != nil {
		t.Fatalf("upload %s: %v", name, err)
	}
	return d
}

func (h *kbHarness) doc(t *testing.T, id uuid.UUID) *model.KnowledgeDocument {
	t.Helper()
	d, err := h.repo.GetDocument(context.Background(), h.tenant, id)
	if err != nil {
		t.Fatalf("get document: %v", err)
	}
	return d
}

func (h *kbHarness) count(t *testing.T, q string, args ...any) int {
	t.Helper()
	var n int
	if err := h.pool.QueryRow(context.Background(), q, args...).Scan(&n); err != nil {
		t.Fatalf("%s: %v", q, err)
	}
	return n
}

// drain runs the worker until the queue is empty (due documents only).
func (h *kbHarness) drain(t *testing.T) {
	t.Helper()
	for i := 0; i < 50; i++ {
		worked, err := h.w.RunOnce(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			return
		}
	}
	t.Fatal("queue never drained")
}

// longEnglish is n sentences (~60 runes each) — about n/16 chunks at 1000.
func longEnglish(n int) string {
	var b strings.Builder
	for i := 0; i < n; i++ {
		b.WriteString("Section ")
		b.WriteString(strconv.Itoa(i))
		b.WriteString(" of the employee handbook covers leave, pay and travel rules. ")
	}
	return b.String()
}
