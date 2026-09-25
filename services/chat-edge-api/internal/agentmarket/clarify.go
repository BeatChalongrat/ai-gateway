package agentmarket

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"unicode"
)

// ── The clarification engine ────────────────────────────────────────────
//
// Rule zero: the model never decides what to ask. A package's checks/
// return findings; this engine turns findings into cards. It is a pure
// function of (findings, answers so far, keys already asked, remembered
// answers) so its behaviour is testable and repeatable:
//
//   - collect every finding of a moment first, then ask once (one card);
//   - a card holds at most MaxQuestionsPerCard questions of 2-4 options;
//   - blocking before cosmetic; a blocking finding is never silently
//     defaulted — if more than fit, the rest go on the next card, which is
//     created once the current one is answered;
//   - cosmetic findings that do not fit take their default now and become
//     assumptions; a cosmetic-only card does not stop the run;
//   - a key already asked in this run is never asked again;
//   - a remembered (install default) answer is applied silently and
//     written down as an assumption.

// MaxQuestionsPerCard is the shape the ask-question UI renders.
const MaxQuestionsPerCard = 4

// MaxReprompts is how many times an uninterpretable answer is re-asked
// before the default is used and recorded as an assumption.
const MaxReprompts = 2

// Finding is what a package check returns (engine/findings.py).
type Finding struct {
	Key          string          `json:"key"`
	Code         string          `json:"code"`
	Severity     string          `json:"severity"`
	Moment       string          `json:"moment"`
	Header       string          `json:"header"`
	Question     string          `json:"question"`
	Options      []Option        `json:"options"`
	Default      json.RawMessage `json:"default"`
	Evidence     json.RawMessage `json:"evidence"`
	Rememberable bool            `json:"rememberable"`
	Kind         string          `json:"kind"`
	Subject      string          `json:"subject"`
	Request      json.RawMessage `json:"request"`
}

// Blocking reports whether the answer changes a number or which rows count.
func (f Finding) Blocking() bool { return f.Severity != "cosmetic" }

// Validate refuses a malformed finding before it can reach a user.
func (f Finding) Validate() error {
	if f.Key == "" || f.Code == "" {
		return fmt.Errorf("finding without key/code")
	}
	if f.Severity != "blocking" && f.Severity != "cosmetic" {
		return fmt.Errorf("%s: severity %q", f.Key, f.Severity)
	}
	if DisplayLen(f.Header) > 12 {
		return fmt.Errorf("%s: header longer than 12 characters", f.Key)
	}
	if f.Kind == "request_files" {
		return nil
	}
	if len(f.Options) < 2 || len(f.Options) > 4 {
		return fmt.Errorf("%s: %d options (2-4 required)", f.Key, len(f.Options))
	}
	found := false
	for _, o := range f.Options {
		if JSONEqual(o.Value, f.Default) {
			found = true
		}
	}
	if !found {
		return fmt.Errorf("%s: default is not an option", f.Key)
	}
	low := strings.ToLower(f.Question)
	for _, w := range []string{"password", "รหัสผ่าน", "token", "โทเคน", "otp", "เลขบัตรประชาชน"} {
		if strings.Contains(low, w) {
			return fmt.Errorf("%s: a card may never ask for a secret", f.Key)
		}
	}
	return nil
}

// DisplayLen counts columns a reader sees: Thai combining marks take none.
func DisplayLen(s string) int {
	n := 0
	for _, r := range s {
		if unicode.Is(unicode.Mn, r) || unicode.Is(unicode.Me, r) {
			continue
		}
		n++
	}
	return n
}

// JSONEqual compares two JSON values semantically (key order ignored).
func JSONEqual(a, b json.RawMessage) bool {
	if bytes.Equal(bytes.TrimSpace(a), bytes.TrimSpace(b)) {
		return true
	}
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	xb, _ := json.Marshal(x)
	yb, _ := json.Marshal(y)
	return bytes.Equal(xb, yb)
}

// PlanInput is everything the engine needs for one check pass.
type PlanInput struct {
	Moment     string
	Findings   []Finding
	Answers    map[string]Answer          // resolved on this run
	Asked      map[string]bool            // keys that appeared on any card of this run
	OpenKeys   map[string]bool            // keys on a card that is still open
	Remembered map[string]json.RawMessage // install defaults by finding code
	MaxPerCard int
}

// CardPlan is one card the engine wants created now.
type CardPlan struct {
	Kind      string     `json:"kind"` // questions | request_files
	Blocking  bool       `json:"blocking"`
	Questions []Question `json:"questions"`
}

// PlanResult is what the runner does next.
type PlanResult struct {
	Card        *CardPlan
	AutoAnswers map[string]Answer
	Assumptions []Assumption
	// Park: the run must wait for a person (a blocking card is open or new).
	Park bool
	// Waiting lists the keys the run is waiting on (for the progress card).
	Waiting []string
}

// Plan runs the engine for one moment.
func Plan(in PlanInput) (PlanResult, error) {
	max := in.MaxPerCard
	if max <= 0 {
		max = MaxQuestionsPerCard
	}
	res := PlanResult{AutoAnswers: map[string]Answer{}}
	var pendingQ, pendingFiles []Finding
	for _, f := range in.Findings {
		if err := f.Validate(); err != nil {
			return res, err
		}
		if _, done := in.Answers[f.Key]; done {
			continue
		}
		if v, ok := in.Remembered[f.Code]; ok && f.Rememberable && f.Kind != "request_files" && validOption(f, v) {
			res.AutoAnswers[f.Key] = Answer{Value: v, Label: labelFor(f, v), Moment: in.Moment, Via: "remembered",
				Cosmetic: !f.Blocking(), Code: f.Code}
			res.Assumptions = append(res.Assumptions, Assumption{
				Code: f.Code, Source: "remembered",
				Text: fmt.Sprintf("%s: ใช้ค่าที่จำไว้ “%s”", f.Header, labelFor(f, v)), ChangesNumbers: f.Blocking(),
			})
			continue
		}
		if f.Kind == "request_files" {
			pendingFiles = append(pendingFiles, f)
		} else {
			pendingQ = append(pendingQ, f)
		}
	}

	// Anything still open keeps the run where it is.
	for _, f := range append(append([]Finding{}, pendingFiles...), pendingQ...) {
		if in.OpenKeys[f.Key] {
			res.Waiting = append(res.Waiting, f.Key)
			if f.Blocking() {
				res.Park = true
			}
		}
	}
	if res.Park {
		return res, nil
	}

	// Missing files come first and on their own card: nothing else can be
	// decided about data that has not arrived.
	var newFiles []Finding
	for _, f := range pendingFiles {
		if !in.Asked[f.Key] {
			newFiles = append(newFiles, f)
		}
	}
	if len(newFiles) > 0 {
		card := &CardPlan{Kind: "request_files", Blocking: true}
		for _, f := range newFiles {
			card.Questions = append(card.Questions, questionOf(f))
		}
		res.Card = card
		res.Park = true
		return res, nil
	}

	var fresh []Finding
	for _, f := range pendingQ {
		if in.Asked[f.Key] || in.OpenKeys[f.Key] {
			continue // never ask the same key twice in one run
		}
		fresh = append(fresh, f)
	}
	sort.SliceStable(fresh, func(i, j int) bool { return fresh[i].Blocking() && !fresh[j].Blocking() })
	if len(fresh) == 0 {
		return res, nil
	}
	onCard := fresh
	if len(fresh) > max {
		onCard = fresh[:max]
		for _, f := range fresh[max:] {
			if f.Blocking() {
				continue // asked on the next card, once this one is answered
			}
			res.AutoAnswers[f.Key] = Answer{Value: f.Default, Label: labelFor(f, f.Default), Moment: in.Moment, Via: "default",
				Cosmetic: true, Code: f.Code}
			res.Assumptions = append(res.Assumptions, Assumption{
				Code: f.Code, Source: "default",
				Text: fmt.Sprintf("%s: ใช้ค่าตั้งต้น “%s” (ไม่ได้ถาม เพื่อไม่ให้คำถามยาวเกินไป)", f.Header, labelFor(f, f.Default)),
			})
		}
	}
	card := &CardPlan{Kind: "questions"}
	for _, f := range onCard {
		card.Questions = append(card.Questions, questionOf(f))
		if f.Blocking() {
			card.Blocking = true
		} else {
			// A cosmetic question does not stop the run: it continues on the
			// default now; answering later re-renders only the files.
			res.AutoAnswers[f.Key] = Answer{Value: f.Default, Label: labelFor(f, f.Default), Moment: in.Moment, Via: "default",
				Cosmetic: true, Code: f.Code}
			res.Assumptions = append(res.Assumptions, Assumption{
				Code: f.Code, Source: "default",
				Text: fmt.Sprintf("%s: ใช้ค่าตั้งต้น “%s” ไปก่อน ตอบภายหลังได้", f.Header, labelFor(f, f.Default)),
			})
		}
	}
	res.Card = card
	res.Park = card.Blocking
	return res, nil
}

func questionOf(f Finding) Question {
	return Question{
		Key: f.Key, Code: f.Code, Severity: f.Severity, Header: f.Header, Question: f.Question,
		Options: f.Options, Default: f.Default, DefaultLabel: labelFor(f, f.Default),
		Evidence: f.Evidence, Rememberable: f.Rememberable, Request: f.Request,
	}
}

func validOption(f Finding, v json.RawMessage) bool {
	for _, o := range f.Options {
		if JSONEqual(o.Value, v) {
			return true
		}
	}
	return false
}

func labelFor(f Finding, v json.RawMessage) string {
	for _, o := range f.Options {
		if JSONEqual(o.Value, v) {
			return o.Label
		}
	}
	return strings.Trim(string(v), `"`)
}

// ── answering ────────────────────────────────────────────────────────────

// Submitted is one question's answer as the UI sends it: option labels
// (clicked) or free text ("อื่นๆ", or a sentence typed in the composer).
type Submitted struct {
	Labels []string `json:"labels,omitempty"`
	Text   string   `json:"text,omitempty"`
	// Skipped: the person pressed "skip" and the card sent the recommended
	// option. Recorded as a skip, written as an assumption, never remembered.
	Skipped bool `json:"skipped,omitempty"`
}

// ResolveSubmission maps clicked labels to option values. Free text is
// returned separately: the runner interprets it (keywords first, then a
// constrained model call), never the control plane.
func ResolveSubmission(questions []Question, sub map[string]Submitted, moment, cardID string) (map[string]Answer, map[string]string, error) {
	answers := map[string]Answer{}
	texts := map[string]string{}
	for _, q := range questions {
		s, ok := sub[q.Key]
		if !ok {
			continue
		}
		if strings.TrimSpace(s.Text) != "" && len(s.Labels) == 0 {
			texts[q.Key] = strings.TrimSpace(s.Text)
			continue
		}
		if len(s.Labels) == 0 {
			continue
		}
		var values []json.RawMessage
		var labels []string
		for _, l := range s.Labels {
			var hit *Option
			for i := range q.Options {
				if q.Options[i].Label == l {
					hit = &q.Options[i]
				}
			}
			if hit == nil {
				// A label that is not an option is the user's own words.
				texts[q.Key] = strings.TrimSpace(strings.Join(s.Labels, " "))
				values = nil
				break
			}
			values = append(values, hit.Value)
			labels = append(labels, hit.Label)
		}
		if values == nil {
			continue
		}
		var v json.RawMessage
		if q.MultiSelect {
			b, _ := json.Marshal(values)
			v = b
		} else {
			if len(values) != 1 {
				return nil, nil, fmt.Errorf("%s: one answer expected", q.Key)
			}
			v = values[0]
		}
		via := "card"
		if s.Skipped {
			via = "skip"
		}
		answers[q.Key] = Answer{Value: v, Label: strings.Join(labels, ", "), Moment: moment, CardID: cardID, Via: via,
			Cosmetic: q.Severity == "cosmetic", Code: q.Code}
	}
	return answers, texts, nil
}

// InterpretText maps a free-text answer to an option by keywords: an
// option whose label (or its first words) appears in the text, or whose
// distinctive words the text contains. Returns ok=false when nothing, or
// more than one option, matches — the caller then asks the model or re-asks.
func InterpretText(q Question, text string) (Option, bool) {
	t := normalizeThai(text)
	var hits []Option
	for _, o := range q.Options {
		l := normalizeThai(o.Label)
		if l != "" && strings.Contains(t, l) {
			hits = append(hits, o)
			continue
		}
		// First two words of the label ("ตัดออก", "นับรวมด้วย", "ใช่ D คือเดบิต").
		words := strings.Fields(o.Label)
		if len(words) > 0 {
			head := normalizeThai(words[0])
			if len([]rune(head)) >= 4 && strings.Contains(t, head) && !negated(t, head) {
				hits = append(hits, o)
			}
		}
	}
	if len(hits) == 1 {
		return hits[0], true
	}
	return Option{}, false
}

func negated(text, word string) bool {
	i := strings.Index(text, word)
	if i < 0 {
		return false
	}
	prefix := text[:i]
	return strings.HasSuffix(prefix, "ไม่") || strings.HasSuffix(prefix, "ไม่ต้อง")
}

func normalizeThai(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	return strings.Join(strings.Fields(s), "")
}
