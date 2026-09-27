package runner

import (
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// The web card already says, under a rememberable question, that the answer
// is kept as next round's default. The card's own note must not repeat it.
func TestBlockingCardCarriesNoRepeatedRememberNote(t *testing.T) {
	if n := cardNote(&am.CardPlan{Kind: "questions", Blocking: true}); n != "" {
		t.Fatalf("a blocking card's note would print twice on screen: %q", n)
	}
	if n := cardNote(&am.CardPlan{Kind: "questions", Blocking: false}); n == "" {
		t.Fatal("a non-blocking card says the run went on with the defaults")
	}
}

// The runner's clock is UTC; the person's day is Thai time. 03:00 in Bangkok
// on 26 Sep is 20:00 UTC on 25 Sep — the files must say the 26th.
func TestRunDateIsTheThaiDay(t *testing.T) {
	r := &Runner{Now: func() time.Time { return time.Date(2026, 9, 25, 20, 0, 0, 0, time.UTC) }}
	rc := &runCtx{r: r}
	if got := rc.today(); got != "2026-09-26" {
		t.Fatalf("run date in Thai time: got %s", got)
	}
}

// AM1b: PDF was not an output (its Thai shaping was wrong). AM2: a PDF is
// the finished report converted by LibreOffice — offered only when the agent
// lists it and the runner has the converter; otherwise a typed request gets
// an honest answer, never a render, and the "more files" card leaves it out.
func TestPDFOnlyWhenTheConverterCanMakeIt(t *testing.T) {
	ask := "ขอเป็น PDF ด้วยครับ" // "ขอเป็น PDF ด้วยครับ"
	if got := detectFormats(ask); len(got) != 0 {
		t.Fatalf("plain words never make a PDF render: %v", got)
	}
	if got := detectFormatsFor(ask, false); len(got) != 0 {
		t.Fatalf("no converter, no PDF: %v", got)
	}
	if got := detectFormatsFor(ask, true); len(got) != 1 || got[0] != "pdf" {
		t.Fatalf("with the converter a PDF request is a render: %v", got)
	}
	if !asksForPDF(ask) || !asksForPDF("พีดีเอฟ") || asksForPDF("Excel") {
		t.Fatal("asksForPDF must recognise a PDF request, and only that")
	}
	has := func(q am.Question) bool {
		for _, o := range q.Options {
			if string(o.Value) == `"pdf"` {
				return true
			}
		}
		return false
	}
	all := []string{"docx", "pptx", "xlsx", "pdf"}
	if has(formatQuestion(all, false)) || !has(formatQuestion(all, true)) {
		t.Fatal("the more-files card offers PDF exactly when it can be made")
	}
	// AM3: an agent that makes one workbook is never offered a report or slides it cannot make
	inv := formatQuestion([]string{"xlsx"}, true)
	if len(inv.Options) != 1 || string(inv.Options[0].Value) != `"xlsx"` || string(inv.Default) != `"xlsx"` {
		t.Fatalf("the invoice agent's more-files card: %+v", inv.Options)
	}
}
