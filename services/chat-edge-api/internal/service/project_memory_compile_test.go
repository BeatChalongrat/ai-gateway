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
		pc := newPromptCompiler(nil, fake, nil)
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
		pc := newPromptCompiler(nil, fake, nil)
		pc.memoryBlock(context.Background(), projectCompileCtx(true, nil))
		if fake.lastProject != nil {
			t.Errorf("a general chat asked for project %s", *fake.lastProject)
		}
	})

	// FIX130 A2 — a memory-off chat inside a project reads nothing at
	// all (FIX56.1 had it read that project's memories "tracelessly").
	t.Run("a memory-off chat in a project reads nothing", func(t *testing.T) {
		fake := &fakeMemoryReader{block: "<saved_user_memory>x</saved_user_memory>"}
		pc := newPromptCompiler(nil, fake, nil)
		pc.memoryBlock(context.Background(), projectCompileCtx(false, &projectID))
		if fake.normalCalls != 0 || fake.tracelessHit != 0 {
			t.Fatalf("a memory-off project chat read memory (normal=%d traceless=%d)", fake.normalCalls, fake.tracelessHit)
		}
	})
}
