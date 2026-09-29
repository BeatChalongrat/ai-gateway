package agentmarket

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// ── Settings an agent declares (AM6) ─────────────────────────────────────
//
// Nothing about a company lives in code. Until AM6 every setting an install
// keeps was a constant of api.go (validSettingKey, matchSettings,
// accountSettings, settingLabel): a new agent with a tolerance of its own
// meant a change here. An agent now declares its settings in its manifest
// (install.settings) — the key, the label a person reads, the kind, the
// bounds, the default — and the platform validates, labels and lists them
// from there, for installs of that agent only.
//
// The platform's own keys stay what they were, for the agents that use them.
// A key an agent declares is checked by what the agent declares.

// Kinds of a declared setting.
const (
	SettingKindMoney   = "money"
	SettingKindPercent = "percent"
	SettingKindInt     = "int"
	SettingKindRatio   = "ratio"
	SettingKindChoice  = "choice"
	SettingKindText    = "text"
)

// DefaultSettingLength is how long a declared text may be when its manifest
// does not say.
const DefaultSettingLength = 2000

// SettingChoice is one value a declared choice may take.
type SettingChoice struct {
	Value string `json:"value"`
	Label string `json:"label"`
}

// DeclaredSetting is one setting of an agent's manifest (install.settings).
type DeclaredSetting struct {
	Key       string          `json:"key"`
	Label     string          `json:"label"`
	Kind      string          `json:"kind"`
	Min       *float64        `json:"min"`
	Max       *float64        `json:"max"`
	Default   string          `json:"default"`
	Help      string          `json:"help"`
	Choices   []SettingChoice `json:"choices"`
	MaxLength int             `json:"max_length"`
}

var declaredKey = regexp.MustCompile(`^[a-z][a-z0-9_]{2,40}$`)

// checkDeclared refuses what the platform could not act on: a key it could
// not keep, a kind it does not know, a choice with nothing to choose.
func checkDeclared(list []DeclaredSetting) error {
	seen := map[string]bool{}
	for _, d := range list {
		if !declaredKey.MatchString(d.Key) {
			return fmt.Errorf("setting key %q is not a lower-case name of 3 to 41 letters, digits and underscores", d.Key)
		}
		if seen[d.Key] {
			return fmt.Errorf("setting %q is declared twice", d.Key)
		}
		seen[d.Key] = true
		if strings.TrimSpace(d.Label) == "" {
			return fmt.Errorf("setting %q has no label", d.Key)
		}
		switch d.Kind {
		case SettingKindMoney, SettingKindPercent, SettingKindInt, SettingKindRatio:
			if d.Min != nil && d.Max != nil && *d.Min > *d.Max {
				return fmt.Errorf("setting %q: min %v is above max %v", d.Key, *d.Min, *d.Max)
			}
		case SettingKindChoice:
			if len(d.Choices) == 0 {
				return fmt.Errorf("setting %q is a choice without values", d.Key)
			}
			values := map[string]bool{}
			for _, c := range d.Choices {
				if strings.TrimSpace(c.Value) == "" || values[c.Value] {
					return fmt.Errorf("setting %q: every value of a choice is named, once", d.Key)
				}
				values[c.Value] = true
			}
		case SettingKindText:
			if d.MaxLength < 0 {
				return fmt.Errorf("setting %q: max_length %d", d.Key, d.MaxLength)
			}
		default:
			return fmt.Errorf("setting %q has kind %q (one of money, percent, int, ratio, choice, text)", d.Key, d.Kind)
		}
		if d.Default != "" {
			if _, problem := d.Check(mustJSON(d.Default)); !problem.Empty() {
				return fmt.Errorf("setting %q: its default %q is not a value it takes", d.Key, d.Default)
			}
		}
	}
	return nil
}

// length is how many characters a declared text may have.
func (d DeclaredSetting) length() int {
	if d.MaxLength > 0 {
		return d.MaxLength
	}
	return DefaultSettingLength
}

// named is the setting as a sentence names it.
func (d DeclaredSetting) named() Fact { return Theirs(d.Label) }

var writtenNumber = regexp.MustCompile(`^-?\d+(?:\.\d+)?$`)

// writtenAs reads a number as a person writes one ("1,250.5", 12, "7 %"): its
// value, its whole part and its decimals as written.
func writtenAs(raw json.RawMessage, percent bool) (v float64, whole, decimals string, ok bool) {
	var s string
	if json.Unmarshal(raw, &s) != nil {
		var f float64
		if len(raw) == 0 || raw[0] == '"' || json.Unmarshal(raw, &f) != nil || math.IsNaN(f) || math.IsInf(f, 0) {
			return 0, "", "", false
		}
		s = strconv.FormatFloat(f, 'f', -1, 64)
	}
	s = strings.ReplaceAll(strings.TrimSpace(s), ",", "")
	if percent {
		s = strings.TrimSpace(strings.TrimSuffix(s, "%"))
	}
	if !writtenNumber.MatchString(s) || len(s) > 40 {
		return 0, "", "", false
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, "", "", false
	}
	whole, decimals, _ = strings.Cut(s, ".")
	sign := ""
	if strings.HasPrefix(whole, "-") {
		sign, whole = "-", whole[1:]
	}
	if whole = strings.TrimLeft(whole, "0"); whole == "" {
		whole = "0"
	}
	return v, sign + whole, decimals, true
}

// allowed says what a declared number may be.
func (d DeclaredSetting) allowed() PersonText {
	what := Text("ตัวเลข")
	if d.Kind == SettingKindInt {
		what = Text("จำนวนเต็ม")
	}
	switch {
	case d.Min != nil && d.Max != nil:
		what = Text("%sตั้งแต่ %s ถึง %s", Words(what), Number(*d.Min), Number(*d.Max))
	case d.Min != nil:
		what = Text("%sตั้งแต่ %s ขึ้นไป", Words(what), Number(*d.Min))
	case d.Max != nil:
		what = Text("%sไม่เกิน %s", Words(what), Number(*d.Max))
	}
	if d.Kind == SettingKindMoney || d.Kind == SettingKindPercent {
		what = Text("%s ทศนิยมไม่เกิน 2 ตำแหน่ง", Words(what))
	}
	return Text("%s ต้องเป็น%s", d.named(), Words(what))
}

// Check reads a value given for the setting: the value as it is kept, or what
// is wrong with it, in a sentence that names the setting and what it takes.
// An amount and a percentage are kept with two decimals, a count as a whole
// number, each as text — the way the manifest writes its defaults.
func (d DeclaredSetting) Check(raw json.RawMessage) (json.RawMessage, PersonText) {
	switch d.Kind {
	case SettingKindMoney, SettingKindPercent, SettingKindInt, SettingKindRatio:
		v, whole, decimals, ok := writtenAs(raw, d.Kind == SettingKindPercent)
		if !ok || (d.Min != nil && v < *d.Min) || (d.Max != nil && v > *d.Max) {
			return nil, d.allowed()
		}
		kept := whole
		switch d.Kind {
		case SettingKindInt:
			if strings.Trim(decimals, "0") != "" {
				return nil, d.allowed()
			}
		case SettingKindRatio:
			if decimals != "" {
				kept += "." + decimals
			}
		default:
			if decimals = strings.TrimRight(decimals, "0"); len(decimals) > 2 {
				return nil, d.allowed()
			}
			kept += "." + (decimals + "00")[:2]
		}
		return mustJSON(kept), NoText
	case SettingKindChoice:
		var s string
		if json.Unmarshal(raw, &s) == nil {
			for _, c := range d.Choices {
				if c.Value == s {
					return mustJSON(s), NoText
				}
			}
		}
		labels := make([]PersonText, 0, len(d.Choices))
		for _, c := range d.Choices {
			labels = append(labels, LabelFromPackage(c.Label, Text("%s", Theirs(c.Value))))
		}
		return nil, Text("%s ต้องเป็นหนึ่งในตัวเลือกนี้: %s", d.named(), Words(Join(", ", labels...)))
	case SettingKindText:
		var s string
		if json.Unmarshal(raw, &s) != nil || len([]rune(s)) > d.length() {
			return nil, Text("%s ต้องเป็นข้อความยาวไม่เกิน %s ตัวอักษร", d.named(), Count(d.length()))
		}
		return mustJSON(s), NoText
	}
	return nil, Text("%s ยังตั้งค่าไม่ได้", d.named())
}

// settingRules is what an install of one agent may set: the platform's own
// settings, and the ones the agent's manifest declares.
type settingRules struct {
	declared map[string]DeclaredSetting
	order    []DeclaredSetting
}

func rulesOf(m Manifest) settingRules {
	r := settingRules{declared: map[string]DeclaredSetting{}, order: m.Install.Settings}
	for _, d := range m.Install.Settings {
		r.declared[d.Key] = d
	}
	return r
}

// known: the key is a setting of this agent's installs.
func (r settingRules) known(key string) bool {
	if _, ok := r.declared[key]; ok {
		return true
	}
	return validSettingKey(key)
}

// label is the setting's name as a person reads it.
func (r settingRules) label(key string) string {
	if d, ok := r.declared[key]; ok {
		return LabelFromPackage(d.Label, Text("ค่าที่ตั้งไว้ของ agent")).String()
	}
	return settingLabel(key)
}

// check reads a value for a key that is known: the value as it is kept, or
// the code and the sentence of the refusal (BAD_SETTING for a setting the
// agent declares, BAD_VALUE for one of the platform's).
func (r settingRules) check(key string, raw json.RawMessage) (kept json.RawMessage, code, problem string) {
	if d, ok := r.declared[key]; ok {
		kept, why := d.Check(raw)
		if !why.Empty() {
			return nil, "BAD_SETTING", why.String()
		}
		return kept, "", ""
	}
	if !validSettingValue(key, raw) {
		return nil, "BAD_VALUE", settingProblem(key, raw)
	}
	return raw, "", ""
}

// DeclaredValue is a declared setting with what the install has set for it.
type DeclaredValue struct {
	Key       string          `json:"key"`
	Label     string          `json:"label"`
	Kind      string          `json:"kind"`
	Min       *float64        `json:"min"`
	Max       *float64        `json:"max"`
	Default   string          `json:"default"`
	Help      string          `json:"help"`
	Choices   []SettingChoice `json:"choices"`
	MaxLength *int            `json:"max_length"`
	Value     json.RawMessage `json:"value"`
	Set       bool            `json:"set"`
}

// values lists the declared settings, in the manifest's order, with the
// install's own values. What a person reads of them is the manifest's words
// as a person is shown them.
func (r settingRules) values(rows []Default) []DeclaredValue {
	set := map[string]json.RawMessage{}
	for _, d := range rows {
		set[d.Key] = d.Value
	}
	out := make([]DeclaredValue, 0, len(r.order))
	for _, d := range r.order {
		v := DeclaredValue{Key: d.Key, Label: r.label(d.Key), Kind: d.Kind, Min: d.Min, Max: d.Max, Default: d.Default,
			Help: FromPackage(d.Help, NoText).String(), Value: json.RawMessage("null")}
		for _, c := range d.Choices {
			v.Choices = append(v.Choices, SettingChoice{Value: c.Value, Label: LabelFromPackage(c.Label, Text("%s", Theirs(c.Value))).String()})
		}
		if d.Kind == SettingKindText {
			n := d.length()
			v.MaxLength = &n
		}
		if raw, ok := set[d.Key]; ok && len(raw) > 0 {
			v.Value, v.Set = raw, true
		}
		out = append(out, v)
	}
	return out
}

// EngineDefaults is what a round hands its package as "defaults": the
// install's own settings and remembered answers (DefaultsForEngine), with the
// manifest's defaults laid under them — a setting the install has not set (a
// version that added it, a value the person un-set) reaches the package as
// the manifest gives it. Only what the install could have kept is laid under.
func EngineDefaults(m Manifest, defs []Default) (map[string]any, map[string]json.RawMessage) {
	out, remembered := DefaultsForEngine(defs)
	rules := rulesOf(m)
	for k, raw := range m.Install.Defaults {
		if _, set := out[k]; set || !rules.known(k) {
			continue
		}
		kept, _, problem := rules.check(k, raw)
		if problem != "" {
			continue
		}
		var v any
		if json.Unmarshal(kept, &v) == nil {
			out[k] = v
		}
	}
	return out, remembered
}

// rulesFor reads the settings of an install's own agent, at the version it is pinned to.
func (a *API) rulesFor(ctx context.Context, in Install) (settingRules, error) {
	def, err := a.store.DefinitionByID(ctx, in.DefinitionID)
	if err != nil {
		return settingRules{}, err
	}
	m, err := ParseManifest(def.Manifest)
	if err != nil {
		return settingRules{}, err
	}
	return rulesOf(m), nil
}
