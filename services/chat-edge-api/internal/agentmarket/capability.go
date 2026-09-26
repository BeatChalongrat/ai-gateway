package agentmarket

import (
	"encoding/json"
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
	Summary       string `json:"summary"`
	PythonPackage string `json:"python_package"`
	TypicalRunSec int    `json:"typical_run_seconds"`
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
	Narrative     string `json:"narrative"`
	DocumentIndex *struct {
		RetentionDays int `json:"retention_days"`
	} `json:"document_index"`
	PhotoNotice string `json:"photo_notice"`
	// Templates (AM2 Phase E): the fields and lists the agent can put into a
	// customer's own template (template.go); absent means it fills none.
	Templates json.RawMessage `json:"templates,omitempty"`
}

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
		return res, fmt.Errorf("ติดตั้งไม่ได้: ระบบยังไม่มีความสามารถที่ agent นี้ต้องใช้ (%s)", strings.Join(res.Missing, ", "))
	}
	return res, nil
}
