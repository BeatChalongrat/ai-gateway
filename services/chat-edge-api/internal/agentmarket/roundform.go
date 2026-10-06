package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
)

// ── A form for this round only (AM10 E) ──────────────────────────────────
//
// Template fill is chosen at install (template.go). What was missing is the
// obvious thing a person tries: "ใส่ในฟอร์มนี้ให้หน่อย" with a file dropped in
// right then. A form dropped into a round is used for that round and never
// becomes the install's: the round carries it (agent_runs.round_template —
// the file, what the model proposed, what the person mapped, what came of
// it), and the thread shows one "form" item that follows it.
//
// The AM8 rules hold exactly: the model proposes the mapping, the code fills
// and re-reads, and a partly filled form is not a pass. When every fillable
// slot has a proposal the mapping check accepts, the form is filled without
// a question; otherwise the item opens the mapping screen (the install's,
// saving to the round), and the fill follows the person's confirmation.
//
// Which file is a form: one the person marked so in the composer; a file of
// a form's kind (.docx .pptx .xlsx .xlsm) that is not one of the agent's
// inputs; or, when the message reads as a form request, every file of a
// form's kind in it. A form goes to the round the message lands in — the one
// just finished (the common case: the files are on screen), or the one still
// running or waiting (filled when it finishes). Without a round to put it
// in, the agent says so and waits for the round's data.

// Form statuses.
const (
	FormInspecting   = "inspecting"    // the runner is reading the file
	FormNeedsMapping = "needs_mapping" // a person decides which slot takes what
	FormReady        = "ready"         // mapped; filled when the round's figures are there
	FormFilled       = "filled"        // delivered, every slot holds its value
	FormUnfilled     = "unfilled"      // delivered beside the agent's files, not passed
	FormFailed       = "failed"        // the form cannot be used (a blocking slot, a file that cannot be read)
	// AM11: refused before any work — the agent has a value for less than half of the blanks the form
	// expects filled (they are named), or the file has nothing that takes a value. A person may still
	// map it themselves; a mapping that covers enough makes it ready.
	FormRefused = "refused"
)

// RoundForm is what a round carries about its form.
type RoundForm struct {
	FileID     uuid.UUID         `json:"file_id"`
	Name       string            `json:"name"`
	Kind       string            `json:"kind"`
	SHA256     string            `json:"sha256"`
	Status     string            `json:"status"`
	Slots      []TemplateSlot    `json:"slots,omitempty"`
	Proposal   map[string]string `json:"proposal,omitempty"`
	ProposedBy string            `json:"proposed_by,omitempty"`
	Mapping    map[string]string `json:"mapping,omitempty"`
	Unfilled   []json.RawMessage `json:"unfilled,omitempty"`
	ArtifactID *uuid.UUID        `json:"artifact_id,omitempty"`
	ItemID     *uuid.UUID        `json:"item_id,omitempty"`
	Why        string            `json:"why,omitempty"`
	UpdatedAt  time.Time         `json:"updated_at"`
	Fills      int               `json:"fills,omitempty"` // how many times it was filled (the artifact's version)
}

// FormPayload is the thread item that follows the round's form.
type FormPayload struct {
	RunID      string     `json:"run_id"`
	FileID     string     `json:"file_id"`
	Name       string     `json:"name"`
	Status     string     `json:"status"`
	Open       int        `json:"open"`     // slots a person still has to map
	Unfilled   []string   `json:"unfilled"` // the labels of the fields that could not be filled (delivered, or refused)
	ArtifactID string     `json:"artifact_id,omitempty"`
	Why        PersonText `json:"why,omitzero"`
	Text       PersonText `json:"text"`
	// AM11: why each field in Unfilled has no value (same order); why the form was refused or cannot be
	// used, as a code the page words; how many blanks the form expects filled and how many have a value.
	UnfilledWhy []PersonText `json:"unfilled_why,omitempty"`
	Reason      string       `json:"reason,omitempty"`
	Fields      int          `json:"fields,omitempty"`
	Mapped      int          `json:"mapped,omitempty"`
}

func (FormPayload) itemKind() string { return "form" }

// RoundForm reads a run's form, or nil.
func (s *Store) RoundForm(ctx context.Context, q Querier, run uuid.UUID) (*RoundForm, error) {
	var raw []byte
	if err := q.QueryRow(ctx, `SELECT round_template FROM agent_market.agent_runs WHERE id = $1`, run).Scan(&raw); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var f RoundForm
	if err := json.Unmarshal(raw, &f); err != nil {
		return nil, err
	}
	return &f, nil
}

// SetRoundForm writes a run's form.
func (s *Store) SetRoundForm(ctx context.Context, q Querier, run uuid.UUID, f *RoundForm) error {
	var b []byte
	if f != nil {
		f.UpdatedAt = time.Now().UTC()
		var err error
		if b, err = json.Marshal(f); err != nil {
			return err
		}
	}
	_, err := q.Exec(ctx, `UPDATE agent_market.agent_runs SET round_template = $2, updated_at = now() WHERE id = $1`, run, nullIfEmpty(b))
	return err
}

// FormText is what the form item says for a status.
func FormText(f RoundForm) PersonText {
	switch f.Status {
	case FormInspecting:
		return Text("กำลังอ่านแบบฟอร์ม %s", Name(f.Name))
	case FormNeedsMapping:
		return Text("อ่านแบบฟอร์ม %s แล้ว — มี %s ช่องที่ยังไม่แน่ใจว่ารับค่าอะไร กด “จับคู่ช่อง” เพื่อยืนยัน แล้วระบบจะกรอกให้",
			Name(f.Name), Count(openSlots(f)))
	case FormReady:
		return Text("จับคู่ช่องของ %s ครบแล้ว — จะกรอกให้เมื่อตัวเลขของรอบนี้พร้อม", Name(f.Name))
	case FormFilled:
		return Text("กรอก %s ให้แล้ว ใช้เฉพาะรอบนี้ — แบบฟอร์มที่ตั้งไว้ของ agent ไม่ถูกเปลี่ยน", Name(f.Name))
	case FormUnfilled:
		return Text("กรอก %s ได้ไม่ครบ %s ช่อง ส่งมาให้ดูแล้ว แต่ยังไม่ควรส่งต่อ — ดูชื่อช่องด้านล่าง", Name(f.Name), Count(len(f.Unfilled)))
	case FormRefused:
		if f.Why == RefusedNoFields {
			return Text("ไม่พบช่องให้กรอกในแบบฟอร์ม %s — ระบบอ่านช่องว่างที่อยู่ข้างหรือใต้ป้ายชื่อในตาราง ช่องที่มีเครื่องหมาย ชื่อช่วงเซลล์ใน Excel "+
				"และช่องกรอกของ Word ลองส่งแบบฟอร์มที่มีช่องแบบนี้ แบบฟอร์มที่ตั้งไว้ของ agent ไม่ถูกเปลี่ยน", Name(f.Name))
		}
		_, lacking, asked := FormRefusal(f.Slots, formMapping(f))
		return Text("ยังไม่กรอก %s ให้ — แบบฟอร์มมีช่องที่ต้องกรอก %s ช่อง agent นี้มีค่าให้ %s ช่อง ซึ่งไม่ถึงครึ่ง ดูชื่อช่องที่ยังไม่มีค่าด้านล่าง "+
			"ถ้าช่องใดรับค่าของ agent ได้ กด “จับคู่ช่อง” เพื่อเลือกเอง ยังไม่มีไฟล์ใดถูกสร้างหรือเปลี่ยน",
			Name(f.Name), Count(asked), Count(asked-len(lacking)))
	case FormFailed:
		if why := formWhy(f.Why); !why.Empty() {
			return Text("ใช้แบบฟอร์ม %s ไม่ได้ — %s แบบฟอร์มที่ตั้งไว้ของ agent ไม่ถูกเปลี่ยน", Name(f.Name), Words(why))
		}
		return Text("ใช้แบบฟอร์ม %s ไม่ได้ — ลองบันทึกเป็น .docx .xlsx หรือ .pptx แบบธรรมดาแล้วส่งใหม่", Name(f.Name))
	}
	return Text("แบบฟอร์ม %s", Name(f.Name))
}

// formWhy says in words why the filler cannot use a form at all (the filler's own code for it); nothing
// for a code it does not know — the sentence then says what to try instead.
func formWhy(code string) PersonText {
	switch code {
	case "chart_not_updated":
		return Text("มีกราฟที่เก็บตัวเลขไว้ในตัวเอง ระบบวาดใหม่ไม่ได้ จึงจะยังแสดงตัวเลขเดิม ลบกราฟออกแล้วส่งใหม่")
	case "unsupported_location":
		return Text("มีช่องที่อยู่ในส่วนของไฟล์ที่ระบบเติมไม่ได้ เช่น เชิงอรรถ หรือข้อความในกราฟ ย้ายช่องมาไว้ในเนื้อหาแล้วส่งใหม่")
	}
	return NoText
}

// unfilledWhy says in words why one field of a delivered form has no value (template_out.WHY_TH's codes).
func unfilledWhy(code, packageWords string) PersonText {
	switch code {
	case "not_mapped":
		return Text("ยังว่าง — ไม่ได้จับคู่ค่าให้ช่องนี้")
	case "no_value":
		return Text("รอบนี้ไม่มีค่าให้ช่องนี้")
	case "wrong_label":
		return Text("ป้ายข้างช่องนี้ไม่ตรงกับป้ายที่จับคู่ไว้")
	}
	return FromPackage(packageWords, Text("กรอกช่องนี้ไม่ได้"))
}

// formMapping is what a form is mapped to so far: the person's mapping, else what was proposed.
func formMapping(f RoundForm) map[string]string {
	if len(f.Mapping) > 0 {
		return f.Mapping
	}
	return f.Proposal
}

// NeedsDecision: a slot somebody has to decide about before a form is filled — every fillable slot,
// except a cell beside a title or a note of an unmarked form (AM11: read as a possible place, not as a
// blank the form expects filled; Side is set by a reader that tells the two apart).
func NeedsDecision(sl TemplateSlot) bool {
	if !sl.Fillable {
		return false
	}
	return !(sl.Kind == "target" && sl.Side != "" && !sl.Required)
}

func openSlots(f RoundForm) int {
	n := 0
	for _, sl := range f.Slots {
		if NeedsDecision(sl) && f.Mapping[sl.Key] == "" && f.Proposal[sl.Key] == "" {
			n++
		}
	}
	return n
}

// FormItemPayload is the thread item for a form as it stands.
func FormItemPayload(run uuid.UUID, f RoundForm) FormPayload {
	p := FormPayload{RunID: run.String(), FileID: f.FileID.String(), Name: f.Name, Status: f.Status, Open: openSlots(f),
		Unfilled: []string{}, Text: FormText(f)}
	if f.Status == FormNeedsMapping {
		p.Open = openSlots(f)
	}
	for _, u := range f.Unfilled {
		var x struct {
			Label  string `json:"label"`
			Key    string `json:"key"`
			Why    string `json:"why"`
			Reason string `json:"reason"`
		}
		if json.Unmarshal(u, &x) == nil {
			label := x.Label
			if label == "" {
				label = x.Key
			}
			if label != "" {
				p.Unfilled = append(p.Unfilled, label)
				p.UnfilledWhy = append(p.UnfilledWhy, unfilledWhy(x.Reason, x.Why))
			}
		}
	}
	// AM11: a refused form names the fields the agent has nothing for; a form that cannot be used says why
	switch f.Status {
	case FormRefused:
		p.Reason = f.Why
		if f.Why == RefusedTooFew {
			_, lacking, asked := FormRefusal(f.Slots, formMapping(f))
			p.Fields, p.Mapped = asked, asked-len(lacking)
			for _, l := range lacking {
				p.Unfilled = append(p.Unfilled, l)
				p.UnfilledWhy = append(p.UnfilledWhy, Text("agent นี้ไม่มีค่าสำหรับช่องนี้"))
			}
		}
	case FormFailed:
		p.Reason = f.Why
	}
	if f.ArtifactID != nil {
		p.ArtifactID = f.ArtifactID.String()
	}
	return p
}

// SyncFormItem writes the form's thread item (one per form, updated in place).
func (s *Store) SyncFormItem(ctx context.Context, q Querier, run Run, f *RoundForm) error {
	p := FormItemPayload(run.ID, *f)
	if f.ItemID != nil {
		b, err := json.Marshal(p)
		if err != nil {
			return err
		}
		_, err = q.Exec(ctx, `UPDATE agent_market.agent_thread_items SET payload = $2, rev = nextval('agent_market.thread_rev'),
			updated_at = now() WHERE id = $1`, *f.ItemID, b)
		return err
	}
	item, _, err := s.AddItemIn(ctx, q, run.InstallID, run.ThreadID, run.TenantID, run.UserID, &run.ID, p, nil)
	if err != nil {
		return err
	}
	f.ItemID = &item.ID
	return nil
}

// FormKind is the template kind of a file name, or "".
func FormKind(name string) string {
	low := strings.ToLower(name)
	for ext, k := range TemplateKinds {
		if strings.HasSuffix(low, ext) {
			return k
		}
	}
	return ""
}

var formWords = []string{"ฟอร์ม", "แบบฟอร์ม", "template", "เทมเพลต", "ใส่ใน", "กรอกใน", "กรอกลง", "ลงใน", "ใส่ลง", "fill in", "put in", "into this"}

// ReadsAsFormRequest: the person asked for their form to be filled.
func ReadsAsFormRequest(text string) bool {
	t := strings.ToLower(text)
	for _, w := range formWords {
		if strings.Contains(t, w) {
			return true
		}
	}
	return false
}

// SplitForms tells the forms in a message from its data: the files the person
// marked, the files of a form's kind the agent does not read as input, and —
// when the message asks for a form — every file of a form's kind.
func SplitForms(m Manifest, files []File, marked map[uuid.UUID]bool, text string) (forms, data []File) {
	if len(m.Templates) == 0 {
		return nil, files
	}
	inputs := map[string]bool{}
	for _, in := range m.Inputs {
		for _, ext := range in.Accept {
			inputs[strings.ToLower(ext)] = true
		}
	}
	asks := ReadsAsFormRequest(text)
	for _, f := range files {
		kind := FormKind(f.Name)
		isInput := false
		for ext := range inputs {
			if strings.HasSuffix(strings.ToLower(f.Name), ext) {
				isInput = true
			}
		}
		switch {
		case kind == "":
			data = append(data, f)
		case marked[f.ID], !isInput, asks:
			forms = append(forms, f)
		default:
			data = append(data, f)
		}
	}
	return forms, data
}

// roundForm puts a dropped form on the round the message lands in and starts
// the runner's reading of it. It returns the run, or nil when there is none
// to put it in (the thread was told).
func (a *API) roundForm(ctx context.Context, tx pgx.Tx, in Install, c middleware.Caller, o roundOpts, run *Run, f File) (*Run, error) {
	if run == nil {
		note := Text("ยังไม่มีรอบที่จะใส่แบบฟอร์ม %s — ส่งไฟล์ข้อมูลของรอบมาก่อน (หรือส่งมาพร้อมแบบฟอร์มในข้อความเดียว) แล้วค่อยบอกว่าจะใส่ในฟอร์มไหน", Name(f.Name))
		_, _, err := a.store.AddItemIn(ctx, tx, in.ID, o.thread, c.TenantUUID, c.UserID, nil, Notice{Text: note, Tone: "warning"}, nil)
		return nil, err
	}
	if _, err := tx.Exec(ctx, `UPDATE agent_market.agent_files SET run_id = $2, status = 'form' WHERE id = $1 AND status IN ('staged', 'pending_decision')`,
		f.ID, run.ID); err != nil {
		return nil, err
	}
	form := &RoundForm{FileID: f.ID, Name: f.Name, Kind: FormKind(f.Name), SHA256: f.SHA256, Status: FormInspecting}
	if err := a.store.SyncFormItem(ctx, tx, *run, form); err != nil {
		return nil, err
	}
	if err := a.store.SetRoundForm(ctx, tx, run.ID, form); err != nil {
		return nil, err
	}
	params, _ := json.Marshal(map[string]any{"file_id": f.ID, "template": true, "run_id": run.ID})
	if _, _, err := a.store.CreateRun(ctx, tx, Run{InstallID: in.ID, TenantID: c.TenantUUID, UserID: c.UserID,
		Kind: KindMappingPreview, Params: params, DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum,
		EngineChecksum: run.EngineChecksum, BindingVersion: in.BindingVersion, ThreadID: o.thread}, "form:"+run.ID.String()+":"+f.SHA256); err != nil {
		return nil, err
	}
	return run, nil
}

// manifestOf reads an install's manifest.
func (a *API) manifestOf(ctx context.Context, in Install) (Manifest, error) {
	def, err := a.store.DefinitionByID(ctx, in.DefinitionID)
	if err != nil {
		return Manifest{}, err
	}
	return ParseManifest(def.Manifest)
}

// formTarget is the round a dropped form goes to: the run the message just
// started or resumed, else the thread's latest round unless it failed or was
// cancelled (nil: nothing to put it in).
func (a *API) formTarget(ctx context.Context, in Install, o roundOpts, started *uuid.UUID) (*Run, error) {
	if started != nil {
		run, err := a.store.RunByID(ctx, *started)
		if err == nil && run.Kind == KindRun {
			return &run, nil
		}
	}
	latest, err := a.store.LatestRunIn(ctx, in.ID, o.thread)
	if errors.Is(err, ErrNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if latest.Status == RunFailed || latest.Status == RunCancelled {
		return nil, nil
	}
	return &latest, nil
}

// ── the mapping screen, saving to the round ──────────────────────────────

// getRunForm: the round's form as the mapping screen needs it.
func (a *API) getRunForm(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	run, err := a.store.GetRun(r.Context(), c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	f, err := a.store.RoundForm(r.Context(), a.store.Pool, run.ID)
	if err != nil || f == nil {
		say(w, http.StatusNotFound, "NO_TEMPLATE")
		return
	}
	in, err := a.store.GetInstall(r.Context(), c.TenantUUID, c.UserID, run.InstallID)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	spec, err := a.templateSpec(r, in)
	if err != nil {
		say(w, http.StatusConflict, "NO_TEMPLATES")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"form": f, "spec": spec,
		"inspect": map[string]any{"slots": f.Slots, "proposal": f.Proposal, "proposed_by": f.ProposedBy}})
}

// putRunForm saves the person's mapping for the round's form and has it filled.
func (a *API) putRunForm(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var body struct {
		Slots   []TemplateSlot    `json:"slots"`
		Mapping map[string]string `json:"mapping"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	run, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, id)
	if err != nil {
		a.fail(w, err, "run")
		return
	}
	in, err := a.store.GetInstall(ctx, c.TenantUUID, c.UserID, run.InstallID)
	if err != nil || !a.agentGate(w, r, in.Slug, false) {
		if err != nil {
			a.fail(w, err, "install")
		}
		return
	}
	spec, err := a.templateSpec(r, in)
	if err != nil {
		say(w, http.StatusConflict, "NO_TEMPLATES")
		return
	}
	f, err := a.store.RoundForm(ctx, a.store.Pool, run.ID)
	if err != nil || f == nil {
		say(w, http.StatusNotFound, "NO_TEMPLATE")
		return
	}
	slots := body.Slots
	if len(slots) == 0 {
		slots = f.Slots
	}
	msg := CheckTemplateMapping(slots, body.Mapping, spec)
	if msg == "" {
		msg = CheckFormCoverage(slots, body.Mapping)
	}
	if msg != "" {
		say(w, http.StatusBadRequest, "BAD_MAPPING", "detail", msg)
		return
	}
	f.Slots, f.Mapping, f.Status, f.Why = slots, body.Mapping, FormReady, ""
	tx, err := a.store.Pool.Begin(ctx)
	if err != nil {
		a.fail(w, err, "template")
		return
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	if err := a.store.SyncFormItem(ctx, tx, run, f); err != nil {
		a.fail(w, err, "template")
		return
	}
	if err := a.store.SetRoundForm(ctx, tx, run.ID, f); err != nil {
		a.fail(w, err, "template")
		return
	}
	// the round's figures are there: fill now; otherwise its render step will
	var job *Run
	if run.Status == RunSucceeded {
		j, err := a.formFillJob(ctx, tx, run)
		if err != nil {
			a.fail(w, err, "render")
			return
		}
		job = &j
	}
	if err := tx.Commit(ctx); err != nil {
		a.fail(w, err, "template")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"form": f, "job": job})
}

// formFillJob is a render job that fills the round's form and nothing else.
func (a *API) formFillJob(ctx context.Context, q Querier, run Run) (Run, error) {
	params, _ := json.Marshal(map[string]any{"formats": []string{}, "why": "round_form", "form_only": true})
	parent := run.ID
	job, _, err := a.store.CreateRun(ctx, q, Run{InstallID: run.InstallID, ParentRunID: &parent, TenantID: run.TenantID,
		UserID: run.UserID, Kind: KindRender, Params: params, DefinitionVersion: run.DefinitionVersion,
		Checksum: run.Checksum, EngineChecksum: run.EngineChecksum, BindingVersion: run.BindingVersion, ThreadID: run.ThreadID}, "")
	return job, err
}

// formRoutes mounts the round form's routes (inside the allowed group).
func (a *API) formRoutes(r chi.Router) {
	r.Get("/runs/{id}/template", a.getRunForm)
	r.Put("/runs/{id}/template", a.putRunForm)
}
