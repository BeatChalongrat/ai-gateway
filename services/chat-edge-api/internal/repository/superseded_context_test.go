package repository_test

// FIX100 Part A — DB-level proof that a regenerated row stops being
// re-loaded into the model's context, and that a FAILED row that
// produced text does not.
//
// Regenerate flips the row it replaces to status=regenerated and leaves
// is_active=true on purpose (the row keeps its token usage for audit and
// the transcript still renders it). Both context reads filtered on
// is_active alone, so the replaced row came back on every subsequent
// load — which is why the owner's conversation grew by exactly one
// message per press of Regenerate: 25 → 26 → 27.
//
// DB-gated: skips when Postgres is unreachable. Runs on a tenant of its
// own and deletes it; no existing conversation is read or written.

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func TestFIX100_SupersededRowsLeaveTheContext(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()

	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	defer pool.Exec(ctx, "DELETE FROM chat.conversations WHERE tenant_id=$1", tenantID)

	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		testdeps.Missing(t, "conv create (schema?): %v", err)
	}

	add := func(role, content, status string) *model.Message {
		t.Helper()
		m, err := mr.Create(ctx, &model.Message{
			ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
			Role: role, ContentMasked: content, Status: "streaming",
		})
		if err != nil {
			t.Fatalf("create %s: %v", role, err)
		}
		if err := mr.UpdateStatus(ctx, m.ID, status); err != nil {
			t.Fatalf("status %s: %v", status, err)
		}
		// created_at defaults to now(); space the rows so the ORDER BY
		// and the "strictly before" cursor are not deciding on a tie.
		time.Sleep(2 * time.Millisecond)
		return m
	}

	add("user", "คำถาม", "completed")
	superseded := add("assistant", "คำตอบเก่าที่ถูกแทนที่แล้ว", "regenerated")
	add("assistant", "คำตอบใหม่", "completed")
	add("user", "ถามต่อ", "completed")
	partial := add("assistant", "เริ่มตอบแล้วพัง", "error") // real text, then died
	target := add("assistant", "", "streaming")             // the row a regenerate would replace

	contents := func(ms []model.Message) []string {
		out := make([]string, 0, len(ms))
		for _, m := range ms {
			out = append(out, m.ContentMasked)
		}
		return out
	}

	// The ordinary send path.
	all, err := mr.ListContextByConversation(ctx, tenantID, conv.ID, 100)
	if err != nil {
		t.Fatalf("list context: %v", err)
	}
	for _, m := range all {
		if m.ID == superseded.ID {
			t.Fatalf("the replaced reply is still in the context of every later send: %v", contents(all))
		}
	}

	// The regenerate path: everything strictly before the target row.
	before, err := mr.ListContextBeforeMessage(ctx, tenantID, conv.ID, target.CreatedAt, 100)
	if err != nil {
		t.Fatalf("list context before: %v", err)
	}
	got := contents(before)
	for _, m := range before {
		if m.ID == superseded.ID {
			t.Fatalf("regenerate still re-loads the replaced reply; this is the 25 → 26 → 27 climb. got %v", got)
		}
	}

	// The trap: the failed row held text the person read. It stays.
	found := false
	for _, m := range before {
		if m.ID == partial.ID {
			found = true
		}
	}
	if !found {
		t.Fatalf("a status=error row that streamed real text must NOT be filtered out here — the user saw it. got %v", got)
	}
	if want := []string{"คำถาม", "คำตอบใหม่", "ถามต่อ", "เริ่มตอบแล้วพัง"}; !fix100SameStrings(got, want) {
		t.Fatalf("context = %v, want %v", got, want)
	}
}

func fix100SameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
