package repository_test

// Canvas-artifact integration tests (migration 030). Same harness
// contract as project_repo_test.go: real postgres-webchat schema, SKIPS
// cleanly when the DB is unreachable or the migration isn't applied.

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func canvasArtifactPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	pool := testPool(t)
	var reg *string
	if err := pool.QueryRow(context.Background(),
		"SELECT to_regclass('chat.canvas_artifacts')::text").Scan(&reg); err != nil || reg == nil {
		pool.Close()
		t.Skip("skipping: chat.canvas_artifacts not present — apply migration 030")
	}
	return pool
}

func TestCanvasArtifactRepo_VersioningAndListing(t *testing.T) {
	pool := canvasArtifactPool(t)
	defer pool.Close()
	ctx := context.Background()
	ar := repository.NewCanvasArtifactRepo(pool)
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	conv := newConv(t, ctx, cr, tenantID, userID)
	t.Cleanup(func() {
		// Hard delete cascades the artifacts away (verified separately in
		// TestCanvasArtifactRepo_ConversationDeleteCascades).
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.conversations WHERE tenant_id = $1", tenantID)
	})

	// save v1 → save v2: versions auto-increment per (conversation, key).
	v1, err := ar.CreateVersion(ctx, tenantID, userID, conv.ID, nil, "report.html", "html", "รายงาน v1", "<h1>v1</h1>")
	if err != nil || v1.Version != 1 {
		t.Fatalf("create v1: %+v err=%v", v1, err)
	}
	if v1.Title == nil || *v1.Title != "รายงาน v1" {
		t.Fatalf("v1 title: %+v", v1.Title)
	}
	v2, err := ar.CreateVersion(ctx, tenantID, userID, conv.ID, nil, "report.html", "html", "รายงาน v2", "<h1>v2</h1>")
	if err != nil || v2.Version != 2 {
		t.Fatalf("create v2: %+v err=%v", v2, err)
	}

	// ListVersions → [v2, v1] (version DESC).
	versions, err := ar.ListVersions(ctx, tenantID, conv.ID, "report.html")
	if err != nil || len(versions) != 2 {
		t.Fatalf("list versions: %d err=%v", len(versions), err)
	}
	if versions[0].Version != 2 || versions[1].Version != 1 {
		t.Fatalf("version order wrong: [%d, %d]", versions[0].Version, versions[1].Version)
	}

	// ListLatest → only v2 for the key.
	latest, err := ar.ListLatest(ctx, tenantID, conv.ID)
	if err != nil || len(latest) != 1 {
		t.Fatalf("list latest: %d err=%v", len(latest), err)
	}
	if latest[0].Version != 2 || latest[0].Content != "<h1>v2</h1>" {
		t.Fatalf("latest is not v2: %+v", latest[0])
	}

	// A second key in the same conversation: both appear in ListLatest,
	// one row each at their own latest version.
	if _, err := ar.CreateVersion(ctx, tenantID, userID, conv.ID, nil, "chart.svg", "svg", "", "<svg/>"); err != nil {
		t.Fatalf("create second key: %v", err)
	}
	latest, err = ar.ListLatest(ctx, tenantID, conv.ID)
	if err != nil || len(latest) != 2 {
		t.Fatalf("list latest two keys: %d err=%v", len(latest), err)
	}
	byKey := map[string]int{}
	for _, a := range latest {
		byKey[a.ArtifactKey] = a.Version
	}
	if byKey["report.html"] != 2 || byKey["chart.svg"] != 1 {
		t.Fatalf("latest per key wrong: %v", byKey)
	}
	// Empty title stored as NULL → nil pointer.
	if got, err := ar.GetLatest(ctx, tenantID, conv.ID, "chart.svg"); err != nil || got.Title != nil {
		t.Fatalf("empty title should scan nil: %+v err=%v", got, err)
	}
}

func TestCanvasArtifactRepo_RevertShapeKeepsHistory(t *testing.T) {
	pool := canvasArtifactPool(t)
	defer pool.Close()
	ctx := context.Background()
	ar := repository.NewCanvasArtifactRepo(pool)
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	conv := newConv(t, ctx, cr, tenantID, userID)
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.conversations WHERE tenant_id = $1", tenantID)
	})

	_, _ = ar.CreateVersion(ctx, tenantID, userID, conv.ID, nil, "app.jsx", "react", "", "const v = 1")
	_, _ = ar.CreateVersion(ctx, tenantID, userID, conv.ID, nil, "app.jsx", "react", "", "const v = 2")

	// Revert shape: read v1, append its content as a NEW version.
	src, err := ar.GetVersion(ctx, tenantID, conv.ID, "app.jsx", 1)
	if err != nil {
		t.Fatalf("get v1: %v", err)
	}
	v3, err := ar.CreateVersion(ctx, tenantID, userID, conv.ID, nil, "app.jsx", src.Type, "", src.Content)
	if err != nil || v3.Version != 3 || v3.Content != "const v = 1" {
		t.Fatalf("revert-shape create: %+v err=%v", v3, err)
	}
	// History intact: all 3 rows still present.
	versions, err := ar.ListVersions(ctx, tenantID, conv.ID, "app.jsx")
	if err != nil || len(versions) != 3 {
		t.Fatalf("history not intact: %d err=%v", len(versions), err)
	}
	// Latest resolves to the revert copy.
	if got, err := ar.GetLatest(ctx, tenantID, conv.ID, "app.jsx"); err != nil ||
		got.Version != 3 || got.Content != "const v = 1" {
		t.Fatalf("latest after revert: %+v err=%v", got, err)
	}
}

func TestCanvasArtifactRepo_TenantAndConversationScoping(t *testing.T) {
	pool := canvasArtifactPool(t)
	defer pool.Close()
	ctx := context.Background()
	ar := repository.NewCanvasArtifactRepo(pool)
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	conv := newConv(t, ctx, cr, tenantID, userID)
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(),
			"DELETE FROM chat.conversations WHERE tenant_id = $1", tenantID)
	})

	if _, err := ar.CreateVersion(ctx, tenantID, userID, conv.ID, nil, "secret.md", "markdown", "", "# ลับ"); err != nil {
		t.Fatalf("create: %v", err)
	}

	// Another tenant sees an empty ListLatest and not-found on the rest,
	// even with the leaked conversation id + key.
	otherTenant := uuid.New()
	if list, err := ar.ListLatest(ctx, otherTenant, conv.ID); err != nil || len(list) != 0 {
		t.Fatalf("cross-tenant ListLatest leaked: %d err=%v", len(list), err)
	}
	if _, err := ar.ListVersions(ctx, otherTenant, conv.ID, "secret.md"); err == nil {
		t.Fatal("cross-tenant ListVersions should be not-found")
	}
	if _, err := ar.GetVersion(ctx, otherTenant, conv.ID, "secret.md", 1); err == nil {
		t.Fatal("cross-tenant GetVersion should be not-found")
	}
	if _, err := ar.GetLatest(ctx, otherTenant, conv.ID, "secret.md"); err == nil {
		t.Fatal("cross-tenant GetLatest should be not-found")
	}

	// Another conversation in the SAME tenant likewise scopes to empty —
	// the key alone never resolves a row. (Per-user ownership is asserted
	// one layer up via MessageService.AssertConversationAccess; the repo
	// contract is tenant+conversation scoping.)
	otherConv := newConv(t, ctx, cr, tenantID, userID)
	if list, err := ar.ListLatest(ctx, tenantID, otherConv.ID); err != nil || len(list) != 0 {
		t.Fatalf("cross-conversation ListLatest leaked: %d err=%v", len(list), err)
	}
	if _, err := ar.ListVersions(ctx, tenantID, otherConv.ID, "secret.md"); err == nil {
		t.Fatal("cross-conversation ListVersions should be not-found")
	}
}

func TestCanvasArtifactRepo_ConversationDeleteCascades(t *testing.T) {
	pool := canvasArtifactPool(t)
	defer pool.Close()
	ctx := context.Background()
	ar := repository.NewCanvasArtifactRepo(pool)
	cr := repository.NewConversationRepo(pool)

	tenantID, userID := uuid.New(), uuid.New()
	conv := newConv(t, ctx, cr, tenantID, userID)

	if _, err := ar.CreateVersion(ctx, tenantID, userID, conv.ID, nil, "doomed.html", "html", "", "<p>bye</p>"); err != nil {
		t.Fatalf("create: %v", err)
	}

	// The API's conversation delete is a SOFT delete (is_deleted flag) —
	// that leaves artifact rows in place by design. The ON DELETE CASCADE
	// fires on the HARD delete (retention purge / test cleanup path).
	if _, err := pool.Exec(ctx, "DELETE FROM chat.conversations WHERE id = $1 AND tenant_id = $2", conv.ID, tenantID); err != nil {
		t.Fatalf("hard delete conversation: %v", err)
	}
	var count int
	if err := pool.QueryRow(ctx,
		"SELECT COUNT(*) FROM chat.canvas_artifacts WHERE conversation_id = $1", conv.ID).Scan(&count); err != nil {
		t.Fatalf("count after cascade: %v", err)
	}
	if count != 0 {
		t.Fatalf("expected 0 artifacts after conversation hard delete, got %d", count)
	}
}
