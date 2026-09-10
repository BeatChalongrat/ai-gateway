// FIX105 Part A — no conversation may reach a state where no trim
// produces a request that fits.
//
// FIX104 measured the way it used to happen: Typhoon answered an
// 824-character question with 18,600 characters of Thai, that first
// exchange was pinned verbatim for the life of the conversation, it alone
// was three times the messages budget, and turn 2 was refused at 16,889
// input tokens — as was every turn after it.
package service

import (
	"context"
	"strings"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func thai(n int) string { return strings.Repeat("ก", n) }

// Typhoon's web budget as measured in FIX104: 3,944 tokens of history.
const typhoonMessagesBudget = 3944

func TestFIX105_OversizedFirstReplyIsNotPinned(t *testing.T) {
	h := []model.Message{msg("user", thai(824)), msg("assistant", thai(18600)), msg("user", thai(852))}
	budget := typhoonMessagesBudget - contextNoteReserve
	plan := planHeadPreservingTrim(h, nil, budget, nil, ratioOf(typhoonMessagesBudget, headPinMaxShare))
	if !plan.headDropped || plan.headKept != 0 {
		t.Fatalf("an 18,600-character first reply must not be pinned: headKept=%d headDropped=%v", plan.headKept, plan.headDropped)
	}
	if len(plan.kept) != 1 || plan.kept[0].ID != h[2].ID {
		t.Fatalf("kept %d messages, want only the newest", len(plan.kept))
	}
	if got := estimateHistoryTokens(plan.kept, nil); got > budget {
		t.Fatalf("kept %d tokens > budget %d: the request still cannot fit", got, budget)
	}
	if plan.dropEnd != 2 {
		t.Fatalf("dropEnd=%d, want 2: the summary must cover the whole first exchange", plan.dropEnd)
	}
}

func TestFIX105_ASmallFirstExchangeIsStillPinned(t *testing.T) {
	h := seed(40)
	budget := estimateHistoryTokens(h, nil) / 2
	plan := planHeadPreservingTrim(h, nil, budget, nil, ratioOf(budget, headPinMaxShare))
	if plan.headKept != 2 || plan.headDropped || plan.kept[0].ID != h[0].ID || plan.kept[1].ID != h[1].ID {
		t.Fatalf("a standing instruction the size of a sentence must still survive: headKept=%d dropped=%v", plan.headKept, plan.headDropped)
	}
}

func TestFIX105_TheHeadGivesWayWhenTheNewestMessageNeedsTheRoom(t *testing.T) {
	// A head well under the cap, a newest message that fits the budget on
	// its own but not beside the head. Pinning would send an over-budget
	// request — the dead end, reached from the other side.
	budget := 1000
	h := []model.Message{
		msg("user", strings.Repeat("a", 600)), msg("assistant", strings.Repeat("b", 400)),
		msg("user", "q"), msg("assistant", "r"),
		msg("user", strings.Repeat("c", 3400)),
	}
	plan := planHeadPreservingTrim(h, nil, budget, nil, budget)
	if !plan.headDropped {
		t.Fatal("the head must give way when keeping it leaves no room for the newest message")
	}
	if got := estimateHistoryTokens(plan.kept, nil); got > budget {
		t.Fatalf("kept %d tokens > budget %d", got, budget)
	}
	if plan.kept[len(plan.kept)-1].ID != h[4].ID {
		t.Fatal("the newest message must ship")
	}
}

func TestFIX105_NoDeadEnd_Swept(t *testing.T) {
	// The invariant itself: whenever the newest message fits the budget on
	// its own, what the plan keeps fits too — whatever the first exchange
	// weighs. And the newest message always ships.
	for _, headChars := range []int{40, 400, 4000, 40000} {
		for _, newestChars := range []int{40, 2000, 3800} {
			for _, budget := range []int{1000, 3944, 14000} {
				h := []model.Message{
					msg("user", strings.Repeat("h", headChars)), msg("assistant", thai(headChars)),
					msg("user", "q1"), msg("assistant", "a1"), msg("user", "q2"), msg("assistant", "a2"),
					msg("user", strings.Repeat("n", newestChars)),
				}
				plan := planHeadPreservingTrim(h, nil, budget, nil, ratioOf(budget, headPinMaxShare))
				newest := estimateHistoryTokens(h[len(h)-1:], nil)
				if kept := estimateHistoryTokens(plan.kept, nil); newest <= budget && kept > budget {
					t.Errorf("head=%d newest=%d budget=%d: kept %d tokens > budget (headKept=%d)", headChars, newestChars, budget, kept, plan.headKept)
				}
				if plan.kept[len(plan.kept)-1].ID != h[len(h)-1].ID {
					t.Errorf("head=%d newest=%d budget=%d: the newest message did not ship", headChars, newestChars, budget)
				}
			}
		}
	}
}

func TestFIX105_CompileSaysTheHeadWentAndNamesAnOversizedMessage(t *testing.T) {
	c := NewContextCompiler(nil)
	c.SetRatios(0.85, 0.60)
	cv := &model.Conversation{ModelID: "Typhoon-S-ThaiLLM-8B-Instruct", MemoryEnabled: true}
	prefix := func() (string, []ContextSource) { return "SYSTEM", nil }

	h := []model.Message{msg("user", thai(824)), msg("assistant", thai(18600)), msg("user", thai(852))}
	out, err := c.Compile(context.Background(), CompileInputs{Conv: cv, History: h, BuildPrefix: prefix})
	if err != nil {
		t.Fatal(err)
	}
	if !out.Compacted || !out.HeadDropped || out.HeadKept != 0 || out.NewestOverBudget {
		t.Fatalf("compacted=%v headDropped=%v headKept=%d newestOverBudget=%v", out.Compacted, out.HeadDropped, out.HeadKept, out.NewestOverBudget)
	}
	if out.SentTokens > out.Budget.MessagesRemaining {
		t.Fatalf("sent %d > budget %d", out.SentTokens, out.Budget.MessagesRemaining)
	}
	if strings.Contains(out.SystemPrefix, "The first exchange and") {
		t.Fatal("the model must not be told the first exchange is included when it is not")
	}

	huge := []model.Message{msg("user", "hi"), msg("assistant", "hello"), msg("user", strings.Repeat("x", 80000))}
	big, _ := c.Compile(context.Background(), CompileInputs{Conv: cv, History: huge, BuildPrefix: prefix})
	if !big.NewestOverBudget {
		t.Fatal("a 20,000-token message against a 14,500 working limit is over budget on its own")
	}
}

func TestFIX105_MessageTooLongRenamesOnlyTheCaseItDescribes(t *testing.T) {
	cle := &StreamProviderError{Code: "context_length_exceeded", Message: "The conversation is too long for this model."}
	if got := messageTooLongOr(cle, true); got.Code != CodeMessageTooLong || !strings.Contains(got.Message, "This message") {
		t.Fatalf("got %+v", got)
	}
	if cle.Code != "context_length_exceeded" {
		t.Fatal("the original frame must not be mutated")
	}
	if got := messageTooLongOr(cle, false); got.Code != "context_length_exceeded" {
		t.Fatal("a conversation-length refusal must keep its code when the newest message fits")
	}
	other := &StreamProviderError{Code: "provider_timeout"}
	if got := messageTooLongOr(other, true); got.Code != "provider_timeout" {
		t.Fatal("only context-length refusals are renamed")
	}
	if messageTooLongOr(nil, true) != nil {
		t.Fatal("nil stays nil")
	}
}
