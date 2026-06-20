package service

// Phase 4A.2.4.1 — Search & read-surface hardening tests.
// Unit: SearchSafeContent matrix. DB-backed: the search service (the LLM
// search_past_conversations tool path) never returns a legacy raw
// content_masked when display lifecycle is on. DB-gated: skips without Postgres.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

const legacySentinel = "SECRET-SENTINEL-LEGACY-0812345678"

func TestSearchSafeContent(t *testing.T) {
	// Flag OFF → passthrough (legacy behavior).
	if got := SearchSafeContent(false, DisplayStateLegacy, "RAW "+legacySentinel); !strings.Contains(got, legacySentinel) {
		t.Fatalf("flag off must passthrough raw, got %q", got)
	}
	// Flag ON.
	if got := SearchSafeContent(true, DisplayStateCanonicalSafe, "salary <<EMAIL_1>>"); got != "salary <<EMAIL_1>>" {
		t.Fatalf("canonical_safe should expose tokenized, got %q", got)
	}
	if got := SearchSafeContent(true, DisplayStateRedacted, RedactedDisplayPlaceholder); got != RedactedDisplayPlaceholder {
		t.Fatalf("redacted should stay placeholder, got %q", got)
	}
	for _, st := range []string{DisplayStateLegacy, ""} {
		got := SearchSafeContent(true, st, "RAW "+legacySentinel)
		if got != RedactedDisplayPlaceholder || strings.Contains(got, legacySentinel) {
			t.Fatalf("state %q must redact legacy raw, got %q", st, got)
		}
	}
}

// SearchPastContext (LLM tool + /search) must never emit legacy raw content.
func TestSearchService_LegacyRedacted_CanonicalTokenized(t *testing.T) {
	pool := dlPool(t)
	defer pool.Close()
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	mr.SetCanonicalHistory(true)
	mr.SetDisplayLifecycle(true)
	tenantID, userID := uuid.New(), uuid.New()
	defer pool.Exec(ctx, "DELETE FROM chat.conversations WHERE tenant_id=$1", tenantID)
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Skipf("conv create (schema?): %v", err)
	}

	// Legacy row: content_masked holds restored RAW (the sentinel). Create
	// leaves display_state at the DB default 'legacy_display'.
	legacy, err := mr.Create(ctx, &model.Message{
		ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
		Role: "assistant", ContentMasked: "zztestterm " + legacySentinel, Status: "completed",
	})
	if err != nil {
		t.Fatalf("legacy create: %v", err)
	}
	// canonical_safe row: content_masked holds the tokenized canonical.
	safe, err := mr.Create(ctx, &model.Message{
		ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
		Role: "assistant", ContentMasked: "zztestterm <<EMAIL_9>>", Status: "completed",
	})
	if err != nil {
		t.Fatalf("safe create: %v", err)
	}
	if _, err := pool.Exec(ctx,
		"UPDATE chat.messages_masked SET display_state='canonical_safe', content_canonical=content_masked, canonical_status='complete' WHERE id=$1",
		safe.ID); err != nil {
		t.Fatalf("set canonical_safe: %v", err)
	}

	// ── ON: legacy redacted, canonical tokenized, sentinel ABSENT ──
	on := NewSearchService(mr, cr, repository.NewMemoryRepo(pool))
	on.SetDisplayLifecycle(true)
	res, err := on.SearchPastContext(ctx, userID, tenantID, "zztestterm", ScopeMessages, 20)
	if err != nil {
		t.Fatalf("search: %v", err)
	}
	if len(res) == 0 {
		t.Skipf("no search hits (tsvector trigger absent in this DB?) — skipping")
	}
	sawLegacyRedacted, sawCanon := false, false
	for _, r := range res {
		if strings.Contains(r.Content, legacySentinel) {
			t.Fatalf("search LEAKED legacy raw sentinel: %q", r.Content)
		}
		if r.ConversationID == conv.ID.String() {
			if r.Content == RedactedDisplayPlaceholder {
				sawLegacyRedacted = true
			}
			if strings.Contains(r.Content, "<<EMAIL_9>>") {
				sawCanon = true
			}
		}
	}
	if !sawLegacyRedacted {
		t.Fatalf("legacy row must be redacted in search results: %+v", res)
	}
	if !sawCanon {
		t.Fatalf("canonical_safe row must surface tokenized in search results: %+v", res)
	}
	_ = legacy

	// ── OFF: passthrough (regression — legacy behavior unchanged) ──
	mrOff := repository.NewMessageRepo(pool) // displayLifecycle off
	off := NewSearchService(mrOff, cr, repository.NewMemoryRepo(pool))
	resOff, err := off.SearchPastContext(ctx, userID, tenantID, "zztestterm", ScopeMessages, 20)
	if err != nil {
		t.Fatalf("search off: %v", err)
	}
	rawSeen := false
	for _, r := range resOff {
		if strings.Contains(r.Content, legacySentinel) {
			rawSeen = true
		}
	}
	if !rawSeen {
		t.Fatalf("flag OFF must passthrough raw (legacy behavior), none seen")
	}
}
