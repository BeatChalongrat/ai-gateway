package service

// FIX127 A2 — the unattached-upload sweep.

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

type sweepFixture struct {
	pool   *pgxpool.Pool
	files  *repository.FileRepo
	audit  *repository.DeletionAuditRepo
	be     storage.Backend
	tenant uuid.UUID
	user   uuid.UUID
	conv   *model.Conversation
	msgs   *repository.MessageRepo
}

func newSweepFixture(t *testing.T) *sweepFixture {
	t.Helper()
	pool := testdb.Fresh(t)
	cr := repository.NewConversationRepo(pool)
	be, err := storage.NewLocalBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	f := &sweepFixture{
		pool:  pool,
		files: repository.NewFileRepo(pool), audit: repository.NewDeletionAuditRepo(pool),
		be: be, tenant: uuid.New(), user: uuid.New(), msgs: repository.NewMessageRepo(pool),
	}
	f.conv, err = cr.Create(context.Background(), &model.Conversation{TenantID: f.tenant, UserID: f.user, ModelID: "m", Title: "FIX127"})
	if err != nil {
		t.Fatal(err)
	}
	return f
}

// row writes a file row + blob with the given age.
func (f *sweepFixture) row(t *testing.T, name string, age time.Duration, generated bool) *model.ConversationFile {
	t.Helper()
	ctx := context.Background()
	key := "k/" + name
	if err := f.be.Upload(ctx, key, strings.NewReader("bytes"), "text/plain", 5); err != nil {
		t.Fatal(err)
	}
	row, err := f.files.Create(ctx, &model.ConversationFile{ConversationID: f.conv.ID, TenantID: f.tenant, UserID: f.user,
		Filename: name, OriginalFilename: name, ContentType: "text/plain", FileSizeBytes: 5, StoragePath: key,
		ExtractionStatus: "completed", IsGenerated: generated})
	if err != nil {
		t.Fatal(err)
	}
	if age > 0 {
		// Age the row directly; there is no API for "created two days ago".
		if _, err := f.pool.Exec(ctx, `UPDATE chat.conversation_files SET created_at = NOW() - $2::interval WHERE id = $1`,
			row.ID, age.String()); err != nil {
			t.Fatal(err)
		}
	}
	return row
}

func TestFIX127_UnattachedSweep_DryRunCountsAndDeletesNothing(t *testing.T) {
	f := newSweepFixture(t)
	old := f.row(t, "old.txt", 72*time.Hour, false)
	young := f.row(t, "young.txt", 0, false)
	sw := NewUnattachedFileSweep(f.files, f.be, f.audit, 48*time.Hour, "dry_run", time.Hour)

	st := sw.RunOnce(context.Background())

	if st.Candidates != 1 || st.Deleted != 0 || len(st.Accounts) != 1 || st.Accounts[0].Count != 1 || st.Accounts[0].Bytes != 5 {
		t.Fatalf("dry run stats = %+v", st)
	}
	for _, r := range []*model.ConversationFile{old, young} {
		if _, err := f.files.GetByID(context.Background(), f.tenant, r.ID); err != nil {
			t.Fatalf("dry run deleted %s: %v", r.OriginalFilename, err)
		}
		if ok, _ := f.be.Exists(context.Background(), r.StoragePath); !ok {
			t.Fatalf("dry run removed the blob of %s", r.OriginalFilename)
		}
	}
	// FIX128 — one summary row under the system tenant, per-tenant counts in details.
	entries, _ := f.audit.ListByTenant(context.Background(), SweepAuditTenant, "", 10)
	if len(entries) != 1 || entries[0].Action != model.AuditActionRetentionDryRun || entries[0].Counts["files"] != 1 {
		t.Fatalf("audit = %+v", entries)
	}
	if per, _ := entries[0].Details["per_tenant"].(map[string]any); per[f.tenant.String()] == nil {
		t.Fatalf("per-tenant count missing: %+v", entries[0].Details)
	}
	if own, _ := f.audit.ListByTenant(context.Background(), f.tenant, "", 10); len(own) != 0 {
		t.Fatalf("a per-account row was written: %+v", own)
	}
}

// FIX128 — the dry-run audit is written only when the numbers move: the same
// pass again, or a restart (a new sweep), writes nothing; a new candidate
// writes one more summary.
func TestFIX128_DryRunAuditOnlyWhenTheNumbersChange(t *testing.T) {
	f := newSweepFixture(t)
	ctx := context.Background()
	f.row(t, "old.txt", 72*time.Hour, false)
	count := func() int {
		e, _ := f.audit.ListByTenant(ctx, SweepAuditTenant, model.AuditActionRetentionDryRun, 100)
		return len(e)
	}
	sw := NewUnattachedFileSweep(f.files, f.be, f.audit, 48*time.Hour, "dry_run", time.Hour)
	sw.RunOnce(ctx)
	sw.RunOnce(ctx)
	NewUnattachedFileSweep(f.files, f.be, f.audit, 48*time.Hour, "dry_run", time.Hour).RunOnce(ctx) // a restart
	if n := count(); n != 1 {
		t.Fatalf("unchanged numbers wrote %d dry-run rows, want 1", n)
	}
	f.row(t, "old2.txt", 72*time.Hour, false)
	sw.RunOnce(ctx)
	if n := count(); n != 2 {
		t.Fatalf("a new candidate wrote %d rows in total, want 2", n)
	}
}

func TestFIX127_UnattachedSweep_DeleteRemovesOnlyOldUnattachedUploads(t *testing.T) {
	f := newSweepFixture(t)
	ctx := context.Background()
	old := f.row(t, "old.txt", 72*time.Hour, false)
	young := f.row(t, "young.txt", 0, false)
	attached := f.row(t, "attached.txt", 72*time.Hour, false)
	generated := f.row(t, "made.xlsx", 72*time.Hour, true)
	msg, _ := f.msgs.Create(ctx, &model.Message{ConversationID: f.conv.ID, TenantID: f.tenant, UserID: f.user, Role: "user", ContentMasked: "hi", Status: "completed", IsActive: true})
	if n, err := f.files.AttachToMessage(ctx, f.tenant, f.conv.ID, msg.ID, []uuid.UUID{attached.ID}); err != nil || n != 1 {
		t.Fatalf("attach: %d %v", n, err)
	}
	sw := NewUnattachedFileSweep(f.files, f.be, f.audit, 48*time.Hour, "delete", time.Hour)

	st := sw.RunOnce(ctx)

	if st.Deleted != 1 || st.Candidates != 1 || st.Errors != 0 {
		t.Fatalf("stats = %+v", st)
	}
	if _, err := f.files.GetByID(ctx, f.tenant, old.ID); err != repository.ErrNotFound {
		t.Fatalf("old unattached row still there: %v", err)
	}
	if ok, _ := f.be.Exists(ctx, old.StoragePath); ok {
		t.Fatal("old unattached blob still in storage")
	}
	for _, keep := range []*model.ConversationFile{young, attached, generated} {
		if _, err := f.files.GetByID(ctx, f.tenant, keep.ID); err != nil {
			t.Fatalf("%s was deleted: %v", keep.OriginalFilename, err)
		}
		if ok, _ := f.be.Exists(ctx, keep.StoragePath); !ok {
			t.Fatalf("%s blob was deleted", keep.OriginalFilename)
		}
	}
	entries, _ := f.audit.ListByTenant(ctx, SweepAuditTenant, "", 10)
	if len(entries) != 1 || entries[0].Action != model.AuditActionFileDeleted || entries[0].Rule != "unattached_upload_expired" || entries[0].Counts["files"] != 1 {
		t.Fatalf("audit = %+v", entries)
	}
	// A second pass finds nothing and writes no audit row.
	if st2 := sw.RunOnce(ctx); st2.Candidates != 0 || st2.Deleted != 0 {
		t.Fatalf("second pass = %+v", st2)
	}
}

func TestFIX127_UnattachedSweep_ModeDefaultsToDryRunAndOffRunsNothing(t *testing.T) {
	f := newSweepFixture(t)
	f.row(t, "old.txt", 72*time.Hour, false)
	if sw := NewUnattachedFileSweep(f.files, f.be, f.audit, 0, "yes please", 0); sw.Mode != "dry_run" || sw.TTL != 48*time.Hour || sw.Interval != time.Hour {
		t.Fatalf("defaults = %+v", sw)
	}
	off := NewUnattachedFileSweep(f.files, f.be, f.audit, 48*time.Hour, "off", time.Hour)
	if st := off.RunOnce(context.Background()); st.Candidates != 0 || len(st.Accounts) != 0 {
		t.Fatalf("off mode ran: %+v", st)
	}
}
