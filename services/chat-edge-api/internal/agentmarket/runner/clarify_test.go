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
