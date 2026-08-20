package service

// Batch 2 T3 — share links are SNAPSHOTS. Same harness contract as the
// resume integration suite: real postgres-webchat, SKIPS cleanly when
// the DB is unreachable. Pins the four load-bearing behaviours:
//   1. a message sent AFTER sharing never appears on the resolved view;
//   2. re-sharing takes a fresh snapshot (and rotates the token);
//   3. a hard-deleted snapshotted message drops out (deletion wins);
//   4. a legacy pre-033 row (NULL snapshot) is lazily frozen on first
//      resolve so it can never publish a future message again.
// Expiry/revoke behaviour is untouched by T3 and covered by 5 below.

import (
	"context"
	"errors"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func shareTestPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Skipf("skipping share integration tests: cannot create pool: %v", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		t.Skipf("skipping share integration tests: DB unreachable (%v)", err)
	}
	return pool
}

// shareFixture builds a ShareService over the real repos plus one
// conversation with two messages.
func shareFixture(
	t *testing.T, pool *pgxpool.Pool,
) (svc *ShareService, mr *repository.MessageRepo, tenantID, userID, convID uuid.UUID) {
	t.Helper()
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr = repository.NewMessageRepo(pool)
	sr := repository.NewShareRepo(pool)

	tenantID, userID = uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{
		TenantID: tenantID, UserID: userID, ModelID: "gpt-4o",
	})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	seedMsg(t, mr, tenantID, userID, conv.ID, "user", "คำถามแรก")
	seedMsg(t, mr, tenantID, userID, conv.ID, "assistant", "คำตอบแรก")

	svc = NewShareService(sr, cr, mr, nil)
	return svc, mr, tenantID, userID, conv.ID
}

func seedMsg(
	t *testing.T, mr *repository.MessageRepo,
	tenantID, userID, convID uuid.UUID, role, content string,
) uuid.UUID {
	t.Helper()
	msg, err := mr.Create(context.Background(), &model.Message{
		ConversationID: convID, TenantID: tenantID, UserID: userID,
		Role: role, ContentMasked: content,
		Status: "completed", IsActive: true,
	})
	if err != nil {
		t.Fatalf("create message: %v", err)
	}
	return msg.ID
}

func contents(msgs []model.Message) []string {
	out := make([]string, 0, len(msgs))
	for i := range msgs {
		out = append(out, msgs[i].ContentMasked)
	}
	return out
}

func TestShareSnapshot_MessageSentAfterSharingStaysPrivate(t *testing.T) {
	pool := shareTestPool(t)
	defer pool.Close()
	svc, mr, tenantID, userID, convID := shareFixture(t, pool)
	ctx := context.Background()

	share, err := svc.Create(ctx, tenantID, userID, convID, "never")
	if err != nil {
		t.Fatalf("create share: %v", err)
	}

	// The conversation continues AFTER sharing.
	seedMsg(t, mr, tenantID, userID, convID, "user", "ความลับหลังแชร์")

	view, err := svc.Resolve(ctx, share.ShareToken)
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	got := contents(view.Messages)
	if len(got) != 2 || got[0] != "คำถามแรก" || got[1] != "คำตอบแรก" {
		t.Fatalf("snapshot leaked or lost messages: %v", got)
	}
}

func TestShareSnapshot_ReShareTakesAFreshSnapshotAndRotatesTheToken(t *testing.T) {
	pool := shareTestPool(t)
	defer pool.Close()
	svc, mr, tenantID, userID, convID := shareFixture(t, pool)
	ctx := context.Background()

	first, err := svc.Create(ctx, tenantID, userID, convID, "never")
	if err != nil {
		t.Fatalf("first share: %v", err)
	}
	seedMsg(t, mr, tenantID, userID, convID, "user", "ข้อความใหม่")

	second, err := svc.Create(ctx, tenantID, userID, convID, "never")
	if err != nil {
		t.Fatalf("second share: %v", err)
	}
	if second.ShareToken == first.ShareToken {
		t.Fatal("re-share must rotate the token")
	}
	// The old token is dead (one link per conversation)…
	if _, err := svc.Resolve(ctx, first.ShareToken); !errors.Is(err, ErrShareNotFound) {
		t.Fatalf("old token: err=%v, want not-found", err)
	}
	// …and the new link carries the post-continuation state.
	view, err := svc.Resolve(ctx, second.ShareToken)
	if err != nil {
		t.Fatalf("resolve second: %v", err)
	}
	if got := contents(view.Messages); len(got) != 3 || got[2] != "ข้อความใหม่" {
		t.Fatalf("fresh snapshot wrong: %v", got)
	}
}

func TestShareSnapshot_HardDeletedMessageDropsOut(t *testing.T) {
	pool := shareTestPool(t)
	defer pool.Close()
	svc, _, tenantID, userID, convID := shareFixture(t, pool)
	ctx := context.Background()

	share, err := svc.Create(ctx, tenantID, userID, convID, "never")
	if err != nil {
		t.Fatalf("create share: %v", err)
	}

	// Simulate retention/purge hard-deleting the assistant row.
	if _, err := pool.Exec(ctx,
		`DELETE FROM chat.messages_masked
		  WHERE conversation_id = $1 AND role = 'assistant'`, convID); err != nil {
		t.Fatalf("hard delete: %v", err)
	}

	view, err := svc.Resolve(ctx, share.ShareToken)
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	if got := contents(view.Messages); len(got) != 1 || got[0] != "คำถามแรก" {
		t.Fatalf("deleted content must not survive via the share: %v", got)
	}
}

func TestShareSnapshot_LegacyLiveRowIsFrozenOnFirstResolve(t *testing.T) {
	pool := shareTestPool(t)
	defer pool.Close()
	svc, mr, tenantID, userID, convID := shareFixture(t, pool)
	ctx := context.Background()

	share, err := svc.Create(ctx, tenantID, userID, convID, "never")
	if err != nil {
		t.Fatalf("create share: %v", err)
	}
	// Regress the row to the pre-033 shape (a backfill miss).
	if _, err := pool.Exec(ctx,
		`UPDATE chat.shared_conversations SET snapshot_message_ids = NULL WHERE id = $1`,
		share.ID); err != nil {
		t.Fatalf("null snapshot: %v", err)
	}

	// First resolve freezes it at the CURRENT state (still 2 messages).
	if view, err := svc.Resolve(ctx, share.ShareToken); err != nil || len(view.Messages) != 2 {
		t.Fatalf("first resolve: err=%v msgs=%d", err, len(view.Messages))
	}
	// A later message must now stay private — the lazy heal wrote the
	// snapshot back.
	seedMsg(t, mr, tenantID, userID, convID, "user", "หลังการเยียวยา")
	view, err := svc.Resolve(ctx, share.ShareToken)
	if err != nil {
		t.Fatalf("second resolve: %v", err)
	}
	if len(view.Messages) != 2 {
		t.Fatalf("lazy heal failed — later message leaked: %v", contents(view.Messages))
	}
}

func TestShareSnapshot_ExpiryAndRevokeStillWork(t *testing.T) {
	pool := shareTestPool(t)
	defer pool.Close()
	svc, _, tenantID, userID, convID := shareFixture(t, pool)
	ctx := context.Background()

	// Expired links 404 (same as before T3).
	share, err := svc.Create(ctx, tenantID, userID, convID, "1h")
	if err != nil {
		t.Fatalf("create share: %v", err)
	}
	if _, err := pool.Exec(ctx,
		`UPDATE chat.shared_conversations SET expires_at = now() - interval '1 minute' WHERE id = $1`,
		share.ID); err != nil {
		t.Fatalf("age share: %v", err)
	}
	if _, err := svc.Resolve(ctx, share.ShareToken); !errors.Is(err, ErrShareNotFound) {
		t.Fatalf("expired resolve: err=%v, want not-found", err)
	}

	// Revoke 404s the token.
	share2, err := svc.Create(ctx, tenantID, userID, convID, "never")
	if err != nil {
		t.Fatalf("re-create share: %v", err)
	}
	if err := svc.Revoke(ctx, tenantID, userID, convID); err != nil {
		t.Fatalf("revoke: %v", err)
	}
	if _, err := svc.Resolve(ctx, share2.ShareToken); !errors.Is(err, ErrShareNotFound) {
		t.Fatalf("revoked resolve: err=%v, want not-found", err)
	}
}
