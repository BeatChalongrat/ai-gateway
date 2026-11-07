package service

// FIX119 Part C2 — test traffic is labelled where it is written, and the
// analytics leave it out unless asked.

import (
	"context"
	"regexp"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func TestIsTest_FlowsFromTheUserToEveryRow(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	prov := NewProvisionService(pool)
	prov.SetTestAccountPattern(regexp.MustCompile(`@local$`))
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)

	probe, person := uuid.New(), uuid.New()
	probeTenant, err := prov.EnsureUserExists(ctx, probe, "fix119-probe@local", "owner", "")
	if err != nil {
		t.Fatalf("provision probe: %v", err)
	}
	personTenant, err := prov.EnsureUserExists(ctx, person, "someone@example.com", "owner", "")
	if err != nil {
		t.Fatalf("provision person: %v", err)
	}
	var probeFlag, personFlag bool
	if err := pool.QueryRow(ctx, "SELECT is_test FROM identity.users WHERE id=$1", probe).Scan(&probeFlag); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, "SELECT is_test FROM identity.users WHERE id=$1", person).Scan(&personFlag); err != nil {
		t.Fatal(err)
	}
	if !probeFlag || personFlag {
		t.Fatalf("users.is_test: probe=%v person=%v", probeFlag, personFlag)
	}

	// Conversations copy the flag from the user, messages from the conversation.
	pc, err := cr.Create(ctx, &model.Conversation{TenantID: probeTenant, UserID: probe, ModelID: "m"})
	if err != nil {
		t.Fatal(err)
	}
	hc, err := cr.Create(ctx, &model.Conversation{TenantID: personTenant, UserID: person, ModelID: "m"})
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []*model.Conversation{pc, hc} {
		if _, err := mr.Create(ctx, &model.Message{ConversationID: c.ID, TenantID: c.TenantID, UserID: c.UserID, Role: "user", ContentMasked: "q", Status: "completed"}); err != nil {
			t.Fatal(err)
		}
		if _, err := mr.Create(ctx, &model.Message{ConversationID: c.ID, TenantID: c.TenantID, UserID: c.UserID, Role: "assistant", ContentMasked: "a", Status: "completed"}); err != nil {
			t.Fatal(err)
		}
	}
	var convFlag bool
	var msgTest, msgReal int
	if err := pool.QueryRow(ctx, "SELECT is_test FROM chat.conversations WHERE id=$1", pc.ID).Scan(&convFlag); err != nil || !convFlag {
		t.Fatalf("probe conversation is_test=%v err=%v", convFlag, err)
	}
	if err := pool.QueryRow(ctx, "SELECT is_test FROM chat.conversations WHERE id=$1", hc.ID).Scan(&convFlag); err != nil || convFlag {
		t.Fatalf("person conversation is_test=%v err=%v", convFlag, err)
	}
	if err := pool.QueryRow(ctx, "SELECT COUNT(*) FILTER (WHERE is_test), COUNT(*) FILTER (WHERE NOT is_test) FROM chat.messages_masked WHERE conversation_id IN ($1,$2)", pc.ID, hc.ID).Scan(&msgTest, &msgReal); err != nil {
		t.Fatal(err)
	}
	if msgTest != 2 || msgReal != 2 {
		t.Fatalf("messages: test=%d real=%d", msgTest, msgReal)
	}

	// An imported conversation + rows copy it too.
	imp := NewImportService(pool, cr, mr, repository.NewFileRepo(pool), NewConversationService(cr, importTestModels{}))
	ic, _, err := imp.ImportConversation(ctx, ImportConversationInput{TenantID: probeTenant, UserID: probe, ClientID: "desk-" + uuid.NewString(), Title: "t", ModelID: "m"})
	if err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, "SELECT is_test FROM chat.conversations WHERE id=$1", ic.ID).Scan(&convFlag); err != nil || !convFlag {
		t.Fatalf("imported probe conversation is_test=%v err=%v", convFlag, err)
	}
}

func TestIsTestAccount_MatcherIsCaseInsensitiveAndNilSafe(t *testing.T) {
	p := NewProvisionService(nil)
	if p.IsTestAccount("fix48-probe@local") {
		t.Fatal("no pattern installed → nothing is a test account")
	}
	p.SetTestAccountPattern(regexp.MustCompile(`@local$`))
	for email, want := range map[string]bool{"fix48-probe@local": true, "  Probe@LOCAL ": true, "someone@gbtech.co.th": false, "local@example.com": false} {
		if got := p.IsTestAccount(email); got != want {
			t.Fatalf("IsTestAccount(%q) = %v, want %v", email, got, want)
		}
	}
}
