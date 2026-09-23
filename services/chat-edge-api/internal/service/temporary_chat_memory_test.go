package service

// What a chat whose memory is not active does with the memories on file.
//
// FIX56.1 made a memory-off (temporary) chat READ them "tracelessly".
// FIX130 A2 reverses that: NIGHT2 found answers quoting saved memories in
// chats the user had switched memory off for, with the "saved memory" chip
// on them. Memory off now means off — nothing read, nothing written — and a
// temporary chat is sealed the same way whatever its memory flag says. The
// model is still told memory is off (the notice), with a lead-in that says
// which of the two switches it is.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// fakeMemoryReader records which read path the compiler took. It still
// implements the retired traceless read so a test can prove that path is
// never taken either.
type fakeMemoryReader struct {
	block        string
	normalCalls  int
	tracelessHit int
	// FIX58 — the project the compiler asked for on the LAST call, so a
	// test can assert which memory set a turn was entitled to read.
	// nil means "a general chat"; the pointer is captured rather than
	// the bool so a wrong project id fails as loudly as a missing one.
	lastProject *uuid.UUID
}

func (f *fakeMemoryReader) BuildMemoryContext(ctx context.Context, u, t uuid.UUID) string {
	s, _ := f.BuildMemoryContextWithSources(ctx, u, t, nil)
	return s
}

func (f *fakeMemoryReader) BuildMemoryContextWithSources(
	_ context.Context, _, _ uuid.UUID, projectID *uuid.UUID,
) (string, []ContextSource) {
	f.normalCalls++
	f.lastProject = projectID
	return f.block, []ContextSource{{SourceType: model.SourceTypeSavedMemory, SourceID: "m1"}}
}

func (f *fakeMemoryReader) BuildMemoryContextTraceless(
	_ context.Context, _, _ uuid.UUID, projectID *uuid.UUID,
) (string, []ContextSource) {
	f.tracelessHit++
	f.lastProject = projectID
	return f.block, []ContextSource{{SourceType: model.SourceTypeSavedMemory, SourceID: "m1"}}
}

func memoryChatCtx(memoryOn, temporary bool) CompileContext {
	return CompileContext{
		Model:    "deepseek-v4-pro",
		Conv:     &model.Conversation{ID: uuid.New(), ModelID: "deepseek-v4-pro", MemoryEnabled: memoryOn, IsTemporary: temporary},
		UserID:   uuid.New(),
		TenantID: uuid.New(),
	}
}

func TestFIX130_MemoryNotActiveReadsNothing(t *testing.T) {
	for _, c := range []struct {
		name                string
		memoryOn, temporary bool
		lead                string
	}{
		{"memory off", false, false, memoryOffLead},
		{"temporary chat", false, true, temporaryChatLead},
		{"temporary chat switched on", true, true, temporaryChatLead},
	} {
		t.Run(c.name, func(t *testing.T) {
			fake := &fakeMemoryReader{block: "<saved_user_memory>the user writes Thai</saved_user_memory>"}
			pc := newPromptCompiler(nil, fake, nil)
			block, sources := pc.memoryBlock(context.Background(), memoryChatCtx(c.memoryOn, c.temporary))
			if strings.Contains(block, "the user writes Thai") || len(sources) != 0 {
				t.Fatalf("memory was read (sources=%d):\n%s", len(sources), block)
			}
			if fake.normalCalls != 0 || fake.tracelessHit != 0 {
				t.Fatalf("the memory store was touched (normal=%d traceless=%d)", fake.normalCalls, fake.tracelessHit)
			}
			if !strings.Contains(block, "<temporary_chat>") || !strings.Contains(block, c.lead) {
				t.Fatalf("the model must be told which switch is off (want %q):\n%s", c.lead, block)
			}
		})
	}
}

func TestOrdinaryChatKeepsTheAccountedRead(t *testing.T) {
	fake := &fakeMemoryReader{block: "<saved_user_memory>x</saved_user_memory>"}
	pc := newPromptCompiler(nil, fake, nil)

	block, _ := pc.memoryBlock(context.Background(), memoryChatCtx(true, false))

	if fake.normalCalls != 1 || fake.tracelessHit != 0 {
		t.Errorf("a normal chat must keep the accounted read (traceless=%d normal=%d)",
			fake.tracelessHit, fake.normalCalls)
	}
	if strings.Contains(block, "<temporary_chat>") {
		t.Errorf("a normal chat must not be told it cannot remember:\n%s", block)
	}
}

func TestMemoryOffNoticeSaysNothingCanBeSavedOrRecalled(t *testing.T) {
	// "No memory tool attached" is not the same as "the model knows it
	// cannot remember". Without the notice it answers "got it, I'll
	// remember that" in a chat where nothing can be saved.
	for _, temporary := range []bool{false, true} {
		pc := newPromptCompiler(nil, &fakeMemoryReader{}, nil)
		block, _ := pc.memoryBlock(context.Background(), memoryChatCtx(false, temporary))
		if !strings.Contains(block, "no tool that can save anything") {
			t.Errorf("the notice must say saving is impossible, not merely discouraged:\n%s", block)
		}
		if !strings.Contains(block, "saved memories are not given to you") {
			t.Errorf("the notice must say the saved memories are not available either:\n%s", block)
		}
		// And it must name what IS kept — a user who reads "temporary" as
		// "this conversation disappears" has been misled by us.
		if !strings.Contains(block, "still saved to the user's chat history") {
			t.Errorf("the notice must not imply the transcript disappears:\n%s", block)
		}
	}
	// A temporary chat is never told memory can be switched back on.
	pc := newPromptCompiler(nil, &fakeMemoryReader{}, nil)
	block, _ := pc.memoryBlock(context.Background(), memoryChatCtx(false, true))
	if strings.Contains(block, "turn it back on") {
		t.Errorf("a temporary chat cannot turn memory on:\n%s", block)
	}
}

// The tool list: no memory tool in a chat whose memory is not active.
func TestTemporaryChatNeverGetsMemoryTools(t *testing.T) {
	e := &ToolExecutor{}
	e.states = &ConversationStateService{}

	for _, c := range []struct {
		memoryOn, temporary, want bool
	}{{true, false, true}, {false, false, false}, {true, true, false}, {false, true, false}} {
		names := map[string]bool{}
		for _, d := range e.ToolDefs(&model.Conversation{MemoryEnabled: c.memoryOn, IsTemporary: c.temporary}) {
			names[d.Function.Name] = true
		}
		for _, w := range []string{"save_memory", "list_memories", "update_conversation_state"} {
			if names[w] != c.want {
				t.Errorf("memory_enabled=%v temporary=%v: tool %q offered=%v, want %v",
					c.memoryOn, c.temporary, w, names[w], c.want)
			}
		}
	}
}
