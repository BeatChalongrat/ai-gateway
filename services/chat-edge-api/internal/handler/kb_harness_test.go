package handler

// FIX132 B — the knowledge-base HTTP surface over the real service stack:
// a private pgvector database, local blob storage, kb-models faked with
// httptest, the background worker driven by the test.

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/kbfake"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

type kbBlobExtractor struct{ store storage.Backend }

func (e kbBlobExtractor) ExtractKnowledgeText(ctx context.Context, key, _ string) (string, string, bool) {
	b, _, _, err := e.store.Download(ctx, key)
	if err != nil {
		return "", service.UnreadableUnknown, false
	}
	defer b.Close()
	x, _ := io.ReadAll(b)
	return string(x), "", true
}

type kbHTTP struct {
	pool   *pgxpool.Pool
	fake   *kbfake.Server
	svc    *service.KnowledgeService
	worker *service.KnowledgeWorker
	rag    *service.RAGService
	h      *KnowledgeHandler
	mux    http.Handler
	tenant uuid.UUID
}

func newKBHTTP(t *testing.T) *kbHTTP {
	t.Helper()
	pool := testdb.Fresh(t)
	fake := kbfake.New()
	t.Cleanup(fake.Close)
	models, err := service.NewKBModelsClient(fake.URL)
	if err != nil {
		t.Fatal(err)
	}
	store, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	repo := repository.NewKnowledgeRepo(pool)
	svc := service.NewKnowledgeService(repo, models, kbBlobExtractor{store}, store, service.KnowledgeServiceKnobs{})
	k := &kbHTTP{pool: pool, fake: fake, svc: svc, tenant: uuid.New()}
	k.worker = service.NewKnowledgeWorker(svc, service.KnowledgeWorkerConfig{})
	k.rag = service.NewRAGService(repo, models, service.DefaultRAGConfig())
	k.h = NewKnowledgeHandler(svc, k.rag)
	k.h.SetAvailability(service.NewKnowledgeAvailability(models.Probe))

	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			uid, err := uuid.Parse(req.Header.Get("X-Test-User"))
			if err != nil {
				http.Error(w, "no test user", http.StatusUnauthorized)
				return
			}
			tid := k.tenant
			if v := req.Header.Get("X-Test-Tenant"); v != "" {
				tid = uuid.MustParse(v)
			}
			ctx := middleware.WithCaller(req.Context(), middleware.Caller{UserID: uid, TenantUUID: tid, TenantID: tid.String()})
			next.ServeHTTP(w, req.WithContext(ctx))
		})
	})
	// Same routes as router.go.
	r.Get("/knowledge-bases", k.h.List)
	r.Get("/knowledge-bases/availability", k.h.Availability)
	r.Post("/knowledge-bases", k.h.Create)
	r.Get("/knowledge-bases/{id}", k.h.Get)
	r.Patch("/knowledge-bases/{id}", k.h.Update)
	r.Delete("/knowledge-bases/{id}", k.h.Delete)
	r.Get("/knowledge-bases/{id}/documents", k.h.ListDocuments)
	r.Post("/knowledge-bases/{id}/documents", k.h.UploadDocument)
	r.Delete("/knowledge-bases/{id}/documents/{docId}", k.h.DeleteDocument)
	r.Post("/knowledge-bases/{id}/documents/{docId}/reindex", k.h.ReindexDocument)
	r.Get("/knowledge-bases/{id}/documents/{docId}/file", k.h.DocumentFile)
	r.Post("/knowledge-bases/{id}/search", k.h.Search)
	k.mux = r
	return k
}

// do sends a JSON request as user; body may be nil.
func (k *kbHTTP) do(t *testing.T, user uuid.UUID, method, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	req := httptest.NewRequest(method, path, rd)
	req.Header.Set("X-Test-User", user.String())
	rec := httptest.NewRecorder()
	k.mux.ServeHTTP(rec, req)
	return rec
}

func (k *kbHTTP) upload(t *testing.T, user uuid.UUID, kbID, name, ct, content string) *httptest.ResponseRecorder {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	h := make(map[string][]string)
	h["Content-Disposition"] = []string{`form-data; name="file"; filename="` + name + `"`}
	h["Content-Type"] = []string{ct}
	part, _ := mw.CreatePart(h)
	_, _ = part.Write([]byte(content))
	_ = mw.Close()
	req := httptest.NewRequest(http.MethodPost, "/knowledge-bases/"+kbID+"/documents", &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	req.Header.Set("X-Test-User", user.String())
	rec := httptest.NewRecorder()
	k.mux.ServeHTTP(rec, req)
	return rec
}

func (k *kbHTTP) drain(t *testing.T) {
	t.Helper()
	for i := 0; i < 50; i++ {
		worked, err := k.worker.RunOnce(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if !worked {
			return
		}
	}
}

func (k *kbHTTP) createKB(t *testing.T, user uuid.UUID, name string) string {
	t.Helper()
	rec := k.do(t, user, http.MethodPost, "/knowledge-bases", map[string]any{"name": name})
	if rec.Code != http.StatusCreated {
		t.Fatalf("create kb: %d %s", rec.Code, rec.Body.String())
	}
	var kb struct {
		ID string `json:"id"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &kb)
	return kb.ID
}

func decode(t *testing.T, rec *httptest.ResponseRecorder, v any) {
	t.Helper()
	if err := json.Unmarshal(rec.Body.Bytes(), v); err != nil {
		t.Fatalf("decode %q: %v", rec.Body.String(), err)
	}
}
