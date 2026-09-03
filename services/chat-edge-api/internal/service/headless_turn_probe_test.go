package service

// FIX13 §1 PROBE — empirical, not read-from-shape. Proves a FULL chat-edge turn
// runs with NO client attached: no *http.Request, no SSE StreamHandle, no user
// session/cookie — only a SendInput (identity + content) and the service's
// gateway credential. This is the exact call a cloud scheduler would make.
//
// Run against the live stack (gateway + mock provider):
//   HEADLESS_PROBE=1 GW_URL=http://localhost:8080/v1 GW_KEY=... GW_SECRET=... \
//     go test ./internal/service/ -run TestHeadlessTurnProbe -count=1 -v
// Skips cleanly when HEADLESS_PROBE is unset or the DB is unreachable.

import (
	"context"
	"os"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

type allowAllModels struct{}

func (allowAllModels) ModelAllowed(string) bool { return true }
func (allowAllModels) ModelIDs() []string       { return []string{"auto", "gpt-4o-mini"} }

func TestHeadlessTurnProbe(t *testing.T) {
	if os.Getenv("HEADLESS_PROBE") != "1" {
		t.Skip("set HEADLESS_PROBE=1 (plus GW_URL/GW_KEY/GW_SECRET) to run the live headless-turn probe")
	}
	dsn := os.Getenv("CHAT_EDGE_TEST_DSN")
	if dsn == "" {
		dsn = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"
	}
	ctx := context.Background()
	// Own database per test, dropped when the test ends — no
	// cleanup code, so none can silently fail (internal/testdb).
	pool := testdb.Fresh(t)

	cr := repository.NewConversationRepo(pool)
	mr := repository.NewMessageRepo(pool)
	gw := NewGatewayClient(os.Getenv("GW_URL"), os.Getenv("GW_KEY"), "", 0, os.Getenv("GW_SECRET"))
	svc := NewMessageService(cr, mr, gw, nil, allowAllModels{}, 10)

	tenantID, userID := uuid.New(), uuid.New()
	conv, err := cr.Create(ctx, &model.Conversation{TenantID: tenantID, UserID: userID, ModelID: "auto"})
	if err != nil {
		t.Fatalf("create conversation: %v", err)
	}

	// The scheduler's exact call: pure data. No request, no stream, no session.
	res, err := svc.SendNonStream(ctx, SendInput{
		TenantID:       tenantID,
		UserID:         userID,
		ConversationID: conv.ID,
		Content:        "Reply with the single word: unwatched",
		RequestID:      "headless-probe",
	})
	if err != nil {
		t.Fatalf("SendNonStream FAILED headless: %v", err)
	}
	if res == nil || res.AssistantMessage == nil {
		t.Fatalf("no assistant message produced")
	}
	t.Logf("HEADLESS TURN OK — assistant produced %d chars (msg id %s)",
		len(res.AssistantMessage.ContentMasked), res.AssistantMessage.ID)
}
