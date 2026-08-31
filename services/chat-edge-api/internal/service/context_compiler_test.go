// FIX33 §6.1 — the compaction logic is pure and provable without a DB:
// trigger at the threshold, the head survives, a manual boundary drops
// what it says, omitted rows are never silent, and the model is told.
package service

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func msg(role, content string) model.Message {
	return model.Message{ID: uuid.New(), Role: role, ContentMasked: content}
}

// seed builds a chronological user/assistant history of n messages whose
// first user message is a standing instruction (the thing that must die
// last) and whose middle carries a fact we can look for.
func seed(n int) []model.Message {
	out := make([]model.Message, 0, n)
	for i := 0; i < n; i++ {
		if i == 0 {
			out = append(out, msg("user", "STANDING INSTRUCTION: end every answer with ผ่าน "+strings.Repeat("x", 200)))
			continue
		}
		role := "assistant"
		if i%2 == 0 {
			role = "user"
		}
		out = append(out, msg(role, fmt.Sprintf("message %d %s", i, strings.Repeat("y", 200))))
	}
	return out
}

func conv() *model.Conversation {
	return &model.Conversation{ID: uuid.New(), ModelID: "gpt-4o-mini", MemoryEnabled: true}
}

func TestPlanHeadPreservingTrim_KeepsFirstExchangeAndNewestTail(t *testing.T) {
	h := seed(40)
	total := estimateHistoryTokens(h, nil)
	plan := planHeadPreservingTrim(h, nil, total/2, nil)
	if plan.headKept != 2 {
		t.Fatalf("head kept = %d, want 2", plan.headKept)
	}
	if plan.kept[0].ID != h[0].ID || plan.kept[1].ID != h[1].ID {
		t.Fatal("the first exchange did not survive")
	}
	if plan.kept[len(plan.kept)-1].ID != h[len(h)-1].ID {
		t.Fatal("the newest message did not survive")
	}
	if plan.dropEnd == 0 || len(plan.kept) >= len(h) {
		t.Fatalf("nothing was dropped: kept=%d of %d", len(plan.kept), len(h))
	}
	// The dropped range is the MIDDLE: right after the head.
	if plan.kept[2].ID != h[plan.dropEnd].ID {
		t.Fatal("the drop did not start immediately after the head")
	}
	if got := estimateHistoryTokens(plan.kept, nil); got > total/2 {
		t.Fatalf("kept %d tokens > budget %d", got, total/2)
	}
}

func TestPlanHeadPreservingTrim_FitsUntouched(t *testing.T) {
	h := seed(6)
	plan := planHeadPreservingTrim(h, nil, estimateHistoryTokens(h, nil), nil)
	if len(plan.kept) != len(h) || plan.dropEnd != 0 {
		t.Fatal("a fitting history must be returned untouched")
	}
}

func TestPlanHeadPreservingTrim_NewestAlwaysShips(t *testing.T) {
	h := seed(4)
	plan := planHeadPreservingTrim(h, nil, 1, nil)
	if plan.kept[len(plan.kept)-1].ID != h[3].ID {
		t.Fatal("the newest message must ship even when it alone overflows")
	}
}

func TestPlanHeadPreservingTrim_ManualBoundaryDropsUpToIt(t *testing.T) {
	h := seed(20)
	boundary := h[10].ID
	plan := planHeadPreservingTrim(h, nil, 1<<20, &boundary) // unlimited budget
	if !plan.boundaryApplied {
		t.Fatal("boundary not applied")
	}
	if plan.headKept != 2 {
		t.Fatalf("head must survive a manual compact: got %d", plan.headKept)
	}
	// kept = head + everything AFTER the boundary
	want := 2 + (20 - 11)
	if len(plan.kept) != want {
		t.Fatalf("kept %d, want %d", len(plan.kept), want)
	}
	if plan.kept[2].ID != h[11].ID {
		t.Fatal("the tail must start right after the boundary")
	}
}

func TestCompile_TriggersAtThresholdNotOverflow(t *testing.T) {
	// No state service: the compaction path still trims (memory off /
	// no extractor), and must not panic (§6.1 "no state → doesn't break").
	c := NewContextCompiler(nil)
	c.SetRatios(0.85, 0.60)
	h := seed(60)
	prefix := "SYSTEM"
	base, _ := c.Compile(context.Background(), CompileInputs{
		Conv: conv(), History: h[:4], BuildPrefix: func() (string, []ContextSource) { return prefix, nil },
	})
	budget := base.Budget.MessagesRemaining
	// Build a history that sits at ~90 % of the budget: above the
	// trigger, BELOW the overflow point.
	per := estimateHistoryTokens(h[:1], nil)
	n := int(float64(budget) * 0.90 / float64(per))
	if n > len(h) {
		t.Skipf("seed too small for this budget (%d msgs needed)", n)
	}
	hist := h[:n]
	tokens := estimateHistoryTokens(hist, nil)
	if tokens > budget {
		t.Fatalf("test setup: history overflows (%d > %d)", tokens, budget)
	}
	out, err := c.Compile(context.Background(), CompileInputs{
		Conv: conv(), History: hist, BuildPrefix: func() (string, []ContextSource) { return prefix, nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	if out.ThresholdTokens != int(float64(budget)*0.85) {
		t.Fatalf("threshold = %d, want %d", out.ThresholdTokens, int(float64(budget)*0.85))
	}
	if !out.Compacted {
		t.Fatalf("expected compaction at %d tokens (threshold %d, budget %d)", tokens, out.ThresholdTokens, budget)
	}
	if out.HeadKept != 2 || out.History[0].ID != hist[0].ID {
		t.Fatal("the first exchange must survive compaction")
	}
	if out.SentTokens > int(float64(budget)*0.60) {
		t.Fatalf("sent %d tokens, want ≤ target %d", out.SentTokens, int(float64(budget)*0.60))
	}
	if !strings.Contains(out.SystemPrefix, "<context_note>") || !strings.Contains(out.SystemPrefix, "do not guess") {
		t.Fatal("the model must be told what is missing, and told not to guess")
	}
	found := false
	for _, s := range out.Sources {
		if s.SourceType == model.SourceTypeCompactedHistory {
			found = true
		}
	}
	if !found {
		t.Fatal("the user must see the compaction marker")
	}
	// Below the threshold: untouched.
	small := h[:4]
	fit, _ := c.Compile(context.Background(), CompileInputs{
		Conv: conv(), History: small, BuildPrefix: func() (string, []ContextSource) { return prefix, nil },
	})
	if fit.Compacted || len(fit.History) != 4 || strings.Contains(fit.SystemPrefix, "<context_note>") {
		t.Fatal("a short history must be returned untouched")
	}
}

func TestCompile_RatioOneRestoresOverflowOnlyBehaviour(t *testing.T) {
	c := NewContextCompiler(nil)
	c.SetRatios(1.0, 1.0)
	h := seed(8)
	out, _ := c.Compile(context.Background(), CompileInputs{
		Conv: conv(), History: h, BuildPrefix: func() (string, []ContextSource) { return "S", nil },
	})
	if out.Compacted {
		t.Fatal("with ratio 1.0 a fitting history must not compact")
	}
}

func TestCompile_OmittedOlderIsNeverSilent(t *testing.T) {
	c := NewContextCompiler(nil)
	h := seed(6) // fits easily
	out, _ := c.Compile(context.Background(), CompileInputs{
		Conv: conv(), History: h, OmittedOlder: 250,
		BuildPrefix: func() (string, []ContextSource) { return "S", nil },
	})
	if !out.Compacted {
		t.Fatal("rows the load ceiling cut must count as compaction")
	}
	if len(out.History) != len(h) {
		t.Fatal("a fitting loaded slice must not be trimmed further")
	}
	label := ""
	for _, s := range out.Sources {
		if s.SourceType == model.SourceTypeCompactedHistory {
			label = s.DisplayLabel
		}
	}
	if !strings.Contains(label, "250") {
		t.Fatalf("marker must name the omitted count, got %q", label)
	}
	if !strings.Contains(out.SystemPrefix, "250 earlier messages") {
		t.Fatal("the context note must name the omitted count")
	}
}

// seedBig — like seed but every message carries `chars` characters, so a
// few hundred messages cross a 100K-token budget.
func seedBig(n, chars int) []model.Message {
	out := seed(n)
	for i := 2; i < len(out); i++ {
		out[i].ContentMasked = fmt.Sprintf("message %d %s", i, strings.Repeat("z", chars))
	}
	return out
}

func TestCompile_MemoryOffStillTrimsAndSaysNoSummary(t *testing.T) {
	c := NewContextCompiler(nil)
	cv := conv()
	cv.MemoryEnabled = false
	h := seedBig(300, 2000) // ≈ 150K tokens ≫ any budget
	out, _ := c.Compile(context.Background(), CompileInputs{
		Conv: cv, History: h, BuildPrefix: func() (string, []ContextSource) { return "S", nil },
	})
	if !out.Compacted || out.ExtractionTriggered {
		t.Fatal("memory off: trim yes, extraction no")
	}
	if !strings.Contains(out.SystemPrefix, "No summary of the older part is available") {
		t.Fatal("the model must be told there is no summary")
	}
}

// FIX33.1 Part 2 — the state rides as a user/assistant pair at the HEAD of
// the sent history (never in the system message), budgeted, only when
// compaction happens — the desktop's shape.
func TestCompile_StateInjectedAsUserAssistantPairAtHead(t *testing.T) {
	c := NewContextCompiler(nil)
	block := "<conversation_state>\nGoal: plan the trip\n" + StateHonestyNote + "\n</conversation_state>\n"
	buildState := func() (string, []ContextSource) {
		return block, []ContextSource{{SourceType: model.SourceTypeConversationState, DisplayLabel: "Goal: plan the trip"}}
	}
	// Fits → no injection, no pair.
	h := seed(6)
	fit, _ := c.Compile(context.Background(), CompileInputs{
		Conv: conv(), History: h, BuildPrefix: func() (string, []ContextSource) { return "S", nil }, BuildState: buildState,
	})
	if fit.StateInjected || fit.History[0].Role != "user" || strings.Contains(fit.History[0].ContentMasked, "<conversation_state>") {
		t.Fatal("no compaction → no injected pair")
	}
	// Compacts → pair first, then the preserved head, then the tail.
	big := seedBig(300, 2000)
	out, _ := c.Compile(context.Background(), CompileInputs{
		Conv: conv(), History: big, BuildPrefix: func() (string, []ContextSource) { return "S", nil }, BuildState: buildState,
	})
	if !out.Compacted || !out.StateInjected {
		t.Fatalf("expected compaction + injection: compacted=%v injected=%v", out.Compacted, out.StateInjected)
	}
	userText, ack := StateInjectionTexts()
	if out.History[0].Role != "user" || !strings.HasPrefix(out.History[0].ContentMasked, userText) || !strings.Contains(out.History[0].ContentMasked, "plan the trip") {
		t.Fatalf("History[0] must be the user preamble + block, got %q", out.History[0].ContentMasked[:60])
	}
	if out.History[1].Role != "assistant" || out.History[1].ContentMasked != ack {
		t.Fatal("History[1] must be the assistant acknowledgement")
	}
	if out.History[2].ID != big[0].ID || out.History[3].ID != big[1].ID {
		t.Fatal("the preserved first exchange must follow the pair")
	}
	if strings.Contains(out.SystemPrefix, "plan the trip") {
		t.Fatal("the summary text must never enter the system message")
	}
	if !strings.Contains(out.SystemPrefix, "A summary of the older part is in <conversation_state>") {
		t.Fatal("the context note must say a summary exists")
	}
	// Budgeted: the pair's tokens are inside the sent total, which stays under target.
	if out.SentTokens > int(float64(out.Budget.MessagesRemaining)*DefaultCompactTargetRatio)+contextNoteReserve {
		t.Fatalf("sent %d tokens exceeds the target with the pair counted", out.SentTokens)
	}
	found := false
	for _, s := range out.Sources {
		if s.SourceType == model.SourceTypeConversationState {
			found = true
		}
	}
	if !found {
		t.Fatal("the state source must be attributed when the pair is injected")
	}
}

func TestStateInjectionTexts_FromAsset(t *testing.T) {
	u, a := StateInjectionTexts()
	if !strings.HasPrefix(u, "[Context from earlier in this conversation") || !strings.HasPrefix(a, "Understood") {
		t.Fatalf("asset not parsed: %q / %q", u, a)
	}
	pair := StateInjectionMessages("<conversation_state>x</conversation_state>")
	if len(pair) != 2 || pair[0].Role != "user" || pair[1].Role != "assistant" || pair[0].ID != uuid.Nil {
		t.Fatal("pair shape")
	}
}

func TestSetRatios_ClampsBadValues(t *testing.T) {
	c := NewContextCompiler(nil)
	c.SetRatios(0, 5)
	if c.TriggerRatio() != DefaultCompactTriggerRatio || c.TargetRatio() != DefaultCompactTargetRatio {
		t.Fatal("bad ratios must fall back to the defaults")
	}
	c.SetRatios(0.5, 0.9)
	if c.TargetRatio() != 0.5 {
		t.Fatal("target must never exceed trigger")
	}
}
