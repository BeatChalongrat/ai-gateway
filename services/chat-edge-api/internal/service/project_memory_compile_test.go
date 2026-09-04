package service

// FIX58 — the compiler asks for the right memory set.
//
// The repository test proves the QUERY separates projects. This one
// proves the compiler hands it the right project in the first place: a
// perfect filter fed a nil project would read the global set inside a
// project chat and separate nothing.

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func projectCompileCtx(memoryOn bool, projectID *uuid.UUID) CompileContext {
	return CompileContext{
		Model: "qwen3.8-27b-fp8",
		Conv: &model.Conversation{
			ID:            uuid.New(),
			ModelID:       "qwen3.8-27b-fp8",
			MemoryEnabled: memoryOn,
			ProjectID:     projectID,
		},
		UserID:   uuid.New(),
		TenantID: uuid.New(),
	}
}

func TestMemoryBlock_AsksForTheConversationsProject(t *testing.T) {
	projectID := uuid.New()

	t.Run("a project chat asks for that project", func(t *testing.T) {
		fake := &fakeMemoryReader{block: "<saved_user_memory>x</saved_user_memory>"}
		pc := newPromptCompiler(nil, fake, nil, true)
		pc.memoryBlock(context.Background(), projectCompileCtx(true, &projectID))
		if fake.lastProject == nil {
			t.Fatal("the compiler asked for the GLOBAL set inside a project chat")
		}
		if *fake.lastProject != projectID {
			t.Errorf("asked for project %s, want %s", *fake.lastProject, projectID)
		}
	})

	t.Run("a general chat asks for the global set", func(t *testing.T) {
		fake := &fakeMemoryReader{block: "<saved_user_memory>x</saved_user_memory>"}
		pc := newPromptCompiler(nil, fake, nil, true)
		pc.memoryBlock(context.Background(), projectCompileCtx(true, nil))
		if fake.lastProject != nil {
			t.Errorf("a general chat asked for project %s", *fake.lastProject)
		}
	})

	// 2.4 (4) — a temporary chat inside a project reads THAT project's
	// memories and still writes none. The traceless path is the one
	// that must carry the project: sending it nil here would quietly
	// hand a temporary chat the global set, which is both the wrong
	// content and a way around the separation.
	t.Run("a temporary chat in a project reads that project, tracelessly", func(t *testing.T) {
		fake := &fakeMemoryReader{block: "<saved_user_memory>x</saved_user_memory>"}
		pc := newPromptCompiler(nil, fake, nil, true)
		pc.memoryBlock(context.Background(), projectCompileCtx(false, &projectID))
		if fake.tracelessHit != 1 {
			t.Fatalf("traceless reads = %d, want 1 (a temporary chat must not stamp use_count)", fake.tracelessHit)
		}
		if fake.normalCalls != 0 {
			t.Errorf("the recording read path ran %d times in a temporary chat", fake.normalCalls)
		}
		if fake.lastProject == nil || *fake.lastProject != projectID {
			t.Errorf("a temporary project chat did not ask for its project (got %v)", fake.lastProject)
		}
	})
}
