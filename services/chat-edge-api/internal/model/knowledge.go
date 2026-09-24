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
	ID       uuid.UUID
	TenantID uuid.UUID
	// UserID is the owner (FIX132 B, migration 049): a knowledge base is
	// private to the user who created it. Nil on rows from before 049,
	// which only the admin endpoints reach.
	UserID         *uuid.UUID
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
//	pending     fresh row, file stored, waiting for the indexer (also: waiting
//	            to retry after kb-models was unavailable — NextAttemptAt)
//	processing  a worker holds it (ClaimedAt, renewed per batch);
//	            ProgressDone / ProgressTotal count embedded chunks
//	indexed     all chunks live in chat.knowledge_chunks (IndexedAt)
//	failed      ErrorCode says why, ErrorMessage says it in words
//
// FIX132 B — indexing runs in the background (knowledge_worker.go).
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

	// FIX132 B (migration 049).
	ProgressDone  int
	ProgressTotal int
	// ErrorCode: unsupported_type | too_large | empty_text | encrypted |
	// extract_failed | models_unavailable | internal.
	ErrorCode     *string
	UploadedBy    *uuid.UUID
	IndexedAt     *time.Time
	Attempts      int
	NextAttemptAt *time.Time
	ClaimedAt     *time.Time
}

// Knowledge document error codes (knowledge_documents.error_code).
const (
	KBErrUnsupportedType   = "unsupported_type"
	KBErrTooLarge          = "too_large"
	KBErrEmptyText         = "empty_text"
	KBErrEncrypted         = "encrypted"
	KBErrExtractFailed     = "extract_failed"
	KBErrModelsUnavailable = "models_unavailable"
	KBErrInternal          = "internal"
)

// SparseVector is bge-m3's lexical weights for one text: token ids (0-based,
// ascending) and weights. Stored as pgvector sparsevec (1-based ids).
type SparseVector struct {
	Indices []int
	Values  []float32
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
	// Sparse is bge-m3's lexical weights (FIX132 B); nil = none.
	Sparse *SparseVector
	// Segmented is Content with its words separated by spaces (kb-models
	// /v1/segment); stored as content_tsv = to_tsvector('simple', Segmented).
	// Empty = no full-text entry.
	Segmented  string
	ChunkIndex int
	Metadata   map[string]any
	CreatedAt  time.Time
}
