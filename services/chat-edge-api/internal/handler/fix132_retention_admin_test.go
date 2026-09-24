package handler

// FIX132 C — an admin retention edit that does not mention the
// deleted-data purge must leave the purge as it was. The admin UI's
// retention form predates the purge switches; before this fix its PUT
// wrote deleted_purge_enabled = false by omission and silently turned
// the 30-day purge off for the tenant.

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestFIX132_RetentionPutWithoutPurgeFieldsKeepsThePurgeOn(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	settings := repository.NewTenantRetentionSettingsRepo(pool)
	h := NewRetentionAdminHandler(settings, repository.NewDeletionAuditRepo(pool), nil)
	tenant := uuid.New()

	put := func(body map[string]any) {
		t.Helper()
		raw, _ := json.Marshal(body)
		rec := httptest.NewRecorder()
		h.PutSettings(rec, httptest.NewRequest(http.MethodPut, "/admin/retention-settings", bytes.NewReader(raw)))
		if rec.Code != http.StatusOK {
			t.Fatalf("PUT = %d: %s", rec.Code, rec.Body.String())
		}
	}

	// The old admin form's body: no purge fields at all.
	put(map[string]any{
		"tenant_id":                       tenant.String(),
		"transcript_retention_enabled":    true,
		"deleted_conversation_purge_days": 30,
		"dry_run":                         true,
	})
	got, err := settings.Get(ctx, tenant)
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if !got.DeletedPurgeEnabled || got.DeletedPurgeDryRun {
		t.Fatalf("an edit that did not mention the purge changed it: enabled=%v dry_run=%v, want true/false",
			got.DeletedPurgeEnabled, got.DeletedPurgeDryRun)
	}

	// Naming the switch still works, both ways.
	put(map[string]any{"tenant_id": tenant.String(), "deleted_conversation_purge_days": 30, "deleted_purge_enabled": false})
	if got, _ = settings.Get(ctx, tenant); got.DeletedPurgeEnabled {
		t.Fatal("deleted_purge_enabled=false was not applied")
	}
	put(map[string]any{"tenant_id": tenant.String(), "deleted_conversation_purge_days": 30})
	if got, _ = settings.Get(ctx, tenant); got.DeletedPurgeEnabled {
		t.Fatal("an unrelated edit turned the purge back on")
	}
}
