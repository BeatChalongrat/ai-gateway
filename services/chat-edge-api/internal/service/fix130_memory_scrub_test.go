package service

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// A4b — a deleted memory's text also leaves the older answers' attribution
// rows (the "used memory" popover after a reload); another account's delete
// cannot touch them.
func TestFIX130_DeletingAMemoryScrubsItFromOlderAnswers(t *testing.T) {
	f := newFix130Fixture(t)
	ctx := context.Background()
	m := f.savedMemory(t)
	srcRepo := repository.NewResponseContextSourceRepo(f.pool)
	msg := uuid.New()
	mid := m.ID
	if err := srcRepo.CreateBatch(ctx, []model.ResponseContextSource{{
		ResponseMessageID: msg, TenantID: f.tenant, UserID: f.user, ConversationID: uuid.New(),
		SourceType: model.SourceTypeSavedMemory, SourceID: &mid, DisplayLabel: fix130Secret,
	}}); err != nil {
		t.Fatal(err)
	}
	if err := f.memSvc.DeleteMemory(ctx, m.ID, uuid.New(), f.tenant); !errors.Is(err, ErrMemoryNotFound) {
		t.Fatalf("a stranger deleted the memory: %v", err)
	}
	if err := f.memSvc.DeleteMemory(ctx, m.ID, f.user, f.tenant); err != nil {
		t.Fatal(err)
	}
	rows, err := srcRepo.ListByMessage(ctx, msg, f.user, f.tenant)
	if err != nil || len(rows) != 1 {
		t.Fatalf("rows: %v %v", rows, err)
	}
	if rows[0].DisplayLabel == fix130Secret {
		t.Fatal("the deleted memory's text is still on the older answer")
	}
}
