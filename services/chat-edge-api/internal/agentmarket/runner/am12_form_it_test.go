package runner

// AM12 (§7) end to end (AGENT_MARKET_IT=1): a purchase request in the
// organisation's own form — a form that carries no marker — read by the
// engine in the real sandbox, mapped by a model that is given only its
// labels and the headings of its table of items, filled from the round's
// computed table, and opened again. Three forms
// (backend/agent_market/tests/prformkit.py → fixtures/forms):
//
//   request_form.docx   Word, Thai     → a draft: the line in the table of items, the totals beside their
//                                         labels, four blanks left and named on the file's own face with
//                                         what no page can tell
//   request_form.xlsx   Excel, English → the same round in a form of another shape; its own formula kept
//   other_form.docx     a leave form   → refused before any work, its blanks named
//   request_form.pdf    PDF, fields    → the same round in a PDF that has fields to type in
//   printed_form.pdf    PDF, no fields → refused: a picture of a form takes no value
//
// The filled files are kept when AM11_KEEP names a folder.

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// what a reader of these forms would say each label is, among the procurement agent's fields, and what
// each heading of a table of items is among the columns of its list — the stand-in for the model: it is
// shown the labels the engine read and nothing else
var (
	am12Labels = map[string]string{
		"วันที่ขอซื้อ": "request_date", "ผู้ขอซื้อ": "requester", "ผู้ขายที่เสนอ": "seller_as_shown",
		"เลขประจำตัวผู้เสียภาษีของผู้ขาย": "seller_tax_id", "สถานที่ส่งของ": "delivery_place", "วันที่ต้องการของ": "needed_by",
		"รวมเป็นเงิน": "subtotal", "ภาษีมูลค่าเพิ่ม": "vat_amount", "รวมทั้งสิ้น": "total",
		"Requested by": "requester", "Date": "request_date", "Deliver to": "delivery_place", "Supplier": "seller_as_shown",
		"Supplier tax ID": "seller_tax_id", "VAT": "vat_amount", "Total": "total",
		"วันที่ยื่นใบลา": "request_date",
		// the PDF form's fields, by the words printed beside them
		"รายการที่ขอซื้อ": "item_description", "จำนวน": "quantity", "ราคาต่อหน่วย (บาท)": "unit_price", "รวมทั้งสิ้น (บาท)": "total",
	}
	am12Columns = map[string]string{
		"ลำดับ": "no", "รายการ": "description", "จำนวน": "quantity", "หน่วย": "unit", "ราคาต่อหน่วย (บาท)": "unit_price",
		"จำนวนเงิน (บาท)": "amount",
		"Description": "description", "Qty": "quantity", "Unit": "unit", "Unit price": "unit_price", "Amount": "amount",
	}
)

// formMappingByLabel answers the mapping prompt from the two tables above: each slot line the prompt carries, by its label.
func formMappingByLabel(body []byte) string {
	var req struct {
		Messages []struct {
			Content string `json:"content"`
		} `json:"messages"`
	}
	_ = json.Unmarshal(body, &req)
	mapping := map[string]string{}
	if n := len(req.Messages); n > 0 {
		for _, line := range strings.Split(req.Messages[n-1].Content, "\n") {
			var slot struct {
				Key          string            `json:"key"`
				Label        string            `json:"label"`
				ColumnLabels map[string]string `json:"column_labels"`
			}
			if !strings.HasPrefix(line, "{") || json.Unmarshal([]byte(line), &slot) != nil {
				continue
			}
			if len(slot.ColumnLabels) > 0 {
				mapping[slot.Key] = "lines"
				for col, heading := range slot.ColumnLabels {
					if c, ok := am12Columns[heading]; ok {
						mapping[slot.Key+"."+col] = "lines." + c
					}
				}
			} else if field, ok := am12Labels[slot.Label]; ok {
				mapping[slot.Key] = field
			}
		}
	}
	b, _ := json.Marshal(map[string]any{"mapping": mapping})
	return string(b)
}

// confirm is the person pressing save on the mapping screen with what was proposed, after the two checks the save runs.
func (e *itEnv) confirm(r *Runner, run am.Run, form *am.RoundForm) {
	e.t.Helper()
	var spec am.TemplateSpec
	_ = json.Unmarshal(r.Engine.Packages[procSlug].Manifest.Templates, &spec)
	if msg := am.CheckTemplateMapping(am.Thai, form.Slots, form.Proposal, spec) + am.CheckFormCoverage(am.Thai, form.Slots, form.Proposal); msg != "" {
		e.t.Fatalf("the proposed mapping is refused at the save: %s", msg)
	}
	form.Mapping, form.Status = form.Proposal, am.FormReady
	if err := e.store.SetRoundForm(context.Background(), e.store.Pool, run.ID, form); err != nil {
		e.t.Fatal(err)
	}
}

func TestAPurchaseRequestIsDraftedIntoTheOrganisationsOwnForm(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.setupProcurement(true)
	web := e.newStandIn()
	web.brief = strings.Replace(web.brief, `"quantity":null`, `"quantity":"6"`, 1)
	r := e.webRunner("r1", web)
	r.Converter = startConverter(t)
	run := e.request("ขอซื้อกระดานไวท์บอร์ดแม่เหล็ก ขนาด 120 x 240 ซม. จำนวน 6 แผ่น ส่งที่สำนักงานใหญ่ ชั้น 5 ถนนสีลม กรุงเทพฯ ใส่ในใบขอซื้อของบริษัทให้ด้วย")
	e.drive(r, run.ID, 4*time.Minute, parked)
	pick, qs := e.openCard(run.ID)

	// 1 · the Word form, dropped while the round waits for the choice. It is read: eleven blanks beside
	// their labels, a table of items, two blanks drawn as lines and a signature line.
	form := e.dropForm(r, run, "request_form.docx")
	if form.Status != am.FormNeedsMapping || form.ProposedBy == "" {
		t.Fatalf("the Word form after reading: %s %q, proposal %v", form.Status, form.Why, form.Proposal)
	}
	var items am.TemplateSlot
	lines := 0
	for _, sl := range form.Slots {
		if strings.HasPrefix(sl.Key, "rows@") {
			items = sl
		}
		if sl.Side == "inline" {
			lines++
		}
	}
	if items.Kind != "table" || items.Rows != 4 || len(items.Columns) != 6 || items.ColumnLabels["c1"] != "รายการ" || lines != 3 {
		t.Fatalf("the table of items and the drawn lines as read: %+v, %d lines", items, lines)
	}
	if form.Proposal[items.Key] != "lines" || form.Proposal[items.Key+".c1"] != "lines.description" || form.Proposal[items.Key+".c5"] != "lines.amount" ||
		form.Proposal["table1.r7c5"] != "total" {
		t.Fatalf("the proposal maps the table's columns by their headings and the totals by their labels: %v", form.Proposal)
	}
	if n := len(e.templates(run.ID)); n != 0 {
		t.Fatalf("a form was filled before the person confirmed and before anything was chosen (%d)", n)
	}
	e.confirm(r, run, form)

	// the person chooses; the round's files are the comparison and the organisation's form — not a second
	// purchase request beside it
	var ev struct {
		Comparison struct {
			Candidates []map[string]any `json:"candidates"`
		} `json:"comparison"`
	}
	_ = json.Unmarshal(qs[0].Evidence, &ev)
	chosen, _ := json.Marshal(ev.Comparison.Candidates[0]["key"])
	e.answerCard(pick, map[string]json.RawMessage{qs[0].Key: chosen})
	done := e.drive(r, run.ID, 5*time.Minute, terminal)
	if done.Status != am.RunSucceeded {
		t.Fatalf("the round: %s %s", done.Status, done.StatusReason)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	formats := map[string]int{}
	for _, a := range arts {
		formats[a.Format]++
	}
	if formats["xlsx"] != 1 || formats["template"] != 1 || formats["docx"] != 0 || len(arts) != 2 {
		t.Fatalf("the round's files: %v", formats)
	}
	form, _ = e.store.RoundForm(ctx, e.store.Pool, run.ID)
	word := e.templates(run.ID)[0]
	var q formQuality
	_ = json.Unmarshal(word.Validation, &q)
	lacking := map[string]string{}
	for _, u := range q.Unfilled {
		lacking[u.Label] = u.Reason
	}
	// a draft: not "every slot filled", and it says which — four blanks nobody has a value for, and two
	// this round has none for (nobody's name is on this run, and the request gave no date)
	want := map[string]string{"เลขที่ใบขอซื้อ": "not_mapped", "แผนก": "not_mapped", "เงื่อนไขการชำระเงิน": "not_mapped", "หมายเหตุ": "not_mapped",
		"ผู้ขอซื้อ": "no_value", "วันที่ต้องการของ": "no_value"}
	if form.Status != am.FormUnfilled || q.Passed || len(lacking) != len(want) {
		t.Fatalf("the Word draft: %s, passed %v, lacking %v", form.Status, q.Passed, lacking)
	}
	for label, why := range want {
		if lacking[label] != why {
			t.Fatalf("the Word draft lacks %s for %q, want %q (%v)", label, lacking[label], why, lacking)
		}
	}
	if ok, _, found := q.check("template.beside_label"); !found || !ok {
		t.Fatalf("the values were not read back beside their labels: %+v", q.Checks)
	}
	// on the file's own face: what no page could tell and the six blanks, each with why — and the file was
	// opened again to see every one of them there
	known := len(e.computedMap(run.ID)["request"].(map[string]any)["gaps"].([]any))
	if ok, values, found := q.check("template.gaps_printed"); !found || !ok || known < 9 || values != known+len(want) {
		t.Fatalf("the gaps on the face of the file: ok %v over %d (the round knows of %d, the form lacks %d)", ok, values, known, len(want))
	}
	item := am.FormItemPayload(run.ID, *form)
	if len(item.Unfilled) != 6 || item.UnfilledWhy[0].Empty() || !strings.Contains(item.Text.String(), "ได้ไม่ครบ") {
		t.Fatalf("the form's item names what it lacks: %+v", item)
	}
	e.keep(word, "request_form.draft.docx")

	// 2 · the same round in an Excel form of another shape, dropped after the round finished
	form = e.dropForm(r, done, "request_form.xlsx")
	if form.Status != am.FormNeedsMapping || form.Proposal["rows@s0r9"] != "lines" || form.Proposal["rows@s0r9.C"] != "lines.description" ||
		form.Proposal["PR!G17"] != "total" {
		t.Fatalf("the Excel form after reading: %s, proposal %v", form.Status, form.Proposal)
	}
	e.confirm(r, done, form)
	if _, err := r.formFillJob(ctx, done); err != nil {
		t.Fatal(err)
	}
	e.driveFormFill(r, run.ID)
	form, _ = e.store.RoundForm(ctx, e.store.Pool, run.ID)
	var sheet am.Artifact
	for _, a := range e.templates(run.ID) {
		if strings.HasSuffix(a.Name, ".xlsx") {
			sheet = a
		}
	}
	q = formQuality{}
	_ = json.Unmarshal(sheet.Validation, &q)
	lacking = map[string]string{}
	for _, u := range q.Unfilled {
		lacking[u.Label] = u.Reason
	}
	if form.Status != am.FormUnfilled || len(lacking) != 3 || lacking["Payment terms"] != "not_mapped" || lacking["Approved by"] != "not_mapped" ||
		lacking["Requested by"] != "no_value" {
		t.Fatalf("the Excel draft: %s, lacking %v", form.Status, lacking)
	}
	if ok, values, found := q.check("template.gaps_printed"); !found || !ok || values != known+3 {
		t.Fatalf("the gaps on the Excel form's last sheet: ok %v over %d (the round knows of %d)", ok, values, known)
	}
	if ok, _, found := q.check("template.formulas_kept"); !found || !ok {
		t.Fatalf("the form's own formula: %+v", q.Checks)
	}
	if ok, _, found := q.check("template.recalculated"); found && !ok {
		t.Fatalf("the form's own subtotal did not recalculate: %+v", q.Checks)
	}
	e.keep(sheet, "request_form.draft.xlsx")

	// 3 · a form for something else: the agent has a value for one of its ten blanks. Refused before any
	// work — no fill job, no file — and what it lacks is named, in the screen's language.
	jobs := e.count(`SELECT count(*) FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render'`, run.ID)
	files := len(e.templates(run.ID))
	form = e.dropForm(r, done, "other_form.docx")
	if form.Status != am.FormRefused || form.Why != am.RefusedTooFew {
		t.Fatalf("the other form: %s %q (proposal %v)", form.Status, form.Why, form.Proposal)
	}
	item = am.FormItemPayload(run.ID, *form)
	if item.Fields != 10 || item.Mapped != 1 || len(item.Unfilled) != 9 {
		t.Fatalf("what the refusal says: %d fields, %d with a value, %d named", item.Fields, item.Mapped, len(item.Unfilled))
	}
	for _, l := range []am.Lang{am.Thai, am.English} {
		said := item.Text.In(l)
		if am.LooksLikeError(said) || len(said) < 60 {
			t.Fatalf("the refusal in %s: %q", l, said)
		}
		t.Logf("refusal (%s): %s", l, said)
	}
	if n := e.count(`SELECT count(*) FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render'`, run.ID); n != jobs {
		t.Fatalf("a refused form started a fill (%d render jobs, was %d)", n, jobs)
	}
	if n := len(e.templates(run.ID)); n != files {
		t.Fatalf("a refused form delivered a file (%d filled forms, was %d)", n, files)
	}
	// 4 · a PDF with fields to type in, whose fields' own names say nothing: read under the words printed
	// beside them, filled, and still a form
	form = e.dropForm(r, done, "request_form.pdf")
	form.Kind = am.FormKindOf(r.Engine.Packages[procSlug].Manifest, form.Name) // (the message's door sets this; dropForm stands in for it)
	if form.Status != am.FormNeedsMapping || len(form.Slots) != 12 || form.Proposal["pdf:f1"] != "requester" || form.Proposal["pdf:f9"] != "total" {
		t.Fatalf("the PDF form after reading: %s %q, %d fields, proposal %v", form.Status, form.Why, len(form.Slots), form.Proposal)
	}
	e.confirm(r, done, form)
	if _, err := r.formFillJob(ctx, done); err != nil {
		t.Fatal(err)
	}
	e.driveFormFill(r, run.ID)
	form, _ = e.store.RoundForm(ctx, e.store.Pool, run.ID)
	var pdf am.Artifact
	for _, a := range e.templates(run.ID) {
		if strings.HasSuffix(a.Name, ".pdf") {
			pdf = a
		}
	}
	q = formQuality{}
	_ = json.Unmarshal(pdf.Validation, &q)
	if form.Status != am.FormUnfilled || pdf.Name == "" || len(q.Unfilled) != 4 {
		t.Fatalf("the PDF draft: %s, %q, lacking %v", form.Status, pdf.Name, q.Unfilled)
	}
	if ok, values, found := q.check("template.gaps_printed"); !found || !ok || values != known+4 {
		t.Fatalf("the gaps on the PDF form's last page: ok %v over %d (the round knows of %d)", ok, values, known)
	}
	e.keep(pdf, "request_form.draft.pdf")

	// 5 · the same page printed with no fields: a picture of a form. Refused as that, in words about a PDF.
	files = len(e.templates(run.ID))
	form = e.dropForm(r, done, "printed_form.pdf")
	form.Kind = am.FormKindOf(r.Engine.Packages[procSlug].Manifest, form.Name) // (the message's door sets this; dropForm stands in for it)
	if form.Status != am.FormRefused || form.Why != am.RefusedNoFields || form.Kind != "pdf" {
		t.Fatalf("the printed PDF: %s %q kind %q", form.Status, form.Why, form.Kind)
	}
	for _, l := range []am.Lang{am.Thai, am.English} {
		said := am.FormItemPayload(run.ID, *form).Text.In(l)
		if am.LooksLikeError(said) || !strings.Contains(said, "PDF") {
			t.Fatalf("the refusal of a printed PDF in %s: %q", l, said)
		}
		t.Logf("refusal of a PDF with no fields (%s): %s", l, said)
	}
	if n := len(e.templates(run.ID)); n != files {
		t.Fatalf("a refused PDF delivered a file (%d filled forms, was %d)", n, files)
	}

	// none of it is the install's own form
	defs, _ := e.store.ListDefaults(ctx, e.install.ID)
	if _, ok := am.SavedTemplate(defs); ok {
		t.Fatal("a round's form became the install's")
	}
}
