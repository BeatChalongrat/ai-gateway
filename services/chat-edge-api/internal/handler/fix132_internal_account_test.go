package handler

// FIX132 C — the backend's account-deletion calls. Only a caller with the
// internal secret gets in; the answer is counts only.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestFIX132_InternalAccountRoutesNeedTheSecret(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	store, _ := storage.NewLocalBackend(t.TempDir())
	svc := service.NewAccountDeletionService(pool,
		repository.NewConversationEraseRepo(pool), repository.NewFileRepo(pool),
		repository.NewDeletionAuditRepo(pool), store, t.TempDir())
	h := NewInternalAccountHandler("s3cret", svc)
	r := chi.NewRouter()
	r.Post("/internal/accounts/{userID}/deletion-request", h.DeletionRequest)
	r.Post("/internal/accounts/{userID}/purge", h.Purge)

	user, tenant := uuid.New(), uuid.New()
	if _, err := pool.Exec(ctx, `INSERT INTO chat.conversations (id, tenant_id, user_id, title, model_id)
		VALUES ($1, $2, $3, 't', 'm')`, uuid.New(), tenant, user); err != nil {
		t.Fatal(err)
	}
	call := func(path, secret string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, path, nil)
		if secret != "" {
			req.Header.Set(InternalServiceSecretHeader, secret)
		}
		rec := httptest.NewRecorder()
		r.ServeHTTP(rec, req)
		return rec
	}

	for _, secret := range []string{"", "wrong"} {
		if rec := call("/internal/accounts/"+user.String()+"/purge", secret); rec.Code != http.StatusUnauthorized {
			t.Fatalf("purge with secret %q = %d, want 401", secret, rec.Code)
		}
	}
	if rec := call("/internal/accounts/not-a-uuid/deletion-request", "s3cret"); rec.Code != http.StatusBadRequest {
		t.Fatalf("bad id = %d, want 400", rec.Code)
	}

	rec := call("/internal/accounts/"+user.String()+"/deletion-request", "s3cret")
	if rec.Code != http.StatusOK {
		t.Fatalf("deletion-request = %d: %s", rec.Code, rec.Body.String())
	}
	var got map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &got)
	if got["conversations_hidden"] != float64(1) {
		t.Fatalf("deletion-request body = %v", got)
	}

	rec = call("/internal/accounts/"+user.String()+"/purge", "s3cret")
	if rec.Code != http.StatusOK {
		t.Fatalf("purge = %d: %s", rec.Code, rec.Body.String())
	}
	var n int
	_ = pool.QueryRow(ctx, `SELECT count(*) FROM chat.conversations WHERE user_id = $1`, user).Scan(&n)
	if n != 0 {
		t.Fatalf("conversations left after purge: %d", n)
	}

	if NewInternalAccountHandler("", svc).Enabled() {
		t.Fatal("with no secret configured the routes must not exist")
	}
}
