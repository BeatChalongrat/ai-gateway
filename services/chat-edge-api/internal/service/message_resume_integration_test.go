package service

// Phase 5 — ResumeAttach integration tests. Same harness contract as
// the repository suites: real postgres-webchat, SKIPS cleanly when the
// DB is unreachable. Covers the ownership boundary and the DB fallback
// paths (terminal replay, young-orphan grace, aged-orphan settle) that
// the pure broker unit tests can't reach.

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func resumeTestPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Skipf("skipping resume integration tests: cannot create pool: %v", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		t.Skipf("skipping resume integration tests: DB unreachable (%v)", err)
	}
	return pool
}

// resumeFixture builds a MessageService over the real repos plus one
// conversation + one assistant message in the given status.
func resumeFixture(
	t *testing.T, pool *pgxpool.Pool, status string,
) (svc *MessageService, tenantID, userID, convID, msgID uuid.UUID) {
	t.Helper()
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)

	tenantID, userID = uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{
		TenantID: tenantID, UserID: userID, ModelID: "gpt-4o",
	})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}
	msg, err := mr.Create(ctx, &model.Message{
		ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
		Role: "assistant", ContentMasked: "partial ตอบไปแล้วครึ่งเดียว",
		Status: status, IsActive: true,
	})
	if err != nil {
		t.Fatalf("create message: %v", err)
	}
	svc = NewMessageService(cr, mr, nil, nil, nil, 10)
	return svc, tenantID, userID, conv.ID, msg.ID
}

func TestResumeAttach_TerminalRowFullReplay(t *testing.T) {
	pool := resumeTestPool(t)
	defer pool.Close()
	svc, tenantID, userID, convID, msgID := resumeFixture(t, pool, "completed")

	att, err := svc.ResumeAttach(context.Background(), tenantID, userID, convID, msgID)
	if err != nil {
		t.Fatalf("resume attach: %v", err)
	}
	if att.Events != nil {
		t.Fatal("terminal row must not return a live channel")
	}
	if att.Snapshot.Content != "partial ตอบไปแล้วครึ่งเดียว" {
		t.Fatalf("full replay content = %q", att.Snapshot.Content)
	}
	if att.Snapshot.Terminal == nil || !att.Snapshot.Terminal.Done {
		t.Fatalf("terminal marker missing: %+v", att.Snapshot.Terminal)
	}
}

func TestResumeAttach_OwnershipRejected(t *testing.T) {
	pool := resumeTestPool(t)
	defer pool.Close()
	svc, tenantID, _, convID, msgID := resumeFixture(t, pool, "completed")
	ctx := context.Background()

	// Another user in the same tenant.
	if _, err := svc.ResumeAttach(ctx, tenantID, uuid.New(), convID, msgID); !errors.Is(err, ErrMessageConversationNotFound) {
		t.Fatalf("stranger reattach: err=%v, want conversation-not-found", err)
	}
	// Another tenant entirely.
	if _, err := svc.ResumeAttach(ctx, uuid.New(), uuid.New(), convID, msgID); !errors.Is(err, ErrMessageConversationNotFound) {
		t.Fatalf("cross-tenant reattach: err=%v, want conversation-not-found", err)
	}
}

func TestResumeAttach_MessageOutsideConversationRejected(t *testing.T) {
	pool := resumeTestPool(t)
	defer pool.Close()
	svc, tenantID, userID, _, msgID := resumeFixture(t, pool, "completed")
	// A second conversation owned by the SAME user — the message id
	// doesn't belong to it, so reattach must refuse (no ID-mixing).
	cr := repository.NewConversationRepo(pool)
	other, err := cr.Create(context.Background(), &model.Conversation{
		TenantID: tenantID, UserID: userID, ModelID: "gpt-4o",
	})
	if err != nil {
		t.Fatalf("create second conversation: %v", err)
	}
	if _, err := svc.ResumeAttach(context.Background(), tenantID, userID, other.ID, msgID); !errors.Is(err, ErrMessageNotFound) {
		t.Fatalf("cross-conversation reattach: err=%v, want message-not-found", err)
	}
}

func TestResumeAttach_YoungStreamingRowUnavailable(t *testing.T) {
	pool := resumeTestPool(t)
	defer pool.Close()
	svc, tenantID, userID, convID, msgID := resumeFixture(t, pool, "streaming")

	// Row was JUST created and the broker has no entry (no generation
	// registered in this process) → the client should retry, not get
	// the row killed under a live generation's feet.
	_, err := svc.ResumeAttach(context.Background(), tenantID, userID, convID, msgID)
	if !errors.Is(err, ErrResumeUnavailable) {
		t.Fatalf("young streaming row: err=%v, want ErrResumeUnavailable", err)
	}
	// And the row must NOT have been marked interrupted.
	msg, err := repository.NewMessageRepo(pool).GetByID(context.Background(), tenantID, msgID)
	if err != nil || msg.Status != "streaming" {
		t.Fatalf("row status = %q err=%v, want streaming untouched", msg.Status, err)
	}
}

func TestResumeAttach_AgedOrphanSettledToError(t *testing.T) {
	pool := resumeTestPool(t)
	defer pool.Close()
	svc, tenantID, userID, convID, msgID := resumeFixture(t, pool, "streaming")
	ctx := context.Background()

	// Backdate the row past the orphan grace — simulates a chat-edge
	// restart that lost the in-process generation.
	if _, err := pool.Exec(ctx,
		`UPDATE chat.messages_masked SET created_at = created_at - interval '5 minutes' WHERE id = $1`,
		msgID,
	); err != nil {
		t.Fatalf("backdate: %v", err)
	}

	att, err := svc.ResumeAttach(ctx, tenantID, userID, convID, msgID)
	if err != nil {
		t.Fatalf("aged orphan attach: %v", err)
	}
	if att.Events != nil {
		t.Fatal("orphan must not return a live channel")
	}
	if att.Snapshot.Terminal == nil || att.Snapshot.Terminal.Err == nil {
		t.Fatalf("orphan terminal must carry an error: %+v", att.Snapshot.Terminal)
	}
	if att.Snapshot.Content != "partial ตอบไปแล้วครึ่งเดียว" {
		t.Fatalf("orphan must replay the persisted partial: %q", att.Snapshot.Content)
	}
	// The row is settled so clients stop waiting on it forever.
	msg, err := repository.NewMessageRepo(pool).GetByID(ctx, tenantID, msgID)
	if err != nil || msg.Status != "error" {
		t.Fatalf("row status = %q err=%v, want error", msg.Status, err)
	}
}

func TestResumeAttach_LiveBrokerWinsOverRowStatus(t *testing.T) {
	pool := resumeTestPool(t)
	defer pool.Close()
	svc, tenantID, userID, convID, msgID := resumeFixture(t, pool, "streaming")

	// Register a live generation for the message and stream a prefix
	// through it — ResumeAttach must return the live channel even
	// though the DB row still says "streaming".
	in := make(chan StreamUpdate)
	out, _ := svc.teeStream(msgID, "", in)
	go func() {
		for range out {
			// primary consumer stand-in
		}
	}()
	in <- StreamUpdate{Delta: "สวัสดี "}

	deadline := time.Now().Add(2 * time.Second)
	var att *ResumeAttachment
	var err error
	for time.Now().Before(deadline) {
		att, err = svc.ResumeAttach(context.Background(), tenantID, userID, convID, msgID)
		if err != nil {
			t.Fatalf("live attach: %v", err)
		}
		if att.Events != nil && att.Snapshot.Content == "สวัสดี " {
			break
		}
		att.Cancel()
		time.Sleep(5 * time.Millisecond)
	}
	if att == nil || att.Events == nil {
		t.Fatal("live generation not attachable via broker")
	}
	defer att.Cancel()

	// Live tail flows to the reattached client.
	in <- StreamUpdate{Delta: "ชาวโลก"}
	in <- StreamUpdate{Done: true}
	close(in)
	got := ""
	for ev := range att.Events {
		got += ev.Delta
	}
	if got != "ชาวโลก" {
		t.Fatalf("live tail = %q, want %q", got, "ชาวโลก")
	}
}
