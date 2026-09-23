package service

// FIX130 A2 — "memory off" means off, and a temporary chat neither reads nor
// writes memory. NIGHT2 (and the coordinator on the test stack): a hand-added
// memory was quoted by the answer in a memory-off chat AND in a temporary
// chat, each turn carrying one saved_memory source.
//
// These run against a throwaway database (testdb) through the real
// MessageService compile path and the real save_memory executor.

import (
	"context"
	"strings"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

const fix130Secret = "ชื่อเล่นว่ากะทิ ชอบกาแฟดำ"

// savedMemory stores one ordinary active memory for the fixture's user.
func (f *fix130Fixture) savedMemory(t *testing.T) *model.Memory {
	t.Helper()
	m, err := f.memSvc.CreateMemory(context.Background(), CreateMemoryInput{
		TenantID: f.tenant, UserID: f.user, ScopeType: model.MemoryScopeGlobalUser,
		MemoryType: model.MemoryTypeExplicitFact, Content: fix130Secret,
		SourceType: model.MemorySourceUserExplicit, ActorType: "user",
	})
	if err != nil {
		t.Fatalf("seed memory: %v", err)
	}
	return m
}

func (f *fix130Fixture) service() *MessageService {
	s := NewMessageService(f.cr, f.mr, nil, nil, allowAllModels{}, 50)
	s.SetMemoryService(f.memSvc)
	return s
}

func compiledFor(t *testing.T, s *MessageService, f *fix130Fixture, conv *model.Conversation) (string, []ContextSource) {
	t.Helper()
	return s.buildSystemPrefixWithSources(context.Background(), f.tenant, f.user, conv, "สวัสดี", OfficeIntent{}, "deepseek-v4-pro")
}

func savedMemorySources(srcs []ContextSource) int {
	n := 0
	for _, s := range srcs {
		if s.SourceType == model.SourceTypeSavedMemory {
			n++
		}
	}
	return n
}

func TestFIX130_MemoryOffChatReadsNoMemory(t *testing.T) {
	f := newFix130Fixture(t)
	f.savedMemory(t)
	conv := f.chat(t, false, false)
	prompt, srcs := compiledFor(t, f.service(), f, conv)
	if strings.Contains(prompt, fix130Secret) || strings.Contains(prompt, "<saved_user_memory>") {
		t.Fatalf("a chat with memory switched off was given the saved memories:\n%s", prompt)
	}
	if n := savedMemorySources(srcs); n != 0 {
		t.Fatalf("a memory-off turn recorded %d saved_memory sources (the 'used memory' chip)", n)
	}
	if !strings.Contains(prompt, "<temporary_chat>") {
		t.Error("the model must still be told memory is off, or it promises to remember")
	}
}

func TestFIX130_TemporaryChatReadsNoMemoryEvenIfSwitchedOn(t *testing.T) {
	f := newFix130Fixture(t)
	f.savedMemory(t)
	conv := f.chat(t, true, true)
	prompt, srcs := compiledFor(t, f.service(), f, conv)
	if strings.Contains(prompt, fix130Secret) || savedMemorySources(srcs) != 0 {
		t.Fatalf("a temporary chat read memory (sources=%d)", savedMemorySources(srcs))
	}
}

func TestFIX130_MemoryOnChatStillReadsMemory(t *testing.T) {
	f := newFix130Fixture(t)
	f.savedMemory(t)
	conv := f.chat(t, false, true)
	prompt, srcs := compiledFor(t, f.service(), f, conv)
	if !strings.Contains(prompt, fix130Secret) || savedMemorySources(srcs) != 1 {
		t.Fatalf("an ordinary memory-on chat must read memory (sources=%d)", savedMemorySources(srcs))
	}
}

func saveCall(content, source string) GatewayToolCall {
	return GatewayToolCall{ID: "c1", Type: "function", Function: GatewayToolCallFunction{
		Name:      "save_memory",
		Arguments: `{"content":"` + content + `","memory_type":"explicit_fact","source":"` + source + `"}`,
	}}
}

func TestFIX130_SaveMemoryIsRefusedWhenMemoryIsOff(t *testing.T) {
	for _, c := range []struct {
		name                string
		temporary, memoryOn bool
	}{{"memory off", false, false}, {"temporary chat switched on", true, true}} {
		t.Run(c.name, func(t *testing.T) {
			f := newFix130Fixture(t)
			conv := f.chat(t, c.temporary, c.memoryOn)
			s := f.service()
			out := s.toolExec.Execute(context.Background(), saveCall("ผมชื่อบีท", "user_requested"), ToolExecContext{
				TenantID: f.tenant, UserID: f.user, ConversationID: conv.ID, Sources: NewSourcesAccumulator(),
			})
			if n := len(f.rows(t, model.MemoryStatusActive)) + len(f.rows(t, model.MemoryStatusPendingConfirmation)); n != 0 {
				t.Fatalf("save_memory wrote %d row(s) in a chat whose memory is not active (result %q)", n, out)
			}
			if !strings.Contains(out, "REFUSED") {
				t.Errorf("the tool result must say it refused, got %q", out)
			}
		})
	}
}

// Coordinator finding: deepseek-v4-flash called save_memory with source
// "user_requested" for plain self-descriptions and the row went ACTIVE.
// The user's own words decide now; with no request to remember in them, the
// save is a pending suggestion.
func TestFIX130_UnaskedUserRequestedSaveBecomesASuggestion(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, false, true)
	s := f.service()
	out := s.toolExec.Execute(context.Background(), saveCall("ชื่อเล่นคือ ภูผา ทำงานเป็นวิศวกรโยธา", "user_requested"), ToolExecContext{
		TenantID: f.tenant, UserID: f.user, ConversationID: conv.ID, Sources: NewSourcesAccumulator(),
	})
	if active := f.rows(t, model.MemoryStatusActive); len(active) != 0 {
		t.Fatalf("an unasked save became an ACTIVE memory: %q (result %q)", active[0].Content, out)
	}
	pending := f.rows(t, model.MemoryStatusPendingConfirmation)
	if len(pending) != 1 || pending[0].SourceType != model.MemorySourceAssistantInferred {
		t.Fatalf("want one pending assistant_inferred suggestion, got %d (result %q)", len(pending), out)
	}
	if strings.HasPrefix(out, "Saved:") {
		t.Errorf("the model was told it saved something it only suggested: %q", out)
	}
}

func TestFIX130_TemporaryChatCannotSwitchMemoryOn(t *testing.T) {
	f := newFix130Fixture(t)
	conv := f.chat(t, true, false)
	svc := NewConversationService(f.cr, allowAllModels{})
	if err := svc.SetMemoryEnabled(context.Background(), f.tenant, f.user, conv.ID, true); err == nil {
		t.Fatal("memory was switched on in a temporary chat")
	}
	// Switching it off stays allowed (and is a no-op here).
	if err := svc.SetMemoryEnabled(context.Background(), f.tenant, f.user, conv.ID, false); err != nil {
		t.Fatalf("switching memory off failed: %v", err)
	}
}

// search_past_conversations stays available in every chat (past chats are
// the user's own history), but a chat whose memory is not active gets no
// saved-memory hits from it.
func TestFIX130_SearchInAMemoryOffChatReturnsNoSavedMemories(t *testing.T) {
	f := newFix130Fixture(t)
	f.savedMemory(t)
	for _, c := range []struct {
		name                string
		temporary, memoryOn bool
		wantMemory          bool
	}{{"memory on", false, true, true}, {"memory off", false, false, false}, {"temporary", true, true, false}} {
		t.Run(c.name, func(t *testing.T) {
			conv := f.chat(t, c.temporary, c.memoryOn)
			s := f.service()
			s.SetSearchService(NewSearchService(f.mr, f.cr, f.mem))
			call := GatewayToolCall{ID: "s1", Type: "function", Function: GatewayToolCallFunction{
				Name: "search_past_conversations", Arguments: `{"query":"กะทิ"}`,
			}}
			acc := NewSourcesAccumulator()
			out := s.toolExec.Execute(context.Background(), call, ToolExecContext{
				TenantID: f.tenant, UserID: f.user, ConversationID: conv.ID, Sources: acc,
			})
			// The query is echoed in the result; look for the rest of the memory.
			if got := strings.Contains(out, "กาแฟดำ"); got != c.wantMemory {
				t.Fatalf("saved memory in the search result = %v, want %v:\n%s", got, c.wantMemory, out)
			}
			if !c.wantMemory && savedMemorySources(acc.Snapshot()) != 0 {
				t.Fatal("a saved_memory source was recorded in a chat whose memory is off")
			}
		})
	}
}
