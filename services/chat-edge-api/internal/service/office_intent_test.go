package service

// FIX112 Part B — the Office plan contracts ship only when asked for.
//
// Pinned: the detector answers Thai and English for each kind, a generic
// "make it a file" lights all three, ordinary chat lights none; the text a
// turn is judged on reaches back two user messages; and the three context
// builders contribute NOTHING for a kind that is neither asked for nor on
// file, while an inspected file still gets its full block regardless.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func TestDetectOfficeIntent_ThaiAndEnglishPerKind(t *testing.T) {
	cases := []struct {
		text string
		want OfficeIntent
	}{
		// Excel
		{"ทำไฟล์ Excel ยอดขายรายเดือนให้หน่อย", OfficeIntent{Excel: true, Doc: false, Pptx: false}},
		{"Make me a spreadsheet of the 12 months of 2026", OfficeIntent{Excel: true}},
		{"อยากได้สเปรดชีตเปรียบเทียบราคา", OfficeIntent{Excel: true}},
		{"export the table to xlsx", OfficeIntent{Excel: true}}, // a named format wins over the generic "export"
		// Word / PDF
		{"ช่วยทำเอกสาร Word ชื่อ 'แผนการตลาด 2569'", OfficeIntent{Excel: false, Doc: true}},
		{"Write me a Word document with a project charter.", OfficeIntent{Doc: true}},
		{"ทำรายงานสรุปผลประจำเดือนเป็น PDF", OfficeIntent{Doc: true}},
		{"Write a complete 40-step user manual so I can hand it to HR.", OfficeIntent{Doc: true}},
		// PowerPoint
		{"ทำสไลด์นำเสนอ 5 หน้าเรื่องพลังงานแสงอาทิตย์", OfficeIntent{Pptx: true}},
		{"Make a PowerPoint deck of 4 slides", OfficeIntent{Pptx: true}},
		{"ขอ presentation แนะนำบริษัท", OfficeIntent{Pptx: true}},
		// generic file ask → every contract, <file_or_reply> decides
		{"สรุปอันนี้แล้วทำเป็นไฟล์ให้หน่อย", OfficeIntent{Excel: true, Doc: true, Pptx: true}},
		{"give it to me as a file", OfficeIntent{Excel: true, Doc: true, Pptx: true}},
		// plain chat → nothing
		{"เขียนเรียงความเรื่องมือถือในอนาคต", OfficeIntent{}},
		{"อธิบายสั้น ๆ ว่าทำไมท้องฟ้าเป็นสีฟ้า", OfficeIntent{}},
		{"Give me a list of five tips for better sleep.", OfficeIntent{}},
		{"", OfficeIntent{}},
	}
	for _, c := range cases {
		if got := DetectOfficeIntent(c.text); got != c.want {
			t.Errorf("%q: got %+v want %+v", c.text, got, c.want)
		}
	}
}

func TestDetectOfficeIntent_IsCaseInsensitive(t *testing.T) {
	if !DetectOfficeIntent("MAKE ME A SPREADSHEET").Excel {
		t.Fatal("upper-case English was not matched")
	}
	if !DetectOfficeIntent("a PowerPoint please").Pptx {
		t.Fatal("mixed-case English was not matched")
	}
}

func userRow(text string) model.Message {
	return model.Message{ID: uuid.New(), Role: "user", ContentMasked: text}
}
func asstRow(text string) model.Message {
	return model.Message{ID: uuid.New(), Role: "assistant", ContentMasked: text}
}

func TestOfficeIntentText_ReachesBackTwoUserMessages(t *testing.T) {
	history := []model.Message{
		userRow("hello"), asstRow("hi"),
		userRow("make me a spreadsheet of sales"), asstRow(":::excel-plan …"),
		userRow("add a total row"), asstRow("done"),
		userRow("and bold the header"),
	}
	got := OfficeIntentText(history, "and bold the header")
	if !strings.Contains(got, "make me a spreadsheet") {
		t.Fatalf("the ask two user turns back should still count; got %q", got)
	}
	if strings.Contains(got, "hello") {
		t.Fatalf("three user turns back is past the lookback; got %q", got)
	}
	if strings.Count(got, "and bold the header") != 1 {
		t.Fatalf("the current message must appear once, got %q", got)
	}
	if !DetectOfficeIntent(got).Excel {
		t.Fatal("the follow-up turn should still carry the Excel intent")
	}
	// Three turns later the ask has aged out — the generated workbook's own
	// file path carries the contract from then on (excelContext).
	later := append(history, asstRow("ok"), userRow("thanks"), asstRow("np"), userRow("what is the capital of Japan?"))
	if DetectOfficeIntent(OfficeIntentText(later, "what is the capital of Japan?")).Any() {
		t.Fatal("an ask three user turns back must not keep shipping the contracts")
	}
}

func TestOfficeIntentText_EmptyCurrentUsesTheNewestUserRow(t *testing.T) {
	history := []model.Message{userRow("first"), asstRow("a"), userRow("ทำสไลด์ให้หน่อย"), asstRow("b")}
	got := OfficeIntentText(history, "")
	if !DetectOfficeIntent(got).Pptx {
		t.Fatalf("regenerate / context-info judge the newest user row; got %q", got)
	}
	if OfficeIntentText(nil, "") != "" {
		t.Fatal("no history, no current → empty")
	}
}

func TestOfficeContexts_NotAskedAndNoFile_ContributeNothing(t *testing.T) {
	s := docGateService(nil, nil)
	ctx := context.Background()
	if got := s.excelContext(ctx, uuid.New(), uuid.New(), false); got != "" {
		t.Fatalf("excel contract shipped without an ask or a file: %d chars", len(got))
	}
	if got := s.docContext(ctx, uuid.New(), uuid.New(), false); got != "" {
		t.Fatalf("doc contract shipped without an ask or a file: %d chars", len(got))
	}
	if got := s.pptxContext(ctx, uuid.New(), uuid.New(), false); got != "" {
		t.Fatalf("pptx contract shipped without an ask or a file: %d chars", len(got))
	}
	// Files of OTHER kinds do not change that.
	s = docGateService([]model.ConversationFile{docxFile()}, nil)
	if got := s.excelContext(ctx, uuid.New(), uuid.New(), false); got != "" {
		t.Fatal("a docx on file must not ship the Excel contract")
	}
}

func TestOfficeContexts_AnInspectedFileShipsItsBlockRegardlessOfTheAsk(t *testing.T) {
	s := docGateService([]model.ConversationFile{docxFile()}, nil)
	got := s.docContext(context.Background(), uuid.New(), uuid.New(), false)
	if !strings.HasPrefix(got, "## Document Context") || !strings.Contains(got, docCapabilitiesPrompt) {
		t.Fatalf("an inspected docx must still get the full block: %q", got[:min(len(got), 80)])
	}
}

func TestOfficeContexts_TheContractsAreTheSizeTheCardSays(t *testing.T) {
	total := len(excelCapabilitiesPrompt) + len(docCapabilitiesPrompt) + len(pptxCapabilitiesPrompt)
	if total < 7000 {
		t.Fatalf("the three contracts total %d chars; the point of the gate is that they are large", total)
	}
	t.Logf("contracts: excel %d, doc %d, pptx %d chars = %d; headerless %d/%d/%d; estimated tokens %d",
		len(excelCapabilitiesPrompt), len(docCapabilitiesPrompt), len(pptxCapabilitiesPrompt), total,
		len(excelCapabilitiesPromptHeaderless), len(docCapabilitiesPromptHeaderless), len(pptxCapabilitiesPromptHeaderless),
		EstimateTokens(excelCapabilitiesPromptHeaderless)+EstimateTokens(docCapabilitiesPromptHeaderless)+EstimateTokens(pptxCapabilitiesPromptHeaderless))
}
