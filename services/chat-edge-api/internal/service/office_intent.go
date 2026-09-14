package service

import (
	"strings"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// Office-file intent detection — FIX112 Part B.
//
// The Excel, Word/PDF and PowerPoint plan contracts (excelCapabilitiesPrompt
// and friends, message_service.go) went out on EVERY web turn, files or no
// files, ask or no ask: 8,029 characters, about two fifths of the whole web
// system prompt, so that "make me a spreadsheet" from scratch would find its
// dialect. That is also what turned "เขียนเรียงความ" into an :::excel-plan on
// a tool-less model (FIX108 added a pointer; the contracts still shipped).
//
// Now a contract ships when one of two things holds: an inspected file of
// that kind is in the conversation (unchanged — excelContext & co.), or the
// user's recent words ask for that kind of file. The second test is this
// file, in the shape of image_intent.go / video_intent.go: keyword lists,
// Thai and English, no ML. Unlike those two it is GENEROUS on purpose — a
// false positive costs a longer prompt on one turn; a false negative is a
// user asking for a workbook and getting prose, the feature gone. So the
// lists include bare nouns ("spreadsheet", "รายงาน", "สไลด์"), a generic
// "make it a file" set that lights all three, and the text checked is the
// newest user message plus the two before it (OfficeIntentText): "add a
// total row" two turns after "make me a spreadsheet" still carries the ask
// until the workbook exists, after which the file path takes over.

// OfficeIntent says which plan contracts the user's words call for.
type OfficeIntent struct {
	Excel bool
	Doc   bool
	Pptx  bool
}

// Any reports whether at least one contract is called for.
func (o OfficeIntent) Any() bool { return o.Excel || o.Doc || o.Pptx }

// officeIntentLookback is how many user messages before the newest one
// still count. Two: a plan card is usually acted on within a turn or two;
// once a generated file exists the file path carries the contract anyway.
const officeIntentLookback = 2

var excelIntentKeywords = []string{
	// Thai
	"excel", "เอ็กเซล", "เอ็กเซ็ล", "สเปรดชีต", "สเปรตชีต", "ตารางคำนวณ", "ไฟล์ตาราง", "ตารางข้อมูล",
	"ชีต", "เวิร์กบุ๊ก", "xlsx", "csv",
	// English
	"spreadsheet", "workbook", "worksheet", ".xlsx", "xlsx", "pivot",
}

var docIntentKeywords = []string{
	// Thai
	"word", "เวิร์ด", "เอกสาร", "รายงาน", "จดหมาย", "บันทึกข้อความ", "สัญญา", "ข้อเสนอ", "คู่มือ", "ใบเสนอราคา", "pdf", "docx",
	// English
	"document", "report", ".docx", "docx", ".pdf", "pdf", "letter", "memo", "contract", "proposal", "manual", "handbook", "quotation", "invoice",
}

var pptxIntentKeywords = []string{
	// Thai
	"powerpoint", "พาวเวอร์พอยต์", "เพาเวอร์พอยต์", "สไลด์", "พรีเซนต์", "พรีเซน", "นำเสนอ", "งานนำเสนอ", "เด็ค", "pptx",
	// English
	"slide", "slides", "deck", "presentation", ".pptx", "pptx", "keynote",
}

// Generic "make it a file" phrases light every contract when no format is
// named: the model gets all three and <file_or_reply> decides.
var anyOfficeIntentKeywords = []string{
	"ทำเป็นไฟล์", "ส่งเป็นไฟล์", "เป็นไฟล์", "ทำไฟล์", "สร้างไฟล์", "ไฟล์ให้", "ดาวน์โหลดไฟล์", "export",
	"make a file", "make it a file", "as a file", "into a file", "save as", "downloadable", "export it",
}

func containsAny(lower string, keywords []string) bool {
	for _, kw := range keywords {
		if strings.Contains(lower, kw) {
			return true
		}
	}
	return false
}

// DetectOfficeIntent reports which Office contracts `text` calls for.
// Case-insensitive; Thai has no case, so lowering is a no-op there.
func DetectOfficeIntent(text string) OfficeIntent {
	lower := strings.ToLower(text)
	if lower == "" {
		return OfficeIntent{}
	}
	out := OfficeIntent{
		Excel: containsAny(lower, excelIntentKeywords),
		Doc:   containsAny(lower, docIntentKeywords),
		Pptx:  containsAny(lower, pptxIntentKeywords),
	}
	// A generic "make it a file" with no format named lights every contract;
	// with a format named ("ทำไฟล์ Excel") the named kind alone is enough.
	if !out.Any() && containsAny(lower, anyOfficeIntentKeywords) {
		return OfficeIntent{Excel: true, Doc: true, Pptx: true}
	}
	return out
}

// OfficeIntentText is what DetectOfficeIntent reads for a turn: `current`
// (the newest user message, masked) followed by the `officeIntentLookback`
// user messages before it in `history`, newest first. When `current` is
// empty the newest user row in history stands in for it (regenerate,
// context-info), and the lookback still counts from there.
func OfficeIntentText(history []model.Message, current string) string {
	parts := make([]string, 0, officeIntentLookback+1)
	remaining := officeIntentLookback
	if strings.TrimSpace(current) != "" {
		parts = append(parts, current)
	} else {
		remaining++ // the newest history row plays the current message
	}
	for i := len(history) - 1; i >= 0 && remaining > 0; i-- {
		if history[i].Role != "user" {
			continue
		}
		if strings.TrimSpace(current) != "" && history[i].ContentMasked == current {
			// The newest row IS the current message (already appended): don't count it twice.
			current = ""
			continue
		}
		parts = append(parts, history[i].ContentMasked)
		remaining--
	}
	return strings.Join(parts, "\n")
}
