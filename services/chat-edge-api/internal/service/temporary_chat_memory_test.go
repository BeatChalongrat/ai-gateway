package service

// FIX56.1 Part 1 — what a temporary chat (conversation.memory_enabled =
// false) does with the memories already on file.
//
// The decision it pins: READ them, write none. "Don't record this" is what
// the user asked for; making the assistant also forget their language,
// stack and standing instructions turns the mode into one people avoid,
// and a privacy switch nobody turns on protects nobody.
//
// The half that is NOT a preference and must never move is the write side:
// no memory tool is attached to a memory-off conversation, and nothing in
// this file's flag can attach one. TestTemporaryChatNeverGetsWriteTools
// holds that line from the other direction.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// fakeMemoryReader records WHICH build path the compiler took, because
// the difference between them is the whole point: the traceless path
// leaves use_count / last_used_at alone.
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

func tempChatCtx(memoryOn bool) CompileContext {
	return CompileContext{
		Model:    "deepseek-v4-pro",
		Conv:     &model.Conversation{ID: uuid.New(), ModelID: "deepseek-v4-pro", MemoryEnabled: memoryOn},
		UserID:   uuid.New(),
		TenantID: uuid.New(),
	}
}

func TestTemporaryChatReadsMemoryTracelessly(t *testing.T) {
	fake := &fakeMemoryReader{block: "<saved_user_memory>the user writes Thai</saved_user_memory>"}
	pc := newPromptCompiler(nil, fake, nil, true)

	block, sources := pc.memoryBlock(context.Background(), tempChatCtx(false))

	if !strings.Contains(block, "the user writes Thai") {
		t.Errorf("a temporary chat should read the saved memories, got:\n%s", block)
	}
	if len(sources) == 0 {
		t.Error("the read must still be attributed — the user should see that memory was used")
	}
	// The read has to be the traceless one. The ordinary path bumps
	// use_count / last_used_at, which stamps "used just now" on exactly
	// the facts consulted during a chat the user believed was leaving
	// nothing behind — a record of what a private conversation was
	// about, in a table the user can see.
	if fake.tracelessHit != 1 || fake.normalCalls != 0 {
		t.Errorf("temporary chat must take the traceless read path (traceless=%d normal=%d)",
			fake.tracelessHit, fake.normalCalls)
	}
}

func TestOrdinaryChatKeepsTheAccountedRead(t *testing.T) {
	fake := &fakeMemoryReader{block: "<saved_user_memory>x</saved_user_memory>"}
	pc := newPromptCompiler(nil, fake, nil, true)

	pc.memoryBlock(context.Background(), tempChatCtx(true))

	if fake.normalCalls != 1 || fake.tracelessHit != 0 {
		t.Errorf("a normal chat must keep the accounted read (traceless=%d normal=%d)",
			fake.tracelessHit, fake.normalCalls)
	}
}

func TestTemporaryChatReadsNothingWhenTheSettingSaysSo(t *testing.T) {
	fake := &fakeMemoryReader{block: "<saved_user_memory>the user writes Thai</saved_user_memory>"}
	pc := newPromptCompiler(nil, fake, nil, false)

	block, sources := pc.memoryBlock(context.Background(), tempChatCtx(false))

	if strings.Contains(block, "the user writes Thai") {
		t.Errorf("TEMPORARY_CHAT_READS_MEMORY=false must read nothing, got:\n%s", block)
	}
	if len(sources) != 0 {
		t.Error("nothing was read, so nothing may be attributed")
	}
	if fake.normalCalls != 0 || fake.tracelessHit != 0 {
		t.Error("the stricter setting must not touch the memory store at all")
	}
	// Still told it cannot remember. The stricter setting must not make
	// the model MORE likely to promise a save.
	if !strings.Contains(block, "<temporary_chat>") {
		t.Errorf("the notice belongs in every memory-off chat:\n%s", block)
	}
}

func TestTemporaryChatNoticeIsPresentWithNothingToRecall(t *testing.T) {
	// "No memory tool attached" is not the same as "the model knows it
	// cannot remember". Without the notice it answers "got it, I'll
	// remember that" in a chat where nothing can be saved.
	fake := &fakeMemoryReader{block: ""}
	pc := newPromptCompiler(nil, fake, nil, true)

	block, _ := pc.memoryBlock(context.Background(), tempChatCtx(false))

	if !strings.Contains(block, "<temporary_chat>") {
		t.Fatalf("expected the notice, got:\n%s", block)
	}
	if !strings.Contains(block, "no tool that can save anything") {
		t.Errorf("the notice must say saving is impossible, not merely discouraged:\n%s", block)
	}
	// And it must name what IS kept — a user who reads "temporary" as
	// "this conversation disappears" has been misled by us, not by the
	// model (FIX56.1 Part 2: temporary chats stay in history).
	if !strings.Contains(block, "still saved to the user's chat history") {
		t.Errorf("the notice must not imply the transcript disappears:\n%s", block)
	}
}

func TestOrdinaryChatHasNoTemporaryNotice(t *testing.T) {
	fake := &fakeMemoryReader{block: "<saved_user_memory>x</saved_user_memory>"}
	pc := newPromptCompiler(nil, fake, nil, true)

	block, _ := pc.memoryBlock(context.Background(), tempChatCtx(true))

	if strings.Contains(block, "<temporary_chat>") {
		t.Errorf("a normal chat must not be told it cannot remember:\n%s", block)
	}
}

// The line that does not move. Reading is now allowed in a temporary
// chat; writing is not, under either value of the read setting, and the
// tool list is where that is enforced.
func TestTemporaryChatNeverGetsWriteTools(t *testing.T) {
	e := &ToolExecutor{}
	e.states = &ConversationStateService{}

	for _, memoryOn := range []bool{true, false} {
		names := map[string]bool{}
		for _, d := range e.ToolDefs(&model.Conversation{MemoryEnabled: memoryOn}) {
			names[d.Function.Name] = true
		}
		for _, w := range []string{"save_memory", "update_conversation_state"} {
			if names[w] != memoryOn {
				t.Errorf("memory_enabled=%v: tool %q offered=%v, want %v",
					memoryOn, w, names[w], memoryOn)
			}
		}
	}
}
