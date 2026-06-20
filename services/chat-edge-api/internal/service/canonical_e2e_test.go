package service

// Canonical-history E2E (DB-backed). Seeds turn-1 in a REAL test DB, then
// drives the REAL history read (ListContextByConversation) + REAL outbound
// provider-payload builder (buildGatewayRequestWithImages) for turn-2 and
// asserts the provider payload carries the canonical token, NOT the restored
// display. DB-gated: skips when Postgres/migration is unavailable.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func e2ePool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"
	}
	p, err := pgxpool.New(context.Background(), dsn)
	if err != nil {
		t.Skipf("skip: %v", err)
	}
	if err := p.Ping(context.Background()); err != nil {
		p.Close()
		t.Skipf("skip: DB unreachable %v", err)
	}
	if b, err := os.ReadFile("../../migrations/024_add_message_canonical_artifact.sql"); err == nil {
		_, _ = p.Exec(context.Background(), string(b)) // idempotent
	}
	return p
}

func canonSig(content string) (string, int) {
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:]), utf8.RuneCountInString(content)
}

// CORE E2E — next-turn provider payload uses canonical, not restored display.
func TestCanonicalHistoryE2E_ProviderPayload(t *testing.T) {
	pool := e2ePool(t)
	defer pool.Close()
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	mr.SetCanonicalHistory(true) // enabled → canonical-only reads/build
	tenantID, userID := uuid.New(), uuid.New()
	defer pool.Exec(ctx, "DELETE FROM chat.conversations WHERE tenant_id=$1", tenantID)

	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Skipf("conv create (schema?): %v", err)
	}

	// Turn 1 — user asks; assistant reply persisted with display + canonical.
	if _, err := mr.Create(ctx, &model.Message{
		ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
		Role: "user", ContentMasked: "เงินเดือนผม 50000 ช่วยจำไว้", Status: "completed",
	}); err != nil {
		t.Fatalf("user msg: %v", err)
	}
	asst, err := mr.Create(ctx, &model.Message{
		ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
		Role: "assistant", ContentMasked: "x", Status: "streaming",
	})
	if err != nil {
		t.Fatalf("asst msg: %v", err)
	}
	display := "จำไว้แล้วว่าเงินเดือนคือ 50000" // restored DISPLAY (raw)
	canon := "จำไว้แล้วว่าเงินเดือนคือ <r0>"     // tokenized CANONICAL
	sha, ln := canonSig(canon)
	sv, gen := 1, "req_turn1"
	if err := mr.UpdateContentAndCanonical(ctx, asst.ID, display, "completed", nil, nil,
		repository.CanonicalWrite{Status: "complete", Content: &canon, SchemaVersion: &sv, GenerationID: &gen, SHA256: &sha, Length: &ln}); err != nil {
		t.Fatalf("persist canonical: %v", err)
	}

	// Turn 2 — REAL history read + REAL outbound provider-payload build.
	history, err := mr.ListContextByConversation(ctx, tenantID, conv.ID, 50)
	if err != nil {
		t.Fatalf("history read: %v", err)
	}
	req := buildGatewayRequestWithImages("m", history, "", nil, nil, nil, true)

	// The ASSISTANT history in the outbound provider payload must carry the
	// canonical token, never the restored assistant DISPLAY. (User-turn
	// content legitimately still contains the user's own value — it is
	// re-masked by the gateway's INBOUND DLP, not by canonical history.)
	var sawCanonAssistant bool
	for _, m := range req.Messages {
		if m.Role != "assistant" {
			continue
		}
		s, _ := m.Content.(string)
		if strings.Contains(s, "50000") {
			t.Fatalf("assistant provider history LEAKED restored display: %q", s)
		}
		if strings.Contains(s, "<r0>") {
			sawCanonAssistant = true
		}
	}
	if !sawCanonAssistant {
		t.Fatalf("provider payload missing canonical assistant token <r0>; msgs=%+v", req.Messages)
	}
	// The full assistant display string must not appear anywhere.
	for _, m := range req.Messages {
		if s, ok := m.Content.(string); ok && strings.Contains(s, "จำไว้แล้วว่าเงินเดือนคือ 50000") {
			t.Fatalf("restored assistant display leaked into provider payload: %q", s)
		}
	}

	// Disabled repo → legacy read path (no canonical columns referenced).
	mr.SetCanonicalHistory(false)
	histLegacy, err := mr.ListContextByConversation(ctx, tenantID, conv.ID, 50)
	if err != nil {
		t.Fatalf("legacy history read: %v", err)
	}
	reqOff := buildGatewayRequestWithImages("m", histLegacy, "", nil, nil, nil, false)
	foundDisplay := false
	for _, m := range reqOff.Messages {
		if s, ok := m.Content.(string); ok && strings.Contains(s, "50000") {
			foundDisplay = true
		}
	}
	if !foundDisplay {
		t.Fatalf("disabled path should use display (legacy)")
	}
}

// Non-stream consumer persists complete canonical from the artifact.
func TestNonStreamConsumerPersistsCanonical(t *testing.T) {
	pool := e2ePool(t)
	defer pool.Close()
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	mr.SetCanonicalHistory(true)
	svc := &MessageService{messages: mr}
	svc.SetCanonicalHistory(true, false)
	tenantID, userID := uuid.New(), uuid.New()
	defer pool.Exec(ctx, "DELETE FROM chat.conversations WHERE tenant_id=$1", tenantID)
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
	if err != nil {
		t.Skipf("conv create: %v", err)
	}

	mk := func() *model.Message {
		m, _ := mr.Create(ctx, &model.Message{
			ConversationID: conv.ID, TenantID: tenantID, UserID: userID,
			Role: "assistant", ContentMasked: "x", Status: "streaming",
		})
		return m
	}

	// Complete artifact → status complete + canonical persisted.
	canon := "ผลลัพธ์ <r0>"
	sha, ln := canonSig(canon)
	art := &CanonicalArtifact{SchemaVersion: 1, GenerationID: "req_ns", RequestID: "req_ns",
		CanonicalContent: canon, CanonicalSHA256: sha, CanonicalLength: ln, Status: "complete"}
	a1 := mk()
	svc.persistNonStreamCanonical(ctx, a1.ID, "display 50000", nil, nil, art)
	got, _ := mr.ListContextByConversation(ctx, tenantID, conv.ID, 50)
	var row *model.Message
	for i := range got {
		if got[i].ID == a1.ID {
			row = &got[i]
		}
	}
	if row == nil || row.CanonicalStatus != "complete" || row.ContentCanonical == nil || *row.ContentCanonical != canon {
		t.Fatalf("non-stream complete not persisted: %+v", row)
	}

	// Missing artifact → invalidated (non-complete), display kept, excluded.
	a2 := mk()
	svc.persistNonStreamCanonical(ctx, a2.ID, "display 99999", nil, nil, nil)
	got, _ = mr.ListContextByConversation(ctx, tenantID, conv.ID, 50)
	for i := range got {
		if got[i].ID == a2.ID {
			if got[i].CanonicalStatus == "complete" || got[i].ContentCanonical != nil {
				t.Fatalf("missing artifact must be non-complete/NULL: %+v", got[i])
			}
		}
	}
}

// Managed-channel gate predicate.
func TestCanonicalHistoryEligible(t *testing.T) {
	svc := &MessageService{}
	svc.SetCanonicalHistory(true, false)
	conv := &model.Conversation{ID: uuid.New()}
	good := SendInput{TenantID: uuid.New(), UserID: uuid.New(), ConversationID: conv.ID}
	if !svc.canonicalHistoryEligible(good, conv) {
		t.Fatalf("managed WebChat with full scope must be eligible")
	}
	if svc.canonicalHistoryEligible(SendInput{UserID: uuid.New()}, conv) {
		t.Fatalf("missing tenant must be ineligible")
	}
	if svc.canonicalHistoryEligible(SendInput{TenantID: uuid.New()}, conv) {
		t.Fatalf("missing actor must be ineligible")
	}
	if svc.canonicalHistoryEligible(good, nil) {
		t.Fatalf("missing conversation must be ineligible")
	}
	// Disabled → never eligible (public Direct API equivalent / flag off).
	off := &MessageService{}
	off.SetCanonicalHistory(false, true)
	if off.canonicalHistoryEligible(good, conv) {
		t.Fatalf("disabled must be ineligible")
	}
}
