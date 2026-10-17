package runner

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM22 §1.2 — the validator measures what the prompt promised. Until this card checkNarrative measured
// nothing and dropped a seventh point silently; the prompt promised the catalogue's limits and nothing
// checked them. CARD-RULES §6c: the refusal case is the test.

func am22Limits() computedView {
	return computedView{Figures: map[string]figureView{"revenue": {Text: "12,480,300", Cite: "c61", Label: "รายได้"}},
		WritingLimits: map[string]int{"summary_chars": 60, "bullet_chars": 30, "bullets_max": 4, "items_max": 20}}
}

func am22Answer(items []string, summary string) string {
	b, _ := json.Marshal(map[string]any{"summary": summary, "movements": items, "watch": []string{}, "next_steps": []string{}})
	return string(b)
}

func TestANineItemListWithOneLongItemIsRefusedNotTrimmedAndANineItemListThatFitsIsKeptWhole(t *testing.T) {
	cv := am22Limits()
	items := make([]string, 9)
	for i := range items {
		items[i] = "ข้อสังเกตข้อ " + strings.Repeat("ก", i+1)
	}
	long := append([]string{}, items...)
	long[6] = strings.Repeat("ยาวเกินไป", 8) // 72 columns over a limit of 30
	_, err := checkNarrative(am.Thai, am22Answer(long, "สรุป {revenue}"), cv)
	if err == nil || !strings.Contains(err.Error(), "movements ข้อที่ 7") || !strings.Contains(err.Error(), "ไม่เกิน 30") {
		t.Fatalf("the seventh point is over the limit: refused naming it, never trimmed: %v", err)
	}
	if strings.Contains(err.Error(), "ปฏิเสธ") || strings.Contains(err.Error(), "ตัดทิ้ง") {
		t.Fatalf("the reason asks for an edit that keeps every point, not a cut: %v", err)
	}
	v, err := checkNarrative(am.Thai, am22Answer(items, "สรุป {revenue}"), cv)
	if err != nil {
		t.Fatalf("nine points within the limits are a usable answer: %v", err)
	}
	if got := v["movements"].([]any); len(got) != 9 {
		t.Fatalf("every one of the nine points reaches the renderer (it was cut to six until AM22): %d", len(got))
	}
	// the renderer breaks nine points across slides (4, 4, 1) — test_am21_contract.py holds that side
}

func TestASummaryOverItsLimitAndAHeadingOverItsCeilingAreRefused(t *testing.T) {
	cv := am22Limits()
	_, err := checkNarrative(am.Thai, am22Answer(nil, strings.Repeat("สรุปยาว", 20)), cv)
	if err == nil || !strings.HasPrefix(err.Error(), "summary ยาว") {
		t.Fatalf("a summary over its limit is refused naming it: %v", err)
	}
	many := make([]string, 21)
	for i := range many {
		many[i] = "ข้อ"
	}
	_, err = checkNarrative(am.Thai, am22Answer(many, "สรุป"), cv)
	if err == nil || !strings.Contains(err.Error(), "movements มี 21 ข้อ") {
		t.Fatalf("a heading over the ceiling is refused: %v", err)
	}
	// the limits are in the run's language: an English run is told in English
	_, err = checkNarrative(am.English, am22Answer(many, "Summary"), cv)
	if err == nil || !strings.Contains(err.Error(), "movements has 21 points") {
		t.Fatalf("an English run's reason is English: %v", err)
	}
	_, err = checkNarrative(am.English, am22Answer(nil, strings.Repeat("a long summary ", 10)), cv)
	if err == nil || !strings.Contains(err.Error(), "summary is") || !strings.Contains(err.Error(), "at most 60") {
		t.Fatalf("an English run's reason is English: %v", err)
	}
}

func TestLengthIsMeasuredInTheCharactersAReaderSees(t *testing.T) {
	// "กำไร" is five code points and four columns (the mark above ก takes none); the Python side counts the same
	if got := columns("กำไร"); got != 4 {
		t.Fatalf("columns(กำไร) = %d, want 4", got)
	}
	if got := columns("ที่ปรึกษา"); got != 6 {
		t.Fatalf("columns(ที่ปรึกษา) = %d, want 6 (9 code points)", got)
	}
	if got := columns("profit"); got != 6 {
		t.Fatalf("columns(profit) = %d", got)
	}
	cv := computedView{WritingLimits: map[string]int{"summary_chars": 6, "bullet_chars": 30, "bullets_max": 4}}
	if _, err := checkNarrative(am.Thai, `{"summary":"ที่ปรึกษา"}`, cv); err != nil {
		t.Fatalf("six columns of nine code points fit a limit of six: %v", err)
	}
	if _, err := checkNarrative(am.Thai, `{"summary":"ที่ปรึกษาฯ"}`, cv); err == nil {
		t.Fatal("seven columns do not")
	}
}

func TestAnEngineThatSendsNoLimitsIsCheckedAsBefore(t *testing.T) {
	cv := computedView{}
	v, err := checkNarrative(am.Thai, am22Answer([]string{strings.Repeat("ยาวมาก", 100)}, strings.Repeat("ยาว", 500)), cv)
	if err != nil || len(v["movements"].([]any)) != 1 {
		t.Fatalf("no limits, no length check: %v", err)
	}
}

// AM22 §1.3, §1.6 — the prompt asks to draft then compress, in the run's language, and the one retry's
// reason is in that language too.
func TestTheModelIsToldToDraftThenCompressInTheRunsLanguageWithNoThreat(t *testing.T) {
	cv := computedView{PeriodLabel: "กันยายน 2569", Figures: map[string]figureView{"revenue": {Text: "12,480,300", Cite: "c61", Label: "รายได้"}},
		WritingLimits: map[string]int{"summary_chars": 388, "bullet_chars": 89, "bullets_max": 4, "items_max": 20}}
	th := narrativePrompt(am.Thai, cv)
	for _, want := range []string{"สรุปไม่เกิน 388", "แต่ละข้อในรายการไม่เกิน 89", "ไม่เกิน 20 ข้อ", "ร่างสิ่งที่ต้องบอกให้ครบก่อน", "เกลา"} {
		if !strings.Contains(th, want) {
			t.Fatalf("the Thai prompt does not say %q:\n%s", want, th)
		}
	}
	if strings.Contains(th, "ปฏิเสธ") {
		t.Fatal("the budget line threatens a refusal; it is a craft instruction")
	}
	cv.WritingLimits = map[string]int{"summary_chars": 480, "bullet_chars": 110, "bullets_max": 4, "items_max": 20}
	en := narrativePrompt(am.English, cv)
	for _, want := range []string{"the summary up to 480", "each point of a list up to 110", "at most 20 points", "draft everything that needs saying first", "copy editor"} {
		if !strings.Contains(en, want) {
			t.Fatalf("the English prompt does not say %q:\n%s", want, en)
		}
	}
	if strings.Contains(en, "ความยาวที่เอกสารรับได้") || strings.Contains(en, "refused") {
		t.Fatal("an English run is told its budget in English, and not threatened")
	}
}

func TestTheRetryReasonIsInTheRunsLanguage(t *testing.T) {
	var second atomic.Value
	var n int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Messages []struct {
				Content string `json:"content"`
			} `json:"messages"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		if atomic.AddInt32(&n, 1) == 2 {
			second.Store(req.Messages[len(req.Messages)-1].Content)
		}
		_, _ = w.Write([]byte(`{"model":"primary","choices":[{"message":{"content":"x"},"finish_reason":"stop"}]}`))
	}))
	defer s.Close()
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "primary", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	first := true
	_, _, _ = m.CompleteWithIn(context.Background(), am.English, []string{"primary"}, "s", "u", func(string) error {
		if first {
			first = false
			return errorString("summary is 70 characters; the document takes at most 60")
		}
		return nil
	})
	got, _ := second.Load().(string)
	if !strings.Contains(got, "The previous answer could not be used because: summary is 70 characters") || strings.Contains(got, "คำตอบก่อนหน้า") {
		t.Fatalf("an English run's retry reason is English: %q", got)
	}
	// a Thai run's is exactly what it was
	if !strings.Contains(retryLine(am.Thai, "x"), "คำตอบก่อนหน้าใช้ไม่ได้เพราะ: x แก้แล้วตอบใหม่เป็น JSON ตามรูปแบบเดิม") {
		t.Fatal("the Thai retry line changed")
	}
}

type errorString string

func (e errorString) Error() string { return string(e) }

// AM22 §1.1 — a package that is not the financial report shows the model what it found as facts under
// its own headings; the report's prompt is as it was.
func TestAPackagesFactsArePrintedUnderTheirHeadingsAndTheReportsPromptIsAsItWas(t *testing.T) {
	cv := computedView{PeriodLabel: "กันยายน 2569", Figures: map[string]figureView{"adjusted_bank": {Text: "1,538,519.60", Cite: "a1", Label: "ยอดหลังปรับปรุงของธนาคาร"}},
		NarrativeFacts: []narrativeFact{{Heading: "ผลการกระทบยอด", Lines: []string{"ยอดหลังปรับปรุงสองฝั่งเท่ากัน รอบนี้ลงตัว", "ยอดหลังปรับปรุงฝั่งธนาคาร {adjusted_bank}"}}}}
	p := narrativePrompt(am.Thai, cv)
	if !strings.Contains(p, "ผลการกระทบยอด:\n- ยอดหลังปรับปรุงสองฝั่งเท่ากัน รอบนี้ลงตัว\n- ยอดหลังปรับปรุงฝั่งธนาคาร {adjusted_bank}\n") {
		t.Fatalf("the facts are printed under their heading:\n%s", p)
	}
	if strings.Contains(p, "ตารางงบกำไรขาดทุน") || strings.Contains(p, "มีงบประมาณ") {
		t.Fatal("a reconciliation's prompt does not show an income statement")
	}
	// nor the raw records of its exceptions, whose amounts are not cited figures (its facts state their reasons)
	cv.Exceptions = []json.RawMessage{json.RawMessage(`{"reason":"เช็คค้างนาน","detail":"ยอด 6,420.00 บาท"}`)}
	if q := narrativePrompt(am.Thai, cv); strings.Contains(q, "6,420.00") || strings.Contains(q, "(exceptions)") {
		t.Fatalf("the raw exceptions were shown to the model:\n%s", q)
	}
	report := computedView{PeriodLabel: "กันยายน 2569", HasBudget: true, Figures: cv.Figures}
	if q := narrativePrompt(am.Thai, report); !strings.Contains(q, "มีงบประมาณ: true") || !strings.Contains(q, "ตารางงบกำไรขาดทุน (คำนวณแล้ว):") {
		t.Fatalf("the report's prompt is as it was:\n%s", q)
	}
}

// AM22 §1.1 — the round's last message of an agent that reads scans and has a model write its words says
// both: which model saw the documents, and which wrote the commentary (or that the rules did).
func TestTheClosingLineOfAnAgentThatReadsScansAndWritesWordsSaysBoth(t *testing.T) {
	var m am.Manifest
	_ = json.Unmarshal([]byte(`{"narrative":"model","capabilities":{"required":["document.extract.e2"],"optional":["model.vision","model.text"]}}`), &m)
	summary := map[string]any{"models": []any{map[string]any{"model": "qwen3.8-27b-fp8", "self_hosted": true, "documents": float64(2)}}}
	done := am.Text("เสร็จแล้วครับ")
	byModel := closingText(m, summary, narrativeOut{Model: "qwen3.8-27b-fp8", SelfHosted: true}, false, done).String()
	// AM23 §3.1: by what read the scans and drafted the words — the organisation's AI assistant — never a model's name
	for _, want := range []string{"ผู้ช่วย AI ขององค์กร (รันในองค์กร) อ่านซ้ำ 2 ฉบับ", "คำอธิบายร่างโดยผู้ช่วย AI ขององค์กร ที่รันในองค์กร"} {
		if !strings.Contains(byModel, want) {
			t.Fatalf("missing %q in %q", want, byModel)
		}
	}
	if strings.Contains(byModel, "qwen") || strings.Contains(closingText(m, summary, narrativeOut{Model: "qwen3.8-27b-fp8", SelfHosted: true}, false, done).In(am.English), "qwen") {
		t.Fatalf("the round's message names no model: %q", byModel)
	}
	byRules := closingText(m, summary, narrativeOut{}, false, done).String()
	if !strings.Contains(byRules, "ผู้ช่วย AI ขององค์กร (รันในองค์กร) อ่านซ้ำ") || !strings.Contains(byRules, "คำอธิบายในรอบนี้เขียนจากกฎ") {
		t.Fatalf("the rules wrote: said beside the reader of the scans: %q", byRules)
	}
	// an agent whose words are still the rules' says only who saw the documents, as before
	var rules am.Manifest
	_ = json.Unmarshal([]byte(`{"narrative":"rules","capabilities":{"required":["document.extract.e2"],"optional":["model.vision"]}}`), &rules)
	if got := closingText(rules, summary, narrativeOut{}, false, done).String(); strings.Contains(got, "คำอธิบาย") || !strings.Contains(got, "อ่านซ้ำ 2 ฉบับ") {
		t.Fatalf("a rules agent's line is as it was: %q", got)
	}
	// the financial report (no scans) says who wrote, as before
	var fin am.Manifest
	_ = json.Unmarshal([]byte(`{"narrative":"model","capabilities":{"required":["model.text"]}}`), &fin)
	if got := closingText(fin, map[string]any{}, narrativeOut{Model: "deepseek-v4-flash"}, false, done).String(); strings.Contains(got, "อ่านซ้ำ") || !strings.Contains(got, "ผู้ให้บริการภายนอก") || strings.Contains(got, "deepseek") {
		t.Fatalf("the report's line is as it was: %q", got)
	}
}

// AM22 §2.4 — a model that proposes one balance for two blanks: neither pair is kept, so the mapping screen asks the
// person (kept pair by pair, the first in key order would have won — the code picking between two labels).
func TestTwoBlanksAModelGaveOneBalanceAreLeftForThePerson(t *testing.T) {
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "..", "backend", "agent_market", "packages", "bank_reconciliation", "agent.json"))
	if err != nil {
		t.Fatal(err)
	}
	var m struct {
		Templates am.TemplateSpec `json:"templates"`
	}
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	slots := []am.TemplateSlot{
		{Key: "S!E8", Kind: "target", Label: "ยอดตามธนาคาร", Fillable: true, Required: true},
		{Key: "S!E9", Kind: "target", Label: "ยอดตามบัญชี", Fillable: true, Required: true},
		{Key: "S!C3", Kind: "target", Label: "ชื่อบริษัท", Fillable: true, Required: true},
	}
	got := keepValid(slots, map[string]string{}, map[string]string{"S!E8": "bank_balance", "S!E9": "bank_balance", "S!C3": "company"}, m.Templates)
	if _, ok := got["S!E8"]; ok {
		t.Fatalf("one of two blanks given one balance was kept: %v", got)
	}
	if _, ok := got["S!E9"]; ok {
		t.Fatalf("one of two blanks given one balance was kept: %v", got)
	}
	if got["S!C3"] != "company" {
		t.Fatalf("an unambiguous pair is kept: %v", got)
	}
	got = keepValid(slots, map[string]string{}, map[string]string{"S!E8": "bank_balance", "S!E9": "book_balance"}, m.Templates)
	if got["S!E8"] != "bank_balance" || got["S!E9"] != "book_balance" {
		t.Fatalf("two blanks given two balances are both kept: %v", got)
	}
}

// AM22 §2.6 — kept on purpose (ARCHITECTURE.md, "A customer's form: what is kept on purpose"): a filled form is
// recalculated by the converter before it is delivered, and a new version of a form is inspected against the saved
// one. The Python side (tests/test_am22_kept.py) holds the read-back, the refusals and the diff; this holds the wiring
// that makes them reach a person — removed, this fails.
func TestTheFilledFormIsRecalculatedAndReadBack(t *testing.T) {
	src, err := os.ReadFile("deliver.go")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(src), `if meta.Kind == "template" {`) || !strings.Contains(string(src), "rc.recalcTemplate(ctx, path, meta.Name, meta.Meta, meta.Quality)") {
		t.Fatal("deliver.go no longer has the converter recalculate a filled form")
	}
	tpl, err := os.ReadFile("template.go")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`"saved": savedArg`, `"op": "inspect_template"`, "Converter.Convert(ctx, filepath.Base(name), data, \"xlsx\")"} {
		if !strings.Contains(string(tpl), want) {
			t.Fatalf("template.go no longer has %s", want)
		}
	}
}

// AM22 — what the engine puts in computed.json is what the runner can read. A word in writing_limits (it once had
// "lang" and "unit") failed the reading of the whole table: every narrative step would have failed on live while
// every unit test passed, because they build computedView by hand. Found by the card's model measurement.
func TestTheTableTheEngineWritesIsReadByTheRunner(t *testing.T) {
	raw := `{"period_label":"กันยายน 2569","writing_limits":{"summary_chars":485,"bullet_chars":161,"bullets_max":12,"items_max":12},
		"figures":{"adjusted_bank":{"text":"1,538,519.65","cite":"a1","label":"ยอดหลังปรับปรุงของธนาคาร"}},
		"narrative_facts":[{"heading":"ผลการกระทบยอด","lines":["ยอดหลังปรับปรุงฝั่งธนาคาร {adjusted_bank}"]}],
		"fallback_narrative":{"summary":"x","movements":[],"watch":[],"next_steps":[]},"quotable":{},"quotable_ids":[]}`
	var cv computedView
	if err := json.Unmarshal([]byte(raw), &cv); err != nil {
		t.Fatalf("the runner cannot read the engine's table: %v", err)
	}
	if cv.WritingLimits["items_max"] != 12 || len(cv.NarrativeFacts) != 1 || cv.Figures["adjusted_bank"].Cite != "a1" {
		t.Fatalf("%+v", cv)
	}
	var bad computedView
	if err := json.Unmarshal([]byte(`{"writing_limits":{"summary_chars":480,"lang":"th"}}`), &bad); err == nil {
		t.Fatal("a word in writing_limits is read without error — the engine's contract test would not be needed")
	}
}

// AM22 §1.6 — an English run is told why its answer could not be used in English, every reason of it.
func TestAnEnglishRunsAnswerIsSentBackWithEnglishReasons(t *testing.T) {
	cv := computedView{Figures: map[string]figureView{"revenue": {Text: "12,480,300", Cite: "c61", Label: "Revenue"}}}
	for s, want := range map[string]string{
		"prose":                                  "not JSON",
		`{"summary":" "}`:                        "no summary",
		`{"summary":"Revenue was {revenue_m}."}`: "not in the table",
		`{"summary":"Revenue was 12,480,300 baht."}`: "do not type a figure",
		`{"summary":"Revenue was {revenue:abs."}`:    "not whole",
	} {
		_, err := checkNarrative(am.English, s, cv)
		if err == nil || !strings.Contains(err.Error(), want) || strings.ContainsFunc(err.Error(), func(r rune) bool { return r >= 0x0E00 && r <= 0x0E7F }) {
			t.Errorf("%s: want an English reason with %q, got %v", s, want, err)
		}
	}
	// a Thai run's reasons are what they were
	if _, err := checkNarrative(am.Thai, `{"summary":"รายได้ 12,480,300 บาท"}`, cv); err == nil || !strings.Contains(err.Error(), "อย่าพิมพ์ตัวเลขเอง") {
		t.Fatalf("Thai: %v", err)
	}
}

// am22Stub is a gateway whose model answers each call with the next of answers (the last one again after them).
func am22Stub(t *testing.T, answers ...string) *ModelClient {
	t.Helper()
	var n int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		i := int(atomic.AddInt32(&n, 1)) - 1
		if i >= len(answers) {
			i = len(answers) - 1
		}
		b, _ := json.Marshal(map[string]any{"model": "primary", "choices": []map[string]any{{"message": map[string]any{"content": answers[i]}, "finish_reason": "stop"}}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	return &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "primary", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
}

// what the narrative step does with a model's answers (deliver.go), without a run around it
func am22Narrative(t *testing.T, m *ModelClient, cv computedView) (map[string]any, string, error) {
	t.Helper()
	var parsed, long map[string]any
	var longBy string
	c, tried, err := m.CompleteWithInBy(context.Background(), am.Thai, []string{"primary"}, "s", "u", func(model, s string) error {
		v, err := checkNarrative(am.Thai, s, cv)
		var tl *tooLongErr
		if errors.As(err, &tl) {
			long, longBy = v, model
		}
		if err != nil {
			return err
		}
		parsed = v
		return nil
	})
	if err != nil {
		if v, model, ok := overLength(long, longBy); ok {
			return v, model, nil
		}
		return nil, "", err
	}
	_ = tried
	return parsed, c.Model, nil
}

// AM22 §1.2, the three stubs of the card: a nine-point list refused on attempt 1 for one long point and complete on
// attempt 2; a model that writes too long twice reaches the renderer with its own words (the renderer steps them down
// the scale, breaks them, or refuses naming the section — test_am22_words.py); and one that types a figure twice
// leaves the rules' words, as before.
func TestTheNarrativeStepWithAModelThatObeysOneThatWritesTooLongAndOneThatTypesAFigure(t *testing.T) {
	cv := am22Limits()
	nine := make([]string, 9)
	for i := range nine {
		nine[i] = "ข้อสังเกตข้อ " + strings.Repeat("ก", i+1)
	}
	long := append([]string{}, nine...)
	long[6] = strings.Repeat("ยาวเกินไป", 8)
	v, model, err := am22Narrative(t, am22Stub(t, am22Answer(long, "สรุป {revenue}"), am22Answer(nine, "สรุป {revenue}")), cv)
	if err != nil || model != "primary" || len(v["movements"].([]any)) != 9 {
		t.Fatalf("the model that obeys on attempt 2: all nine points, its words: %v %q %v", err, model, v)
	}
	v, model, err = am22Narrative(t, am22Stub(t, am22Answer(long, "สรุป {revenue}")), cv)
	if err != nil || model != "primary" || len(v["movements"].([]any)) != 9 {
		t.Fatalf("too long twice: the model's own nine points go on to the renderer: %v %q %v", err, model, v)
	}
	if got := v["movements"].([]any)[6].(string); got != long[6] {
		t.Fatalf("nothing is cut on the way: %q", got)
	}
	_, _, err = am22Narrative(t, am22Stub(t, `{"summary":"รายได้ 12,480,300 บาท"}`), cv)
	if !errors.Is(err, ErrNoModel) || errors.Is(err, ErrModelUnreachable) {
		t.Fatalf("a figure typed twice: no answer the renderer may have — the rules write: %v", err)
	}
	// too long and a typed figure in the same answer is not "only its length": the rules write
	_, _, err = am22Narrative(t, am22Stub(t, am22Answer(long, "รายได้ 12,480,300 บาท")), cv)
	if err == nil {
		t.Fatal("an answer too long that also types a figure is not one the renderer may have")
	}
}

// AM22 — the round's message says why the rules wrote: a model that answered and was refused is not "no model".
func TestTheClosingLineSaysWhetherTheModelWasAwayOrItsAnswersCouldNotBeUsed(t *testing.T) {
	var fin am.Manifest
	_ = json.Unmarshal([]byte(`{"narrative":"model","capabilities":{"required":["model.text"]}}`), &fin)
	done := am.Text("เสร็จแล้วครับ")
	if got := closingText(fin, map[string]any{}, narrativeOut{Why: "unusable"}, false, done).String(); !strings.Contains(got, "คำตอบของโมเดลใช้ไม่ได้") {
		t.Fatalf("refused answers: %q", got)
	}
	if got := closingText(fin, map[string]any{}, narrativeOut{Why: "unavailable"}, false, done).String(); !strings.Contains(got, "ไม่มีโมเดลที่ตอบได้") {
		t.Fatalf("no model: %q", got)
	}
}

// AM22 — a comma after a figure is the sentence's, in both languages: the guard reads "13, 14 and 15" as three small
// counts and "12,480,300, and" as the table's figure (it refused both, reading "13," and "12,480,300,").
func TestACommaAfterAFigureIsTheSentencesNotTheFigures(t *testing.T) {
	quotable := map[string]string{"revenue": "12,480,300"}
	if err := GuardNumbersIn(am.English, "Revenue was 12,480,300, and refunds came on 13, 14 and 15 September.", quotable, nil); err != nil {
		t.Fatalf("English: %v", err)
	}
	if err := GuardNumbersIn(am.Thai, "รายได้ 12,480,300, คืนเงินวันที่ 13, 14 และ 15", quotable, nil); err != nil {
		t.Fatalf("Thai: %v", err)
	}
	if err := GuardNumbersIn(am.English, "Revenue was 12,480,301, and more.", quotable, nil); err == nil {
		t.Fatal("a figure the table does not have still has to be refused")
	}
}

// AM22 — a word the agent's documents never contain is refused in the model's answer, in either language and case,
// with the reason in the run's language; the PO matching's gate would refuse the file.
func TestAWordTheAgentNeverWritesIsRefusedEvenInANegation(t *testing.T) {
	cv := computedView{NarrativeNever: []string{"อนุมัติแล้ว", "จ่ายแล้ว", "approved", "paid"},
		Figures: map[string]figureView{"n1": {Text: "3", Cite: "n1"}}, WritingLimits: map[string]int{}}
	_, err := checkNarrative(am.English, `{"summary":"No line may be treated as Approved.","watch":[]}`, cv)
	if err == nil || !strings.Contains(err.Error(), "never appears in this agent's documents") {
		t.Fatalf("English: %v", err)
	}
	_, err = checkNarrative(am.Thai, `{"summary":"ไม่ได้หมายความว่าใบแจ้งหนี้อนุมัติแล้ว","watch":[]}`, cv)
	if err == nil || !strings.Contains(err.Error(), "ห้ามมีในเอกสารของ agent นี้") {
		t.Fatalf("Thai: %v", err)
	}
	if _, err := checkNarrative(am.English, `{"summary":"{n1} invoices are ready to be put forward; a person decides.","watch":[]}`, cv); err != nil {
		t.Fatalf("an answer without the words: %v", err)
	}
}

// AM22 — an agent with no figure table copies its numbers: a {name} is refused, with the reason, not left in the file.
func TestAnAgentWithNoFigureTableHasItsFigureNamesRefused(t *testing.T) {
	cv := computedView{Quotable: map[string]string{"items": "3"}, WritingLimits: map[string]int{}}
	_, err := checkNarrative(am.English, `{"summary":"The brief produced {items} items.","watch":[]}`, cv)
	if err == nil || !strings.Contains(err.Error(), "have no names") {
		t.Fatalf("a figure name with no table: %v", err)
	}
	if _, err := checkNarrative(am.English, `{"summary":"The brief produced 3 items.","watch":[]}`, cv); err != nil {
		t.Fatalf("the number copied: %v", err)
	}
}

// AM22 — the PO matching declares the words its documents never contain, and the runner reads them from the manifest.
func TestThePOMatchingDeclaresTheWordsItsPaperNeverSays(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "..", "backend", "agent_market", "packages", "po_matching", "agent.json"))
	if err != nil {
		t.Fatal(err)
	}
	m, err := am.ParseManifest(raw)
	if err != nil {
		t.Fatal(err)
	}
	got := strings.Join(m.NarrativeNever, " ")
	for _, w := range []string{"อนุมัติแล้ว", "จ่ายแล้ว", "approved", "paid"} {
		if !strings.Contains(got, w) {
			t.Fatalf("narrative_never lacks %q: %v", w, m.NarrativeNever)
		}
	}
}
