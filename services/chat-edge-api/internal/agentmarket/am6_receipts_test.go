package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM6 — what a round records, and a receipt confirmed in this system.
//
// The invoice ↔ PO agent keeps more of a document than the duplicate check's
// four values (its lines, the PO lines it billed, a receipt someone confirmed
// here), and a round that computes again (a restored document, a confirmed
// receipt) replaces what it recorded rather than adding to it. A person who
// has no receipt file confirms the receipt of a PO line in the system itself:
// the answer is recorded as theirs and the same round computes again.

func TestADocumentIndexRecordsDetailAndARoundThatComputesAgainReplacesIt(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run := e.finishedRun(in, "2026-09", "ตรวจใบแจ้งหนี้")
	other := e.finishedRun(in, "2026-08", "ตรวจใบแจ้งหนี้")
	year := 365 * 24 * time.Hour
	if _, err := e.store.AddDocumentIndex(ctx, other, []IndexEntry{{DocKey: "iv-0001", Name: "IV-0001.pdf", SHA256: "a",
		SellerTaxID: strp("0105563012343"), DocumentNumber: strp("IV-0001"), Total: strp("1000.00")}}, year); err != nil {
		t.Fatal(err)
	}
	first := []IndexEntry{
		{DocKey: "iv-0101", Name: "IV-0101.pdf", SHA256: "b", SellerTaxID: strp("0105563012343"), DocumentNumber: strp("IV-0101"),
			DocumentDate: strp("2026-09-10"), Total: strp("5350.00"), Detail: json.RawMessage(`{"kind":"invoice","outcome":"blocked"}`)},
		{DocKey: "iv-0102", Name: "IV-0102.pdf", SHA256: "c", Total: strp("700.00")},
	}
	if _, err := e.store.AddDocumentIndex(ctx, run, first, year); err != nil {
		t.Fatal(err)
	}
	// the same round computes again: IV-0101 now passes, IV-0102 is no longer listed, a confirmed receipt is
	again := []IndexEntry{
		{DocKey: "iv-0101", Name: "IV-0101.pdf", SHA256: "b", SellerTaxID: strp("0105563012343"), DocumentNumber: strp("IV-0101"),
			DocumentDate: strp("2026-09-10"), Total: strp("5350.00"), Detail: json.RawMessage(`{"kind":"invoice","outcome":"pass"}`)},
		{DocKey: "receipt:6f1c", Name: "ยืนยันรับของในระบบ", SHA256: "d", DocumentNumber: strp("PO6909001"), DocumentDate: strp("2026-10-02"),
			Detail: json.RawMessage(`{"kind":"receipt","akey":"PO6909001#l:10","quantity":"40","confirmed_by":"สมหญิง ผู้ตรวจ"}`)},
	}
	if _, err := e.store.AddDocumentIndex(ctx, run, again, year); err != nil {
		t.Fatalf("a confirmed receipt has no total and no seller's tax id: %v", err)
	}
	got, err := e.store.DocumentIndex(ctx, in.ID, uuid.Nil, 100)
	if err != nil {
		t.Fatal(err)
	}
	byKey := map[string]IndexEntry{}
	var keys []string
	for _, x := range got {
		byKey[x.DocKey] = x
		keys = append(keys, x.DocKey)
	}
	sort.Strings(keys)
	if strings.Join(keys, " ") != "iv-0001 iv-0101 receipt:6f1c" {
		t.Fatalf("what the round records now, and the other round's as it was: %v", keys)
	}
	var d map[string]any
	_ = json.Unmarshal(byKey["iv-0101"].Detail, &d)
	if d["outcome"] != "pass" {
		t.Fatalf("what the round recorded is replaced: %s", byKey["iv-0101"].Detail)
	}
	r := byKey["receipt:6f1c"]
	_ = json.Unmarshal(r.Detail, &d)
	if r.Total != nil || r.SellerTaxID != nil || d["kind"] != "receipt" || d["confirmed_by"] != "สมหญิง ผู้ตรวจ" {
		t.Fatalf("a confirmed receipt: %+v %s", r, r.Detail)
	}
	if len(byKey["iv-0001"].Detail) != 0 {
		t.Fatalf("an entry recorded without detail has none: %s", byKey["iv-0001"].Detail)
	}
	// as the package is handed it: detail is an object, absent when there is none
	b, _ := json.Marshal(got)
	var handed []map[string]any
	_ = json.Unmarshal(b, &handed)
	for _, h := range handed {
		det, has := h["detail"]
		switch h["doc_key"] {
		case "iv-0001":
			if has {
				t.Fatalf("an entry without detail is handed without one: %s", b)
			}
		case "receipt:6f1c":
			if m, ok := det.(map[string]any); !ok || m["kind"] != "receipt" {
				t.Fatalf("detail is handed as an object: %s", b)
			}
		}
	}
}

const offeringComputed = `{"period":"2026-09","receipt_offers":[
 {"akey":"PO6909001#l:10","key":"pomatch_receipt:PO6909001#l:10","po":"PO6909001","line_no":10,"item_code":"A-100",
  "description":"กระดาษ A4","ordered":"100","unit":"รีม","billed":"40","file":"po_sep.xlsx","sheet":"PO","row":4}]}`

// offeringRun is a finished round of the install's whose compute step left the given table.
func (e *testEnv) offeringRun(in Install, computed string) Run {
	e.t.Helper()
	ctx := context.Background()
	run := e.finishedRun(in, "2026-09", "ตรวจใบแจ้งหนี้กับใบสั่งซื้อ")
	stepKey := StepKey(run.ID, "compute", "am6-receipts")
	key := BlobKey(run.TenantID, run.ID, stepKey, "computed.json")
	if err := e.api.files.Upload(ctx, key, strings.NewReader(computed), "application/json", int64(len(computed))); err != nil {
		e.t.Fatal(err)
	}
	st, _, err := e.store.BeginStep(ctx, run.ID, "compute", "compute", stepKey, json.RawMessage(`{}`))
	if err != nil {
		e.t.Fatal(err)
	}
	out, _ := json.Marshal(map[string]any{"computed": map[string]any{"key": key}, "computed_key": key, "summary": map[string]any{}})
	if err := e.store.FinishStep(ctx, st.ID, "succeeded", out, "", "scripts/match.py", "", 1); err != nil {
		e.t.Fatal(err)
	}
	rid := run.ID
	if _, _, err := e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, &rid,
		Progress{RunID: run.ID.String(), Kind: KindRun, Status: RunSucceeded, Outcome: OutcomeCompleted}, nil); err != nil {
		e.t.Fatal(err)
	}
	return run
}

func TestAReceiptConfirmedInThisSystemComputesTheRoundAgain(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	code, body := e.installDeclaring(nil)
	if code != 200 {
		t.Fatalf("install: %d %v", code, body)
	}
	in, _ := e.store.InstallByID(ctx, uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	run := e.offeringRun(in, offeringComputed)
	const key = "pomatch_receipt:PO6909001#l:10"
	post := func(run Run, body map[string]any, hdr ...string) (int, map[string]any) {
		return e.do("POST", "/runs/"+run.ID.String()+"/receipts", body, hdr...)
	}
	// what cannot be confirmed is refused, and nothing changes
	for _, c := range []struct {
		body map[string]any
		code int
		err  string
	}{
		{map[string]any{"key": "pomatch_receipt:PO6909009#l:10", "quantity": "all"}, http.StatusNotFound, "NOT_OFFERED"},
		{map[string]any{"key": key, "quantity": "-1"}, http.StatusBadRequest, "BAD_QUANTITY"},
		{map[string]any{"key": key, "quantity": "1.1234567"}, http.StatusBadRequest, "BAD_QUANTITY"},
		{map[string]any{"key": key, "quantity": "สี่สิบ"}, http.StatusBadRequest, "BAD_QUANTITY"},
		{map[string]any{"key": key}, http.StatusBadRequest, "BAD_QUANTITY"},
	} {
		code, out := post(run, c.body)
		if ec, msg := errMsg(out); code != c.code || ec != c.err || !ReadsForPerson(msg) {
			t.Fatalf("%v: %d %v", c.body, code, out)
		}
	}
	if code, _ := post(run, map[string]any{"key": key, "quantity": "all"}, "X-Test-User", uuid.NewString()); code != http.StatusNotFound {
		t.Fatalf("another person's round: %d", code)
	}
	if got, _ := e.store.RunByID(ctx, run.ID); got.Status != RunSucceeded || strings.Contains(string(got.Answers), "pomatch_receipt") {
		t.Fatalf("a refused confirmation changes nothing: %s %s", got.Status, got.Answers)
	}
	// "all": recorded as given by the signed-in person, and the same round computes again
	code, out := post(run, map[string]any{"key": key, "quantity": "all"})
	if code != http.StatusAccepted || out["run"].(map[string]any)["id"] != run.ID.String() || out["run"].(map[string]any)["status"] != RunQueued {
		t.Fatalf("confirm: %d %v", code, out)
	}
	got, _ := e.store.RunByID(ctx, run.ID)
	var answers map[string]Answer
	_ = json.Unmarshal(got.Answers, &answers)
	a := answers[key]
	if string(a.Value) != `"all"` || a.Via != "action" || a.By == nil || *a.By != e.user.UserID {
		t.Fatalf("the answer, and who gave it: %+v", a)
	}
	if it, err := e.store.ItemFor(ctx, in.ID, "progress", "run_id", run.ID.String()); err != nil || !strings.Contains(string(it.Payload), `"status":"queued"`) {
		t.Fatalf("the progress card says the round is working again: %v %s", err, it.Payload)
	}
	told := lastOfKind(e.threadItems(in, ""), "notice")
	if text, _ := told["payload"].(map[string]any)["text"].(string); !strings.Contains(text, "PO6909001") || !ReadsForPerson(text) {
		t.Fatalf("the thread says what was confirmed: %v", told)
	}
	// a number: recorded as typed
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded' WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	if code, out := post(run, map[string]any{"key": key, "quantity": "40.5"}); code != http.StatusAccepted {
		t.Fatalf("a quantity: %d %v", code, out)
	}
	got, _ = e.store.RunByID(ctx, run.ID)
	_ = json.Unmarshal(got.Answers, &answers)
	if jsonOf(answers[key].Value) != `{"typed":"40.5"}` {
		t.Fatalf("typed: %s", answers[key].Value)
	}
	// a round that is working, or did not succeed, is not confirmed into
	if code, out := post(run, map[string]any{"key": key, "quantity": "all"}); code != http.StatusConflict {
		t.Fatalf("a round that is working: %d %v", code, out)
	}
	open := e.offeringRun(in, offeringComputed)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'failed', gate_open = true, delivered_at = now()
		WHERE id = $1`, open.ID); err != nil {
		t.Fatal(err)
	}
	if code, out := post(open, map[string]any{"key": key, "quantity": "all"}); code != http.StatusConflict {
		t.Fatalf("a round that did not succeed: %d %v", code, out)
	}
	// an agent that offers no receipt: nothing to confirm
	plain := e.offeringRun(in, `{"period":"2026-09"}`)
	if code, out := post(plain, map[string]any{"key": key, "quantity": "all"}); code != http.StatusConflict {
		t.Fatalf("no offers: %d %v", code, out)
	}
}

func TestMigration005DownUndoesTheDetailOfTheIndex(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	column := func() int {
		var n int
		_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market'
			AND table_name = 'document_index' AND column_name = 'detail'`).Scan(&n)
		return n
	}
	if column() != 1 {
		t.Fatal("after up: no detail column")
	}
	down, err := migrationFS.ReadFile("migrations/005_am6.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	if column() != 0 {
		t.Fatal("down leaves the detail column")
	}
	if _, err := Migrate(ctx, e.store.Pool); err != nil || column() != 1 {
		t.Fatalf("up again: %v", err)
	}
}
