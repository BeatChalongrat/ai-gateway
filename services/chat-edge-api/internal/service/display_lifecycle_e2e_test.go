package service

// Phase 4A.2.4 — Display Lifecycle DB-backed tests. Verify the WRITE path no
// longer persists restored raw display under the flag, and that provider
// history remains canonical-only (regression). DB-gated: skips without Postgres.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func dlPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	// Own database per test, dropped when the test ends — no
	// cleanup code, so none can silently fail (internal/testdb).
	return testdb.Fresh(t)
}

func dlService(pool *pgxpool.Pool) (*MessageService, *repository.MessageRepo) {
	mr := repository.NewMessageRepo(pool)
	mr.SetCanonicalHistory(true)
	mr.SetDisplayLifecycle(true)
	svc := &MessageService{messages: mr}
	svc.SetCanonicalHistory(true, false)
	svc.SetDisplayLifecycle(true, false, false)
	return svc, mr
}

func TestDisplayLifecycleWritePath_NoRawAtRest(t *testing.T) {
	pool := dlPool(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	svc, mr := dlService(pool)
	tenantID, userID := uuid.New(), uuid.New()
	defer pool.Exec(ctx, "DELETE FROM chat.conversations WHERE tenant_id=$1", tenantID)
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Skipf("conv create (schema?): %v", err)
	}

	mk := func() *model.Message {
		m, _ := mr.Create(ctx, &model.Message{
			ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
			Role: "assistant", ContentMasked: "x", Status: "streaming",
		})
		return m
	}

	// Complete canonical → content_masked stores the TOKENIZED canonical, not
	// the restored raw display.
	canon := "จำไว้แล้วว่าเงินเดือนคือ <<EMAIL_1>>"
	sha, ln := canonSig(canon)
	art := &CanonicalArtifact{
		SchemaVersion: 1, GenerationID: "req_dl1", RequestID: "req_dl1",
		CanonicalContent: canon, CanonicalSHA256: sha, CanonicalLength: ln, Status: "complete",
	}
	st := &CanonicalStreamState{CapabilitySeen: true, Artifact: art}
	complete := mk()
	svc.persistFinishContent(ctx, complete.ID,
		"จำไว้แล้วว่าเงินเดือนคือ 50000 alice@x.com", "completed", nil, nil, st, true)

	// Non-complete (no artifact) → redacted placeholder, NEVER partial raw.
	nonComplete := mk()
	svc.persistFinishContent(ctx, nonComplete.ID,
		"PARTIAL RAW 99999 bob@x.com", "completed", nil, nil, &CanonicalStreamState{CapabilitySeen: true}, true)

	rows, _, err := mr.ListByConversation(ctx, tenantID, conv.ID, 50, uuid.Nil)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	var rc, rn *model.Message
	for i := range rows {
		switch rows[i].ID {
		case complete.ID:
			rc = &rows[i]
		case nonComplete.ID:
			rn = &rows[i]
		}
	}
	if rc == nil || rn == nil {
		t.Fatalf("rows not found back")
	}

	// Complete row: provider-safe canonical at rest, NOT raw.
	if rc.DisplayState != DisplayStateCanonicalSafe {
		t.Fatalf("complete row display_state=%q want canonical_safe", rc.DisplayState)
	}
	if rc.ContentMasked != canon {
		t.Fatalf("content_masked must equal canonical, got %q", rc.ContentMasked)
	}
	if strings.Contains(rc.ContentMasked, "50000") || strings.Contains(rc.ContentMasked, "alice@x.com") {
		t.Fatalf("RAW display leaked at rest: %q", rc.ContentMasked)
	}
	if rc.ContentCanonical == nil || *rc.ContentCanonical != canon || rc.CanonicalStatus != CanonicalStatusComplete {
		t.Fatalf("canonical not persisted complete: %+v", rc)
	}

	// Non-complete row: redacted, canonical NULL, no partial raw.
	if rn.DisplayState != DisplayStateRedacted {
		t.Fatalf("non-complete display_state=%q want redacted", rn.DisplayState)
	}
	if rn.ContentMasked != RedactedDisplayPlaceholder {
		t.Fatalf("non-complete must be placeholder, got %q", rn.ContentMasked)
	}
	if strings.Contains(rn.ContentMasked, "99999") || strings.Contains(rn.ContentMasked, "bob@x.com") {
		t.Fatalf("partial RAW leaked at rest: %q", rn.ContentMasked)
	}
	if rn.ContentCanonical != nil || rn.CanonicalStatus == CanonicalStatusComplete {
		t.Fatalf("non-complete canonical must be NULL/non-complete: %+v", rn)
	}

	// REGRESSION — provider history still uses canonical (<<EMAIL_1>>), and the
	// non-complete row is excluded. Display lifecycle must not change this.
	hist, err := mr.ListContextByConversation(ctx, tenantID, conv.ID, 50)
	if err != nil {
		t.Fatalf("context read: %v", err)
	}
	req := buildGatewayRequestWithImages("m", hist, "", nil, nil, nil, true)
	sawCanon := false
	for _, mm := range req.Messages {
		s, _ := mm.Content.(string)
		if mm.Role == "assistant" {
			if strings.Contains(s, "50000") || strings.Contains(s, "alice@x.com") {
				t.Fatalf("provider history leaked raw: %q", s)
			}
			if strings.Contains(s, "<<EMAIL_1>>") {
				sawCanon = true
			}
			if s == RedactedDisplayPlaceholder {
				t.Fatalf("redacted placeholder must NOT be replayed to provider")
			}
		}
	}
	if !sawCanon {
		t.Fatalf("provider history missing canonical token <<EMAIL_1>>; msgs=%+v", req.Messages)
	}
}
