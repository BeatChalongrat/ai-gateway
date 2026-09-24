package service

// FIX132 F19 — the sidebar's title search was one plain substring: NIGHT3
// j2-06 searched "NIGHT3 j2" for a chat titled "[NIGHT3] j2 เที่ยวเกียวโต"
// and got "ไม่พบบทสนทนาที่ตรงกัน". Now every word of the query must appear in
// the title, in any order, whatever sits between them. A Thai query with no
// spaces is one word — the same substring match as before.

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

func TestFIX132_TitleSearchMatchesEveryWordInAnyOrder(t *testing.T) {
	pool := testdb.Fresh(t)
	ctx := context.Background()
	cr := repository.NewConversationRepo(pool)
	tenantID, userID := uuid.New(), uuid.New()
	svc := NewConversationService(cr, nil)
	mk := func(title string) uuid.UUID {
		c, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "m"})
		if err != nil {
			testdeps.Missing(t, "conv create (schema?): %v", err)
		}
		if err := svc.Rename(ctx, tenantID, userID, c.ID, title); err != nil {
			t.Fatalf("rename: %v", err)
		}
		return c.ID
	}
	kyoto := mk("[NIGHT3] j2 เที่ยวเกียวโต")
	budget := mk("งบไตรมาส 3 ฝ่ายขาย")
	_ = mk("100% done_list")
	ids := func(q string) map[uuid.UUID]bool {
		got, err := svc.Search(ctx, tenantID, userID, q, 50)
		if err != nil {
			t.Fatalf("search %q: %v", q, err)
		}
		m := map[uuid.UUID]bool{}
		for _, c := range got {
			m[c.ID] = true
		}
		return m
	}
	for _, q := range []string{"NIGHT3 j2", "j2 night3", "  nIgHt3   J2 ", "เกียวโต j2"} {
		if got := ids(q); !got[kyoto] || len(got) != 1 {
			t.Fatalf("%q should find only the Kyoto chat, got %v", q, got)
		}
	}
	if got := ids("ไตรมาส"); !got[budget] || len(got) != 1 {
		t.Fatalf("a Thai word still matches as a substring: %v", got)
	}
	if got := ids("NIGHT3 budget"); len(got) != 0 {
		t.Fatalf("every word must match: %v", got)
	}
	// LIKE meta-characters are literal, per word.
	if got := ids("100%"); len(got) != 1 {
		t.Fatalf("100%% should match literally once: %v", got)
	}
	if got := ids("e_l"); len(got) != 1 {
		t.Fatalf("an underscore is literal: %v", got)
	}
}
