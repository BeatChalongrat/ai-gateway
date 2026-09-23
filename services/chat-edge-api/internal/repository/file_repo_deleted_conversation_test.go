package repository_test

// FIX129 B1 — a deleted chat's attachments must be unreachable.
//
// Verified live before this card: DELETE /conversations/{id} soft-deletes
// the row (is_deleted = true), the chat 404s, and yet
// GET /files/{id}/download and /files/{id}/text kept answering 200 with
// the full file. Every read of a single file goes through
// FileRepo.GetByID, so that is where the rule lives: a row whose
// conversation is soft-deleted does not exist to a reader. Restoring
// the conversation brings the files back — the rule is a join, not a
// purge, because the retention program is what actually erases.

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// seedFileInConversation creates one conversation for (tenant, user) and
// one uploaded file row inside it. Returns the ids.
func seedFileInConversation(
	t *testing.T, ctx context.Context, pool *pgxpool.Pool, tenantID, userID uuid.UUID,
) (convID, fileID uuid.UUID) {
	t.Helper()
	cr := repository.NewConversationRepo(pool)
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "gpt-4o-mini", Title: "fix129"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	fr := repository.NewFileRepo(pool)
	f, err := fr.Create(ctx, &model.ConversationFile{
		ConversationID:   conv.ID,
		TenantID:         tenantID,
		UserID:           userID,
		Filename:         "fix129.txt",
		OriginalFilename: "fix129.txt",
		ContentType:      "text/plain",
		FileSizeBytes:    5,
		StoragePath:      fmt.Sprintf("fix129/%s.txt", uuid.NewString()),
	})
	if err != nil {
		t.Fatalf("create file: %v", err)
	}
	return conv.ID, f.ID
}

func TestFileRepo_GetByID_HiddenWhileConversationIsDeleted(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()
	tenantID, userID := uuid.New(), uuid.New()
	convID, fileID := seedFileInConversation(t, ctx, pool, tenantID, userID)

	files := repository.NewFileRepo(pool)
	convs := repository.NewConversationRepo(pool)

	// Live conversation: the file is found.
	if _, err := files.GetByID(ctx, tenantID, fileID); err != nil {
		t.Fatalf("live conversation: GetByID = %v, want the row", err)
	}

	// Soft-delete the conversation exactly as DELETE /conversations/{id} does.
	if err := convs.SoftDelete(ctx, tenantID, convID); err != nil {
		t.Fatalf("soft delete: %v", err)
	}
	if _, err := files.GetByID(ctx, tenantID, fileID); !errors.Is(err, repository.ErrNotFound) {
		t.Fatalf("deleted conversation: GetByID err = %v, want ErrNotFound (the file must be unreachable)", err)
	}

	// Restore: the rule is a join, not a purge — the file comes back.
	if err := convs.Restore(ctx, tenantID, convID); err != nil {
		t.Fatalf("restore: %v", err)
	}
	if _, err := files.GetByID(ctx, tenantID, fileID); err != nil {
		t.Fatalf("restored conversation: GetByID = %v, want the row again", err)
	}
}

// ListVersions backs GET /conversations/{convID}/files/{fileID}/versions,
// which filters on conversation id and user only — it never consults the
// conversation row. Same rule: a deleted conversation lists nothing.
func TestFileRepo_ListVersions_EmptyWhileConversationIsDeleted(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()
	tenantID, userID := uuid.New(), uuid.New()
	convID, fileID := seedFileInConversation(t, ctx, pool, tenantID, userID)

	files := repository.NewFileRepo(pool)
	convs := repository.NewConversationRepo(pool)

	if vs, err := files.ListVersions(ctx, tenantID, fileID); err != nil || len(vs) != 1 {
		t.Fatalf("live conversation: ListVersions = %d rows, err %v; want 1 row", len(vs), err)
	}
	if err := convs.SoftDelete(ctx, tenantID, convID); err != nil {
		t.Fatalf("soft delete: %v", err)
	}
	if vs, err := files.ListVersions(ctx, tenantID, fileID); err != nil || len(vs) != 0 {
		t.Fatalf("deleted conversation: ListVersions = %d rows, err %v; want 0 rows", len(vs), err)
	}
	if err := convs.Restore(ctx, tenantID, convID); err != nil {
		t.Fatalf("restore: %v", err)
	}
	if vs, err := files.ListVersions(ctx, tenantID, fileID); err != nil || len(vs) != 1 {
		t.Fatalf("restored conversation: ListVersions = %d rows, err %v; want 1 row", len(vs), err)
	}
}
