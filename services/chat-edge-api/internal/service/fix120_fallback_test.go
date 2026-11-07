package service

// FIX120 — chat-edge's side of the fallback policy: it never re-runs a
// turn on its own (every terminal cause the policy file lists is give_up
// for a client), it keeps the requested model of a rescued turn on the
// row, and it reads the gateway's fallback_info frame strictly.

import (
	"context"
	"encoding/json"
	"os"
	"regexp"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

const fallbackPolicyPath = "../../../../docs/contracts/fallback_policy.json"

func TestFallbackPolicy_ClientsNeverReRunATurn(t *testing.T) {
	raw, err := os.ReadFile(fallbackPolicyPath)
	if err != nil {
		t.Fatalf("read %s: %v", fallbackPolicyPath, err)
	}
	var pol struct {
		Actions       []string `json:"actions"`
		DecisionTable struct {
			TerminalCause map[string]string `json:"terminal_cause"`
			StopReason    map[string]string `json:"stop_reason"`
		} `json:"decision_table"`
		HardForbidden struct {
			TerminalCause []string `json:"terminal_cause"`
			StopReason    []string `json:"stop_reason"`
		} `json:"hard_forbidden"`
	}
	if err := json.Unmarshal(raw, &pol); err != nil {
		t.Fatalf("parse: %v", err)
	}
	// The terminal causes the policy names are exactly this package's set.
	if len(pol.DecisionTable.TerminalCause) != len(TerminalCauses) {
		t.Fatalf("policy names %d terminal causes, chat-edge has %d", len(pol.DecisionTable.TerminalCause), len(TerminalCauses))
	}
	for _, c := range TerminalCauses {
		action, ok := pol.DecisionTable.TerminalCause[c]
		if !ok {
			t.Fatalf("terminal cause %q has no policy row", c)
		}
		want := "give_up"
		if c == TerminalCauseError {
			want = "by_error_code"
		}
		if action != want {
			t.Fatalf("terminal cause %q → %q, want %q (a client never re-runs a turn)", c, action, want)
		}
	}
	// Every stop reason is a delivered answer: give_up, and hard-forbidden
	// for refusal / content_filter.
	if len(pol.DecisionTable.StopReason) != len(StopReasons) {
		t.Fatalf("policy names %d stop reasons, chat-edge has %d", len(pol.DecisionTable.StopReason), len(StopReasons))
	}
	for _, r := range StopReasons {
		if pol.DecisionTable.StopReason[r] != "give_up" {
			t.Fatalf("stop reason %q → %q, want give_up", r, pol.DecisionTable.StopReason[r])
		}
	}
	forbidden := func(list []string, v string) bool {
		for _, x := range list {
			if x == v {
				return true
			}
		}
		return false
	}
	if !forbidden(pol.HardForbidden.TerminalCause, TerminalCauseStoppedByUser) {
		t.Fatal("stopped_by_user must be hard-forbidden")
	}
	for _, r := range []string{"refusal", "content_filter"} {
		if !forbidden(pol.HardForbidden.StopReason, r) {
			t.Fatalf("stop reason %q must be hard-forbidden", r)
		}
	}
	if len(pol.Actions) != 6 || pol.Actions[5] != "give_up" {
		t.Fatalf("actions closed set changed: %v", pol.Actions)
	}
}

func TestFallbackFromInfo_ReadsTheFrameStrictly(t *testing.T) {
	cases := []struct {
		name      string
		info      map[string]any
		wantFrom  string
		wantModel string
	}{
		{"nil", nil, "", ""},
		{"swap", map[string]any{"requested_model": "gpt-5.6-luna", "actual_model": "qwen3.8-27b-fp8", "reason": "x"}, "gpt-5.6-luna", "qwen3.8-27b-fp8"},
		{"same model twice is not a swap", map[string]any{"requested_model": "m", "actual_model": "m"}, "", ""},
		{"missing actual", map[string]any{"requested_model": "m"}, "", ""},
		{"wrong types", map[string]any{"requested_model": 1, "actual_model": []string{"m"}}, "", ""},
		{"blank", map[string]any{"requested_model": "  ", "actual_model": "m"}, "", ""},
	}
	for _, c := range cases {
		from, actual := FallbackFromInfo(c.info)
		if from != c.wantFrom || actual != c.wantModel {
			t.Fatalf("%s: got (%q,%q) want (%q,%q)", c.name, from, actual, c.wantFrom, c.wantModel)
		}
	}
}

func TestFallbackFrom_RidesTheRowAndTheImport(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	prov := NewProvisionService(pool)
	prov.SetTestAccountPattern(regexp.MustCompile(`@local$`))
	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)

	user := uuid.New()
	tenant, err := prov.EnsureUserExists(ctx, user, "fix120-probe@local", "owner", "")
	if err != nil {
		t.Fatalf("provision: %v", err)
	}
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenant, UserID: user, ModelID: "gpt-5.6-luna"})
	if err != nil {
		t.Fatal(err)
	}

	// A live turn: the row is written, then the handler records the swap.
	msg, err := mr.Create(ctx, &model.Message{ConversationID: conv.ID, TenantID: tenant, UserID: user, Role: "assistant", ContentMasked: "a", Status: "completed"})
	if err != nil {
		t.Fatal(err)
	}
	msgID := msg.ID
	svc := &MessageService{messages: mr}
	if err := svc.SetServedModel(ctx, msgID, "qwen3.8-27b-fp8"); err != nil {
		t.Fatal(err)
	}
	if err := svc.SetFallbackFrom(ctx, msgID, "gpt-5.6-luna"); err != nil {
		t.Fatal(err)
	}
	var from, modelID *string
	if err := pool.QueryRow(ctx, "SELECT fallback_from, model_id FROM chat.messages_masked WHERE id=$1", msgID).Scan(&from, &modelID); err != nil {
		t.Fatal(err)
	}
	if from == nil || *from != "gpt-5.6-luna" || modelID == nil || *modelID != "qwen3.8-27b-fp8" {
		t.Fatalf("row: fallback_from=%v model_id=%v", from, modelID)
	}
	// The list wire carries it.
	rows, _, err := mr.ListByConversation(ctx, tenant, conv.ID, 10, uuid.Nil)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, r := range rows {
		if r.ID == msgID {
			found = true
			if r.FallbackFrom == nil || *r.FallbackFrom != "gpt-5.6-luna" {
				t.Fatalf("list row fallback_from=%v", r.FallbackFrom)
			}
		}
	}
	if !found {
		t.Fatal("row not listed")
	}
	// "" writes nothing (no swap) — the column stays as it is.
	if err := svc.SetFallbackFrom(ctx, msgID, ""); err != nil {
		t.Fatal(err)
	}

	// A desktop import: the item says which model was asked for.
	imp := NewImportService(pool, cr, mr, repository.NewFileRepo(pool), NewConversationService(cr, importTestModels{}))
	ic, _, err := imp.ImportConversation(ctx, ImportConversationInput{TenantID: tenant, UserID: user, ClientID: "desk-" + uuid.NewString(), Title: "t", ModelID: "gpt-5.6-luna"})
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	items := []ImportMessageInput{
		{ClientID: "u1", Role: "user", Content: "q", ModelID: "gpt-5.6-luna", CreatedAt: now, FallbackFrom: "gpt-5.6-luna"},
		{ClientID: "a1", Role: "assistant", Content: "a", ModelID: "qwen3.8-27b-fp8", CreatedAt: now.Add(time.Millisecond), Status: "completed", FallbackFrom: "gpt-5.6-luna"},
		{ClientID: "a2", Role: "assistant", Content: "b", ModelID: "gpt-5.6-luna", CreatedAt: now.Add(2 * time.Millisecond), Status: "completed", FallbackFrom: "gpt-5.6-luna"},
		{ClientID: "a3", Role: "assistant", Content: "c", ModelID: "gpt-5.6-luna", CreatedAt: now.Add(3 * time.Millisecond), Status: "completed"},
	}
	if _, _, _, err := imp.ImportMessages(ctx, tenant, user, ic.ID, items); err != nil {
		t.Fatalf("import: %v", err)
	}
	want := map[string]*string{"u1": nil, "a1": strp("gpt-5.6-luna"), "a2": nil, "a3": nil}
	for clientID, exp := range want {
		var got *string
		if err := pool.QueryRow(ctx, "SELECT fallback_from FROM chat.messages_masked WHERE conversation_id=$1 AND client_id=$2", ic.ID, clientID).Scan(&got); err != nil {
			t.Fatalf("%s: %v", clientID, err)
		}
		if (exp == nil) != (got == nil) || (exp != nil && *exp != *got) {
			t.Fatalf("%s: fallback_from=%v want %v", clientID, got, exp)
		}
	}
}

func strp(s string) *string { return &s }
