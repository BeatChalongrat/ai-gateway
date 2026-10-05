package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
)

// ── Which files a round makes (AM10 E) ───────────────────────────────────
//
// When an agent is about to produce its deliverables and more than one
// sensible shape exists, it asks — the same question card as every other
// clarification (clarify.go), at the postcompute moment, cosmetic: the round
// runs on with the install's set, and an answer re-renders. When the install
// already says what to produce (the wizard's formats step, the settings
// page, an earlier answer), nothing is asked. The answer is remembered as the
// install's default like every other rememberable answer, and the install's
// "formats" setting follows it, so the next round makes that set from the
// start.

// OutputSetCode is the finding's code (and the remembered default "answers.output_set").
const OutputSetCode = "output_set"

var outputWords = map[string]string{"docx": "รายงาน Word", "pptx": "สไลด์", "xlsx": "ตาราง Excel"}

// OutputShape is one sensible set of files.
type OutputShape struct {
	Formats []string
	Label   string
}

// OutputShapes are the sensible shapes an agent's outputs make, at most
// four: the default set, each file alone, everything — only when there is
// more than one. PDF is left to the "more files" card: it needs a converter.
func OutputShapes(m Manifest) []OutputShape {
	var all []string
	for _, o := range m.Outputs {
		if o.Format != "pdf" && !contains(all, o.Format) {
			all = append(all, o.Format)
		}
	}
	if len(all) < 2 {
		return nil
	}
	def := []string{}
	for _, f := range m.DefaultFormats() {
		if f != "pdf" {
			def = append(def, f)
		}
	}
	if len(def) == 0 {
		def = all
	}
	seen := map[string]bool{}
	var out []OutputShape
	add := func(fs []string, label string) {
		k := strings.Join(fs, "+")
		if seen[k] || len(fs) == 0 || len(out) == 4 {
			return
		}
		seen[k] = true
		out = append(out, OutputShape{Formats: fs, Label: label})
	}
	add(def, shapeLabel(def)+" (ชุดมาตรฐาน)")
	add(all, shapeLabel(all))
	for _, f := range all {
		add([]string{f}, outputWords[f]+" อย่างเดียว")
	}
	return out
}

func shapeLabel(fs []string) string {
	var words []string
	for _, f := range fs {
		if w, ok := outputWords[f]; ok {
			words = append(words, w)
		} else {
			words = append(words, "."+f)
		}
	}
	return strings.Join(words, " + ")
}

// OutputSetFinding is the question, or nil when there is nothing to ask:
// one shape only, the install already says (its formats default was set by
// a person, not copied from the manifest), or the answer is remembered.
func OutputSetFinding(m Manifest, defaults []Default, current []string) *Finding {
	shapes := OutputShapes(m)
	if len(shapes) < 2 {
		return nil
	}
	for _, d := range defaults {
		if d.Key == "formats" && d.Source != "install" {
			return nil
		}
		if d.Key == "answers."+OutputSetCode {
			return nil
		}
	}
	var opts []Option
	var def json.RawMessage
	for _, s := range shapes {
		v, _ := json.Marshal(s.Formats)
		opts = append(opts, Option{Label: s.Label, Value: v})
		if sameSet(s.Formats, current) {
			def = v
		}
	}
	if def == nil {
		def = opts[0].Value
	}
	return &Finding{Key: OutputSetCode, Code: OutputSetCode, Severity: "cosmetic", Moment: "postcompute", Header: "ชุดไฟล์",
		Question: "รอบนี้ต้องการไฟล์แบบไหนครับ ถ้าไม่ตอบจะทำชุดมาตรฐานให้ก่อน และจำคำตอบไว้ใช้รอบหน้า",
		Options:  opts, Default: def, Rememberable: true, Kind: "question"}
}

func sameSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for _, x := range a {
		if !contains(b, x) {
			return false
		}
	}
	return true
}

// OutputSetOf reads the answer's formats (nil when there is none).
func OutputSetOf(answers map[string]Answer) []string {
	a, ok := answers[OutputSetCode]
	if !ok {
		return nil
	}
	var fs []string
	if json.Unmarshal(a.Value, &fs) != nil || len(fs) == 0 {
		return nil
	}
	return fs
}

// ApplyOutputSet makes an answered output set the run's and the install's:
// the run makes it (SetFormats), and the install's "formats" setting says it
// from now on, so nothing is asked again.
func (s *Store) ApplyOutputSet(ctx context.Context, q Querier, run Run, answers map[string]Answer) error {
	fs := OutputSetOf(answers)
	if fs == nil {
		return nil
	}
	if err := s.SetFormats(ctx, q, run.ID, fs); err != nil {
		return err
	}
	a := answers[OutputSetCode]
	if a.Via != "card" && a.Via != "text" && a.Via != "typed" {
		return nil // a default or a remembered answer changes no setting
	}
	v, _ := json.Marshal(fs)
	rid := run.ID
	return s.SetDefault(ctx, q, run.InstallID, Default{Key: "formats", Value: v, Source: "answer", Label: "ชุดไฟล์ที่ต้องการ",
		Note: "ตอบไว้ในรอบ " + PeriodLabel(periodOf(run)), FromRunID: &rid})
}
