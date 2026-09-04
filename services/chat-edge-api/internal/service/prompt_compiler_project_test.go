package service

// Phase-4 project-context injection tests. The regression guard is the
// point: with NO project the compiled prompt must be byte-identical to
// the pre-Phase-4 output; with a project the instructions render inside
// Layer 10, AFTER the user's personal custom instructions.

import (
	"context"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// stubPrefsReader satisfies PreferencesReader with a fixed block.
type stubPrefsReader struct{ block string }

func (s stubPrefsReader) BuildInstructionsBlock(context.Context, uuid.UUID, uuid.UUID) string {
	return s.block
}

func TestLayer10_NoProject_ByteIdentical(t *testing.T) {
	pc := newPromptCompiler(stubPrefsReader{block: "## About the user\nไทย\n"}, nil, nil, true)
	base := CompileContext{Model: "gpt-4o", TenantID: uuid.New(), UserID: uuid.New()}

	withEmpty := base
	withEmpty.ProjectInstructions = ""
	a, _ := pc.Build(context.Background(), base)
	b, _ := pc.Build(context.Background(), withEmpty)
	if a != b {
		t.Fatal("empty ProjectInstructions changed the compiled prompt")
	}
	if strings.Contains(a, "## Project instructions") {
		t.Fatal("project block leaked into a no-project prompt")
	}
}

func TestLayer10_ProjectRendersAfterUserPrefs(t *testing.T) {
	pc := newPromptCompiler(stubPrefsReader{block: "## About the user\nนักการเงิน\n"}, nil, nil, true)
	in := CompileContext{
		Model: "gpt-4o", TenantID: uuid.New(), UserID: uuid.New(),
		ProjectInstructions: "ตอบเป็นทางการ อ้างอิงสัญญาเลขที่เสมอ",
	}
	got, _ := pc.Build(context.Background(), in)

	userIdx := strings.Index(got, "## About the user")
	projIdx := strings.Index(got, "## Project instructions")
	if userIdx < 0 || projIdx < 0 {
		t.Fatalf("blocks missing: user=%d proj=%d\n%s", userIdx, projIdx, got)
	}
	if projIdx < userIdx {
		t.Fatal("project instructions must render AFTER user custom instructions")
	}
	if !strings.Contains(got, "อ้างอิงสัญญาเลขที่เสมอ") {
		t.Fatal("project instruction text missing")
	}
}

func TestLayer10_ProjectWithoutUserPrefs(t *testing.T) {
	pc := newPromptCompiler(stubPrefsReader{block: ""}, nil, nil, true)
	in := CompileContext{
		Model: "gpt-4o", TenantID: uuid.New(), UserID: uuid.New(),
		ProjectInstructions: "โปรเจกต์เดี่ยว",
	}
	got, _ := pc.Build(context.Background(), in)
	if !strings.Contains(got, "## Project instructions\nโปรเจกต์เดี่ยว") {
		t.Fatalf("project block missing without user prefs:\n%s", got)
	}
}

// stubRAG captures which KB id the retrieval was asked for.
type stubRAG struct{ gotKB *uuid.UUID }

func (s *stubRAG) BuildContext(_ context.Context, _, _, kbID uuid.UUID, _ string) string {
	k := kbID
	s.gotKB = &k
	return "ctx"
}

func TestRAGContext_ProjectKBFallbackPriority(t *testing.T) {
	convKB, projKB := uuid.New(), uuid.New()
	mk := func() (*MessageService, *stubRAG) {
		rag := &stubRAG{}
		return &MessageService{ragSvc: rag}, rag
	}

	// (1) conversation KB wins over the project fallback.
	s, rag := mk()
	conv := &model.Conversation{TenantID: uuid.New(), KnowledgeBaseID: &convKB}
	if out := s.ragContext(context.Background(), uuid.New(), conv, "q", &projKB); out != "ctx" {
		t.Fatalf("expected retrieval, got %q", out)
	}
	if rag.gotKB == nil || *rag.gotKB != convKB {
		t.Fatalf("conversation KB must win, asked for %v", rag.gotKB)
	}

	// (2) no conversation KB → the project's KB is used.
	s, rag = mk()
	conv = &model.Conversation{TenantID: uuid.New()}
	if out := s.ragContext(context.Background(), uuid.New(), conv, "q", &projKB); out != "ctx" {
		t.Fatalf("expected fallback retrieval, got %q", out)
	}
	if rag.gotKB == nil || *rag.gotKB != projKB {
		t.Fatalf("project KB fallback not used, asked for %v", rag.gotKB)
	}

	// (3) neither → no retrieval at all.
	s, rag = mk()
	conv = &model.Conversation{TenantID: uuid.New()}
	if out := s.ragContext(context.Background(), uuid.New(), conv, "q", nil); out != "" {
		t.Fatalf("expected no retrieval, got %q", out)
	}
	if rag.gotKB != nil {
		t.Fatal("retrieval must not run without any KB")
	}
}
