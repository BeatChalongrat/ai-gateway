package service

import "testing"

// A turn that attaches tool definitions must say so in the hints — the
// gateway uses it as a hard capability filter, and without it "auto"
// lands on the cheapest model in the catalog (the memory over-trigger).
func TestBuildRoutingHints_ToolsFlagged(t *testing.T) {
	h := BuildRoutingHints("สวัสดี", false, false, 0, false, 8)
	if !h.HasTools {
		t.Fatal("HasTools should be true when the turn carries tools")
	}
	if h.ToolCount != 8 {
		t.Fatalf("ToolCount = %d, want 8", h.ToolCount)
	}
}

func TestBuildRoutingHints_NoToolsLeavesFlagUnset(t *testing.T) {
	h := BuildRoutingHints("สวัสดี", false, false, 0, false, 0)
	if h.HasTools || h.ToolCount != 0 {
		t.Fatalf("expected no tool hints, got %+v", h)
	}
}
