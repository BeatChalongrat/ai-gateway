package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM6 — a round that finished with its gate open delivered its work, and the
// invoice ↔ PO agent's files card says what it did.

// A round that finished with its gate open delivered its work and was charged
// for it: it is the organisation's record like a succeeded round, not a failed
// round's scratch.
func TestADeliveredOpenRoundIsKeptLikeASucceededOne(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	exists := func(key string) bool {
		ok, _ := e.api.files.Exists(ctx, key)
		return ok
	}
	round := func(open bool, ended time.Duration) (Run, string, string) {
		r := e.openRun(in, 30, 12)
		if !open {
			if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET gate_open = false, delivered_at = NULL WHERE id = $1`, r.ID); err != nil {
				t.Fatal(err)
			}
		}
		blob := BlobKey(in.TenantID, r.ID, strings.Repeat("ab", 16), "computed.json")
		up := UploadKey(in.TenantID, in.ID, uuid.New(), "gl.xlsx")
		for _, k := range []string{blob, up} {
			if err := e.api.files.Upload(ctx, k, strings.NewReader("x"), "", 1); err != nil {
				t.Fatal(err)
			}
		}
		st, _, _ := e.store.BeginStep(ctx, r.ID, "compute", "compute", "k-"+r.ID.String(), json.RawMessage(`{}`))
		out, _ := json.Marshal(map[string]any{"computed": map[string]any{"key": blob}})
		_ = e.store.FinishStep(ctx, st.ID, "succeeded", out, "", "", "", 1)
		rid := r.ID
		if _, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: uuid.New(), TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
			RunID: &rid, Name: "gl.xlsx", Size: 1, SHA256: "y", StorageKey: up, Status: "active"}); err != nil {
			t.Fatal(err)
		}
		if _, _, err := e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, &rid, OutputsPayload{RunID: r.ID.String(), Draft: true}, nil); err != nil {
			t.Fatal(err)
		}
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET finished_at = now() - make_interval(secs => $2) WHERE id = $1`,
			r.ID, ended.Seconds()); err != nil {
			t.Fatal(err)
		}
		return r, blob, up
	}
	delivered, deliveredBlob, deliveredUpload := round(true, 30*24*time.Hour)
	failed, failedBlob, failedUpload := round(false, 30*24*time.Hour)
	if _, err := e.store.Retention(ctx, e.api.files, 30*24*time.Hour, 7*24*time.Hour, 30*24*time.Hour); err != nil {
		t.Fatal(err)
	}
	if !exists(deliveredBlob) || !exists(deliveredUpload) {
		t.Fatalf("a delivered round's evidence is kept past a failed round's 7 days: blob %v upload %v", exists(deliveredBlob), exists(deliveredUpload))
	}
	if exists(failedBlob) || exists(failedUpload) {
		t.Fatal("a round that delivered nothing is a failed round's scratch, as before")
	}
	if r, _ := e.store.RunByID(ctx, delivered.ID); r.ID != delivered.ID {
		t.Fatal("the delivered round is kept")
	}
	// it ends with the organisation's years, as a succeeded round does
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET finished_at = now() - interval '6 years' WHERE id = $1`, delivered.ID); err != nil {
		t.Fatal(err)
	}
	if c, err := e.store.Retention(ctx, e.api.files, 30*24*time.Hour, 7*24*time.Hour, 30*24*time.Hour); err != nil || c.ExpiredRuns != 1 {
		t.Fatalf("its retention ends: %+v %v", c, err)
	}
	// a personal delete keeps the organisation's record of it
	kept, _, _ := round(true, 24*time.Hour)
	counts, err := NewAccountProvider(e.store, e.api.files).Purge(ctx, in.UserID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.RunByID(ctx, kept.ID); err != nil || counts["org_records_kept"] != 1 {
		t.Fatalf("a delivered round is the organisation's record: %v %v", counts, err)
	}
	var cards int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_thread_items WHERE run_id = $1 AND kind = 'outputs'`, kept.ID).Scan(&cards)
	if cards != 1 {
		t.Fatalf("its files card stays with it: %d", cards)
	}
	_ = failed
}

// The invoice ↔ PO agent's summary reaches the files card with every key the
// web reads: its counts, its values, and its package's own words.
func TestAPOMatchingSummaryReachesAPersonWithEveryKey(t *testing.T) {
	summary := map[string]any{"kind": "po_matching", "period": "2026-09", "period_label": "กันยายน 2569",
		"invoices": 4, "lines": 9, "passed": 2, "blocked": 1, "person": 1, "three_way": 5, "two_way": 3, "unlinked": 1, "over_billed": 1,
		"value_passed": "58,850.00", "value_blocked": "12,305.00", "value_person": "3,210.00",
		"received_not_billed": "48,450.00", "billed_not_received": "0.00",
		"by_rule": 7, "by_person": 2, "waiting": 0, "run_exceptions": 2, "review": 1, "receipt_offers": 2,
		"by_reason": []any{map[string]any{"code": "price_above_po", "label": "ราคาต่อหน่วยสูงกว่า PO เกินเกณฑ์", "outcome": "blocked",
			"invoices": 1, "lines": 1, "value": "12,305.00"}},
		"by_depth": []any{map[string]any{"depth": "three_way", "label": "ตรวจ 3 ทาง (PO · ใบรับของ · ใบแจ้งหนี้)", "lines": 5, "amount": "58,850.00"},
			map[string]any{"depth": "two_way", "label": "ตรวจ 2 ทาง (PO · ใบแจ้งหนี้)", "lines": 3, "amount": "15,515.00"}},
		"practice": "ออกใบรับของ",
		"tiers": []any{map[string]any{"tier": "exact", "label": "ตรงกันทุกค่า", "groups": 7, "by_rule": 7, "by_person": 0, "waiting": 0},
			map[string]any{"tier": "fuzzy", "label": "ชื่อสินค้าคล้ายกัน", "groups": 2, "by_rule": 0, "by_person": 2, "waiting": 0}},
		"not_included": []any{map[string]any{"doc_key": "d1", "name": "สแกน_เบลอ.jpg", "reason": "อ่านเลขที่ใบแจ้งหนี้จากภาพไม่ได้",
			"how": "ส่งเป็น PDF จากระบบของผู้ขาย", "can_restore": false}},
	}
	raw, _ := json.Marshal(map[string]any{"run_id": uuid.NewString(), "summary": summary})
	var sent map[string]any
	_ = json.Unmarshal(ForClient("outputs", raw), &sent)
	got, _ := sent["summary"].(map[string]any)
	for k, want := range summary {
		if jsonOf(got[k]) != jsonOf(want) {
			t.Errorf("summary.%s reaches a person as it was written:\n   written: %s\n   sent:    %s", k, jsonOf(want), jsonOf(got[k]))
		}
	}
	// the bank reconciliation's tiers are counts by code, as they were
	raw, _ = json.Marshal(map[string]any{"run_id": uuid.NewString(), "summary": map[string]any{"tiers": map[string]any{"exact": 12, "fuzzy": 1}}})
	_ = json.Unmarshal(ForClient("outputs", raw), &sent)
	if jsonOf(sent["summary"].(map[string]any)["tiers"]) != `{"exact":12,"fuzzy":1}` {
		t.Errorf("counts by code: %v", sent["summary"])
	}
}
