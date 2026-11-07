package service

// FIX132 B — a new knowledge base is created with the rune-based chunking
// defaults (1000 / 150), never the old byte-based 512 / 50.

import (
	"context"
	"testing"
)

func TestFIX132_NewKnowledgeBaseGetsTheCurrentChunkingDefaults(t *testing.T) {
	h := newKBHarness(t)
	if h.kb.ChunkSize != DefaultKBChunkSize || h.kb.ChunkOverlap != DefaultKBChunkOverlap {
		t.Fatalf("new KB chunking %d/%d, want %d/%d", h.kb.ChunkSize, h.kb.ChunkOverlap, DefaultKBChunkSize, DefaultKBChunkOverlap)
	}
	kb, err := h.svc.CreateKB(context.Background(), CreateKBInput{TenantID: h.tenant, UserID: h.user, Name: "custom", ChunkSize: 800, ChunkOverlap: 100})
	if err != nil || kb.ChunkSize != 800 || kb.ChunkOverlap != 100 {
		t.Fatalf("an explicit choice is kept: %+v %v", kb, err)
	}
}
