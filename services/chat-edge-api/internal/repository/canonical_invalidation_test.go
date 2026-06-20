package repository_test

// Phase 4A.2.2.1 — DB-level proof that a display-only update INVALIDATES a
// prior complete canonical (H3/§5). DB-gated: skips when Postgres/migration
// is unavailable.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"testing"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func TestCanonicalInvalidationDB(t *testing.T) {
	pool := testPool(t) // skips if DB unreachable / base schema missing
	ctx := context.Background()
	if sqlBytes, err := os.ReadFile("../../migrations/024_add_message_canonical_artifact.sql"); err == nil {
		_, _ = pool.Exec(ctx, string(sqlBytes)) // idempotent
	}

	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	mr.SetCanonicalHistory(true)
	tenantID, userID := uuid.New(), uuid.New()
	defer pool.Exec(ctx, "DELETE FROM chat.conversations WHERE tenant_id=$1", tenantID)

	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Skipf("conv create (schema?): %v", err)
	}
	asst, err := mr.Create(ctx, &model.Message{
		ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
		Role: "assistant", ContentMasked: "pending", Status: "streaming",
	})
	if err != nil {
		t.Fatalf("msg create: %v", err)
	}

	// Write a complete canonical.
	cc := "ผลลัพธ์ <r0>"
	sum := sha256.Sum256([]byte(cc))
	sha := hex.EncodeToString(sum[:])
	ln, sv, gen := utf8.RuneCountInString(cc), 1, "req_g"
	if err := mr.UpdateContentAndCanonical(ctx, asst.ID, "display 50000", "completed", nil, nil,
		repository.CanonicalWrite{Status: "complete", Content: &cc, SchemaVersion: &sv, GenerationID: &gen, SHA256: &sha, Length: &ln}); err != nil {
		t.Fatalf("complete write: %v", err)
	}
	hist, _ := mr.ListContextByConversation(ctx, tenantID, conv.ID, 50)
	if hist[0].CanonicalStatus != "complete" || hist[0].ContentCanonical == nil {
		t.Fatalf("precondition: row should be complete, got %+v", hist[0])
	}

	// Display-only update must INVALIDATE the stale canonical (H3).
	if err := mr.UpdateContentInvalidateCanonical(ctx, asst.ID, "new display 99999", "completed",
		"unavailable", "display_only_update", "", nil, nil); err != nil {
		t.Fatalf("invalidate: %v", err)
	}
	hist, _ = mr.ListContextByConversation(ctx, tenantID, conv.ID, 50)
	r := hist[0]
	if r.CanonicalStatus == "complete" {
		t.Fatalf("stale canonical NOT invalidated: status still complete")
	}
	if r.ContentCanonical != nil {
		t.Fatalf("stale canonical content NOT cleared: %q", *r.ContentCanonical)
	}
	if r.ContentMasked != "new display 99999" {
		t.Fatalf("display not updated: %q", r.ContentMasked)
	}
}
