package handler

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// FIX130 C1 — a reattached viewer on the ledger's live tail gets the same
// ": ping" heartbeat as the primary stream. Before, a quiet stretch after a
// reattach (a slow provider, a tool run) sent no byte at all, and a client
// with a read deadline could not tell it from a dead connection.
func TestFIX130_LedgerResumeTailHeartbeatsThroughAQuietStretch(t *testing.T) {
	old := heartbeatInterval
	heartbeatInterval = 15 * time.Millisecond
	defer func() { heartbeatInterval = old }()

	live := make(chan service.LedgerEvent)
	go func() {
		time.Sleep(120 * time.Millisecond) // the quiet stretch
		live <- service.LedgerEvent{Seq: 3, Event: "content_delta", Data: json.RawMessage(`{"delta":"late","run_id":"r1","seq":3}`)}
		live <- service.LedgerEvent{Seq: 4, Event: "message_complete", Data: json.RawMessage(`{"message_id":"m","run_id":"r1","seq":4}`)}
		close(live)
	}()
	att := &service.ResumeAttachment{
		Frames: &service.FrameAttachment{
			RunID:  "r1",
			Replay: []service.LedgerEvent{{Seq: 2, Event: "content_delta", Data: json.RawMessage(`{"delta":"early","run_id":"r1","seq":2}`)}},
			Live:   live,
			Cancel: func() {},
			Lagged: func() bool { return false },
		},
	}
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/v1/conversations/c/messages/m/stream?starting_after=1", nil)
	(&MessageHandler{}).resumeFromLedger(rec, rec, req, uuid.New(), att)

	body := rec.Body.String()
	if got := strings.Count(body, ": ping\n\n"); got < 3 {
		t.Fatalf("want >=3 heartbeat frames on the live tail's quiet stretch, got %d in %q", got, body)
	}
	// The recorded frames still go out verbatim, in order, around the pings.
	early, late, done := strings.Index(body, `"delta":"early"`), strings.Index(body, `"delta":"late"`), strings.Index(body, "event: message_complete")
	if early < 0 || late < 0 || done < 0 || !(early < late && late < done) {
		t.Fatalf("frames missing or out of order: %q", body)
	}
}
