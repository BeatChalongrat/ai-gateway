package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/google/uuid"
)

// AM10 E: a form dropped into a round, for that round only; the "which
// files" question only when the install does not already say.

func formManifest(t *testing.T) Manifest {
	t.Helper()
	m, err := ParseManifest(json.RawMessage(invoiceTemplatesManifest))
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func TestAFormIsToldFromTheRoundsData(t *testing.T) {
	m := formManifest(t) // reads .pdf; fills forms
	files := []File{{ID: uuid.New(), Name: "ใบกำกับภาษี.pdf"}, {ID: uuid.New(), Name: "ฟอร์มสรุป.docx"}, {ID: uuid.New(), Name: "ทะเบียน.xlsx"}}
	forms, data := SplitForms(m, files, nil, "")
	if len(forms) != 2 || len(data) != 1 || data[0].Name != "ใบกำกับภาษี.pdf" {
		t.Fatalf("a file of a form's kind the agent does not read as input is a form: forms %d data %d", len(forms), len(data))
	}
	// an agent that reads .xlsx as input: an .xlsx is data unless the person says form, or marks it
	m.Inputs[0].Accept = []string{".xlsx"}
	forms, data = SplitForms(m, files[2:], nil, "เดือนนี้ครับ")
	if len(forms) != 0 || len(data) != 1 {
		t.Fatalf("an input kind is data: forms %d", len(forms))
	}
	forms, _ = SplitForms(m, files[2:], nil, "ใส่ในฟอร์มนี้ให้หน่อย")
	if len(forms) != 1 {
		t.Fatal("the words make it a form")
	}
	forms, _ = SplitForms(m, files[2:], map[uuid.UUID]bool{files[2].ID: true}, "")
	if len(forms) != 1 {
		t.Fatal("the mark makes it a form")
	}
	// an agent that fills no forms: everything is data, whatever the words
	m.Templates = nil
	forms, data = SplitForms(m, files, nil, "ใส่ในฟอร์มนี้")
	if len(forms) != 0 || len(data) != 3 {
		t.Fatal("no templates block: no form")
	}
	for _, s := range []string{"ใส่ในฟอร์มนี้ให้หน่อย", "กรอกลงแบบฟอร์มของบริษัท", "put it into this template"} {
		if !ReadsAsFormRequest(s) {
			t.Errorf("%q reads as a form request", s)
		}
	}
	if ReadsAsFormRequest("ทำไมยอดเดือนนี้ลด") {
		t.Error("a question is not a form request")
	}
}

func TestAFormDroppedAfterARoundIsThatRoundsOnly(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.install() // seeds the catalogue and the runner's capabilities
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.invoice-processing", Version: "1.0.0", Name: "ใบกำกับภาษี",
		Manifest: json.RawMessage(invoiceTemplatesManifest), Checksum: "inv", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	_, body := e.do("POST", "/installs", map[string]any{"slug": "sran.invoice-processing"})
	in, _ := e.store.GetInstall(ctx, e.user.TenantUUID, e.user.UserID, uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	// no round yet: the agent asks for the round's data first
	f0 := e.stagedFile(in, "ฟอร์มสรุป.docx")
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{f0.ID.String()}, "client_id": "f0"})
	if code != 200 || body["route"] != "form" || body["run_id"] != nil {
		t.Fatalf("a form before any round: %d %v", code, body)
	}
	if n := lastOfKind(e.threadItems(in, ""), "notice"); n == nil {
		t.Fatal("the thread is told to send the round's data first")
	}
	// a finished round, then the form
	done := e.finishedRun(in, "2026-09", "")
	f := e.stagedFile(in, "ฟอร์มสรุป.docx")
	code, body = e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{f.ID.String()},
		"text": "ใส่ในฟอร์มนี้ให้หน่อย", "client_id": "f1"})
	if code != 200 || body["route"] != "form" || body["run_id"] != done.ID.String() {
		t.Fatalf("the form goes to the round just finished: %d %v", code, body)
	}
	form, err := e.store.RoundForm(ctx, e.store.Pool, done.ID)
	if err != nil || form == nil || form.FileID != f.ID || form.Status != FormInspecting || form.Kind != "docx" || form.ItemID == nil {
		t.Fatalf("the round carries its form: %+v %v", form, err)
	}
	items := e.threadItems(in, "")
	item := lastOfKind(items, "form")
	if item == nil || item["run_id"] != done.ID.String() {
		t.Fatalf("the thread shows the form: %v", item)
	}
	p := item["payload"].(map[string]any)
	if p["status"] != FormInspecting || p["name"] != "ฟอร์มสรุป.docx" || p["text"] == nil || p["text"] == "" {
		t.Fatalf("the form item says what is happening: %v", p)
	}
	// the file is the round's form, never one of its inputs
	var status string
	_ = e.store.Pool.QueryRow(ctx, `SELECT status FROM agent_market.agent_files WHERE id = $1`, f.ID).Scan(&status)
	if status != "form" {
		t.Fatalf("file status %q", status)
	}
	if fs, _ := e.store.RunFiles(ctx, done.ID, "active"); len(fs) != 0 {
		t.Fatal("the form is listed among the round's inputs")
	}
	// the runner is asked to read it, for this round
	var params json.RawMessage
	_ = e.store.Pool.QueryRow(ctx, `SELECT params FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'mapping_preview'
		ORDER BY created_at DESC LIMIT 1`, in.ID).Scan(&params)
	var pr struct {
		Template bool      `json:"template"`
		RunID    uuid.UUID `json:"run_id"`
	}
	_ = json.Unmarshal(params, &pr)
	if !pr.Template || pr.RunID != done.ID {
		t.Fatalf("the inspection job names the round: %s", params)
	}
	// the install's own form is untouched
	if code, _ := e.do("GET", "/installs/"+in.ID.String()+"/template", nil); code != http.StatusNotFound {
		t.Fatalf("the install gained a template: %d", code)
	}
	// the mapping screen, saving to the round: a person's mapping makes it ready, and fills it
	form.Slots, form.Status = testSlots(), FormNeedsMapping
	_ = e.store.SetRoundForm(ctx, e.store.Pool, done.ID, form)
	code, got := e.do("GET", "/runs/"+done.ID.String()+"/template", nil)
	if code != 200 || got["spec"] == nil || got["inspect"].(map[string]any)["slots"] == nil {
		t.Fatalf("the round's form for the mapping screen: %d %v", code, got)
	}
	if code, _ := e.do("PUT", "/runs/"+done.ID.String()+"/template", map[string]any{"mapping": map[string]string{"GrandTotal": "total_thb"}}); code != http.StatusBadRequest {
		t.Fatalf("a formula cell is refused: %d", code)
	}
	code, saved := e.do("PUT", "/runs/"+done.ID.String()+"/template", map[string]any{"mapping": map[string]string{"CompanyName": "company", "LinesTable": "lines", "LinesTable.A": "lines.description"}})
	if code != 200 || saved["form"].(map[string]any)["status"] != FormReady || saved["job"] == nil {
		t.Fatalf("the mapping saved to the round, and a fill job: %d %v", code, saved)
	}
	var jp json.RawMessage
	_ = e.store.Pool.QueryRow(ctx, `SELECT params FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render'
		ORDER BY created_at DESC LIMIT 1`, done.ID).Scan(&jp)
	var jpp struct {
		FormOnly bool `json:"form_only"`
	}
	_ = json.Unmarshal(jp, &jpp)
	if !jpp.FormOnly {
		t.Fatalf("the fill job fills the form only: %s", jp)
	}
	if lastOfKind(e.threadItems(in, ""), "form")["payload"].(map[string]any)["status"] != FormReady {
		t.Fatal("the form item follows")
	}
	// the next round does not carry it
	next := e.finishedRun(in, "2026-10", "")
	if nf, _ := e.store.RoundForm(ctx, e.store.Pool, next.ID); nf != nil {
		t.Fatal("the form leaked into the next round")
	}
}

func TestWhichFilesIsAskedOnlyWhenTheInstallDoesNotSay(t *testing.T) {
	m := Manifest{Outputs: []struct {
		Format  string `json:"format"`
		Label   string `json:"label"`
		Default bool   `json:"default"`
	}{{"docx", "รายงาน", true}, {"pptx", "สไลด์", true}, {"xlsx", "ตาราง", false}, {"pdf", "PDF", false}}}
	shapes := OutputShapes(m)
	if len(shapes) != 4 || shapes[0].Label != "รายงาน Word + สไลด์ (ชุดมาตรฐาน)" || len(shapes[1].Formats) != 3 || shapes[2].Label != "รายงาน Word อย่างเดียว" {
		t.Fatalf("shapes: %+v", shapes)
	}
	copied := []Default{{Key: "formats", Value: json.RawMessage(`["docx","pptx"]`), Source: "install"}}
	f := OutputSetFinding(m, copied, []string{"docx", "pptx"})
	if f == nil || f.Severity != "cosmetic" || !f.Rememberable || f.Moment != "postcompute" || len(f.Options) != 4 {
		t.Fatalf("asked when the install's set was only copied from the manifest: %+v", f)
	}
	if err := f.Validate(); err != nil {
		t.Fatal(err)
	}
	if !JSONEqual(f.Default, f.Options[0].Value) {
		t.Fatalf("the default is the current set: %s", f.Default)
	}
	if OutputSetFinding(m, []Default{{Key: "formats", Value: json.RawMessage(`["docx"]`), Source: "settings"}}, []string{"docx"}) != nil {
		t.Fatal("the wizard's or the settings page's choice is the install saying")
	}
	if OutputSetFinding(m, []Default{{Key: "answers.output_set", Value: json.RawMessage(`["pptx"]`), Source: "answer"}}, nil) != nil {
		t.Fatal("a remembered answer is the install saying")
	}
	one := Manifest{Outputs: []struct {
		Format  string `json:"format"`
		Label   string `json:"label"`
		Default bool   `json:"default"`
	}{{"xlsx", "ทะเบียน", true}}}
	if OutputSetFinding(one, copied, nil) != nil {
		t.Fatal("one shape: nothing to ask")
	}
}

func TestAnAnsweredOutputSetBecomesTheRunsAndTheInstalls(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	run := e.finishedRun(in, "2026-09", "")
	answers := map[string]Answer{OutputSetCode: {Value: json.RawMessage(`["docx","xlsx"]`), Via: "card", Code: OutputSetCode}}
	if err := e.store.ApplyOutputSet(ctx, e.store.Pool, run, answers); err != nil {
		t.Fatal(err)
	}
	r, _ := e.store.RunByID(ctx, run.ID)
	var fs []string
	_ = json.Unmarshal(r.Formats, &fs)
	if len(fs) != 2 || fs[1] != "xlsx" {
		t.Fatalf("the run's set: %v", fs)
	}
	defs, _ := e.store.ListDefaults(ctx, in.ID)
	var formats *Default
	for i := range defs {
		if defs[i].Key == "formats" {
			formats = &defs[i]
		}
	}
	var setTo []string
	if formats != nil {
		_ = json.Unmarshal(formats.Value, &setTo)
	}
	if formats == nil || formats.Source != "answer" || len(setTo) != 2 || setTo[1] != "xlsx" {
		t.Fatalf("the install's formats setting follows the answer: %+v", formats)
	}
	// a remembered answer changes the run only, never the setting again
	m, _ := e.store.DefinitionByID(ctx, in.DefinitionID)
	mf, _ := ParseManifest(m.Manifest)
	if OutputSetFinding(mf, defs, fs) != nil {
		t.Fatal("asked again although the install now says")
	}
	run2 := e.finishedRun(in, "2026-10", "")
	_ = e.store.ApplyOutputSet(ctx, e.store.Pool, run2, map[string]Answer{OutputSetCode: {Value: json.RawMessage(`["pptx"]`), Via: "remembered"}})
	defs, _ = e.store.ListDefaults(ctx, in.ID)
	for _, d := range defs {
		var v []string
		_ = json.Unmarshal(d.Value, &v)
		if d.Key == "formats" && (len(v) != 2 || v[1] != "xlsx") {
			t.Fatalf("a remembered answer rewrote the setting: %s", d.Value)
		}
	}
}
