package runner

import (
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

func TestVisionReadingKeepsOnlyKnownFieldsAsText(t *testing.T) {
	got, err := visionJSON("```json\n{\"total\": 12850.7, \"seller_tax_id\": \"0105545009875\", \"vat_amount\": null," +
		" \"line_items\": [{\"amount\": 1}], \"note\": \"ignore me\", \"document_date\": \"2026-09-15\"}\n```")
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"total": "12850.70", "seller_tax_id": "0105545009875", "document_date": "2026-09-15"}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for k, v := range want {
		if got[k] != v {
			t.Fatalf("%s: %q, want %q", k, got[k], v)
		}
	}
	for _, bad := range []string{"not json", `{"note": "x"}`, `[1, 2]`} {
		if _, err := visionJSON(bad); err == nil {
			t.Fatalf("%q must be refused", bad)
		}
	}
}

func TestExtrasLeaveAM1StepKeysUnchanged(t *testing.T) {
	var x runExtras
	if x.keyParts() != nil {
		t.Fatal("no history and no proposals add nothing to a step key")
	}
	req := map[string]any{}
	if in := x.attach(req, nil); len(in) != 0 || len(req) != 0 {
		t.Fatalf("nothing attached: %v %v", in, req)
	}
	x.History = []am.IndexEntry{}
	x.Proposals = map[string]visionReading{"d1": {Model: "m", Fields: map[string]string{"total": "1.00"}}}
	in := x.attach(req, nil)
	if len(in) != 2 || req["history"] != "history.json" || req["proposals"] != "proposals.json" || x.keyParts() == nil {
		t.Fatalf("attached: %v %v", in, req)
	}
	if !strings.Contains(string(in[1].Bytes), `"total":"1.00"`) {
		t.Fatalf("proposals body: %s", in[1].Bytes)
	}
}

// AM23 §3.1: the note says what read the scans — the organisation's AI assistant — and where it ran, never by the
// model's name (the run's own record keeps that).
func TestOutputsSayWhatSawTheDocumentsAndWhereNeverWhichModel(t *testing.T) {
	plain := documentsNote(map[string]any{}).String()
	if !strings.Contains(plain, "โมเดลไม่ได้เป็นผู้กำหนดตัวเลข") || strings.Contains(plain, "อ่านซ้ำ") {
		t.Fatalf("no model: %s", plain)
	}
	note := documentsNote(map[string]any{"models": []any{map[string]any{"model": "qwen3.8-27b-fp8", "self_hosted": true, "documents": float64(3)}}})
	if s := note.String(); !strings.Contains(s, "ผู้ช่วย AI ขององค์กร (รันในองค์กร)") || !strings.Contains(s, "3 ฉบับ") || strings.Contains(s, "qwen") {
		t.Fatalf("what read them, where, and no model's name: %s", s)
	}
	if strings.Contains(note.In(am.English), "qwen") {
		t.Fatalf("nor in English: %s", note.In(am.English))
	}
}
