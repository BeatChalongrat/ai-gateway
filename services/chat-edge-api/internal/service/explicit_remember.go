package service

// FIX130 — did the user EXPLICITLY ask to remember something in this turn?
//
// save_memory carries a `source` the model fills in: "user_requested" made
// the row an ACTIVE memory, injected into every later chat, on the model's
// word alone. On the test stack deepseek-v4-flash labelled plain self-
// descriptions ("ผมชื่อเล่นว่าภูผา ทำงานเป็นวิศวกรโยธา…" — no request to
// remember anywhere) as user_requested, three times in one chat. The label is
// provenance, and provenance cannot be the model's claim: anything that can
// steer the model (a fetched page, a tool result, a pasted document) could
// otherwise plant an always-on instruction.
//
// So the server asks the user's own words. ExplicitRememberRequest is the
// ONE matcher: execSaveMemory keeps user_requested (active at once) only
// when the turn's user message matches it, and anything else becomes a
// pending suggestion the user confirms on the Memory page, like the
// extractor's. The desktop host has a port of it; both are tested against
// testdata/explicit_remember_cases.json so the two clients cannot drift.
//
// Plain substring rules on purpose, not regexes: the port has to behave the
// same in Go and JavaScript, and Thai has no word boundaries to lean on.
// The negatives are removed first — Thai words that merely START with จำ
// (จำแนก classify, จำลอง simulate, จำนวน amount, …) and English questions or
// statements ABOUT remembering ("do you remember", "I don't remember") — so
// the positives below cannot fire on them.

import "strings"

var explicitRememberNegatives = []string{
	// Thai compounds that begin with จำ but have nothing to do with memory.
	"จำแนก", "จำลอง", "จำเป็น", "จำหน่าย", "จำกัด", "จำนวน", "จำนอง", "จำพวก",
	"จำเพาะ", "จำเลย", "จำศีล", "จำยอม", "จำใจ", "จำต้อง",
	// Asking whether the assistant remembers is not asking it to.
	"จำได้ไหม", "จำได้มั้ย", "จำได้หรือเปล่า", "จำได้รึเปล่า",
	"do you remember", "did you remember", "don't remember", "dont remember",
	"do not remember", "can't remember", "cant remember", "cannot remember",
	"i remember", "remembered", "remembering", "remember when",
}

var explicitRememberPhrases = []string{
	// Thai
	"จำไว้", "ช่วยจำ", "จำด้วย", "จำนะ", "จำให้", "จดไว้", "จดจำ",
	"บันทึกไว้ว่า", "บันทึกไว้ด้วย", "บันทึกไว้นะ", "บันทึกไว้หน่อย",
	"บันทึกความจำ", "บันทึกลงความจำ", "บันทึกเป็นความจำ", "เก็บไว้ในความจำ",
	"อย่าลืม", "จากนี้ไป", "ตั้งแต่นี้ไป", "ต่อจากนี้ไป",
	// English
	"remember", "don't forget", "dont forget", "do not forget",
	"note that", "take note", "make a note", "keep in mind",
	"save this", "save that", "store this", "memorize", "memorise",
	"for future reference", "from now on", "going forward",
}

// ExplicitRememberRequest reports whether the user's message explicitly asks
// the assistant to remember something (or sets a standing "from now on"
// rule). Stating a fact about oneself is NOT a request: that is what the
// review queue is for.
func ExplicitRememberRequest(text string) bool {
	s := strings.ToLower(strings.ReplaceAll(text, "’", "'"))
	if strings.TrimSpace(s) == "" {
		return false
	}
	for _, n := range explicitRememberNegatives {
		s = strings.ReplaceAll(s, n, " ")
	}
	for _, p := range explicitRememberPhrases {
		if strings.Contains(s, p) {
			return true
		}
	}
	return false
}
