package repository_test

// Thai-substring search integration tests (migration 028).
//
// Same harness contract as folder_repo_test.go: runs against the real
// postgres-webchat schema, SKIPS cleanly when the DB is unreachable or
// migration 028 (pg_trgm) isn't applied, so CI without a database
// stays green. The /search endpoint contract is untouched — these
// tests pin the repo layer the endpoint sits on.

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func trigramPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	pool := testPool(t) // skips when DB unreachable / schema missing
	var ext *string
	if err := pool.QueryRow(context.Background(),
		"SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'").Scan(&ext); err != nil || ext == nil {
		pool.Close()
		testdeps.Missing(t, "skipping: pg_trgm not installed — apply migration 028")
	}
	return pool
}

// seedSearchFixture inserts a conversation + one user message and
// registers cleanup. Direct SQL so the fixture doesn't depend on the
// full message-service stack; the 016 trigger fills search_vector.
func seedSearchFixture(
	t *testing.T, ctx context.Context, pool *pgxpool.Pool,
	tenantID, userID uuid.UUID, title, content string,
) (convID, msgID uuid.UUID) {
	t.Helper()
	convID = uuid.New()
	msgID = uuid.New()
	_, err := pool.Exec(ctx, `
		INSERT INTO chat.conversations (id, tenant_id, user_id, title, model_id, message_count)
		VALUES ($1, $2, $3, $4, 'gpt-4o-mini', 1)`,
		convID, tenantID, userID, title)
	if err != nil {
		t.Fatalf("seed conversation: %v", err)
	}
	_, err = pool.Exec(ctx, `
		INSERT INTO chat.messages_masked (id, conversation_id, tenant_id, user_id, role, content_masked, status)
		VALUES ($1, $2, $3, $4, 'user', $5, 'completed')`,
		msgID, convID, tenantID, userID, content)
	if err != nil {
		t.Fatalf("seed message: %v", err)
	}
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(), `DELETE FROM chat.messages_masked WHERE conversation_id = $1`, convID)
		_, _ = pool.Exec(context.Background(), `DELETE FROM chat.conversations WHERE id = $1`, convID)
	})
	return convID, msgID
}

func TestSearchUserMessages_ThaiSubstringMatch(t *testing.T) {
	pool := trigramPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	repo := repository.NewMessageRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	// Thai written WITHOUT spaces — the exact case tsvector cannot
	// match: the query is a mid-phrase substring of the content.
	_, msgID := seedSearchFixture(t, ctx, pool, tenantID, userID,
		"งานเอกสาร", "ใบเสนอราคาลูกค้ากรุณาตรวจสอบก่อนส่ง")

	hits, err := repo.SearchUserMessages(ctx, userID, tenantID, "เสนอราคา", 10)
	if err != nil {
		t.Fatalf("thai search: %v", err)
	}
	found := false
	for _, h := range hits {
		if h.MessageID == msgID {
			found = true
			if h.Rank <= 0 {
				t.Errorf("thai hit should carry a positive similarity rank, got %v", h.Rank)
			}
		}
	}
	if !found {
		t.Fatalf("substring 'เสนอราคา' did not find the seeded message; hits=%d", len(hits))
	}
}

func TestSearchUserMessages_EnglishStillWorks(t *testing.T) {
	pool := trigramPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	repo := repository.NewMessageRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	_, msgID := seedSearchFixture(t, ctx, pool, tenantID, userID,
		"Work docs", "the quarterly revenue report is ready for review")

	hits, err := repo.SearchUserMessages(ctx, userID, tenantID, "quarterly report", 10)
	if err != nil {
		t.Fatalf("english search: %v", err)
	}
	found := false
	for _, h := range hits {
		if h.MessageID == msgID {
			found = true
		}
	}
	if !found {
		t.Fatalf("tsvector branch regressed: 'quarterly report' missed the seeded message; hits=%d", len(hits))
	}
}

func TestSearchUserMessages_OwnershipScopeHoldsOnTrigramBranch(t *testing.T) {
	pool := trigramPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	repo := repository.NewMessageRepo(pool)

	tenantID, ownerID := uuid.New(), uuid.New()
	seedSearchFixture(t, ctx, pool, tenantID, ownerID,
		"งานเอกสาร", "ใบเสนอราคาลูกค้ากรุณาตรวจสอบ")

	// A different user in the same tenant must see nothing.
	stranger := uuid.New()
	hits, err := repo.SearchUserMessages(ctx, stranger, tenantID, "เสนอราคา", 10)
	if err != nil {
		t.Fatalf("scope search: %v", err)
	}
	if len(hits) != 0 {
		t.Fatalf("trigram branch leaked another user's messages: %d hits", len(hits))
	}
}

func TestSearchTitlesForUser_ThaiSubstringMatch(t *testing.T) {
	pool := trigramPool(t)
	t.Cleanup(pool.Close) // registered first -> closes last, after row cleanup
	ctx := context.Background()
	repo := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	convID, _ := seedSearchFixture(t, ctx, pool, tenantID, userID,
		"สรุปใบเสนอราคาประจำเดือน", "เนื้อหา")

	hits, err := repo.SearchTitlesForUser(ctx, tenantID, userID, "เสนอราคา", 10)
	if err != nil {
		t.Fatalf("thai title search: %v", err)
	}
	found := false
	for _, h := range hits {
		if h.ID == convID {
			found = true
		}
	}
	if !found {
		t.Fatalf("substring 'เสนอราคา' did not find the seeded conversation title; hits=%d", len(hits))
	}
}
