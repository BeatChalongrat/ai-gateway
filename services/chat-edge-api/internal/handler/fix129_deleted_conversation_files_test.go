package handler

// FIX129 B1 — through the real handlers: once a conversation is deleted,
// GET /files/{id}/download and GET /files/{id}/text answer 404, and
// they answer 200 again when the conversation is restored.
//
// Verified live before this card: both kept answering 200 with the full
// file after DELETE /conversations/{id}. Real auth middleware, real
// FileService over a local storage backend in a temp dir, a private
// database clone.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

func TestFix129_DeletedConversationFilesAnswer404(t *testing.T) {
	pool := probePool(t)
	ctx := context.Background()

	userID := uuid.New()
	portalTenant := uuid.New()
	token := mintProbeToken(userID, portalTenant.String())
	prov := service.NewProvisionService(pool)
	tenantUUID, err := prov.EnsureUserExists(ctx, userID, "fix129-probe@local", "owner", portalTenant.String())
	if err != nil {
		t.Fatalf("provision: %v", err)
	}

	convs := repository.NewConversationRepo(pool)
	conv, err := convs.Create(ctx, &model.Conversation{TenantID: tenantUUID, UserID: userID, ModelID: "gpt-4o-mini", Title: "fix129"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}

	store, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatalf("local storage: %v", err)
	}
	key := "fix129/" + uuid.NewString() + ".txt"
	if err := store.Upload(ctx, key, strings.NewReader("hello"), "text/plain", 5); err != nil {
		t.Fatalf("upload blob: %v", err)
	}
	files := repository.NewFileRepo(pool)
	f, err := files.Create(ctx, &model.ConversationFile{
		ConversationID:   conv.ID,
		TenantID:         tenantUUID,
		UserID:           userID,
		Filename:         "hello.txt",
		OriginalFilename: "hello.txt",
		ContentType:      "text/plain",
		FileSizeBytes:    5,
		StoragePath:      key,
		ExtractionStatus: "completed",
	})
	if err != nil {
		t.Fatalf("create file row: %v", err)
	}

	fileSvc := service.NewFileService(files, convs, store, service.Knobs{StorageRoot: t.TempDir()})
	fh := NewFileHandler(fileSvc)
	r := chi.NewRouter()
	r.Route("/api/v1", func(r chi.Router) {
		r.Use(middleware.RequireAuth(probePortalSecret, prov, nil))
		r.Get("/files/{fileID}/download", fh.Download)
		r.Get("/files/{fileID}/text", fh.Text)
	})
	get := func(suffix string) int {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/files/"+f.ID.String()+suffix, nil)
		req.Header.Set("Authorization", "Bearer "+token)
		rec := httptest.NewRecorder()
		r.ServeHTTP(rec, req)
		return rec.Code
	}

	if code := get("/download"); code != http.StatusOK {
		t.Fatalf("live conversation: GET /download = %d, want 200", code)
	}
	if code := get("/text"); code != http.StatusOK {
		t.Fatalf("live conversation: GET /text = %d, want 200", code)
	}

	if err := convs.SoftDelete(ctx, tenantUUID, conv.ID); err != nil {
		t.Fatalf("soft delete: %v", err)
	}
	if code := get("/download"); code != http.StatusNotFound {
		t.Errorf("deleted conversation: GET /download = %d, want 404", code)
	}
	if code := get("/text"); code != http.StatusNotFound {
		t.Errorf("deleted conversation: GET /text = %d, want 404", code)
	}

	if err := convs.Restore(ctx, tenantUUID, conv.ID); err != nil {
		t.Fatalf("restore: %v", err)
	}
	if code := get("/download"); code != http.StatusOK {
		t.Errorf("restored conversation: GET /download = %d, want 200", code)
	}
}
