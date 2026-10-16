package agentmarket

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

// ── Capability resolver ─────────────────────────────────────────────────
//
// A package asks for abstract capabilities ("deliverable.deck.pptx"); the
// runner publishes what it can bind them to (its heartbeat), and install
// resolves one against the other. The binding — and its version — is stored
// on the install and on every run, so an audit can say exactly which
// implementation produced a file.

// Manifest is the subset of agent.json the platform reads.
type Manifest struct {
	SchemaVersion string `json:"schema_version"`
	ID            string `json:"id"`
	Version       string `json:"version"`
	Name          string `json:"name"`
	NameEN        string `json:"name_en"`
	ShortName     string `json:"short_name"`
	Category      string `json:"category"`
	Icon          string `json:"icon"`
	// AM10. IconFile: the package's own icon (look.go); Accent: its colour per
	// theme. Icon stays the glyph name a client falls back to.
	IconFile      string  `json:"icon_file"`
	Accent        *Accent `json:"accent"`
	Summary       string  `json:"summary"`
	PythonPackage string  `json:"python_package"`
	// AM11. Languages: the languages the package itself speaks — what it asks,
	// the labels of its figures, the documents it makes ("th", "en"). None
	// said = Thai only. The screen and the platform's sentences around an
	// agent follow the person's language either way (Speaks).
	Languages     []string `json:"languages"`
	TypicalRunSec int      `json:"typical_run_seconds"`
	Greeting      struct {
		Text    string `json:"text"`
		Role    string `json:"role"`
		Tagline string `json:"tagline"`
	} `json:"greeting"`
	Inputs []struct {
		Role     string   `json:"role"`
		Label    string   `json:"label"`
		Note     string   `json:"note"`
		Required bool     `json:"required"`
		Accept   []string `json:"accept"`
	} `json:"inputs"`
	Outputs []struct {
		Format  string `json:"format"`
		Label   string `json:"label"`
		Default bool   `json:"default"`
	} `json:"outputs"`
	Capabilities struct {
		Required []string `json:"required"`
		Optional []string `json:"optional"`
	} `json:"capabilities"`
	Install struct {
		Defaults  map[string]json.RawMessage `json:"defaults"`
		Questions []string                   `json:"questions"`
		// Settings (AM6): the settings the agent declares — what an install of
		// it may set beside the platform's own, each with its kind and bounds
		// (settings.go).
		Settings []DeclaredSetting `json:"settings"`
	} `json:"install"`
	Permissions struct {
		Default string   `json:"default"`
		Denied  []string `json:"denied"`
	} `json:"permissions"`
	Cannot   string `json:"cannot"`
	SelfTest string `json:"self_test"`
	Limits   struct {
		MaxFileMB int `json:"max_file_mb"`
		MaxFiles  int `json:"max_files"`
	} `json:"limits"`
	// AM2. Narrative "rules": the package's own words, no model step (an
	// extraction agent has nothing for a model to write). DocumentIndex: the
	// runner keeps the install's duplicate index (migration 002) and hands it
	// to the package as history. PhotoNotice: shown where photos are accepted.
	Narrative string `json:"narrative"`
	// AM22 §1.1: what a commentary the model writes falls back to when no model it may use can be reached.
	// "rules": the package's rules write the words, the document says so on its face, and the run delivers —
	// an agent whose deliverable is its figures does not hold its working paper back for a commentary.
	// Empty (the financial report, whose commentary is the report): the run waits for its model (AM15).
	NarrativeWhenAway string `json:"narrative_when_away"`
	DocumentIndex     *struct {
		RetentionDays int `json:"retention_days"`
	} `json:"document_index"`
	PhotoNotice string `json:"photo_notice"`
	// Templates (AM2 Phase E): the fields and lists the agent can put into a
	// customer's own template (template.go); absent means it fills none.
	Templates json.RawMessage `json:"templates,omitempty"`
	// AM5. CompletionGate: the package's computed table says whether the
	// round's work passes (computed.json "gate": the bank reconciliation's
	// two adjusted balances are equal). A round whose gate does not pass is
	// delivered as a draft and is not a success. Review.SeparateReviewer: the
	// person who signs a run is not the person who prepared it.
	CompletionGate bool `json:"completion_gate"`
	// RecordWhoAnswered: the compute step is handed the name of whoever
	// answered each card (a confirmed pair names the person who confirmed it).
	RecordWhoAnswered bool `json:"record_who_answered"`
	Review            struct {
		SeparateReviewer bool `json:"separate_reviewer"`
	} `json:"review"`
	// AM6. Scope: "generic" — the agent holds nothing of one company, and any
	// organisation can install it as it is — or "org-specific". A manifest that
	// does not say reads as org-specific (ScopeSaid): an agent is not offered
	// as generic on nobody's word. Jurisdiction: whose law and practice its
	// rules follow ("TH").
	Scope        string `json:"scope"`
	Jurisdiction string `json:"jurisdiction"`
	// AM12. StartsFrom "request": a round of the agent starts from what a person
	// types, not from files they send. Web: the agent has the web searched and
	// pages captured as evidence for it (web.go) — the prompts a model is given
	// are the package's own files, the caps the most one round may use.
	StartsFrom string   `json:"starts_from"`
	Web        *WebSpec `json:"web"`
	// AM11b. Instructions: what a person can ask of the agent's documents in their own words, and what
	// they are known to ask that it cannot do (instructions.go). Absent: the agent makes its documents
	// one way.
	Instructions *InstructionSpec `json:"instructions,omitempty"`
}

// What a manifest says of its scope.
const (
	ScopeGeneric     = "generic"
	ScopeOrgSpecific = "org-specific"
)

// ScopeOf reads a scope as written: only "generic" is generic.
func ScopeOf(said string) string {
	if said == ScopeGeneric {
		return ScopeGeneric
	}
	return ScopeOrgSpecific
}

// ScopeSaid is the agent's scope, org-specific when its manifest does not say.
func (m Manifest) ScopeSaid() string { return ScopeOf(m.Scope) }

// defaultUploadTypes is what an agent that declares no input types accepts
// (AM1's spreadsheets and PDFs).
var defaultUploadTypes = []string{".xlsx", ".xlsm", ".csv", ".txt", ".pdf"}

// UploadTypes are the file extensions the agent's inputs accept.
func (m Manifest) UploadTypes() []string {
	seen := map[string]bool{}
	var out []string
	for _, in := range m.Inputs {
		for _, a := range in.Accept {
			a = strings.ToLower(strings.TrimSpace(a))
			if strings.HasPrefix(a, ".") && !seen[a] {
				seen[a] = true
				out = append(out, a)
			}
		}
	}
	if len(out) == 0 {
		return defaultUploadTypes
	}
	return out
}

// IndexRetention is how long the duplicate index keeps a document (0 = the
// package keeps none).
func (m Manifest) IndexRetention() time.Duration {
	if m.DocumentIndex == nil || m.DocumentIndex.RetentionDays <= 0 {
		return 0
	}
	return time.Duration(m.DocumentIndex.RetentionDays) * 24 * time.Hour
}

// ParseManifest validates the parts the platform depends on.
func ParseManifest(raw json.RawMessage) (Manifest, error) {
	var m Manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		return m, fmt.Errorf("manifest: %w", err)
	}
	if m.SchemaVersion != "agent.market.v1" {
		return m, fmt.Errorf("manifest: unsupported schema %q", m.SchemaVersion)
	}
	if m.ID == "" || m.Version == "" || m.Name == "" || m.PythonPackage == "" {
		return m, fmt.Errorf("manifest: id, version, name and python_package are required")
	}
	if m.Permissions.Default != "read_only" {
		return m, fmt.Errorf("manifest: AM1 installs read-only agents only (permissions.default=%q)", m.Permissions.Default)
	}
	for _, c := range m.Capabilities.Required {
		if !strings.Contains(c, ".") {
			return m, fmt.Errorf("manifest: capability %q is not namespaced", c)
		}
	}
	switch m.Scope {
	case "", ScopeGeneric, ScopeOrgSpecific:
	default:
		return m, fmt.Errorf("manifest: scope %q is neither %q nor %q", m.Scope, ScopeGeneric, ScopeOrgSpecific)
	}
	m.Jurisdiction = strings.TrimSpace(m.Jurisdiction)
	if err := CheckIconFile(m.IconFile); err != nil {
		return m, fmt.Errorf("manifest: %w", err)
	}
	if err := CheckAccent(m.Accent); err != nil {
		return m, fmt.Errorf("manifest: %w", err)
	}
	if err := checkDeclared(m.Install.Settings); err != nil {
		return m, fmt.Errorf("manifest: %w", err)
	}
	return m, nil
}

// DefaultFormats are the package's default output set.
func (m Manifest) DefaultFormats() []string {
	var out []string
	for _, o := range m.Outputs {
		if o.Default {
			out = append(out, o.Format)
		}
	}
	return out
}

// FormatAllowed reports whether the package can produce a format.
func (m Manifest) FormatAllowed(f string) bool {
	for _, o := range m.Outputs {
		if o.Format == f {
			return true
		}
	}
	return false
}

// Binding is one resolved capability.
type Binding struct {
	Capability string          `json:"capability"`
	Impl       string          `json:"impl"`
	Version    string          `json:"version"`
	Detail     json.RawMessage `json:"detail,omitempty"`
}

// Resolution is what install stores.
type Resolution struct {
	Version  int                `json:"version"`
	Bindings map[string]Binding `json:"bindings"`
	Missing  []string           `json:"missing,omitempty"`
	Optional []string           `json:"optional_missing,omitempty"`
}

// Resolve binds a manifest's capabilities against what the runner offers.
// A missing required capability refuses the install; a missing optional
// one is recorded (the agent works without it).
func Resolve(m Manifest, available map[string]Binding, version int) (Resolution, error) {
	res := Resolution{Version: version, Bindings: map[string]Binding{}}
	for _, c := range m.Capabilities.Required {
		if b, ok := available[c]; ok {
			res.Bindings[c] = b
		} else {
			res.Missing = append(res.Missing, c)
		}
	}
	for _, c := range m.Capabilities.Optional {
		if b, ok := available[c]; ok {
			res.Bindings[c] = b
		} else {
			res.Optional = append(res.Optional, c)
		}
	}
	sort.Strings(res.Missing)
	sort.Strings(res.Optional)
	if len(res.Missing) > 0 {
		return res, &MissingCapabilities{IDs: res.Missing}
	}
	return res, nil
}

// MissingCapabilities is what Resolve returns when the runner lacks
// something the agent requires. Error() is for the log; Names() is what a
// person reads — the parts named in words, never by their ids (AM5).
type MissingCapabilities struct{ IDs []string }

func (m *MissingCapabilities) Error() string {
	return "missing capabilities: " + strings.Join(m.IDs, ", ")
}

// capabilityNames names a capability for a person (written in Thai like every
// sentence of the source; each one's English is in the catalogue).
var capabilityNames = map[string]wording{
	"spreadsheet.read.xlsx":   "การอ่านไฟล์ Excel",
	"document.extract.e2":     "การอ่านใบกำกับภาษีและใบเสร็จ",
	"document.match.e3":       "การจับคู่รายการของสองตาราง",
	"compute.python.decimal":  "ส่วนคำนวณ",
	"model.text":              "โมเดลที่เขียนคำบรรยาย",
	"model.vision":            "โมเดลที่อ่านสแกนและรูปถ่าย",
	"deliverable.report.docx": "การสร้างรายงาน Word",
	"deliverable.report.pdf":  "การแปลงรายงานเป็น PDF",
	"deliverable.deck.pptx":   "การสร้างสไลด์ PowerPoint",
	"deliverable.table.xlsx":  "การสร้างตาราง Excel",
	"deliverable.template":    "การกรอกแบบฟอร์มของบริษัท",
	"web.search":              "การค้นหาเว็บ",
	"web.fetch":               "การเก็บหน้าเว็บเป็นหลักฐาน",
}

// Names lists the missing parts in words, in l.
func (m *MissingCapabilities) Names(l Lang) string {
	var out []string
	seen := map[wording]bool{}
	for _, id := range m.IDs {
		name, ok := capabilityNames[id]
		if !ok {
			name = "ส่วนประกอบหนึ่งของระบบ"
		}
		if !seen[name] {
			seen[name] = true
			out = append(out, T(l, name))
		}
	}
	return strings.Join(out, ", ")
}

// missingWords is what a capability error says to a person, in l.
func missingWords(l Lang, err error) string {
	var m *MissingCapabilities
	if errors.As(err, &m) {
		return m.Names(l)
	}
	return T(l, "ส่วนประกอบหนึ่งของระบบ")
}

// Speaks: the package writes its own words — and its documents — in l.
// A package that names no language speaks Thai.
func (m Manifest) Speaks(l Lang) bool {
	if len(m.Languages) == 0 {
		return l == Thai
	}
	for _, x := range m.Languages {
		if strings.EqualFold(strings.TrimSpace(x), string(l)) {
			return true
		}
	}
	return false
}

// NameIn is the agent's name on a screen in l: the English name its package
// gives (manifest name_en) on an English screen, its name otherwise. A
// package with no English name keeps the one it has, in both languages.
func (m Manifest) NameIn(l Lang, name string) string {
	if en := strings.TrimSpace(m.NameEN); l == English && en != "" {
		return en
	}
	return name
}
