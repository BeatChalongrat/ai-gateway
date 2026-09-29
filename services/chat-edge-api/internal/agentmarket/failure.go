package agentmarket

import (
	"regexp"
	"strings"
	"unicode/utf8"
)

// ── What a person reads when a round does not work (AM5, AM6) ────────────
//
// The live screen showed a failed round with "'NoneType' object is not
// iterable" in it: the runner pasted the error's own text into the thread
// and onto the progress card. Two rules make that impossible rather than
// unlikely:
//
//  1. What a person reads about a failure is built from a reason CODE and
//     facts about the round (FailureFor) — the function takes no error, so
//     no caller can hand it one. The error's text goes where it belongs: the
//     run's step rows (the run page) and the problem report.
//  2. AM6: whatever writes to the thread takes a PersonText (persontext.go),
//     and whatever leaves the thread is what its kind's schema declares
//     (schema.go), each text as ShownToPerson shows it (reads.go). AM5 took
//     out the error shapes it knew; that list is kept below as a second look
//     that can only withhold more, and decides nothing.
//
// A failed round says four things, in Thai: what happened, what to try
// next, whether the files are safe, and that nothing was charged.
//
// AM6: a round whose package gate did not pass and whose draft was delivered
// is not a failed round. It delivered its work — the files, and how much is
// unexplained — and says so (CompletionFor): "เสร็จแล้ว · ยังไม่ลงตัว".

// Failure is a failed round as a person reads it.
type Failure struct {
	Reason   string       `json:"reason"` // the code; the web never shows it
	What     PersonText   `json:"what"`
	Next     PersonText   `json:"next"`
	Files    PersonText   `json:"files"`
	Charged  PersonText   `json:"charged"`
	Credits  float64      `json:"credits"`
	CanRetry bool         `json:"can_retry"`
	Failed   []PersonText `json:"failed_checks,omitempty"`
}

// FailureFacts is what the four sentences depend on besides the reason.
type FailureFacts struct {
	Kind string // the run's kind
	// Credits is what the round is still charged after it ended: nothing,
	// unless an earlier pass of the same round was delivered (a document
	// brought back into a finished round, whose second pass then failed).
	Credits  float64
	HasFiles bool // the round has files of the person's that a retry can use
	KeepDays int  // how long a failed round's files are kept (0 = unknown)
	// FailedChecks are the quality gate's failed checks, in the package's own
	// Thai words ("รายงาน: หัวข้อครบทุกส่วน").
	FailedChecks []string
	// GateWhat and GateNext are the package's own sentences for a round whose
	// completion gate did not pass and that delivered nothing — read from its
	// computed table, never from an error.
	GateWhat string
	GateNext string
}

type failureCopy struct {
	what  wording
	next  wording
	retry bool // trying again with the same files can work
}

const (
	nextRetry   = "กด “ลองใหม่ด้วยไฟล์เดิม” ได้เลย ถ้ายังไม่สำเร็จอีกให้กด “แจ้งปัญหา” ผู้ดูแลจะเห็นรอบนี้พร้อมขั้นตอนที่หยุด"
	nextLater   = "รอสักครู่แล้วกด “ลองใหม่ด้วยไฟล์เดิม” ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา”"
	nextNewFile = "ตรวจว่าไฟล์เปิดได้ตามปกติ ไม่ได้ตั้งรหัสผ่าน แล้วส่งไฟล์มาใหม่ หรือกด “ลองใหม่ด้วยไฟล์เดิม”"
	nextDraft   = "เปิดไฟล์ร่างของรอบนี้เพื่อดูสาเหตุ แก้ไขแล้วส่งไฟล์มาใหม่เพื่อเริ่มรอบใหม่"
	nextFixed   = "แก้สาเหตุแล้วส่งไฟล์มาใหม่เพื่อเริ่มรอบใหม่ ถ้าไม่แน่ใจว่าต้องแก้อะไรให้กด “แจ้งปัญหา”"
)

// failureCopies: one entry per reason a run can stop with. A reason without
// an entry reads as the last one (""). AM6: a gate that did not pass is not
// among them — a round that delivered its draft is a completed round
// (CompletionFor), and one that delivered nothing says the gate's own words.
var failureCopies = map[string]failureCopy{
	"parse_failed":        {"อ่านไฟล์ที่ส่งมาไม่สำเร็จ", nextNewFile, true},
	"vision_failed":       {"อ่านเอกสารที่เป็นสแกนหรือรูปถ่ายไม่สำเร็จ", "ส่งเป็น PDF จากระบบหรือสแกน 200–300 DPI ถ้ามี หรือกด “ลองใหม่ด้วยไฟล์เดิม”", true},
	"preflight_failed":    {"ตรวจความครบถ้วนของไฟล์ไม่สำเร็จ", nextNewFile, true},
	"compute_failed":      {"คำนวณจากไฟล์ที่ส่งมาไม่สำเร็จ", nextRetry, true},
	"postcompute_failed":  {"ตรวจผลคำนวณไม่สำเร็จ", nextRetry, true},
	"narrative_failed":    {"เขียนคำอธิบายของรายงานไม่สำเร็จ", nextRetry, true},
	"render_failed":       {"สร้างไฟล์ผลงานไม่สำเร็จ ตัวเลขคำนวณเสร็จแล้วแต่ยังไม่ได้เป็นไฟล์", nextRetry, true},
	"publish_failed":      {"เก็บไฟล์ผลงานไม่สำเร็จ", nextLater, true},
	"stamp_failed":        {"ลงชื่อผู้ตรวจทานบนไฟล์ไม่สำเร็จ ไฟล์เดิมที่ยังไม่ได้ลงชื่อยังใช้ได้", "กดลงชื่อตรวจทานอีกครั้ง ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา”", false},
	"quality_gate":        {"สร้างไฟล์ได้ แต่ไม่ผ่านด่านตรวจคุณภาพ จึงยังไม่ส่งให้", "ลองขอไฟล์ชนิดอื่น (เช่น .docx แทน PDF) หรือกด “ลองใหม่ด้วยไฟล์เดิม”", true},
	"package_changed":     {"agent ถูกอัปเดตระหว่างรอบนี้ ตัวเลขของรอบนี้จึงไม่ถูกส่ง", "กด “ลองใหม่ด้วยไฟล์เดิม” เพื่อทำรอบนี้ด้วยรุ่นปัจจุบัน", true},
	"sandbox_unavailable": {"ส่วนคำนวณของ agent ยังไม่พร้อมใช้งานในตอนนี้", nextLater, true},
	"engine_missing":      {"รุ่นของ agent ที่ติดตั้งไว้ไม่มีในระบบแล้ว", "อัปเกรดเป็นรุ่นใหม่ในหน้าตั้งค่าของ agent แล้วกด “ลองใหม่ด้วยไฟล์เดิม”", true},
	"no_period":           {"หางวด (เดือน) ของข้อมูลในไฟล์ไม่เจอ", "ส่งไฟล์ที่มีคอลัมน์วันที่ หรือพิมพ์บอกงวด เช่น “ปิดงบเดือนกันยายน 2569”", false},
	"too_many_rounds":     {"คำตอบกับไฟล์ในรอบนี้ยังขัดกันอยู่หลายครั้ง จึงหยุดไว้ก่อน", "ตรวจคำตอบในการ์ดคำถาม แล้วกด “ลองใหม่ด้วยไฟล์เดิม”", true},
	"budget_exhausted":    {"งบเครดิตของ agent นี้สำหรับเดือนนี้หมดแล้ว รอบนี้จึงหยุดไว้", "ขอเพิ่มงบจากผู้ดูแล Agent Market แล้วกด “ลองใหม่ด้วยไฟล์เดิม” แชทปกติยังใช้ได้ตามเดิม", true},
	"self_test_seed":      {"เตรียมข้อมูลตัวอย่างสำหรับทดสอบไม่สำเร็จ", "กดทดสอบกับข้อมูลตัวอย่างอีกครั้งในหน้าตั้งค่า ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา”", false},
	"bad_format":          {"ชนิดไฟล์ที่ขอยังสร้างให้ไม่ได้", "เลือกชนิดไฟล์อื่นจาก “ขอไฟล์เพิ่ม”", false},
	"internal_error":      {"ระบบของ agent หยุดทำงานกลางรอบนี้", nextRetry, true},
	"":                    {"รอบนี้ทำไม่สำเร็จ", nextRetry, true},
}

// FailureReasons lists every reason with its own sentence (tests walk it).
func FailureReasons() []string {
	out := make([]string, 0, len(failureCopies))
	for k := range failureCopies {
		if k != "" {
			out = append(out, k)
		}
	}
	return out
}

// FailureFor builds what a person reads. It takes the reason's code and
// facts about the round — never an error.
func FailureFor(reason string, facts FailureFacts) Failure {
	c, ok := failureCopies[reason]
	if !ok {
		c = failureCopies[""]
	}
	what, next := Text(c.what), Text(c.next)
	if facts.GateWhat != "" || facts.GateNext != "" {
		// the package's gate did not pass and nothing was delivered: the same
		// files give the same verdict, and the sentences are the package's —
		// what a person is shown of them, or the platform's own
		c.retry = false
		what = FromPackage(facts.GateWhat, Text("งานของรอบนี้ยังไม่ผ่านเกณฑ์ของ agent และยังไม่ได้ส่งไฟล์ให้"))
		next = FromPackage(facts.GateNext, Text(nextFixed))
	}
	f := Failure{Reason: reason, What: what, Next: next, Credits: facts.Credits}
	f.CanRetry = c.retry && facts.HasFiles && facts.Kind == KindRun
	if !f.CanRetry && c.retry {
		// nothing of the person's to try again with (a self-test, a follow-up job)
		f.Next = Text("ลองอีกครั้ง ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา” ผู้ดูแลจะเห็นรอบนี้พร้อมขั้นตอนที่หยุด")
		if facts.Kind == KindRun {
			f.Next = Text("ส่งไฟล์มาใหม่เพื่อเริ่มรอบนี้อีกครั้ง ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา”")
		}
	}
	for _, x := range facts.FailedChecks {
		if t := FromPackage(x, NoText); !t.Empty() {
			f.Failed = append(f.Failed, t)
		}
	}
	if len(f.Failed) > 0 {
		f.What = Text("%s (ข้อที่ไม่ผ่าน: %s)", Words(f.What), Words(Join(" · ", f.Failed...)))
	}
	switch {
	case !facts.HasFiles:
		f.Files = Text("ผลงานของรอบก่อน ๆ ยังอยู่ครบ ไม่มีอะไรถูกลบหรือแก้ไข")
	case facts.KeepDays > 0:
		f.Files = Text("ไฟล์ที่ส่งมาในรอบนี้ยังอยู่ครบและเก็บไว้ให้อีก %s วัน ผลงานของรอบก่อน ๆ ไม่ถูกแตะต้อง", Count(facts.KeepDays))
	default:
		f.Files = Text("ไฟล์ที่ส่งมาในรอบนี้และผลงานของรอบก่อน ๆ ยังอยู่ครบ")
	}
	// AM6: a round that delivered nothing is charged nothing
	f.Charged = Text("รอบนี้ไม่คิดเครดิต เพราะยังไม่ได้ส่งงานให้")
	if facts.Credits > 0 {
		f.Charged = Text("ครั้งนี้ไม่คิดเครดิต เพราะยังไม่ได้ส่งงานให้ ส่วน %s เครดิตที่คิดไปแล้วเป็นของไฟล์ชุดที่ส่งให้ก่อนหน้านี้", Credits(facts.Credits))
	}
	return f
}

// Text is the failure as one paragraph (a thread item's text; the web shows
// the four parts on the chat's failed-turn card).
func (f Failure) Text() PersonText {
	return Text("%s — %s %s %s", Words(f.What), Words(f.Next), Words(f.Files), Words(f.Charged))
}

// ── a round that finished with its gate open (AM6) ───────────────────────

// Completion is a round that delivered its work while its package's gate did
// not pass, as a person reads it: not an error.
type Completion struct {
	Code    string     `json:"code"` // the gate's code; the web never shows it
	Title   PersonText `json:"title"`
	What    PersonText `json:"what"`
	Next    PersonText `json:"next"`
	Files   PersonText `json:"files"`
	Charged PersonText `json:"charged"`
	Credits float64    `json:"credits"`
}

// CompletionFacts is what the sentences depend on besides the gate's code.
type CompletionFacts struct {
	Credits  float64 // what the round was charged
	KeepDays int     // how long the round's own files are kept (0 = unknown)
	// GateWhat and GateNext are the package's own sentences, read from its
	// computed table.
	GateWhat string
	GateNext string
}

// GateNotTied is the code of a reconciliation whose two balances differ.
const GateNotTied = "not_tied"

// CompletionTitle is how such a round is named.
func CompletionTitle(code string) PersonText {
	if code == GateNotTied {
		return Text("เสร็จแล้ว · ยังไม่ลงตัว")
	}
	return Text("เสร็จแล้ว · ยังไม่ผ่านเกณฑ์ของ agent")
}

// openWhat is the platform's own sentence for a gate, when nothing of the
// package's can be shown.
func openWhat(code string) PersonText {
	if code == GateNotTied {
		return Text("งบกระทบยอดยังไม่ลงตัว ยอดหลังปรับปรุงของธนาคารและของบัญชียังไม่เท่ากัน")
	}
	return Text("งานของรอบนี้ยังไม่ผ่านเกณฑ์ของ agent")
}

// GateWords are a package gate's own sentences as a person is shown them (the
// files card, the progress card). A gate that did not pass always says
// something: the platform's own sentence when nothing of the package's can be
// shown.
func GateWords(passed bool, code, what, next string) (PersonText, PersonText) {
	if passed {
		return FromPackage(what, NoText), FromPackage(next, NoText)
	}
	return FromPackage(what, openWhat(code)), FromPackage(next, NoText)
}

// CompletionFor builds what a person reads. It takes the gate's code, the
// package's sentences and facts about the round — never an error.
func CompletionFor(code string, facts CompletionFacts) Completion {
	c := Completion{Code: code, Title: CompletionTitle(code), Credits: facts.Credits,
		What: FromPackage(facts.GateWhat, openWhat(code)), Next: FromPackage(facts.GateNext, Text(nextDraft))}
	c.Files = Text("ไฟล์ของรอบนี้ส่งให้แล้วเป็นฉบับร่าง มีคำว่า “ร่าง” กำกับบนหน้าแรก ไฟล์ที่ส่งมาและผลงานของรอบก่อน ๆ ยังอยู่ครบ")
	if facts.KeepDays > 0 {
		c.Files = Text("ไฟล์ของรอบนี้ส่งให้แล้วเป็นฉบับร่าง มีคำว่า “ร่าง” กำกับบนหน้าแรก ไฟล์ที่ส่งมาในรอบนี้ยังอยู่ครบและเก็บไว้ให้อีก %s วัน "+
			"ผลงานของรอบก่อน ๆ ไม่ถูกแตะต้อง", Count(facts.KeepDays))
	}
	switch {
	case facts.Credits <= 0:
		c.Charged = Text("รอบนี้ไม่ได้ใช้เครดิต")
	case code == GateNotTied:
		c.Charged = Text("รอบนี้ใช้ไป %s เครดิต งานของรอบนี้เสร็จและส่งไฟล์ให้แล้ว จึงคิดเครดิตตามปกติ แม้ยอดจะยังไม่ลงตัว", Credits(facts.Credits))
	default:
		c.Charged = Text("รอบนี้ใช้ไป %s เครดิต งานของรอบนี้เสร็จและส่งไฟล์ให้แล้ว จึงคิดเครดิตตามปกติ แม้งานจะยังไม่ผ่านเกณฑ์ของ agent",
			Credits(facts.Credits))
	}
	return c
}

// Text is the completion as one paragraph (a thread item's text).
func (c Completion) Text() PersonText {
	return Text("%s — %s %s %s %s", Words(c.Title), Words(c.What), Words(c.Next), Words(c.Files), Words(c.Charged))
}

// ── the list of AM5 ──────────────────────────────────────────────────────
//
// What follows is AM5's list of error shapes. It decides nothing since AM6:
// what a person is shown is decided by reads.go, which then asks this list
// once more (LooksLikeError) and withholds what it knows — it can only ever
// take more out. writeErr, the last line of an HTTP message, still uses it.

// errorText is what an error looks like: an exception or its type, a stack
// frame, a transport or database error, an address.
var errorText = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\btraceback\b|\bstack ?trace\b|\bpanic:|\bgoroutine \d|\bnil pointer\b|\bsegmentation fault\b|\berrno\b`),
	regexp.MustCompile(`\b[A-Z][A-Za-z]*(?:Error|Exception|Warning)\b`),
	regexp.MustCompile(`(?i)\b(?:error|fatal|warning|exception):`),
	regexp.MustCompile(`(?i)\bobject (?:is not|has no)\b|\bNoneType\b|'[A-Za-z_]+' object|\bnot (?:iterable|subscriptable|callable)\b`),
	regexp.MustCompile(`(?i)\b[\w./\\-]+\.(?:py|go|js|ts)\b(?:["']?[:,] ?(?:line )?\d+)?|\bline \d+\b`),
	regexp.MustCompile(`(?i)\bdial tcp\b|\bconnection re(?:fused|set)\b|\bunexpected EOF\b|\bEOF\b|\bcontext (?:canceled|deadline exceeded)\b|\bno such (?:file|host)\b|\bexit (?:status|code) ?\d+|\bbroken pipe\b|\bi/o timeout\b`),
	regexp.MustCompile(`(?i)\bengine step failed\b|\bsandbox:|\bquality gate:|\bconverter(?: \d{3}|:)|\bstopped:|\bno result \(exit|\bjson: |\binvalid character\b|\bcannot unmarshal\b|\bSQLSTATE\b|\bpq: |\bpgx\b`),
	regexp.MustCompile(`(?i)\bdeadlock\b|\bviolates\b|\bconstraint\b|\bsyntax error\b|\bpermission denied\b|\btimed out\b|\bnot found\b|\bundefined\b`),
	regexp.MustCompile(`(?i)\bhttps?://\S+|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b`),
}

// engineWords is what only the engine calls things: a step id, a reason
// code, a finding key; and a sentence in English, which no message of this
// Thai surface is written in.
var engineWords = []*regexp.Regexp{
	// an identifier (file names are taken out first)
	regexp.MustCompile(`\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b`),
	// three lower-case words in a row
	regexp.MustCompile(`\b[a-z]{2,}(?:\s+[a-z]{2,}){2,}\b`),
}

// fileToken is a file's name: whatever it is called, it is the person's own
// (or a deliverable's) and is never read as an identifier.
var fileToken = regexp.MustCompile(`(?i)[^\s,;:()"'“”]+\.(?:xlsx|xlsm|xls|csv|pdf|xml|jpe?g|png|docx?|pptx?|txt|json|zip)\b`)

// LooksLikeError: the text carries an error's own words. (A message that
// names a field of the person's own settings — "categories.meals.per_claim:
// ต้องเป็นจำนวนเงิน" — does not.)
func LooksLikeError(s string) bool {
	s = fileToken.ReplaceAllString(s, " ")
	for _, re := range errorText {
		if re.MatchString(s) {
			return true
		}
	}
	return false
}

// LooksTechnical: the text carries something the AM5 list knows — an error's
// own words, or the engine's names for things.
func LooksTechnical(s string) bool {
	if LooksLikeError(s) {
		return true
	}
	s = fileToken.ReplaceAllString(s, " ")
	for _, re := range engineWords {
		if re.MatchString(s) {
			return true
		}
	}
	return false
}

var parenthetical = regexp.MustCompile(`\s*[(（][^()（）]*[)）]`)

// ForPerson is AM5's repair: a parenthesis that carries what its list knows
// is taken out of its sentence; if what remains still does there is nothing
// to show (""). Only writeErr uses it now.
func ForPerson(s string) string {
	if !LooksTechnical(s) {
		return s
	}
	out := parenthetical.ReplaceAllStringFunc(s, func(p string) string {
		if LooksTechnical(p) {
			return ""
		}
		return p
	})
	// an opening parenthesis whose text was cut before it closed ("(engine step failed: …")
	if i := strings.LastIndexAny(out, "(（"); i >= 0 && !strings.ContainsAny(out[i:], ")）") && LooksTechnical(out[i:]) {
		out = out[:i]
	}
	out = strings.TrimSpace(out)
	if out == "" || LooksTechnical(out) {
		return ""
	}
	return out
}

// TrimText cuts s to at most n bytes without splitting a character.
func TrimText(s string, n int) string {
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n] + "…"
}
