package service

// FIX127 A1 — a file that was uploaded but never attached to a sent message
// (the composer chip was removed, the draft abandoned, the upload response
// lost) must not reach the model. The rule is enforced on the server, on
// the rows, so it holds whatever the client sends.

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestFIX127_UnsentFileNeverReachesTheModel(t *testing.T) {
	f := newFix126FileFixture(t)
	conv := f.conv(t)
	removed := f.file(t, conv, "removed.txt", "text/plain", "completed", strp("REMOVED CHIP BODY"))
	sent := f.file(t, conv, "sent.txt", "text/plain", "completed", strp("SENT FILE BODY"))

	// Turn 1 sends only `sent`; `removed` stays message_id NULL — exactly
	// the row a removed chip leaves behind (R3: ddbb237d saw 26 files).
	in := SendInput{TenantID: f.tenant, UserID: f.user, ConversationID: conv.ID, Content: "สรุปไฟล์",
		RequestID: uuid.NewString(), FileIDs: []uuid.UUID{sent.ID}}
	h, err := f.s.SendStream(context.Background(), in)
	if err != nil {
		t.Fatalf("send: %v", err)
	}
	drainHandle(h)
	raw, _ := json.Marshal(f.gw.body(0))
	body := unescapeJSON(string(raw))
	if !strings.Contains(body, "SENT FILE BODY") {
		t.Fatal("the sent file's text is missing from the gateway request")
	}
	if strings.Contains(body, "REMOVED CHIP BODY") || strings.Contains(body, "removed.txt") {
		t.Fatalf("a file that was never sent reached the model:\n%s", body)
	}

	// Turn 2, no files at all: the unsent row must still be out, and the
	// file from turn 1 must still be in (it belongs to the transcript).
	h, err = f.s.SendStream(context.Background(), SendInput{TenantID: f.tenant, UserID: f.user,
		ConversationID: conv.ID, Content: "ต่อ", RequestID: uuid.NewString()})
	if err != nil {
		t.Fatalf("send 2: %v", err)
	}
	drainHandle(h)
	raw, _ = json.Marshal(f.gw.body(1))
	body = unescapeJSON(string(raw))
	if strings.Contains(body, "REMOVED CHIP BODY") {
		t.Fatal("turn 2 still carries the file that was never sent")
	}
	if !strings.Contains(body, "SENT FILE BODY") {
		t.Fatal("turn 2 lost the file that WAS sent")
	}
	_ = removed
}
