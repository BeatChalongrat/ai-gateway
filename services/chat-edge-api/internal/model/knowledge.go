package model

import (
	"time"

	"github.com/google/uuid"
)

// KnowledgeBase maps to “chat.knowledge_bases“.
//
// One row per logical "library" of documents inside a tenant. A
// conversation can bind to zero or one knowledge base (the binding
// lives on chat.conversations.knowledge_base_id). The same KB can
// be referenced by many conversations across a tenant.
//
// “DocumentCount“ and “TotalChunks“ are denormalised counters so
// the sidebar can render stats without an aggregate query per row;
// the indexing service updates them after each (un)index.
type KnowledgeBase struct {
	ID             uuid.UUID
	TenantID       uuid.UUID
	Name           string
	Description    string
	EmbeddingModel string
	ChunkSize      int
	ChunkOverlap   int
	IsActive       bool
	DocumentCount  int
	TotalChunks    int
	CreatedAt      time.Time
	UpdatedAt      time.Time
}

// KnowledgeDocument maps to “chat.knowledge_documents“.
//
// Lifecycle:
//
//	pending     fresh row, file written to disk, nothing indexed yet
//	processing  extraction + chunking + embedding in progress
//	indexed     all chunks live in chat.knowledge_chunks
//	failed      something blew up — ErrorMessage carries the reason
type KnowledgeDocument struct {
	ID               uuid.UUID
	KnowledgeBaseID  uuid.UUID
	TenantID         uuid.UUID
	Filename         string // sanitized, on-disk name
	OriginalFilename string // what the user typed
	ContentType      string
	FileSizeBytes    int64
	StoragePath      string
	Status           string // pending | processing | indexed | failed
	ChunkCount       int
	CharCount        int
	ErrorMessage     *string
	CreatedAt        time.Time
	UpdatedAt        time.Time
}

// KnowledgeChunk maps to “chat.knowledge_chunks“.
//
// “Embedding“ is the pgvector-encoded text embedding. The repo
// serializes it to “[f1,f2,...]“ form for the pgvector column on
// insert and parses the same form back on read. Float32 is the
// pgvector-native type; converting to float64 would be lossy.
type KnowledgeChunk struct {
	ID              uuid.UUID
	DocumentID      uuid.UUID
	KnowledgeBaseID uuid.UUID
	TenantID        uuid.UUID
	Content         string
	Embedding       []float32
	ChunkIndex      int
	Metadata        map[string]any
	CreatedAt       time.Time
}
