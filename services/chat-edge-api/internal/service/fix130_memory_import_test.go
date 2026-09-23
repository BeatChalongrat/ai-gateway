package service

// FIX130 A1 — desktop turns reach chat-edge only through the import path
// (the host assembles its own prompt and talks to the gateway), so before
// this nothing a desktop user said was ever read by the memory extractor.
// An import that adds messages now kicks the same trigger a web turn does.

import (
	"context"
	"testing"
	"time"
)

func TestFIX130_ImportedDesktopTurnsAreLearnedFrom(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true)
	gw := newExtractGateway(t, func() string { return completion("[]", "stop", 3, 0) })
	s := NewMessageService(f.cr, f.mr, nil, nil, allowAllModels{}, 50)
	s.bgWaitPoll, s.bgWaitMax = 5*time.Millisecond, 5*time.Second
	s.SetPreferenceExtractor(f.extractor(gw))
	imp := NewImportService(f.pool, f.cr, f.mr, nil, NewConversationService(f.cr, allowAllModels{}))
	imp.SetAfterImport(s.AfterImport)

	now := time.Now().Add(-time.Minute)
	items := []ImportMessageInput{
		{ClientID: "u1", Role: "user", Content: "ผมชื่อบีท ทำงานที่ GB Tech", CreatedAt: now},
		{ClientID: "a1", Role: "assistant", Content: "สวัสดีครับ", CreatedAt: now.Add(time.Second)},
		{ClientID: "u2", Role: "user", Content: "ช่วยเขียนอีเมลสั้นๆ", CreatedAt: now.Add(2 * time.Second)},
		{ClientID: "a2", Role: "assistant", Content: "อีเมล: …", CreatedAt: now.Add(3 * time.Second)},
	}
	if n, _, _, err := imp.ImportMessages(context.Background(), f.tenant, f.user, conv.ID, items); err != nil || n != 4 {
		t.Fatalf("import: n=%d err=%v", n, err)
	}
	waitFor(t, func() bool { return len(gw.calls()) == 1 }, "the extraction call after an imported two-turn desktop chat")
}
