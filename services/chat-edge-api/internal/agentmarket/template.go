package agentmarket

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

// ── The customer's own template (AM2 Phase E) ────────────────────────────
//
// An install may keep one template (.docx, .xlsx or .pptx) that each run
// fills with its values (backend/agent_market/deliverables/template_out.py):
//
//	POST   /installs/{id}/template   upload a template (a new version) → the
//	                                 runner inspects it (a mapping_preview job)
//	                                 and proposes a mapping, diffed against the
//	                                 saved version
//	PUT    /installs/{id}/template   save the mapping the person confirmed
//	GET    /installs/{id}/template   the saved template
//	DELETE /installs/{id}/template   stop filling a template
//
// Saved as the install default "template"; the file stays an install file
// (status active, no run). The mapping is checked against the template's
// own slots and the agent's manifest (its "templates" block) before it is
// kept: a value can only go to a place that takes one, from a field the
// agent has.

// TemplateKinds are the files a template can be.
var TemplateKinds = map[string]string{".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pptx": "pptx"}

// TemplateDefault is the saved template (install default "template").
type TemplateDefault struct {
	FileID  uuid.UUID         `json:"file_id"`
	Name    string            `json:"name"`
	Kind    string            `json:"kind"`
	SHA256  string            `json:"sha256"`
	Version int               `json:"version"`
	Slots   []TemplateSlot    `json:"slots"`
	Mapping map[string]string `json:"mapping"`
	SavedAt time.Time         `json:"saved_at"`
}

// TemplateSlot is one place a template takes a value (template_out.slots).
type TemplateSlot struct {
	Key      string   `json:"key"`
	Kind     string   `json:"kind"`
	Label    string   `json:"label"`
	Where    string   `json:"where,omitempty"`
	Fillable bool     `json:"fillable"`
	Reason   *string  `json:"reason"`
	Columns  []string `json:"columns,omitempty"`
	// AM8: the text around the slot (what the model reads to propose a field for it), and whether the
	// slot stops the template being used at all (a chart left with old numbers, a marker the filler
	// cannot reach) — refused before any work, with its reason.
	Context  string `json:"context,omitempty"`
	Blocking bool   `json:"blocking,omitempty"`
	// AM11: a blank found by its label in a form that carries no marker (kind "target"). Required: the
	// form expects it filled (a colon, a drawn cell, a list of fields, a table of a Word form) — a title
	// with an empty cell beside it is not. Side and LabelAt say where its label is; the filled file is
	// opened again and the label read there.
	Required bool   `json:"required,omitempty"`
	Side     string `json:"side,omitempty"`
	LabelAt  string `json:"label_at,omitempty"`
	// AM12: the rows of a table of items in a form that carries no marker (kind "table", key "rows@…"):
	// each column is named by the heading over it, and Rows is how many rows the form has for lines.
	ColumnLabels map[string]string `json:"column_labels,omitempty"`
	Rows         int               `json:"rows,omitempty"`
}

// Why a form is refused before any work (AM11).
const (
	RefusedTooFew   = "too_few_fields" // the agent has a value for less than half of the blanks the form expects filled
	RefusedNoFields = "no_fields"      // the file has nothing that takes a value
)

// refuseBelow: a form is filled when the agent has a value for at least this share of the blanks it
// expects filled. Under it the result would be a mostly empty form with an agent's name on it; the
// form is refused instead, and the blanks it has nothing for are named. With fewer than
// refuseFrom such blanks there is too little to judge by, and the form is filled and its gaps named.
const (
	refuseBelowNum, refuseBelowDen = 1, 2
	refuseFrom                     = 3
)

// FormRefusal says whether a form is refused before any work, given what is mapped so far: the reason
// ("" = not refused), the labels of the expected blanks nothing is mapped to, and how many blanks the
// form expects filled. Only the blanks of an unmarked form count: a marker, a named cell or a control a
// person left "not used" was their decision about their own form (AM8).
func FormRefusal(slots []TemplateSlot, mapping map[string]string) (why string, lacking []string, asked int) {
	fillable := 0
	mapped := 0
	for _, s := range slots {
		if !s.Fillable {
			continue
		}
		fillable++
		if s.Kind != "target" || !s.Required {
			continue
		}
		asked++
		if mapping[s.Key] != "" {
			mapped++
		} else {
			lacking = append(lacking, s.Label)
		}
	}
	if fillable == 0 {
		return RefusedNoFields, nil, 0
	}
	if asked >= refuseFrom && mapped*refuseBelowDen < asked*refuseBelowNum {
		return RefusedTooFew, lacking, asked
	}
	return "", nil, asked
}

// TemplateSpec is the manifest's "templates" block: the fields and lists an
// agent can put into a template.
type TemplateSpec struct {
	Fields map[string]json.RawMessage `json:"fields"`
	Lists  map[string]struct {
		Label   string            `json:"label"`
		Columns map[string]string `json:"columns"`
	} `json:"lists"`
}

// SavedTemplate reads an install's saved template (ok=false when there is none).
func SavedTemplate(defs []Default) (TemplateDefault, bool) {
	for _, d := range defs {
		if d.Key == "template" {
			var t TemplateDefault
			if json.Unmarshal(d.Value, &t) == nil && t.FileID != uuid.Nil {
				return t, true
			}
		}
	}
	return TemplateDefault{}, false
}

func (a *API) templateSpec(r *http.Request, in Install) (TemplateSpec, error) {
	def, err := a.store.DefinitionByID(r.Context(), in.DefinitionID)
	if err != nil {
		return TemplateSpec{}, err
	}
	m, err := ParseManifest(def.Manifest)
	if err != nil {
		return TemplateSpec{}, err
	}
	var spec TemplateSpec
	if len(m.Templates) == 0 || json.Unmarshal(m.Templates, &spec) != nil || len(spec.Fields)+len(spec.Lists) == 0 {
		return TemplateSpec{}, errNoTemplates
	}
	return spec, nil
}

var errNoTemplates = errors.New("this agent fills no templates")

func (a *API) uploadTemplate(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok || !a.agentGate(w, r, in.Slug, false) {
		return
	}
	if _, err := a.templateSpec(r, in); err != nil {
		say(w, http.StatusConflict, "NO_TEMPLATES")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, a.cfg.MaxUploadBytes+(1<<20))
	if err := r.ParseMultipartForm(8 << 20); err != nil {
		say(w, http.StatusRequestEntityTooLarge, "UPLOAD_TOO_LARGE", "mb", fmt.Sprint(a.cfg.MaxUploadBytes>>20))
		return
	}
	defer r.MultipartForm.RemoveAll() //nolint:errcheck
	fh, hdr, err := r.FormFile("file")
	if err != nil {
		badRequest(w, "NO_FILE")
		return
	}
	defer fh.Close()
	name := SafeName(hdr.Filename)
	kind := FormKind(name)
	if m, err := a.manifestOf(r.Context(), in); err == nil {
		kind = FormKindOf(m, name) // AM12: a PDF with fields, for an agent that reads them
	}
	if kind == "" {
		say(w, http.StatusUnsupportedMediaType, "TEMPLATE_FILE_TYPE")
		return
	}
	tmp, err := os.CreateTemp("", "am-template-*")
	if err != nil {
		a.fail(w, err, "template")
		return
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()
	h := sha256.New()
	size, err := io.Copy(io.MultiWriter(tmp, h), fh)
	if err != nil {
		a.fail(w, err, "template")
		return
	}
	if _, err := tmp.Seek(0, io.SeekStart); err != nil {
		a.fail(w, err, "template")
		return
	}
	id := uuid.New()
	key := UploadKey(c.TenantUUID, in.ID, id, name)
	if err := a.files.Upload(r.Context(), key, tmp, hdr.Header.Get("Content-Type"), size); err != nil {
		a.fail(w, err, "template")
		return
	}
	f, err := a.store.CreateFile(r.Context(), a.store.Pool, File{ID: id, TenantID: c.TenantUUID, UserID: c.UserID,
		InstallID: in.ID, Name: name, ContentType: hdr.Header.Get("Content-Type"), Size: size,
		SHA256: hex.EncodeToString(h.Sum(nil)), StorageKey: key, Status: "active"})
	if err != nil {
		a.fail(w, err, "template")
		return
	}
	params, _ := json.Marshal(map[string]any{"file_id": f.ID, "template": true})
	job, _, err := a.store.CreateRun(r.Context(), a.store.Pool, Run{InstallID: in.ID, TenantID: c.TenantUUID, UserID: c.UserID,
		Kind: KindMappingPreview, Params: params, DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum,
		BindingVersion: in.BindingVersion}, "")
	if err != nil {
		a.fail(w, err, "template")
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"file": f, "kind": kind, "job": job})
}

func (a *API) saveTemplate(w http.ResponseWriter, r *http.Request) {
	in, c, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	spec, err := a.templateSpec(r, in)
	if err != nil {
		say(w, http.StatusConflict, "NO_TEMPLATES")
		return
	}
	var body struct {
		FileID  uuid.UUID         `json:"file_id"`
		Slots   []TemplateSlot    `json:"slots"`
		Mapping map[string]string `json:"mapping"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil || body.FileID == uuid.Nil {
		badRequest(w, "BAD_JSON")
		return
	}
	f, err := a.store.GetFile(r.Context(), c.TenantUUID, c.UserID, body.FileID)
	if err != nil || f.InstallID != in.ID || f.Status == "removed" {
		say(w, http.StatusNotFound, "NO_TEMPLATE_FILE")
		return
	}
	kind := FormKind(f.Name)
	if m, err := a.manifestOf(r.Context(), in); err == nil {
		kind = FormKindOf(m, f.Name)
	}
	if kind == "" {
		say(w, http.StatusUnsupportedMediaType, "TEMPLATE_FILE_TYPE")
		return
	}
	msg := CheckTemplateMapping(langOfWriter(w), body.Slots, body.Mapping, spec)
	if msg == "" {
		msg = CheckFormCoverage(langOfWriter(w), body.Slots, body.Mapping)
	}
	if msg != "" {
		say(w, http.StatusBadRequest, "BAD_MAPPING", "detail", msg)
		return
	}
	defs, err := a.store.ListDefaults(r.Context(), in.ID)
	if err != nil {
		a.fail(w, err, "template")
		return
	}
	version := 1
	if prev, ok := SavedTemplate(defs); ok {
		version = prev.Version
		if prev.SHA256 != f.SHA256 {
			version++
		}
	}
	t := TemplateDefault{FileID: f.ID, Name: f.Name, Kind: kind, SHA256: f.SHA256, Version: version, Slots: body.Slots,
		Mapping: body.Mapping, SavedAt: time.Now().UTC()}
	value, _ := json.Marshal(t)
	if err := a.store.SetDefault(r.Context(), a.store.Pool, in.ID, Default{Key: "template", Value: value, Source: "settings",
		Label: T(LangOf(r.Context()), "แบบฟอร์มของคุณ"), Note: fmt.Sprintf(T(LangOf(r.Context()), "%s รุ่นที่ %d"), f.Name, version)}); err != nil {
		a.fail(w, err, "template")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"template": t})
}

func (a *API) getTemplate(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	defs, err := a.store.ListDefaults(r.Context(), in.ID)
	if err != nil {
		a.fail(w, err, "template")
		return
	}
	t, ok := SavedTemplate(defs)
	if !ok {
		say(w, http.StatusNotFound, "NO_TEMPLATE")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"template": t})
}

func (a *API) removeTemplate(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	removed, err := a.store.DeleteDefault(r.Context(), in.ID, "template")
	if err != nil {
		a.fail(w, err, "template")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"removed": removed})
}

// CheckTemplateMapping says what is wrong with a mapping ("" when nothing):
// every key names a place in the template that takes a value (a list's
// column as "slot.column"), every value one of the agent's fields (a list's
// column as "list.column"), and a list goes only to a list or a table.
// AM11: it says so in l — the request's language at a save, the run's in the runner.
func CheckTemplateMapping(l Lang, slots []TemplateSlot, mapping map[string]string, spec TemplateSpec) string {
	by := map[string]TemplateSlot{}
	for _, s := range slots {
		by[s.Key] = s
	}
	var stops []string
	for _, s := range slots {
		if s.Blocking {
			stops = append(stops, s.Label)
		}
	}
	if len(stops) > 0 {
		return T(l, "แบบฟอร์มนี้ใช้ไม่ได้: มีส่วนที่ระบบเติมหรืออัปเดตไม่ได้ (") + strings.Join(stops, ", ") +
			T(l, ") แก้แบบฟอร์มแล้วอัปโหลดใหม่")
	}
	if len(mapping) == 0 {
		return T(l, "ยังไม่ได้เลือกค่าให้ช่องใดเลย")
	}
	for k, v := range mapping {
		base, col, sub := strings.Cut(k, ".")
		s, ok := by[k]
		if !ok && sub {
			s, ok = by[base]
		}
		if !ok {
			return T(l, "ไม่มีช่อง “") + k + T(l, "” ในแบบฟอร์ม")
		}
		if !s.Fillable {
			return T(l, "ช่อง “") + s.Label + T(l, "” เติมค่าไม่ได้")
		}
		isList := s.Kind == "list" || s.Kind == "table"
		if _, exact := by[k]; exact || !sub {
			if isList {
				if _, ok := spec.Lists[v]; !ok {
					return T(l, "ช่อง “") + s.Label + T(l, "” ต้องเป็นรายการ")
				}
			} else if _, ok := spec.Fields[v]; !ok {
				return T(l, "ไม่มีค่า “") + v + T(l, "” ใน agent ตัวนี้")
			}
			continue
		}
		// a list column: slot.column → list.column
		if !isList || !contains(s.Columns, col) {
			return T(l, "ไม่มีคอลัมน์ “") + col + T(l, "” ในช่อง “") + s.Label + "”"
		}
		list, lcol, _ := strings.Cut(v, ".")
		found, ok := spec.Lists[list]
		if !ok || mapping[base] != list {
			return T(l, "คอลัมน์ “") + col + T(l, "” ต้องมาจากรายการเดียวกับช่อง “") + s.Label + "”"
		}
		if _, ok := found.Columns[lcol]; !ok {
			return T(l, "ไม่มีคอลัมน์ “") + lcol + T(l, "” ในรายการ “") + found.Label + "”"
		}
	}
	return ""
}

// CheckFormCoverage says why a mapping a person saves is not enough to fill the form at all ("" when it
// is): AM11 — a form left mostly empty is not filled; the blanks nothing was chosen for are named. Asked
// at the save, after CheckTemplateMapping, and by the runner once a model has read the form — never of a
// mapping that is still being put together pair by pair.
func CheckFormCoverage(l Lang, slots []TemplateSlot, mapping map[string]string) string {
	why, lacking, asked := FormRefusal(slots, mapping)
	if why != RefusedTooFew {
		return ""
	}
	return T(l, "แบบฟอร์มนี้มีช่องที่ต้องกรอก ") + strconv.Itoa(asked) + T(l, " ช่อง เลือกค่าให้แล้ว ") + strconv.Itoa(asked-len(lacking)) +
		T(l, " ช่อง ซึ่งไม่ถึงครึ่ง จึงยังไม่กรอกให้ — ช่องที่ยังไม่มีค่า: ") + quotedList(lacking)
}

// quotedList is the customer's own labels, each in quotation marks (they are theirs, read as written).
func quotedList(labels []string) string {
	out := make([]string, 0, len(labels))
	for _, l := range labels {
		out = append(out, quoted(l))
	}
	return strings.Join(out, ", ")
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}
