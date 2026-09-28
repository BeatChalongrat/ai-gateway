package agentmarket

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ── What a person reads when a round does not work (AM5) ─────────────────
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
//  2. Whatever is written to, or read from, the thread passes ForPerson: an
//     exception, a type name, a stack frame, a transport error or a step id
//     is taken out before it is stored or sent — including rows written
//     before this rule existed.
//
// A failed round says four things, in Thai: what happened, what to try
// next, whether the files are safe, and whether anything was charged.

// Failure is a failed round as a person reads it.
type Failure struct {
	Reason   string   `json:"reason"` // the code; the web never shows it
	What     string   `json:"what"`
	Next     string   `json:"next"`
	Files    string   `json:"files"`
	Charged  string   `json:"charged"`
	Credits  float64  `json:"credits"`
	CanRetry bool     `json:"can_retry"`
	Failed   []string `json:"failed_checks,omitempty"`
}

// FailureFacts is what the four sentences depend on besides the reason.
type FailureFacts struct {
	Kind     string  // the run's kind
	Credits  float64 // credits the round has spent (steps that finished before it stopped)
	HasFiles bool    // the round has files of the person's that a retry can use
	KeepDays int     // how long a failed round's files are kept (0 = unknown)
	// FailedChecks are the quality gate's failed checks, in the package's own
	// Thai words ("รายงาน: หัวข้อครบทุกส่วน").
	FailedChecks []string
	// GateWhat and GateNext are the package's own sentences for a round whose
	// completion gate did not pass ("ยังไม่ลงตัว ต่างกัน 21,435.00 บาท") — read
	// from its computed table, never from an error. Draft: the round's files
	// were delivered, marked as a draft.
	GateWhat string
	GateNext string
	Draft    bool
}

type failureCopy struct {
	what  string
	next  string
	retry bool // trying again with the same files can work
}

const (
	nextRetry   = "กด “ลองใหม่ด้วยไฟล์เดิม” ได้เลย ถ้ายังไม่สำเร็จอีกให้กด “แจ้งปัญหา” ผู้ดูแลจะเห็นรอบนี้พร้อมขั้นตอนที่หยุด"
	nextLater   = "รอสักครู่แล้วกด “ลองใหม่ด้วยไฟล์เดิม” ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา”"
	nextNewFile = "ตรวจว่าไฟล์เปิดได้ตามปกติ ไม่ได้ตั้งรหัสผ่าน แล้วส่งไฟล์มาใหม่ หรือกด “ลองใหม่ด้วยไฟล์เดิม”"
	nextDraft   = "เปิดไฟล์ร่างของรอบนี้เพื่อดูสาเหตุ แก้ไขแล้วส่งไฟล์มาใหม่เพื่อเริ่มรอบใหม่"
)

// failureCopies: one entry per reason a run can stop with. A reason without
// an entry reads as the last one ("").
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
	// AM5: the package's own gate. The same files give the same result, so
	// trying again with them is not offered.
	"not_tied":        {"งบกระทบยอดยังไม่ลงตัว ยอดหลังปรับปรุงของธนาคารและของบัญชียังไม่เท่ากัน", nextDraft, false},
	"gate_not_passed": {"งานของรอบนี้ยังไม่ผ่านเกณฑ์ของ agent จึงยังไม่นับว่าสำเร็จ", nextDraft, false},
	"":                {"รอบนี้ทำไม่สำเร็จ", nextRetry, true},
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
		if facts.GateWhat != "" || facts.Draft {
			c = failureCopies["gate_not_passed"]
		}
	}
	// a gate's sentences are the package's, and pass the same guard as
	// everything else a person reads: what is left of an error's text is
	// nothing, and the reason's own sentence stands
	if what := ForPerson(facts.GateWhat); what != "" && !LooksTechnical(what) {
		c.what = what
	}
	if next := ForPerson(facts.GateNext); next != "" && !LooksTechnical(next) {
		c.next = next
	}
	f := Failure{Reason: reason, What: c.what, Next: c.next, Credits: facts.Credits}
	f.CanRetry = c.retry && facts.HasFiles && facts.Kind == KindRun
	if !f.CanRetry && c.retry {
		// nothing of the person's to try again with (a self-test, a follow-up job)
		f.Next = "ลองอีกครั้ง ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา” ผู้ดูแลจะเห็นรอบนี้พร้อมขั้นตอนที่หยุด"
		if facts.Kind == KindRun {
			f.Next = "ส่งไฟล์มาใหม่เพื่อเริ่มรอบนี้อีกครั้ง ถ้ายังไม่สำเร็จให้กด “แจ้งปัญหา”"
		}
	}
	for _, x := range facts.FailedChecks {
		if x = ForPerson(x); x != "" {
			f.Failed = append(f.Failed, x)
		}
	}
	if len(f.Failed) > 0 {
		f.What += " (ข้อที่ไม่ผ่าน: " + strings.Join(f.Failed, " · ") + ")"
	}
	switch {
	case facts.Draft && facts.KeepDays > 0:
		f.Files = fmt.Sprintf("ไฟล์ร่างของรอบนี้ส่งให้แล้ว มีคำว่า “ร่าง” กำกับบนหน้าแรก ไฟล์ที่ส่งมาในรอบนี้ยังอยู่ครบและเก็บไว้ให้อีก %d วัน "+
			"ผลงานของรอบก่อน ๆ ไม่ถูกแตะต้อง", facts.KeepDays)
	case facts.Draft:
		f.Files = "ไฟล์ร่างของรอบนี้ส่งให้แล้ว มีคำว่า “ร่าง” กำกับบนหน้าแรก ไฟล์ที่ส่งมาและผลงานของรอบก่อน ๆ ยังอยู่ครบ"
	case !facts.HasFiles:
		f.Files = "ผลงานของรอบก่อน ๆ ยังอยู่ครบ ไม่มีอะไรถูกลบหรือแก้ไข"
	case facts.KeepDays > 0:
		f.Files = fmt.Sprintf("ไฟล์ที่ส่งมาในรอบนี้ยังอยู่ครบและเก็บไว้ให้อีก %d วัน ผลงานของรอบก่อน ๆ ไม่ถูกแตะต้อง", facts.KeepDays)
	default:
		f.Files = "ไฟล์ที่ส่งมาในรอบนี้และผลงานของรอบก่อน ๆ ยังอยู่ครบ"
	}
	if facts.Credits > 0 {
		f.Charged = "รอบนี้ใช้ไป " + formatCredits(facts.Credits) + " เครดิต สำหรับขั้นตอนที่ทำเสร็จก่อนจะหยุด ขั้นตอนที่ไม่สำเร็จไม่คิดเครดิต"
	} else {
		f.Charged = "รอบนี้ไม่ได้คิดเครดิต"
	}
	return f
}

// Text is the failure as one paragraph (a thread item's text; the web shows
// the four parts on the chat's failed-turn card).
func (f Failure) Text() string {
	return f.What + " — " + f.Next + " " + f.Files + " " + f.Charged
}

func formatCredits(c float64) string {
	s := strconv.FormatFloat(c, 'f', 2, 64)
	s = strings.TrimRight(strings.TrimRight(s, "0"), ".")
	if s == "" {
		return "0"
	}
	return s
}

// ── the guard ────────────────────────────────────────────────────────────

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

// LooksTechnical: the text carries something only an engineer can read — an
// error's own words, or the engine's names for things. This is the rule for
// what the agent says in the thread: a failure, a notice, a progress line, a
// check.
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

// ForPerson returns text a person may read: a parenthesis that carries
// technical detail is taken out of its sentence; if what remains is still
// technical there is nothing to show ("").
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

// GuardItem makes a thread item's payload safe to read, whoever wrote it and
// when. Only the agent's side is touched; what a person typed is theirs.
func GuardItem(kind string, payload json.RawMessage) json.RawMessage {
	switch kind {
	case "agent_text", "notice", "progress", "outputs":
	default:
		return payload
	}
	var p map[string]any
	if err := json.Unmarshal(payload, &p); err != nil {
		return payload
	}
	changed := false
	clean := func(m map[string]any, key, otherwise string) {
		s, ok := m[key].(string)
		if !ok || !LooksTechnical(s) {
			return
		}
		out := ForPerson(s)
		if out == "" {
			out = otherwise
		}
		m[key] = out
		changed = true
	}
	switch kind {
	case "agent_text":
		if tone, _ := p["tone"].(string); tone == "error" {
			reason := ""
			if f, ok := p["failure"].(map[string]any); ok {
				reason, _ = f["reason"].(string)
			}
			clean(p, "text", FailureFor(reason, FailureFacts{Kind: KindRun}).What)
		}
	case "notice":
		clean(p, "text", "")
	case "progress":
		steps, _ := p["steps"].([]any)
		for _, s := range steps {
			if m, ok := s.(map[string]any); ok {
				clean(m, "detail", "")
			}
		}
	case "outputs":
		arts, _ := p["artifacts"].([]any)
		for _, a := range arts {
			am, _ := a.(map[string]any)
			q, _ := am["quality"].(map[string]any)
			checks, _ := q["checks"].([]any)
			for _, c := range checks {
				if m, ok := c.(map[string]any); ok {
					clean(m, "detail", "")
					clean(m, "label", "การตรวจของระบบ")
				}
			}
		}
	}
	if !changed {
		return payload
	}
	out, err := json.Marshal(p)
	if err != nil {
		return payload
	}
	return out
}
