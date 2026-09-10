package service

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// FIX100 Part A — the regression this round exists for.
//
// The owner's conversation e493ca28 failed six times in a row on
// 2026-09-10. The tail of that conversation, read straight out of
// chat.messages_masked, was:
//
//	dad280a3  user       completed   len 20   07:54:53
//	6ed1e473  assistant  error       len  0   07:54:53
//	24e97567  user       completed   len 20   07:55:03
//	4a12bb6d  assistant  regenerated len  0   07:55:03
//	73d251ea  assistant  regenerated len  0   07:55:15
//	5587ab4b  assistant  regenerated len  0   07:55:53
//	f379b442  assistant  regenerated len  0   07:55:59
//	6481d1de  assistant  error       len  0   07:58:41
//
// Every one of those rows is is_active, so every one of them was loaded
// and shipped upstream as {"role":"assistant","content":""}, one more of
// them on each press of Regenerate (25 → 26 → 27 messages). Call 7 of
// the reproduction ledger sent that shape to deepseek and got back
//
//	{"code":"upstream_bad_response","retryable":false,
//	 "details":{"provider":"deepseek","status":400,"body":{"error":{
//	   "message":"The `reasoning_content` in the thinking mode must be
//	              passed back to the API.", ...}}}}
//
// which the person then answered by pressing the one button we offered
// them, Regenerate, which added another empty row and asked again.
//
// These tests do not assert the provider's sentence — that is deepseek's
// to change. They assert the thing that was ours: the payload.
func fix100Msg(role, content, status string, min int) model.Message {
	return model.Message{
		ID:            uuid.New(),
		Role:          role,
		ContentMasked: content,
		Status:        status,
		IsActive:      true,
		CreatedAt:     time.Date(2026, 9, 10, 7, min, 0, 0, time.UTC),
	}
}

func fix100AsstContents(req GatewayChatRequest) []string {
	var out []string
	for _, m := range req.Messages {
		if m.Role != "assistant" {
			continue
		}
		s, _ := m.Content.(string)
		out = append(out, s)
	}
	return out
}

func fix100Roles(req GatewayChatRequest) []string {
	out := make([]string, 0, len(req.Messages))
	for _, m := range req.Messages {
		out = append(out, m.Role)
	}
	return out
}

// The exact tail above must not put a single empty assistant turn on the
// wire — and must not merely be tidied into a shorter list of empties.
func TestFIX100_AstraCall7_NoEmptyAssistantReachesProvider(t *testing.T) {
	history := []model.Message{
		fix100Msg("user", "ครับ", "completed", 54),
		fix100Msg("assistant", "เข้าใจแล้วครับ ผมจะสรุปให้", "completed", 54),
		fix100Msg("user", "ต่อเลย", "completed", 54),
		fix100Msg("assistant", "", "error", 54),
		fix100Msg("user", "ลองอีกที", "completed", 55),
		fix100Msg("assistant", "", "regenerated", 55),
		fix100Msg("assistant", "", "regenerated", 55),
		fix100Msg("assistant", "", "regenerated", 55),
		fix100Msg("assistant", "", "regenerated", 55),
		fix100Msg("assistant", "", "error", 58),
	}
	req := buildGatewayRequestWithImages("deepseek-chat", history, "sys", nil, nil, nil, false)

	for i, m := range req.Messages {
		s, isStr := m.Content.(string)
		if m.Role == "assistant" && isStr && s == "" {
			t.Fatalf("message %d is an empty assistant turn; the whole point of FIX100 Part A is that this never leaves the process. roles=%v", i, fix100Roles(req))
		}
	}
	if got := fix100AsstContents(req); len(got) != 1 || got[0] != "เข้าใจแล้วครับ ผมจะสรุปให้" {
		t.Fatalf("the one real reply must survive and be the only assistant turn; got %#v", got)
	}
	// system, user, assistant, user(merged), and nothing else.
	if want := []string{"system", "user", "assistant", "user"}; !fix100Equal(fix100Roles(req), want) {
		t.Fatalf("role sequence = %v, want %v", fix100Roles(req), want)
	}
}

// The trap the brief names, pinned so nobody "tidies" this into a status
// filter later. Measured on the live database at the time of the fix:
// 109 assistant rows with status=error and 30 with status=cancelled hold
// text a person read on their screen.
func TestFIX100_FailedRowThatStreamedRealTextIsKept(t *testing.T) {
	partial := "ตามที่ถามมา ขั้นตอนแรกคือ"
	history := []model.Message{
		fix100Msg("user", "อธิบายหน่อย", "completed", 10),
		fix100Msg("assistant", partial, "error", 10), // streamed, THEN died
		fix100Msg("user", "ต่อ", "completed", 11),
		fix100Msg("assistant", "หยุดกลางคัน", "cancelled", 11), // user pressed Stop
		fix100Msg("user", "เอาใหม่", "completed", 12),
	}
	req := buildGatewayRequestWithImages("deepseek-chat", history, "sys", nil, nil, nil, false)
	got := fix100AsstContents(req)
	if len(got) != 2 || got[0] != partial || got[1] != "หยุดกลางคัน" {
		t.Fatalf("a failed or cancelled row that produced text is something the user SAW; dropping it deletes their transcript. got %#v", got)
	}
}

// A blank row marked completed is blank all the same. 29 of these exist
// live, and a status filter would have shipped every one of them.
func TestFIX100_BlankCompletedRowIsAlsoDropped(t *testing.T) {
	history := []model.Message{
		fix100Msg("user", "hi", "completed", 10),
		fix100Msg("assistant", "   \n\t ", "completed", 10),
		fix100Msg("user", "still there?", "completed", 11),
	}
	req := buildGatewayRequestWithImages("m", history, "", nil, nil, nil, false)
	if got := fix100AsstContents(req); len(got) != 0 {
		t.Fatalf("whitespace-only completed row must not be sent; got %#v", got)
	}
	if want := []string{"user"}; !fix100Equal(fix100Roles(req), want) {
		t.Fatalf("roles = %v, want %v (the two user turns merge)", fix100Roles(req), want)
	}
}

// Nothing changes for a conversation that never failed.
func TestFIX100_HealthyHistoryIsUntouched(t *testing.T) {
	history := []model.Message{
		fix100Msg("user", "a", "completed", 10),
		fix100Msg("assistant", "b", "completed", 10),
		fix100Msg("user", "c", "completed", 11),
		fix100Msg("assistant", "d", "completed", 11),
		fix100Msg("user", "e", "completed", 12),
	}
	req := buildGatewayRequestWithImages("m", history, "sys", nil, nil, nil, false)
	if want := []string{"system", "user", "assistant", "user", "assistant", "user"}; !fix100Equal(fix100Roles(req), want) {
		t.Fatalf("roles = %v, want %v", fix100Roles(req), want)
	}
	if len(req.Messages) != 6 {
		t.Fatalf("len = %d, want 6", len(req.Messages))
	}
}

func fix100Equal(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
