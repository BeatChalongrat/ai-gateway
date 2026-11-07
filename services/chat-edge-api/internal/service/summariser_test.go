// FIX102 Part D — a model chosen to write the summary.
package service

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type convsWithModel struct{ modelID string }

func (c convsWithModel) GetByID(_ context.Context, _, id uuid.UUID) (*model.Conversation, error) {
	return &model.Conversation{ID: id, ModelID: c.modelID, MemoryEnabled: true}, nil
}

func thaiTopics(n int) []model.Message {
	out := make([]model.Message, 0, n)
	for i := 0; i < n; i++ {
		role, text := "user", fmt.Sprintf("หัวข้อที่ %d: การออกแบบระบบแจ้งเตือน ขอรายละเอียดเพิ่มเติมด้วยครับ", i/2+1)
		if i%2 == 1 {
			role, text = "assistant", "การออกแบบระบบแจ้งเตือนควรเริ่มจากวิเคราะห์เหตุการณ์ที่ต้องแจ้ง"
		}
		out = append(out, model.Message{ID: uuid.New(), Role: role, ContentMasked: text})
	}
	return out
}

func modelsCalled(gw *fakeGateway) []string {
	out := make([]string, 0, len(gw.calls))
	for _, c := range gw.calls {
		out = append(out, c.Model)
	}
	return out
}

func TestFIX102_TheSummariserWritesTheSummaryWhenConfigured(t *testing.T) {
	gw := &fakeGateway{replies: []string{goalJSON("g")}}
	usage := &fakeUsage{}
	svc := newConversationStateServiceWith(&fakeStates{}, &fakeMessages{all: thaiTopics(6)}, convsWithModel{"Typhoon-S-ThaiLLM-8B-Instruct"}, gw)
	svc.SetCompactionUsageRecorder(usage)
	svc.SetExtractionModel(" qwen3.8-27b-fp8 ")
	if _, folded, err := svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{}); err != nil || folded != 6 {
		t.Fatalf("folded=%d err=%v", folded, err)
	}
	if got := modelsCalled(gw); len(got) != 1 || got[0] != "qwen3.8-27b-fp8" {
		t.Fatalf("called %v, want only the summariser", got)
	}
	if len(usage.rows) != 1 || usage.rows[0].ModelID != "qwen3.8-27b-fp8" {
		t.Fatalf("the bill must name the model that wrote it: %+v", usage.rows)
	}
}

func TestFIX102_ASummariserThatProducesNoSummaryFallsBackToTheChatModel(t *testing.T) {
	for name, first := range map[string]string{
		"the call failed":     "", // fakeGateway: "" is an error
		"the reply chatted":   "Sure! Here is a summary of the conversation…",
		"the reply was empty": "   ",
	} {
		t.Run(name, func(t *testing.T) {
			gw := &fakeGateway{replies: []string{first, goalJSON("g")}}
			st := &fakeStates{}
			svc := newConversationStateServiceWith(st, &fakeMessages{all: thaiTopics(4)}, convsWithModel{AutoModelID}, gw)
			svc.SetExtractionModel("a-model-this-tenant-cannot-use")
			_, folded, _ := svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{ChatModel: "gpt-5.6-terra"})
			if got := modelsCalled(gw); len(got) != 2 || got[1] != "gpt-5.6-terra" {
				t.Fatalf("called %v, want the summariser then the model that answers (not the auto sentinel)", got)
			}
			if folded != 4 || st.row == nil || st.row.SummarizedUpToMessageID == nil {
				t.Fatal("the fallback's summary must be saved and the anchor advanced")
			}
		})
	}
}

func TestFIX102_WithoutASummariserTheModelThatAnswersWritesIt(t *testing.T) {
	gw := &fakeGateway{replies: []string{goalJSON("g"), goalJSON("g")}}
	svc := newConversationStateServiceWith(&fakeStates{}, &fakeMessages{all: thaiTopics(2)}, convsWithModel{AutoModelID}, gw)
	_, _, _ = svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{ChatModel: "qwen3.8-27b-fp8"})
	svc2 := newConversationStateServiceWith(&fakeStates{}, &fakeMessages{all: thaiTopics(2)}, convsWithModel{"Typhoon-S-ThaiLLM-8B-Instruct"}, gw)
	_, _, _ = svc2.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{})
	if got := modelsCalled(gw); len(got) != 2 || got[0] != "qwen3.8-27b-fp8" || got[1] != "Typhoon-S-ThaiLLM-8B-Instruct" {
		t.Fatalf("called %v", got)
	}
}

func TestFIX102_WhenNeitherProducesOneTheStateAndAnchorStay(t *testing.T) {
	msgs := &fakeMessages{all: thaiTopics(6)}
	goal := "keep"
	anchor := msgs.all[1].ID
	st := &fakeStates{row: &model.ConversationState{CurrentGoal: &goal, SummarizedUpToMessageID: &anchor}}
	gw := &fakeGateway{replies: []string{"", "not json"}}
	svc := newConversationStateServiceWith(st, msgs, convsWithModel{"Typhoon-S-ThaiLLM-8B-Instruct"}, gw)
	svc.SetExtractionModel("qwen3.8-27b-fp8")
	upto := msgs.all[4].ID
	_, reached, err := svc.EnsureStateCoversUpTo(context.Background(), uuid.New(), uuid.New(), uuid.New(), &upto, ExtractionOptions{})
	if err != nil || reached || len(gw.calls) != 2 || *st.row.SummarizedUpToMessageID != anchor {
		t.Fatalf("reached=%v calls=%d err=%v", reached, len(gw.calls), err)
	}
}

func TestFIX102_TheSummaryLanguageFollowsTheConversation(t *testing.T) {
	gw := &fakeGateway{replies: []string{goalJSON("g"), goalJSON("g")}}
	svc := newConversationStateServiceWith(&fakeStates{}, &fakeMessages{all: thaiTopics(4)}, fakeConvs{}, gw)
	_, _, _ = svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{})
	body := gw.calls[0].Messages[1].Content.(string)
	if !strings.Contains(body, "This conversation is in Thai. Write every value in the JSON in Thai") {
		t.Fatalf("a Thai thread must be summarised in Thai:\n%s", body[:200])
	}
	svc2 := newConversationStateServiceWith(&fakeStates{}, &fakeMessages{all: history(4)}, fakeConvs{}, gw)
	_, _, _ = svc2.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{})
	if body := gw.calls[1].Messages[1].Content.(string); !strings.Contains(body, "This conversation is in English") {
		t.Fatal("an English thread is summarised in English")
	}
	// The shared system asset is untouched — the desktop byte-compares it.
	if gw.calls[0].Messages[0].Content.(string) != ExtractionSystemPrompt() {
		t.Fatal("the language line must not enter the shared system prompt")
	}
}

func TestFIX102_ExtractionAsksForNoThinking(t *testing.T) {
	gw := &fakeGateway{replies: []string{goalJSON("g")}}
	svc := newConversationStateServiceWith(&fakeStates{}, &fakeMessages{all: history(2)}, fakeConvs{}, gw)
	_, _, _ = svc.GenerateOrUpdateStateOpts(context.Background(), uuid.New(), uuid.New(), uuid.New(), ExtractionOptions{})
	if r := gw.calls[0].Reasoning; r == nil || r.Mode != "off" {
		t.Fatalf("reasoning %+v", r)
	}
}

func TestFIX102_TheCompilerHandsTheSummariserTheModelThatAnswers(t *testing.T) {
	h := seedBig(300, 2000)
	gw := &fakeGateway{replies: []string{goalJSON("g"), goalJSON("g"), goalJSON("g")}}
	svc := newConversationStateServiceWith(&fakeStates{}, &fakeMessages{all: h}, convsWithModel{AutoModelID}, gw)
	cv := conv()
	cv.ModelID = AutoModelID
	_, _ = NewContextCompiler(svc).Compile(context.Background(), CompileInputs{
		Conv: cv, History: h, BudgetModelID: "gpt-5.6-terra",
		BuildPrefix: func() (string, []ContextSource) { return "S", nil },
	})
	if got := modelsCalled(gw); len(got) == 0 || got[0] != "gpt-5.6-terra" {
		t.Fatalf("an auto conversation's summary went to %v, not the model that answers", got)
	}
}
