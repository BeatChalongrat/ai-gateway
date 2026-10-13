package agentmarket

import (
	"encoding/json"

	"github.com/google/uuid"
)

// ── What a person asks of the documents, beyond the package's own list (AM18, part B) ──
//
// AM11b and AM13 made an agent do what it is asked of its documents: a model reads the person's words
// into the directives the package declares, the code applies them, the thread says what was done and
// what could not be, with the reason. Three things a person says about the documents were still not
// heard:
//
//   - the language. The documents follow the run's language since AM18 part A; a person who asks for
//     "the English version" asks a thing the platform does, not a package. The two languages a package
//     speaks are two directives of the platform's (LanguagesAsked), read with the package's own; a
//     language the package does not speak, or any other, is what cannot be done, with its reason
//     (LanguagesNotAsked) — said before any work, and no model translates a document at run time.
//   - from now on, or this once. "เรียงตามวันที่ทุกครั้ง" is the install's own way until a person clears it;
//     "เรียงตามวันที่" is this round's. The model says which it read (Asked.Standing); the platform keeps
//     the standing ones as the install's defaults (StandingKey, LanguageKey) — where a person sees them
//     and clears them — and the thread says what it recorded.
//   - what could mean two things. A request that could be two directives of the list — "เรียงใหม่" of a
//     table that is ordered two ways — is not guessed: the model names the ids (Asked.Ask), and the round
//     asks with a card of its own (AskedChoiceFinding), the question card every other decision uses.
//
// Nothing here is a list of words a person must type. The model reads their words; this file names what
// the platform can do with the reading, and says what it did.

const (
	// LangThai, LangEnglish: the platform's two directives for the language of the documents.
	LangThai    = "lang_th"
	LangEnglish = "lang_en"
	// LangOther is the id a model answers with for a language that is neither.
	LangOther = "lang_other"
	// StandingKey is the install default that keeps the package directives a person asked for from now on.
	StandingKey = "instructions"
	// LanguageKey is the install default that keeps the language of the documents (the platform's own
	// "language" setting, which a person may also set by hand).
	LanguageKey = "language"
	// AskedChoiceCode is the question a round asks when the words could mean two things it can do.
	AskedChoiceCode = "asked_choice"
)

// Speaking is spec with the languages the package's documents are written in (its manifest's
// "languages"; Thai alone when it says none).
func (spec InstructionSpec) Speaking(langs []string) InstructionSpec {
	spec.languages = append([]string(nil), langs...)
	return spec
}

func (spec InstructionSpec) speaks(l Lang) bool {
	if len(spec.languages) == 0 {
		return l == Thai
	}
	for _, s := range spec.languages {
		if Lang(s) == l {
			return true
		}
	}
	return false
}

// speaksID: id is the directive of a language the package speaks.
func (spec InstructionSpec) speaksID(id string) bool {
	for _, d := range spec.LanguagesAsked() {
		if d.ID == id {
			return true
		}
	}
	return false
}

// LanguageDirective is one language a person may ask the documents in.
type LanguageDirective struct {
	ID    string
	Lang  Lang
	Label PersonText
}

func languageDirectives() []LanguageDirective {
	return []LanguageDirective{
		{LangThai, Thai, Text("ทำเอกสารเป็นภาษาไทย")},
		{LangEnglish, English, Text("ทำเอกสารเป็นภาษาอังกฤษ")},
	}
}

func LangOfID(id string) Lang {
	for _, d := range languageDirectives() {
		if d.ID == id {
			return d.Lang
		}
	}
	return ""
}

// LangID is the directive of a language.
func LangID(l Lang) string {
	for _, d := range languageDirectives() {
		if d.Lang == l {
			return d.ID
		}
	}
	return ""
}

// LanguagesAsked: the languages the documents can be asked in — those the package speaks.
func (spec InstructionSpec) LanguagesAsked() []LanguageDirective {
	var out []LanguageDirective
	for _, d := range languageDirectives() {
		if spec.speaks(d.Lang) {
			out = append(out, d)
		}
	}
	return out
}

// LanguageName is a language as a sentence names it.
func LanguageName(l Lang) PersonText {
	if l == English {
		return Text("ภาษาอังกฤษ")
	}
	return Text("ภาษาไทย")
}

// languageNames: the languages the package speaks, as a sentence names them.
func (spec InstructionSpec) languageNames() PersonText {
	if spec.speaks(Thai) && spec.speaks(English) {
		return Text("ภาษาไทยและภาษาอังกฤษ")
	}
	if spec.speaks(English) {
		return LanguageName(English)
	}
	return LanguageName(Thai)
}

// LanguagesNotAsked: a language the package does not speak, and any other language — each with the
// reason, the way NeverAsked gives the platform's.
func (spec InstructionSpec) LanguagesNotAsked() []Never {
	var out []Never
	for _, d := range languageDirectives() {
		if !spec.speaks(d.Lang) {
			out = append(out, Never{d.ID, d.Label, Text("agent นี้ทำเอกสารได้เป็น%sเท่านั้น", Words(spec.languageNames()))})
		}
	}
	out = append(out, Never{LangOther, Text("ทำเอกสารเป็นภาษาอื่น"),
		Text("agent นี้ทำเอกสารได้เป็น%sเท่านั้น และไม่ให้โมเดลแปลเอกสาร", Words(spec.languageNames()))})
	return out
}

// StandingOf reads the install's own way out of its defaults: the package directives kept under
// StandingKey, and the language under LanguageKey ("" when none is kept).
func StandingOf(defaults []Default) (ids []string, lang Lang) {
	for _, d := range defaults {
		switch d.Key {
		case StandingKey:
			_ = json.Unmarshal(d.Value, &ids)
		case LanguageKey:
			var s string
			if json.Unmarshal(d.Value, &s) == nil && (Lang(s) == Thai || Lang(s) == English) {
				lang = Lang(s)
			}
		}
	}
	return ids, lang
}

// StandingDefault is the default that keeps ids as the install's own way. Its source is "answer" — what a
// person said in the chat, as a remembered answer is — so the settings page lists it among what is
// remembered, with the one button that clears it.
func StandingDefault(spec InstructionSpec, ids []string, from *uuid.UUID) Default {
	v, _ := json.Marshal(ids)
	return Default{Key: StandingKey, Value: v, Source: "answer", Label: T(Thai, standingLabel), Note: T(Thai, standingNote), FromRunID: from}
}

// LanguageDefault is the default that keeps l as the language of the install's documents from now on.
func LanguageDefault(l Lang, from *uuid.UUID) Default {
	v, _ := json.Marshal(string(l))
	return Default{Key: LanguageKey, Value: v, Source: "answer", Label: T(Thai, "ภาษาของเอกสาร"), Note: T(Thai, standingNote), FromRunID: from}
}

const (
	standingLabel wording = "คำขอที่จำไว้"
	standingNote  wording = "ขอไว้ในแชต — ล้างได้ที่นี่"
)

// AskedStanding says what was recorded as the install's own way from now on, and where it is seen and
// cleared. ids are package directives or a language's.
func AskedStanding(spec InstructionSpec, ids []string) PersonText {
	var parts []PersonText
	for _, id := range ids {
		if d, ok := spec.can(id); ok {
			parts = append(parts, LabelFromPackage(d.Label, NoText))
			continue
		}
		for _, l := range spec.LanguagesAsked() {
			if l.ID == id {
				parts = append(parts, l.Label)
			}
		}
	}
	if len(parts) == 0 {
		return NoText
	}
	return Text("จำไว้เป็นค่าตั้งต้นของ agent นี้ต่อไป: %s — ดูหรือล้างได้ในหน้าตั้งค่าของ agent", Words(Join(" · ", parts...)))
}

// AskedOnce says what the same words asked for this round only, when they also asked for something from
// now on — so a person reads which is which.
func AskedOnce(spec InstructionSpec, ids []string) PersonText {
	var parts []PersonText
	for _, id := range ids {
		if d, ok := spec.can(id); ok {
			parts = append(parts, LabelFromPackage(d.Label, NoText))
		}
	}
	if len(parts) == 0 {
		return NoText
	}
	return Text("ส่วน %s ใช้กับรอบนี้เท่านั้น", Words(Join(" · ", parts...)))
}

// AskedLanguage says the round's documents are in the language asked.
func AskedLanguage(l Lang) PersonText {
	return Text("เอกสารรอบนี้ทำเป็น%sตามที่ขอ", Words(LanguageName(l)))
}

// AskedLanguageAlready: the finished round's documents are in that language already.
func AskedLanguageAlready(l Lang) PersonText {
	return Text("เอกสารของรอบนี้เป็น%sอยู่แล้ว", Words(LanguageName(l)))
}

// AskedNewRoundInLanguage: a finished round's files keep their language; a new round is started from
// the same files in the one asked.
func AskedNewRoundInLanguage(l Lang) PersonText {
	return Text("ไฟล์ของรอบที่เสร็จแล้วคงเป็นภาษาเดิม กำลังเริ่มรอบใหม่จากไฟล์ชุดเดิมเป็น%sตามที่ขอ", Words(LanguageName(l)))
}

// AskedChoiceFinding is the question a round asks when the words could mean two things it can do: one
// option per id, in the language of the run. nil when fewer than two of the ids are things it can do.
// The default — what a skipped card records — is the first the package lists, the rule ReadAsked keeps
// for two of a group asked in the same words.
func AskedChoiceFinding(l Lang, spec InstructionSpec, n int, ask AskedAmbiguous) *Finding {
	var opts []Option
	for _, id := range ask.IDs {
		label := ""
		if d, ok := spec.can(id); ok {
			label = LabelFromPackage(d.Label, NoText).In(l)
		}
		for _, x := range spec.LanguagesAsked() {
			if x.ID == id {
				label = x.Label.In(l)
			}
		}
		if label == "" {
			continue
		}
		v, _ := json.Marshal(id)
		opts = append(opts, Option{Label: label, Value: v})
	}
	if len(opts) < 2 {
		return nil
	}
	q := Text("ที่ขอมาทำได้มากกว่าหนึ่งแบบ จะเอาแบบไหนครับ").In(l)
	if ask.Words != "" {
		q = Text("“%s” ทำได้มากกว่าหนึ่งแบบ จะเอาแบบไหนครับ", Typed(ask.Words, 160)).In(l)
	}
	key := AskedChoiceCode
	if n > 0 {
		key = AskedChoiceCode + "." + itoa(n)
	}
	// (blocking: the round waits for the answer — a cosmetic card would let it go on with the default and
	// make the files once more when the answer came; a person who was asked is answered first)
	// (the moment is the preflight's: the card is asked before any work, as a preflight question is, and
	// the table of cards knows that moment)
	return &Finding{Key: key, Code: AskedChoiceCode, Severity: "blocking", Moment: "preflight", Header: T(l, "คำขอ"),
		Question: q, Options: opts, Default: opts[0].Value, Kind: "question"}
}

// AskedChoiceKey is the key of the n-th choice card of a round.
func AskedChoiceKey(n int) string {
	if n <= 0 {
		return AskedChoiceCode
	}
	return AskedChoiceCode + "." + itoa(n)
}

// AskedByStanding says what the documents were made with because the install keeps it as its own way —
// asked for earlier, not in these words — and where it is cleared.
func AskedByStanding(spec InstructionSpec, ids []string) PersonText {
	var parts []PersonText
	for _, id := range ids {
		if d, ok := spec.can(id); ok {
			parts = append(parts, LabelFromPackage(d.Label, NoText))
		}
	}
	if len(parts) == 0 {
		return NoText
	}
	return Text("ทำตามที่ขอไว้ก่อนหน้า: %s — ล้างได้ในหน้าตั้งค่าของ agent", Words(Join(" · ", parts...)))
}

// AskedChoices reads what a person chose on the round's choice cards: the ids, in the order asked.
func AskedChoices(answers map[string]Answer) []string {
	var out []string
	for i := 0; i < 8; i++ {
		key := AskedChoiceKey(i)
		a, ok := answers[key]
		if !ok {
			continue
		}
		var id string
		if json.Unmarshal(a.Value, &id) == nil && id != "" {
			out = append(out, id)
		}
	}
	return out
}
