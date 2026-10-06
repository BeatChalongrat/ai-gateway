package runner

// AM11 (D) end to end (AGENT_MARKET_IT=1): forms that carry no marker, read
// by the engine in the real sandbox, mapped by a model that is given only
// their labels, filled, and opened again. Four forms of four shapes
// (backend/agent_market/tests/formkit.py → fixtures/forms):
//
//   monthly_memo.docx       every blank has a value  → filled without a question, passed
//   board_cover.xlsx        six of eight             → the person confirms; a draft naming the two
//   purchase_request.docx   one of twelve            → refused before any work, eleven named
//   plain_letter.docx       nothing to fill          → refused as that
//
// The filled files are kept when AM11_KEEP names a folder.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// what a reader of these forms would say each label is, among the report agent's fields — the stand-in
// for the model: it sees the labels the engine read and nothing else
var am11Labels = map[string]string{
	"ชื่อบริษัท": "company", "งวดบัญชี": "period", "รายได้จากการขาย (บาท)": "revenue", "กำไรขั้นต้น (บาท)": "gross_profit",
	"ค่าใช้จ่ายรวม (บาท)": "total_expenses", "กำไรสุทธิ (บาท)": "net_profit",
	"Company": "company", "Period": "period", "Revenue (THB)": "revenue", "Gross profit (THB)": "gross_profit",
	"Total expenses (THB)": "total_expenses", "Net profit (THB)": "net_profit",
}

// mappingByLabel answers the mapping prompt from am11Labels: each slot line the prompt carries, by its label.
func mappingByLabel(body []byte) string {
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
				Key   string `json:"key"`
				Label string `json:"label"`
			}
			if strings.HasPrefix(line, "{") && json.Unmarshal([]byte(line), &slot) == nil {
				if field, ok := am11Labels[slot.Label]; ok {
					mapping[slot.Key] = field
				}
			}
		}
	}
	b, _ := json.Marshal(map[string]any{"mapping": mapping})
	return string(b)
}

func (e *itEnv) formFile(name string) am.File {
	e.t.Helper()
	ctx := context.Background()
	data, err := os.ReadFile(filepath.Join(e.root, "backend", "agent_market", "fixtures", "forms", name))
	if err != nil {
		e.t.Fatal(err)
	}
	id := uuid.New()
	key := am.UploadKey(e.install.TenantID, e.install.ID, id, name)
	if err := e.files.Upload(ctx, key, bytes.NewReader(data), "", int64(len(data))); err != nil {
		e.t.Fatal(err)
	}
	f, err := e.store.CreateFile(ctx, e.store.Pool, am.File{ID: id, TenantID: e.install.TenantID, UserID: e.install.UserID,
		InstallID: e.install.ID, Name: name, Size: int64(len(data)), SHA256: fmt.Sprintf("%x", sha(data)), StorageKey: key, Status: "active"})
	if err != nil {
		e.t.Fatal(err)
	}
	return f
}

// dropForm is what the composer's message does with a form for a finished round, and the runner reading it.
func (e *itEnv) dropForm(r *Runner, run am.Run, name string) *am.RoundForm {
	e.t.Helper()
	ctx := context.Background()
	f := e.formFile(name)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_files SET run_id = $2, status = 'form' WHERE id = $1`, f.ID, run.ID); err != nil {
		e.t.Fatal(err)
	}
	form := &am.RoundForm{FileID: f.ID, Name: f.Name, Kind: am.FormKind(f.Name), SHA256: f.SHA256, Status: am.FormInspecting}
	if err := e.store.SyncFormItem(ctx, e.store.Pool, run, form); err != nil {
		e.t.Fatal(err)
	}
	if err := e.store.SetRoundForm(ctx, e.store.Pool, run.ID, form); err != nil {
		e.t.Fatal(err)
	}
	params, _ := json.Marshal(map[string]any{"file_id": f.ID, "template": true, "run_id": run.ID})
	job, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: e.install.ID, TenantID: e.install.TenantID,
		UserID: e.install.UserID, Kind: am.KindMappingPreview, Params: params, DefinitionVersion: e.install.DefinitionVersion,
		Checksum: e.install.Checksum}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	if got := e.drive(r, job.ID, 3*time.Minute, terminal); got.Status != am.RunSucceeded {
		e.t.Fatalf("reading %s: %s %s", name, got.Status, got.StatusReason)
	}
	form, err = e.store.RoundForm(ctx, e.store.Pool, run.ID)
	if err != nil || form == nil {
		e.t.Fatalf("the round lost its form: %v", err)
	}
	return form
}

// templates lists the round's filled forms.
func (e *itEnv) templates(run uuid.UUID) []am.Artifact {
	arts, _ := e.store.RunArtifacts(context.Background(), run)
	var out []am.Artifact
	for _, a := range arts {
		if a.Format == "template" {
			out = append(out, a)
		}
	}
	return out
}

func (e *itEnv) keep(a am.Artifact, as string) {
	dir := os.Getenv("AM11_KEEP")
	if dir == "" {
		return
	}
	body, _, _, err := e.files.Download(context.Background(), a.StorageKey)
	if err != nil {
		e.t.Fatalf("keeping %s: %v", as, err)
	}
	defer body.Close()
	data, _ := io.ReadAll(body)
	_ = os.MkdirAll(dir, 0o755)
	if err := os.WriteFile(filepath.Join(dir, as), data, 0o644); err != nil {
		e.t.Fatal(err)
	}
}

type formQuality struct {
	Passed bool `json:"passed"`
	Checks []struct {
		Name   string `json:"name"`
		OK     bool   `json:"ok"`
		Values int    `json:"values"`
	} `json:"checks"`
	Unfilled []struct {
		Label  string `json:"label"`
		Reason string `json:"reason"`
	} `json:"unfilled"`
}

func (q formQuality) check(name string) (ok bool, values int, found bool) {
	for _, c := range q.Checks {
		if c.Name == name {
			return c.OK, c.Values, true
		}
	}
	return false, 0, false
}

func TestFormsWithNoMarkerAreReadMappedFilledAndOpenedAgain(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	conv := startConverter(t)
	ctx := context.Background()
	r := e.runner("r1")
	r.Converter = conv
	e.formLabels = am11Labels

	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx", "sample-data/งบประมาณ_2569.xlsx")
	if got := e.drive(r, run.ID, 5*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("the round: %s %s", got.Status, got.StatusReason)
	}
	done, _ := e.store.RunByID(ctx, run.ID)
	reports := func() int {
		arts, _ := e.store.RunArtifacts(ctx, run.ID)
		n := 0
		for _, a := range arts {
			if a.Format != "template" {
				n++
			}
		}
		return n
	}
	before := reports()

	// 1 · the Word memo: a blank beside each label, a row of blanks under a row of headings. Every blank
	// has a proposal, so nobody is asked and the fill follows at once.
	form := e.dropForm(r, done, "monthly_memo.docx")
	if form.Status != am.FormReady || len(form.Mapping) != 6 {
		t.Fatalf("the memo after reading: %s %q, %d slots, mapping %v, proposal %v", form.Status, form.Why, len(form.Slots), form.Mapping, form.Proposal)
	}
	for _, sl := range form.Slots {
		if sl.Kind != "target" || !sl.Required || sl.LabelAt == "" {
			t.Fatalf("a blank of the memo was read as %+v", sl)
		}
	}
	e.driveFormFill(r, run.ID)
	form, _ = e.store.RoundForm(ctx, e.store.Pool, run.ID)
	memo := e.templates(run.ID)
	if form.Status != am.FormFilled || len(memo) != 1 || !strings.HasSuffix(memo[0].Name, ".docx") {
		t.Fatalf("the memo: %s, %d filled forms", form.Status, len(memo))
	}
	var q formQuality
	_ = json.Unmarshal(memo[0].Validation, &q)
	if ok, values, _ := q.check("template.every_slot"); !q.Passed || !ok || values != 6 || len(q.Unfilled) != 0 {
		t.Fatalf("the memo's own re-read: passed %v, every_slot %v over %d, unfilled %v", q.Passed, ok, values, q.Unfilled)
	}
	if ok, _, found := q.check("template.beside_label"); !found || !ok {
		t.Fatalf("the memo was not read back beside its labels: %+v", q.Checks)
	}
	e.keep(memo[0], "monthly_memo.filled.docx")

	// 2 · the Excel cover sheet: six of its eight blanks have a value. That is enough to fill and too
	// little to call done: the person confirms, and the delivered file is a draft that names the two.
	form = e.dropForm(r, done, "board_cover.xlsx")
	if form.Status != am.FormNeedsMapping || len(form.Proposal) != 6 || form.ProposedBy == "" {
		t.Fatalf("the cover after reading: %s, proposal %v, by %q", form.Status, form.Proposal, form.ProposedBy)
	}
	if n := len(e.templates(run.ID)); n != 1 {
		t.Fatalf("the cover was filled before the person confirmed (%d filled forms)", n)
	}
	var spec am.TemplateSpec
	_ = json.Unmarshal(r.Engine.Packages["sran.financial-report"].Manifest.Templates, &spec)
	if msg := am.CheckTemplateMapping(am.Thai, form.Slots, form.Proposal, spec) + am.CheckFormCoverage(am.Thai, form.Slots, form.Proposal); msg != "" {
		t.Fatalf("six of eight is refused at the save: %s", msg)
	}
	form.Mapping, form.Status = form.Proposal, am.FormReady
	if err := e.store.SetRoundForm(ctx, e.store.Pool, run.ID, form); err != nil {
		t.Fatal(err)
	}
	if _, err := r.formFillJob(ctx, done); err != nil {
		t.Fatal(err)
	}
	e.driveFormFill(r, run.ID)
	form, _ = e.store.RoundForm(ctx, e.store.Pool, run.ID)
	filled := e.templates(run.ID)
	if form.Status != am.FormUnfilled || len(filled) != 2 {
		t.Fatalf("the cover: %s, %d filled forms", form.Status, len(filled))
	}
	var cover am.Artifact
	for _, a := range filled {
		if strings.HasSuffix(a.Name, ".xlsx") {
			cover = a
		}
	}
	q = formQuality{}
	_ = json.Unmarshal(cover.Validation, &q)
	if q.Passed || len(q.Unfilled) != 2 {
		t.Fatalf("a form with two blanks left is a draft naming two: passed %v, unfilled %v", q.Passed, q.Unfilled)
	}
	lacking := map[string]string{}
	for _, u := range q.Unfilled {
		lacking[u.Label] = u.Reason
	}
	if lacking["Headcount"] != "not_mapped" || lacking["Cash at bank (THB)"] != "not_mapped" {
		t.Fatalf("the two it lacks, by name and reason: %v", lacking)
	}
	if ok, values, _ := q.check("template.every_slot"); ok || values != 8 {
		t.Fatalf("every_slot over the form's eight blanks: ok %v, %d", ok, values)
	}
	if ok, _, found := q.check("template.recalculated"); found && !ok {
		t.Fatalf("the form's own formula did not recalculate: %+v", q.Checks)
	}
	item := am.FormItemPayload(run.ID, *form)
	if len(item.Unfilled) != 2 || len(item.UnfilledWhy) != 2 || item.UnfilledWhy[0].Empty() {
		t.Fatalf("the form's item names what it lacks and why: %+v", item)
	}
	e.keep(cover, "board_cover.draft.xlsx")

	// 3 · the purchase request: the agent has a value for one of its twelve blanks. Refused before any
	// work — no file, no fill job — and the eleven are named.
	jobs := e.count(`SELECT count(*) FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render'`, run.ID)
	form = e.dropForm(r, done, "purchase_request.docx")
	if form.Status != am.FormRefused || form.Why != am.RefusedTooFew {
		t.Fatalf("the purchase request: %s %q (proposal %v)", form.Status, form.Why, form.Proposal)
	}
	item = am.FormItemPayload(run.ID, *form)
	if item.Fields != 12 || item.Mapped != 1 || len(item.Unfilled) != 11 {
		t.Fatalf("what the refusal says: %d fields, %d with a value, %d named", item.Fields, item.Mapped, len(item.Unfilled))
	}
	for _, l := range item.Unfilled {
		if l == "ชื่อบริษัท" {
			t.Fatal("the one field the agent has a value for is named as lacking")
		}
	}
	if n := e.count(`SELECT count(*) FROM agent_market.agent_runs WHERE parent_run_id = $1 AND kind = 'render'`, run.ID); n != jobs {
		t.Fatalf("a refused form started a fill (%d render jobs, was %d)", n, jobs)
	}
	if n := len(e.templates(run.ID)); n != 2 {
		t.Fatalf("a refused form delivered a file (%d filled forms)", n)
	}
	// the person may map it themselves; a save that still covers less than half is refused with the names
	if msg := am.CheckFormCoverage(am.Thai, form.Slots, form.Proposal); !strings.Contains(msg, "12 ช่อง") || !strings.Contains(msg, "เหตุผลที่ขอซื้อ") {
		t.Fatalf("the save of one of twelve: %q", msg)
	}

	// 4 · a letter: nothing in it takes a value
	form = e.dropForm(r, done, "plain_letter.docx")
	if form.Status != am.FormRefused || form.Why != am.RefusedNoFields {
		t.Fatalf("the letter: %s %q", form.Status, form.Why)
	}
	if text := am.FormItemPayload(run.ID, *form).Text.String(); !strings.Contains(text, "ไม่พบช่อง") {
		t.Fatalf("the letter's item: %s", text)
	}

	// none of it made the agent's own files again, and the install has no form of its own
	if n := reports(); n != before {
		t.Fatalf("the round's own files: %d, were %d", n, before)
	}
	defs, _ := e.store.ListDefaults(ctx, e.install.ID)
	if _, ok := am.SavedTemplate(defs); ok {
		t.Fatal("a round's form became the install's")
	}
}

// driveFormFill runs the round's newest fill job to its end.
func (e *itEnv) driveFormFill(r *Runner, run uuid.UUID) {
	e.t.Helper()
	var id uuid.UUID
	if err := e.store.Pool.QueryRow(context.Background(), `SELECT id FROM agent_market.agent_runs WHERE parent_run_id = $1
		AND kind = 'render' AND params->>'why' = 'round_form' ORDER BY created_at DESC LIMIT 1`, run).Scan(&id); err != nil {
		e.t.Fatalf("no fill job for the round's form: %v", err)
	}
	if got := e.drive(r, id, 5*time.Minute, terminal); got.Status != am.RunSucceeded {
		e.t.Fatalf("filling the form: %s %s", got.Status, got.StatusReason)
	}
}
