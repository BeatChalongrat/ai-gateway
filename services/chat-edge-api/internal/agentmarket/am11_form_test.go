package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM11 (D) — a form with no marker in it. The blanks a form expects filled
// are known by reading it (template_fill: required); what the agent can put
// into them is known once the model has proposed and the person has mapped.
// A form the agent can fill less than half of is refused before any work,
// with the fields it has nothing for named; a form it can fill most of is
// filled, and what it lacks is named on the draft.

func targets(labels ...string) []TemplateSlot {
	var out []TemplateSlot
	for i, l := range labels {
		out = append(out, TemplateSlot{Key: "table0.r" + string(rune('0'+i/10)) + string(rune('0'+i%10)) + "c1", Kind: "target",
			Label: l, Fillable: true, Required: true, Side: "right"})
	}
	return out
}

var purchaseRequest = []string{"เลขที่ใบขอซื้อ", "วันที่ขอซื้อ", "ผู้ขอซื้อ", "แผนก", "ชื่อบริษัท", "ผู้ขายที่เสนอ",
	"เลขประจำตัวผู้เสียภาษีของผู้ขาย", "วันที่ต้องการของ", "สถานที่ส่งของ", "เงื่อนไขการชำระเงิน", "งบประมาณที่อนุมัติ (บาท)", "เหตุผลที่ขอซื้อ"}

func TestAFormTheAgentCanFillLessThanHalfOfIsRefusedAndNamesWhatItLacks(t *testing.T) {
	slots := targets(purchaseRequest...)
	mapping := map[string]string{slots[4].Key: "company"}
	why, lacking, asked := FormRefusal(slots, mapping)
	if why != RefusedTooFew || asked != 12 || len(lacking) != 11 {
		t.Fatalf("refusal = %q, %d lacking of %d", why, len(lacking), asked)
	}
	for _, l := range lacking {
		if l == "ชื่อบริษัท" {
			t.Fatal("the one field it has a value for is named as lacking")
		}
	}
	// the save says the same, in words, with the names
	spec := TemplateSpec{Fields: map[string]json.RawMessage{"company": json.RawMessage(`{"label":"ชื่อบริษัท"}`)}}
	if msg := CheckTemplateMapping(Thai, slots, mapping, spec); msg != "" {
		t.Fatalf("one valid pair is a valid mapping, whatever it covers (the runner builds one pair by pair): %s", msg)
	}
	msg := CheckFormCoverage(Thai, slots, mapping)
	for _, want := range []string{"12 ช่อง", "1 ช่อง", "เลขที่ใบขอซื้อ", "เหตุผลที่ขอซื้อ"} {
		if !strings.Contains(msg, want) {
			t.Fatalf("the refusal does not say %q: %s", want, msg)
		}
	}
	if !ReadsForPerson(msg, purchaseRequest...) {
		t.Fatalf("the refusal does not read as written for a person: %s", msg)
	}
}

func TestAFormTheAgentCanFillMostOfIsNotRefused(t *testing.T) {
	slots := targets("Company", "Period", "Revenue (THB)", "Gross profit (THB)", "Total expenses (THB)", "Net profit (THB)",
		"Headcount", "Cash at bank (THB)")
	mapping := map[string]string{}
	for _, s := range slots[:6] {
		mapping[s.Key] = "f"
	}
	if why, lacking, _ := FormRefusal(slots, mapping); why != "" || len(lacking) != 0 {
		t.Fatalf("six of eight was refused: %q %v", why, lacking)
	}
	// exactly half is enough; one below half is not
	half := map[string]string{}
	for _, s := range slots[:4] {
		half[s.Key] = "f"
	}
	if why, _, _ := FormRefusal(slots, half); why != "" {
		t.Fatalf("four of eight was refused: %q", why)
	}
	delete(half, slots[0].Key)
	if why, lacking, _ := FormRefusal(slots, half); why != RefusedTooFew || len(lacking) != 5 {
		t.Fatalf("three of eight: %q, %d lacking", why, len(lacking))
	}
}

func TestWhatIsNotAFieldTheFormExpectsFilledNeverRefusesIt(t *testing.T) {
	// a title with an empty cell beside it; a marker, a named cell, a control: the person's "not used" stands
	slots := []TemplateSlot{
		{Key: "S!C2", Kind: "target", Label: "Monthly board pack", Fillable: true},
		{Key: "revenue", Kind: "marker", Label: "revenue", Fillable: true},
		{Key: "Total", Kind: "name", Label: "Total", Fillable: true},
		{Key: "Notes", Kind: "control", Label: "Notes", Fillable: true},
		{Key: "Other", Kind: "name", Label: "Other", Fillable: true},
	}
	if why, _, asked := FormRefusal(slots, map[string]string{"revenue": "revenue"}); why != "" || asked != 0 {
		t.Fatalf("refused %q (asked %d)", why, asked)
	}
	// two blanks are too few to call a form mostly empty
	two := targets("บริษัท", "งวด")
	if why, _, _ := FormRefusal(two, map[string]string{}); why != "" {
		t.Fatalf("a form of two blanks was refused: %q", why)
	}
}

func TestAFileWithNothingToFillIsRefusedAsThat(t *testing.T) {
	if why, _, _ := FormRefusal(nil, nil); why != RefusedNoFields {
		t.Fatalf("no slot at all: %q", why)
	}
	onlyUnfillable := []TemplateSlot{{Key: "Total", Kind: "name", Label: "Total", Fillable: false}}
	if why, _, _ := FormRefusal(onlyUnfillable, nil); why != RefusedNoFields {
		t.Fatalf("nothing fillable: %q", why)
	}
}

func TestARefusedFormSaysHowManyAndWhichAndIsNotCalledFilled(t *testing.T) {
	slots := targets(purchaseRequest...)
	f := RoundForm{FileID: uuid.New(), Name: "ใบขอซื้อ.docx", Kind: "docx", Status: FormRefused, Why: RefusedTooFew, Slots: slots,
		Proposal: map[string]string{slots[4].Key: "company"}}
	p := FormItemPayload(uuid.New(), f)
	if p.Status != FormRefused || p.Reason != RefusedTooFew || p.Fields != 12 || p.Mapped != 1 {
		t.Fatalf("payload = %+v", p)
	}
	if len(p.Unfilled) != 11 || p.Unfilled[0] != "เลขที่ใบขอซื้อ" {
		t.Fatalf("the fields it lacks = %v", p.Unfilled)
	}
	text := p.Text.String()
	for _, want := range []string{"12", "1 ช่อง", "ใบขอซื้อ.docx"} {
		if !strings.Contains(text, want) {
			t.Fatalf("the item does not say %q: %s", want, text)
		}
	}
	if strings.Contains(text, "กรอก ใบขอซื้อ.docx ให้แล้ว") {
		t.Fatalf("a refused form reads as filled: %s", text)
	}
	if !ReadsForPerson(text) {
		t.Fatalf("not written for a person: %s", text)
	}
	// through the client's schema nothing is lost: the names are the customer's own words
	b, _ := json.Marshal(p)
	out := ForClient("form", b)
	var got struct {
		Reason   string   `json:"reason"`
		Fields   int      `json:"fields"`
		Mapped   int      `json:"mapped"`
		Unfilled []string `json:"unfilled"`
		Text     string   `json:"text"`
	}
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatal(err)
	}
	if got.Reason != RefusedTooFew || got.Fields != 12 || got.Mapped != 1 || len(got.Unfilled) != 11 || got.Text == "" {
		t.Fatalf("what the client receives = %+v", got)
	}
}

func TestAFormThatCannotBeUsedSaysWhyInWords(t *testing.T) {
	for code, want := range map[string]string{
		"chart_not_updated":    "กราฟ",
		"unsupported_location": "เติมไม่ได้",
		RefusedNoFields:        "ไม่พบช่อง",
	} {
		status := FormFailed
		if code == RefusedNoFields {
			status = FormRefused
		}
		f := RoundForm{FileID: uuid.New(), Name: "form.docx", Status: status, Why: code}
		p := FormItemPayload(uuid.New(), f)
		if !strings.Contains(p.Text.String(), want) {
			t.Fatalf("%s: the item does not say why (%q): %s", code, want, p.Text)
		}
		if strings.Contains(p.Text.String(), code) {
			t.Fatalf("%s: the code itself is in the sentence: %s", code, p.Text)
		}
		if !ReadsForPerson(p.Text.String()) {
			t.Fatalf("%s: not written for a person: %s", code, p.Text)
		}
	}
}

func TestADraftNamesEachFieldItLacksWithItsReason(t *testing.T) {
	unfilled := []json.RawMessage{
		json.RawMessage(`{"slot":"Cover!D10","label":"Headcount","why":"ยังว่าง — ไม่ได้จับคู่ค่าให้ช่องนี้","reason":"not_mapped"}`),
		json.RawMessage(`{"slot":"Cover!C4","label":"Company","why":"รอบนี้ไม่มีค่าให้ช่องนี้","reason":"no_value"}`),
	}
	f := RoundForm{FileID: uuid.New(), Name: "board_cover.xlsx", Status: FormUnfilled, Unfilled: unfilled}
	p := FormItemPayload(uuid.New(), f)
	if len(p.Unfilled) != 2 || len(p.UnfilledWhy) != 2 {
		t.Fatalf("payload = %+v", p)
	}
	if p.Unfilled[0] != "Headcount" || !strings.Contains(p.UnfilledWhy[0].String(), "ไม่ได้จับคู่") {
		t.Fatalf("first gap = %q · %q", p.Unfilled[0], p.UnfilledWhy[0])
	}
	if !strings.Contains(p.UnfilledWhy[1].String(), "ไม่มีค่า") {
		t.Fatalf("second gap's reason = %q", p.UnfilledWhy[1])
	}
}

// The same form dropped into the same run a second time is read a second time. Before AM11 the job
// that reads a form was keyed by the form's content: a second drop of the same file found the first
// drop's finished job, queued nothing, and its card stayed at "reading the form" for good — the walk
// of the built app met it by dropping a form it had dropped before.
func TestTheSameFormDroppedAgainIsReadAgain(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.install()
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.invoice-processing", Version: "1.0.0", Name: "ใบกำกับภาษี",
		Manifest: json.RawMessage(invoiceTemplatesManifest), Checksum: "inv", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	_, body := e.do("POST", "/installs", map[string]any{"slug": "sran.invoice-processing"})
	in, _ := e.store.GetInstall(ctx, e.user.TenantUUID, e.user.UserID, uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	done := e.finishedRun(in, "2026-09", "")
	jobs := func() (n int, newestFile, newestStatus string) {
		rows, err := e.store.Pool.Query(ctx, `SELECT params->>'file_id', status FROM agent_market.agent_runs
			WHERE install_id = $1 AND kind = 'mapping_preview' AND params->>'run_id' = $2 ORDER BY created_at`, in.ID, done.ID.String())
		if err != nil {
			t.Fatal(err)
		}
		defer rows.Close()
		for rows.Next() {
			_ = rows.Scan(&newestFile, &newestStatus)
			n++
		}
		return
	}
	// the first drop: one job to read it
	first := e.stagedFile(in, "ฟอร์มสรุป.docx")
	if code, b := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{first.ID.String()}, "client_id": "a1"}); code != 200 || b["route"] != "form" {
		t.Fatalf("first drop: %d %v", code, b)
	}
	if n, file, _ := jobs(); n != 1 || file != first.ID.String() {
		t.Fatalf("after the first drop: %d job(s), newest for %s", n, file)
	}
	// that job finishes (the runner read the form)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded', finished_at = now()
		WHERE install_id = $1 AND kind = 'mapping_preview'`, in.ID); err != nil {
		t.Fatal(err)
	}
	// the same file again — the same name and the same content, a new upload
	second := e.stagedFile(in, "ฟอร์มสรุป.docx")
	if second.SHA256 != first.SHA256 || second.ID == first.ID {
		t.Fatal("the test needs the same content uploaded twice")
	}
	if code, b := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{second.ID.String()}, "client_id": "a2"}); code != 200 || b["route"] != "form" {
		t.Fatalf("second drop: %d %v", code, b)
	}
	form, err := e.store.RoundForm(ctx, e.store.Pool, done.ID)
	if err != nil || form == nil || form.FileID != second.ID || form.Status != FormInspecting {
		t.Fatalf("the run's form is the second upload, waiting to be read: %+v %v", form, err)
	}
	n, file, status := jobs()
	if n != 2 || file != second.ID.String() || status != "queued" {
		t.Fatalf("the second drop must queue its own job to read it: %d job(s), newest for %s, %s — a form marked as being read with nothing reading it", n, file, status)
	}
	// the same request sent twice (a retry of one drop) still makes one job
	if code, _ := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{second.ID.String()}, "client_id": "a2"}); code != 200 {
		t.Fatalf("the retried request: %d", code)
	}
	if n, _, _ := jobs(); n != 2 {
		t.Fatalf("a retried drop made another job: %d", n)
	}
}
