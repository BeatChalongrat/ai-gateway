// Unit tests for BuildRAGContext. The query-side (embedding +
// vector search) is exercised end-to-end against the real pgvector +
// OpenAI stack — mocking it here would only verify our mocks. The
// context-block formatter is the part with hand-written formatting
// logic, so it's where unit-test value sits.
package service

import (
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestBuildRAGContext_Empty(t *testing.T) {
	if got := BuildRAGContext(nil); got != "" {
		t.Errorf("nil chunks → %q, want empty", got)
	}
	if got := BuildRAGContext([]RetrievedChunk{}); got != "" {
		t.Errorf("empty chunks → %q, want empty", got)
	}
}

func TestBuildRAGContext_Shape(t *testing.T) {
	docA := uuid.New()
	docB := uuid.New()
	chunks := []RetrievedChunk{
		{
			Content:    "Our refund policy is 30 days from purchase.",
			Score:      0.92,
			DocumentID: docA,
			Filename:   "faq.pdf",
			ChunkIndex: 2,
		},
		{
			Content:    "Contact support at help@example.com for assistance.",
			Score:      0.85,
			DocumentID: docB,
			Filename:   "handbook.docx",
			ChunkIndex: 5,
		},
	}
	ctx := BuildRAGContext(chunks)
	wants := []string{
		"## Retrieved Knowledge Base Context",
		"Cite your sources using [Source: filename] format",
		"[Source: faq.pdf, section 3]", // ChunkIndex+1
		"[Source: handbook.docx, section 6]",
		"refund policy is 30 days",
		"help@example.com",
	}
	for _, w := range wants {
		if !strings.Contains(ctx, w) {
			t.Errorf("missing %q in:\n%s", w, ctx)
		}
	}
}

// Citation format must remain stable — the front-end MessageBubble
// post-processor matches “\[Source:\s*([^\]]+?)\]“ exactly. If we
// ever ship a context shape that breaks that regex, the citation
// pills disappear silently. This test pins the contract.
func TestBuildRAGContext_CitationRegexCompatible(t *testing.T) {
	chunks := []RetrievedChunk{
		{
			Content:    "...",
			Filename:   "weird name (v2).pdf",
			ChunkIndex: 0,
		},
	}
	ctx := BuildRAGContext(chunks)
	if !strings.Contains(ctx, "[Source: weird name (v2).pdf, section 1]") {
		t.Errorf("citation marker not emitted in expected form:\n%s", ctx)
	}
}
