package agentmarket

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// AM6 — what a manifest says of its scope and of the settings it declares is
// read when the manifest is, and a manifest the platform could not act on is
// refused there.

func manifestWith(t *testing.T, edit func(m map[string]any)) json.RawMessage {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(declaringManifest), &m); err != nil {
		t.Fatal(err)
	}
	edit(m)
	b, _ := json.Marshal(m)
	return b
}

func settingsOf(m map[string]any) []any {
	return m["install"].(map[string]any)["settings"].([]any)
}

func TestAManifestSaysItsScopeAndDeclaresItsSettings(t *testing.T) {
	m, err := ParseManifest(json.RawMessage(declaringManifest))
	if err != nil {
		t.Fatal(err)
	}
	if m.Scope != "generic" || m.ScopeSaid() != ScopeGeneric || m.Jurisdiction != "TH" || len(m.Install.Settings) != 7 {
		t.Fatalf("scope %q jurisdiction %q, %d settings", m.Scope, m.Jurisdiction, len(m.Install.Settings))
	}
	pct := m.Install.Settings[1]
	if pct.Key != "ap_price_tolerance_pct" || pct.Kind != "percent" || pct.Min == nil || *pct.Min != 0 || pct.Max == nil || *pct.Max != 50 ||
		pct.Default != "2.00" || !ReadsForPerson(pct.Label) {
		t.Fatalf("a declared percentage: %+v", pct)
	}
	if gr := m.Install.Settings[0]; gr.Kind != "choice" || len(gr.Choices) != 2 || gr.Choices[0].Value != "yes" || gr.Choices[0].Label != "ออกใบรับของ" {
		t.Fatalf("a declared choice: %+v", gr)
	}
	// an agent that does not say is not offered as generic
	plain, err := ParseManifest(json.RawMessage(testManifest))
	if err != nil || plain.Scope != "" || plain.ScopeSaid() != ScopeOrgSpecific || len(plain.Install.Settings) != 0 {
		t.Fatalf("a manifest that says nothing: %q %q %v", plain.Scope, plain.ScopeSaid(), err)
	}
	if m, err := ParseManifest(manifestWith(t, func(m map[string]any) { m["scope"] = "org-specific" })); err != nil || m.ScopeSaid() != ScopeOrgSpecific {
		t.Fatalf("org-specific: %v", err)
	}
	for name, c := range map[string]struct {
		edit func(m map[string]any)
		says string
	}{
		"a scope nobody knows":    {func(m map[string]any) { m["scope"] = "everyone" }, "scope"},
		"a kind nobody knows":     {func(m map[string]any) { settingsOf(m)[1].(map[string]any)["kind"] = "bool" }, "kind"},
		"no kind":                 {func(m map[string]any) { delete(settingsOf(m)[1].(map[string]any), "kind") }, "kind"},
		"a key with a capital":    {func(m map[string]any) { settingsOf(m)[1].(map[string]any)["key"] = "AP_price" }, "key"},
		"a key with a dot":        {func(m map[string]any) { settingsOf(m)[1].(map[string]any)["key"] = "answers.period_mixed" }, "key"},
		"a key of two letters":    {func(m map[string]any) { settingsOf(m)[1].(map[string]any)["key"] = "ap" }, "key"},
		"a key too long":          {func(m map[string]any) { settingsOf(m)[1].(map[string]any)["key"] = "a" + strings.Repeat("b", 41) }, "key"},
		"a key declared twice":    {func(m map[string]any) { settingsOf(m)[2].(map[string]any)["key"] = "ap_price_tolerance_pct" }, "twice"},
		"a choice without values": {func(m map[string]any) { settingsOf(m)[0].(map[string]any)["choices"] = []any{} }, "choice"},
		"a choice whose value is empty": {func(m map[string]any) {
			settingsOf(m)[0].(map[string]any)["choices"] = []any{map[string]any{"value": "", "label": "ออกใบรับของ"}}
		}, "choice"},
		"bounds the wrong way round": {func(m map[string]any) { settingsOf(m)[1].(map[string]any)["min"] = 60 }, "min"},
	} {
		if _, err := ParseManifest(manifestWith(t, c.edit)); err == nil || !strings.Contains(err.Error(), c.says) {
			t.Errorf("%s is refused when the manifest is read: %v", name, err)
		}
	}
}

// The runner hands the install's settings to the package as "defaults": a
// declared setting reaches it like any other, and what the manifest gives as
// defaults lies under what the install has set.
func TestTheManifestsDefaultsLieUnderTheInstallsOwnValues(t *testing.T) {
	m, err := ParseManifest(json.RawMessage(declaringManifest))
	if err != nil {
		t.Fatal(err)
	}
	rows := []Default{
		{Key: "ap_price_tolerance_pct", Value: json.RawMessage(`"5.00"`)}, // set by the person
		{Key: "ap_gr_practice", Value: json.RawMessage(`"no"`)},           // declared, without a default
		{Key: "company", Value: json.RawMessage(`"บริษัท ตัวอย่าง จำกัด"`)},
		{Key: "answers.currency_mixed", Value: json.RawMessage(`"amounts_in_base"`)},
	}
	got, remembered := EngineDefaults(m, rows)
	for k, want := range map[string]any{
		"ap_price_tolerance_pct": "5.00", // the install's own value, not the manifest's 2.00
		"ap_gr_practice":         "no",
		"ap_date_window_days":    "0", // the manifest's default: the install has no row for it
		"ap_fuzzy_threshold":     "0.80",
		"currency":               "THB",
		"company":                "บริษัท ตัวอย่าง จำกัด",
	} {
		if got[k] != want {
			t.Errorf("%s reaches the package as %v, want %v", k, got[k], want)
		}
	}
	if f, ok := got["formats"].([]any); !ok || len(f) != 1 || f[0] != "xlsx" {
		t.Errorf("formats: %v", got["formats"])
	}
	if string(remembered["currency_mixed"]) != `"amounts_in_base"` || got["answers"].(map[string]json.RawMessage)["currency_mixed"] == nil {
		t.Errorf("remembered answers are kept as they were: %v %v", remembered, got["answers"])
	}
	// what the install has is what DefaultsForEngine gave before, and nothing of it is changed
	before, _ := DefaultsForEngine(rows)
	for k, v := range before {
		if k != "answers" && got[k] != v {
			t.Errorf("%s: %v, was %v", k, got[k], v)
		}
	}
	// a default the platform would not keep as a setting is not handed over either
	odd, err := ParseManifest(manifestWith(t, func(m map[string]any) {
		m["install"].(map[string]any)["defaults"].(map[string]any)["something_else"] = "x"
	}))
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := EngineDefaults(odd, nil); got["something_else"] != nil || got["ap_fuzzy_threshold"] != "0.80" {
		t.Errorf("only settings: %v", got)
	}
}

// Every agent the product ships is read by the platform as its manifest says:
// its scope, and every setting it declares with a default the setting takes.
func TestEveryPackagesManifestIsReadWithItsSettings(t *testing.T) {
	files, _ := filepath.Glob(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", "*", "agent.json"))
	if len(files) < 5 {
		t.Fatalf("the packages were not found: %v", files)
	}
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		m, err := ParseManifest(raw)
		if err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		if m.ScopeSaid() != ScopeGeneric || m.Jurisdiction != "TH" {
			t.Errorf("%s: scope %q jurisdiction %q", f, m.ScopeSaid(), m.Jurisdiction)
		}
		for _, d := range m.Install.Settings {
			if !ReadsForPerson(d.Label) {
				t.Errorf("%s: %s is named %q", f, d.Key, d.Label)
			}
		}
		// what the manifest gives as defaults reaches the package as settings it takes
		got, _ := EngineDefaults(m, nil)
		for k := range m.Install.Defaults {
			if _, ok := got[k]; !ok && rulesOf(m).known(k) {
				t.Errorf("%s: the default of %s is not one the setting takes", f, k)
			}
		}
	}
}
