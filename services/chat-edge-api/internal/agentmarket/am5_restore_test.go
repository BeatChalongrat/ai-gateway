package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM5 phase C — the day-one trap: the first card a person meets recommended
// "ตัดเอกสารนี้ออก", which left a real purchase out of the month, and nothing
// afterwards said so. What a card recommends is no longer what skipping it
// records; every document left out stays on the round's list; one click
// brings it back.

func vatFinding() Finding {
	return Finding{Key: "e2_check_failed:doc1:vat_7_percent", Code: "e2_check_failed", Severity: "blocking", Moment: "preflight",
		Header: "VAT ไม่ตรง", Question: "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf: ภาษีมูลค่าเพิ่มไม่เท่ากับ 7% ของฐาน",
		Options: []Option{{Label: "บันทึกตามเอกสาร มีข้อยกเว้น", Value: json.RawMessage(`"accept_as_printed"`)},
			{Label: "ตัดเอกสารนี้ออก", Value: json.RawMessage(`"exclude"`)}},
		Default: json.RawMessage(`"exclude"`), Recommended: json.RawMessage(`"accept_as_printed"`), LeavesOut: true, Subject: "doc1:vat_7_percent"}
}

func TestACardRecommendsWhatKeepsThePurchase(t *testing.T) {
	res, err := Plan(PlanInput{Moment: "preflight", Findings: []Finding{vatFinding()}, Answers: map[string]Answer{},
		Asked: map[string]bool{}, OpenKeys: map[string]bool{}})
	if err != nil || res.Card == nil || len(res.Card.Questions) != 1 {
		t.Fatalf("plan: %v %+v", err, res)
	}
	q := res.Card.Questions[0]
	if q.RecommendedLabel != "บันทึกตามเอกสาร มีข้อยกเว้น" || !q.LeavesOut || q.DefaultLabel != "ตัดเอกสารนี้ออก" {
		t.Fatalf("what the card recommends, and what skipping it does: %+v", q)
	}
	b, _ := json.Marshal(q)
	if !strings.Contains(string(b), `"recommended_label":"บันทึกตามเอกสาร มีข้อยกเว้น"`) || !strings.Contains(string(b), `"leaves_out":true`) {
		t.Fatalf("the web reads them: %s", b)
	}
	// a finding that recommends nothing says nothing
	f := vatFinding()
	f.Recommended = nil
	res, _ = Plan(PlanInput{Moment: "preflight", Findings: []Finding{f}, Answers: map[string]Answer{}, Asked: map[string]bool{}, OpenKeys: map[string]bool{}})
	if q := res.Card.Questions[0]; q.RecommendedLabel != "" || !q.LeavesOut {
		t.Fatalf("no recommendation: %+v", q)
	}
	// and one that recommends something that is not on the card is refused
	f.Recommended = json.RawMessage(`"delete_everything"`)
	if f.Validate() == nil {
		t.Fatal("a recommendation must be one of the card's answers")
	}
}

// computedRun is a finished invoice round: one document recorded, three left
// out (a VAT card skipped, a missing tax ID skipped, the same file twice).
func (e *testEnv) computedRun(in Install) Run {
	e.t.Helper()
	ctx := context.Background()
	answers := map[string]Answer{
		"e2_check_failed:docvat:vat_7_percent":    {Value: json.RawMessage(`"exclude"`), Label: "ตัดเอกสารนี้ออก", Moment: "preflight", Via: "skip", Code: "e2_check_failed"},
		"e2_field_missing:doctax:seller_tax_id":   {Value: json.RawMessage(`"exclude"`), Label: "ตัดเอกสารนี้ออก", Moment: "preflight", Via: "card", Code: "e2_field_missing"},
		"e2_low_confidence:docok:document_number": {Value: json.RawMessage(`{"accept":"IV6909-0015"}`), Label: "ถูกต้อง IV6909-0015", Moment: "preflight", Via: "card", Code: "e2_low_confidence"},
	}
	ab, _ := json.Marshal(answers)
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum, Answers: ab}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	computed := map[string]any{"schema": "invoice.register.v1", "not_included": []map[string]any{
		{"doc_key": "docvat", "name": "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf", "code": "check_failed", "reason": "ตรวจตัวเลขไม่ผ่าน",
			"detail": "ภาษีมูลค่าเพิ่มไม่เท่ากับ 7% ของฐาน", "how": "นำกลับมารวมได้: บันทึกตามที่เอกสารพิมพ์ พร้อมข้อยกเว้นให้ผู้ตรวจดู", "can_restore": true,
			"restore": map[string]any{"set": map[string]any{"e2_check_failed:docvat:vat_7_percent": map[string]any{"value": "accept_as_printed",
				"label": "บันทึกตามเอกสาร มีข้อยกเว้น", "code": "e2_check_failed"}}, "drop": []string{}}},
		{"doc_key": "doctax", "name": "ใบกำกับภาษี ไม่มีเลขผู้เสียภาษี.pdf", "code": "missing_required_field", "reason": "ขาดข้อมูลที่เอกสารชนิดนี้ต้องมี",
			"detail": "", "how": "นำกลับมารวมได้: ต้องตอบคำถามของเอกสารนี้ก่อน", "can_restore": true,
			"restore": map[string]any{"set": map[string]any{}, "drop": []string{"e2_field_missing:doctax:seller_tax_id"}}},
		{"doc_key": "doctwice", "name": "ใบกำกับภาษี ก.ย.69 ร้านเครื่องเขียน.pdf", "code": "same_file_twice", "reason": "ไฟล์เดียวกันส่งมาซ้ำ",
			"detail": "", "how": "ไม่ต้องนำกลับ: ไฟล์เดียวกันนี้อยู่ในทะเบียนแล้ว", "can_restore": false, "restore": map[string]any{"set": map[string]any{}, "drop": []string{}}},
	}}
	cb, _ := json.Marshal(computed)
	key := BlobKey(in.TenantID, run.ID, "k-compute-am5-restore", "computed.json")
	if err := e.api.files.Upload(ctx, key, strings.NewReader(string(cb)), "application/json", int64(len(cb))); err != nil {
		e.t.Fatal(err)
	}
	st, _, err := e.store.BeginStep(ctx, run.ID, "compute", "compute", "k-compute-am5-restore", json.RawMessage(`{}`))
	if err != nil {
		e.t.Fatal(err)
	}
	out, _ := json.Marshal(map[string]any{"computed_key": key})
	if err := e.store.FinishStep(ctx, st.ID, "succeeded", out, "", "scripts/register.py", "", 1); err != nil {
		e.t.Fatal(err)
	}
	// the two cards the person met
	for _, qk := range []string{"e2_check_failed:docvat:vat_7_percent", "e2_field_missing:doctax:seller_tax_id"} {
		qs, _ := json.Marshal([]Question{{Key: qk, Code: strings.SplitN(qk, ":", 2)[0], Severity: "blocking", Header: "คำถาม", Question: "…",
			Options: []Option{{Label: "ก", Value: json.RawMessage(`"a"`)}, {Label: "ตัดเอกสารนี้ออก", Value: json.RawMessage(`"exclude"`)}}}})
		card, err := e.store.CreateCard(ctx, e.store.Pool, Card{RunID: run.ID, InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
			Moment: "preflight", Kind: "questions", Blocking: true, Questions: qs})
		if err != nil {
			e.t.Fatal(err)
		}
		rid := run.ID
		if _, _, err := e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, &rid, "card", CardItemPayload(card, "", ""), nil); err != nil {
			e.t.Fatal(err)
		}
		if _, _, err := e.store.CloseCard(ctx, e.store.Pool, card.ID, "answered", json.RawMessage(`{}`), "card", nil); err != nil {
			e.t.Fatal(err)
		}
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded', started_at = now() - interval '2 minutes',
		finished_at = now(), period = '2026-09' WHERE id = $1`, run.ID); err != nil {
		e.t.Fatal(err)
	}
	run, _ = e.store.RunByID(ctx, run.ID)
	return run
}

func runAnswers(t *testing.T, e *testEnv, id uuid.UUID) map[string]Answer {
	t.Helper()
	run, err := e.store.RunByID(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]Answer{}
	_ = json.Unmarshal(run.Answers, &out)
	return out
}

func TestADocumentLeftOutIsBroughtBackInOneClick(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run := e.computedRun(in)
	path := "/runs/" + run.ID.String() + "/restore"

	// the VAT purchase: recorded as printed, with an exception — the round computes again
	code, body := e.do("POST", path, map[string]any{"doc_key": "docvat"})
	if code != 200 || body["changed"] != true {
		t.Fatalf("restore: %d %v", code, body)
	}
	a := runAnswers(t, e, run.ID)
	got := a["e2_check_failed:docvat:vat_7_percent"]
	if string(got.Value) != `"accept_as_printed"` || got.Label != "บันทึกตามเอกสาร มีข้อยกเว้น" || got.Via != "card" {
		t.Fatalf("the answer that left it out is replaced: %+v", got)
	}
	if !JSONEqual(a["e2_low_confidence:docok:document_number"].Value, json.RawMessage(`{"accept":"IV6909-0015"}`)) {
		t.Fatalf("the other documents' answers are untouched: %+v", a)
	}
	cur, _ := e.store.RunByID(ctx, run.ID)
	if cur.Status != RunQueued {
		t.Fatalf("the same round is computed again: %v", cur.Status)
	}
	said := false
	for _, it := range e.threadItems(in, "") {
		if it["kind"] == "notice" {
			said = said || strings.Contains(it["payload"].(map[string]any)["text"].(string), "ใบกำกับภาษี ค่าบริการ ก.ย.69.pdf")
		}
	}
	if !said {
		t.Fatal("the thread says which document came back")
	}
	// while it is being computed again, a second click changes nothing
	if code, body := e.do("POST", path, map[string]any{"doc_key": "docvat"}); code != http.StatusConflict {
		t.Fatalf("already on its way: %d %v", code, body)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded' WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}

	// a document that needs a value first: its answer is taken back and its question may be asked again
	code, body = e.do("POST", path, map[string]any{"doc_key": "doctax"})
	if code != 200 || body["changed"] != true || body["asks"] != true {
		t.Fatalf("restore (ask): %d %v", code, body)
	}
	if _, still := runAnswers(t, e, run.ID)["e2_field_missing:doctax:seller_tax_id"]; still {
		t.Fatal("the answer that left it out is taken back")
	}
	cards, _ := e.store.RunCards(ctx, run.ID)
	for _, c := range cards {
		var qs []Question
		_ = json.Unmarshal(c.Questions, &qs)
		want := "answered"
		if qs[0].Key == "e2_field_missing:doctax:seller_tax_id" {
			want = "superseded"
		}
		if c.Status != want {
			t.Fatalf("the old question of %s makes way for the new one: %s", qs[0].Key, c.Status)
		}
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded' WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}

	// the same file twice is already in the register; an unknown document is not on the list
	code, body = e.do("POST", path, map[string]any{"doc_key": "doctwice"})
	if msg := body["error"].(map[string]any)["message"].(string); code != http.StatusConflict || !strings.Contains(msg, "อยู่ในทะเบียนแล้ว") {
		t.Fatalf("nothing to bring back: %d %q", code, msg)
	}
	if code, _ := e.do("POST", path, map[string]any{"doc_key": "nope"}); code != http.StatusNotFound {
		t.Fatalf("not on the list: %d", code)
	}
	// someone else's round
	if code, _ := e.do("POST", path, map[string]any{"doc_key": "docvat"}, "X-Test-User", uuid.NewString()); code != http.StatusNotFound {
		t.Fatalf("someone else's: %d", code)
	}
}
