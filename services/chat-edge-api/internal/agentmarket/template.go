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
	if !ok {
		return
	}
	if _, err := a.templateSpec(r, in); err != nil {
		writeErr(w, http.StatusConflict, "NO_TEMPLATES", "agent ตัวนี้ยังไม่รองรับแบบฟอร์มของคุณ")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, a.cfg.MaxUploadBytes+(1<<20))
	if err := r.ParseMultipartForm(8 << 20); err != nil {
		writeErr(w, http.StatusRequestEntityTooLarge, "UPLOAD_TOO_LARGE", fmt.Sprintf("ไฟล์ใหญ่เกิน %d MB", a.cfg.MaxUploadBytes>>20))
		return
	}
	defer r.MultipartForm.RemoveAll() //nolint:errcheck
	fh, hdr, err := r.FormFile("file")
	if err != nil {
		writeErr(w, http.StatusBadRequest, "NO_FILE", "file field required")
		return
	}
	defer fh.Close()
	name := SafeName(hdr.Filename)
	kind := ""
	for ext, k := range TemplateKinds {
		if strings.HasSuffix(strings.ToLower(name), ext) {
			kind = k
		}
	}
	if kind == "" {
		writeErr(w, http.StatusUnsupportedMediaType, "UNSUPPORTED_FILE", "แบบฟอร์มต้องเป็นไฟล์ .docx .xlsx หรือ .pptx")
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
		writeErr(w, http.StatusConflict, "NO_TEMPLATES", "agent ตัวนี้ยังไม่รองรับแบบฟอร์มของคุณ")
		return
	}
	var body struct {
		FileID  uuid.UUID         `json:"file_id"`
		Slots   []TemplateSlot    `json:"slots"`
		Mapping map[string]string `json:"mapping"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil || body.FileID == uuid.Nil {
		writeErr(w, http.StatusBadRequest, "BAD_JSON", "file_id, slots and mapping required")
		return
	}
	f, err := a.store.GetFile(r.Context(), c.TenantUUID, c.UserID, body.FileID)
	if err != nil || f.InstallID != in.ID || f.Status == "removed" {
		writeErr(w, http.StatusNotFound, "NO_FILE", "ไม่พบไฟล์แบบฟอร์มนี้")
		return
	}
	kind := ""
	for ext, k := range TemplateKinds {
		if strings.HasSuffix(strings.ToLower(f.Name), ext) {
			kind = k
		}
	}
	if kind == "" {
		writeErr(w, http.StatusUnsupportedMediaType, "UNSUPPORTED_FILE", "แบบฟอร์มต้องเป็นไฟล์ .docx .xlsx หรือ .pptx")
		return
	}
	if msg := CheckTemplateMapping(body.Slots, body.Mapping, spec); msg != "" {
		writeErr(w, http.StatusBadRequest, "BAD_MAPPING", msg)
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
		Label: "แบบฟอร์มของคุณ", Note: fmt.Sprintf("%s รุ่นที่ %d", f.Name, version)}); err != nil {
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
		writeErr(w, http.StatusNotFound, "NO_TEMPLATE", "ยังไม่มีแบบฟอร์ม")
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
func CheckTemplateMapping(slots []TemplateSlot, mapping map[string]string, spec TemplateSpec) string {
	by := map[string]TemplateSlot{}
	for _, s := range slots {
		by[s.Key] = s
	}
	if len(mapping) == 0 {
		return "ยังไม่ได้เลือกค่าให้ช่องใดเลย"
	}
	for k, v := range mapping {
		base, col, sub := strings.Cut(k, ".")
		s, ok := by[k]
		if !ok && sub {
			s, ok = by[base]
		}
		if !ok {
			return "ไม่มีช่อง “" + k + "” ในแบบฟอร์ม"
		}
		if !s.Fillable {
			return "ช่อง “" + s.Label + "” เติมค่าไม่ได้"
		}
		isList := s.Kind == "list" || s.Kind == "table"
		if _, exact := by[k]; exact || !sub {
			if isList {
				if _, ok := spec.Lists[v]; !ok {
					return "ช่อง “" + s.Label + "” ต้องเป็นรายการ"
				}
			} else if _, ok := spec.Fields[v]; !ok {
				return "ไม่มีค่า “" + v + "” ใน agent ตัวนี้"
			}
			continue
		}
		// a list column: slot.column → list.column
		if !isList || !contains(s.Columns, col) {
			return "ไม่มีคอลัมน์ “" + col + "” ในช่อง “" + s.Label + "”"
		}
		list, lcol, _ := strings.Cut(v, ".")
		l, ok := spec.Lists[list]
		if !ok || mapping[base] != list {
			return "คอลัมน์ “" + col + "” ต้องมาจากรายการเดียวกับช่อง “" + s.Label + "”"
		}
		if _, ok := l.Columns[lcol]; !ok {
			return "ไม่มีคอลัมน์ “" + lcol + "” ในรายการ “" + l.Label + "”"
		}
	}
	return ""
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}
