// KnowledgeService — orchestrates KB CRUD, document upload, and the
// extract → chunk → embed → store pipeline.
//
// Synchronous indexing (Phase 3 contract): the upload handler waits
// for IndexDocument to finish before returning. For 50-page PDFs
// this can take ~30s; that's acceptable for now. A background
// worker queue is on the Phase 4 roadmap.
//
// Storage layout mirrors FileService (one directory per tenant per
// KB) so the on-disk shape stays predictable across products.
package service

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"mime/multipart"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// Storage-key prefix for KB documents. Sibling of the
// conversation-files prefix so the bucket / fs root stays organised.
const knowledgeKeyPrefix = "knowledge"

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrKBNotFound         = errors.New("kb: not found")
	ErrKBDocumentNotFound = errors.New("kb: document not found")
	ErrKBDocumentTooLarge = errors.New("kb: document too large")
	ErrKBDocumentEmpty    = errors.New("kb: document empty")
	ErrKBDocumentBadType  = errors.New("kb: document type not allowed")
	ErrKBNameRequired     = errors.New("kb: name required")
)

// ---------------------------------------------------------------------------
// Allowed types for KB ingest. PDFs / DOCX / XLSX / CSV / TXT are
// the formats the existing extract_text.py + the plain readers can
// handle reliably. Images intentionally NOT supported — vision RAG
// is a different design (CLIP embeddings, separate column).
// ---------------------------------------------------------------------------

var allowedKBContentTypes = map[string]bool{
	"application/pdf": true,
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document": true,
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":       true,
	"text/csv":   true,
	"text/plain": true,
}

// TextExtractor is the slice of FileService the KB service needs.
// Tiny interface keeps the dependency direction clean. The
// implementation reads the object via the shared storage.Backend and
// materialises a local copy when the extractor needs one — callers
// here only deal in storage keys.
type TextExtractor interface {
	ExtractTextForStorageKey(ctx context.Context, key, contentType string) (string, bool)
}

// KnowledgeServiceKnobs is the runtime config bag for the KB
// pipeline. StorageRoot is retained for backwards-compat only — the
// active path now goes through the storage.Backend instance.
type KnowledgeServiceKnobs struct {
	StorageRoot      string
	MaxFileSizeBytes int64
	EmbedBatchSize   int
}

type KnowledgeService struct {
	repo      *repository.KnowledgeRepo
	models    *KBModelsClient
	extractor TextExtractor
	storage   storage.Backend
	knobs     KnowledgeServiceKnobs
}

func NewKnowledgeService(
	repo *repository.KnowledgeRepo,
	models *KBModelsClient,
	extractor TextExtractor,
	store storage.Backend,
	knobs KnowledgeServiceKnobs,
) *KnowledgeService {
	if knobs.StorageRoot == "" {
		knobs.StorageRoot = "./data/knowledge"
	}
	if knobs.MaxFileSizeBytes <= 0 {
		knobs.MaxFileSizeBytes = 50 * 1024 * 1024 // 50 MB — bigger than conv-files
	}
	if knobs.EmbedBatchSize <= 0 || knobs.EmbedBatchSize > KBEmbedBatchMax {
		knobs.EmbedBatchSize = KBEmbedBatchMax
	}
	return &KnowledgeService{
		repo:      repo,
		models:    models,
		extractor: extractor,
		storage:   store,
		knobs:     knobs,
	}
}

// ---------------------------------------------------------------------------
// Knowledge bases
// ---------------------------------------------------------------------------

type CreateKBInput struct {
	TenantID     uuid.UUID
	Name         string
	Description  string
	ChunkSize    int
	ChunkOverlap int
}

func (s *KnowledgeService) CreateKB(ctx context.Context, in CreateKBInput) (*model.KnowledgeBase, error) {
	in.Name = strings.TrimSpace(in.Name)
	if in.Name == "" {
		return nil, ErrKBNameRequired
	}
	if len([]rune(in.Name)) > 255 {
		in.Name = string([]rune(in.Name)[:255])
	}
	return s.repo.CreateKB(ctx, &model.KnowledgeBase{
		TenantID:     in.TenantID,
		Name:         in.Name,
		Description:  in.Description,
		ChunkSize:    in.ChunkSize,
		ChunkOverlap: in.ChunkOverlap,
	})
}

func (s *KnowledgeService) ListKBs(ctx context.Context, tenantID uuid.UUID) ([]model.KnowledgeBase, error) {
	return s.repo.ListKBs(ctx, tenantID)
}

func (s *KnowledgeService) GetKB(ctx context.Context, tenantID, id uuid.UUID) (*model.KnowledgeBase, error) {
	kb, err := s.repo.GetKB(ctx, tenantID, id)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrKBNotFound
		}
		return nil, err
	}
	return kb, nil
}

type UpdateKBInput struct {
	Name        *string
	Description *string
	IsActive    *bool
}

func (s *KnowledgeService) UpdateKB(ctx context.Context, tenantID, id uuid.UUID, in UpdateKBInput) error {
	if err := s.repo.UpdateKB(ctx, tenantID, id, in.Name, in.Description, in.IsActive); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrKBNotFound
		}
		return err
	}
	return nil
}

func (s *KnowledgeService) DeleteKB(ctx context.Context, tenantID, id uuid.UUID) error {
	// Pull documents first so we can unlink on-disk blobs after the
	// row is gone. The FK cascade already wipes the DB rows.
	docs, _ := s.repo.ListDocuments(ctx, tenantID, id)
	if err := s.repo.DeleteKB(ctx, tenantID, id); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrKBNotFound
		}
		return err
	}
	for _, d := range docs {
		if d.StoragePath != "" {
			_ = s.storage.Delete(ctx, d.StoragePath)
		}
	}
	return nil
}

func (s *KnowledgeService) ListDocuments(ctx context.Context, tenantID, kbID uuid.UUID) ([]model.KnowledgeDocument, error) {
	if _, err := s.GetKB(ctx, tenantID, kbID); err != nil {
		return nil, err
	}
	return s.repo.ListDocuments(ctx, tenantID, kbID)
}

// ---------------------------------------------------------------------------
// Document upload
// ---------------------------------------------------------------------------

type UploadDocumentInput struct {
	TenantID        uuid.UUID
	KnowledgeBaseID uuid.UUID
	// UserID is the uploading end-user (FIX 2) — forwarded to the gateway
	// so the embedding egress is attributed/audited to them. May be Nil
	// for system callers (indexDocument falls back to the tenant marker).
	UserID       uuid.UUID
	OriginalName string
	ContentType  string
	Size         int64
	Body         multipart.File
}

// UploadDocument validates + persists the blob, creates the row, and
// runs the full extract → chunk → embed → store pipeline before
// returning. The returned row reflects post-index state (status =
// "indexed" on success, "failed" with error_message on failure).
func (s *KnowledgeService) UploadDocument(ctx context.Context, in UploadDocumentInput) (*model.KnowledgeDocument, error) {
	if in.Size <= 0 {
		return nil, ErrKBDocumentEmpty
	}
	if in.Size > s.knobs.MaxFileSizeBytes {
		return nil, fmt.Errorf("%w: %d > %d", ErrKBDocumentTooLarge, in.Size, s.knobs.MaxFileSizeBytes)
	}
	if !allowedKBContentTypes[strings.ToLower(in.ContentType)] {
		return nil, fmt.Errorf("%w: %s", ErrKBDocumentBadType, in.ContentType)
	}
	kb, err := s.GetKB(ctx, in.TenantID, in.KnowledgeBaseID)
	if err != nil {
		return nil, err
	}

	clean := sanitizeFilename(in.OriginalName)
	docID := uuid.New()
	storageName := storageObjectName(docID, clean) // FIX130 C3.4
	key := storage.JoinKey(
		knowledgeKeyPrefix,
		in.TenantID.String(),
		kb.ID.String(),
		storageName,
	)

	if err := s.storage.Upload(ctx, key, in.Body, in.ContentType, in.Size); err != nil {
		return nil, fmt.Errorf("kb storage upload: %w", err)
	}
	written := in.Size

	doc, err := s.repo.CreateDocument(ctx, &model.KnowledgeDocument{
		KnowledgeBaseID:  kb.ID,
		TenantID:         in.TenantID,
		Filename:         storageName,
		OriginalFilename: clean,
		ContentType:      in.ContentType,
		FileSizeBytes:    written,
		StoragePath:      key,
		Status:           "processing",
	})
	if err != nil {
		_ = s.storage.Delete(ctx, key)
		return nil, err
	}
	// Run the indexer synchronously per Phase 3 contract. Errors are
	// recorded on the row but we still return success — the user can
	// see "failed" in the UI and retry via reindex.
	if err := s.indexDocument(ctx, kb, doc, in.UserID); err != nil {
		slog.Warn("kb index failed", "doc_id", doc.ID.String(), "err", err)
	}
	// Re-fetch so the response carries post-index state.
	updated, err := s.repo.GetDocument(ctx, in.TenantID, doc.ID)
	if err != nil {
		return doc, nil
	}
	return updated, nil
}

// DeleteDocument removes the row + blob + cascaded chunks, then
// recomputes the KB's denormalised counters.
func (s *KnowledgeService) DeleteDocument(ctx context.Context, tenantID, kbID, docID uuid.UUID) error {
	doc, err := s.repo.GetDocument(ctx, tenantID, docID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrKBDocumentNotFound
		}
		return err
	}
	if doc.KnowledgeBaseID != kbID {
		return ErrKBDocumentNotFound
	}
	if err := s.repo.DeleteDocument(ctx, tenantID, docID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrKBDocumentNotFound
		}
		return err
	}
	if doc.StoragePath != "" {
		_ = s.storage.Delete(ctx, doc.StoragePath)
	}
	_ = s.repo.RecomputeKBStats(ctx, kbID)
	return nil
}

// ReindexDocument wipes the existing chunks and re-runs the
// pipeline. Used when chunk-size config changes or extraction
// previously failed and the user wants to try again.
func (s *KnowledgeService) ReindexDocument(ctx context.Context, tenantID, kbID, docID uuid.UUID) (*model.KnowledgeDocument, error) {
	doc, err := s.repo.GetDocument(ctx, tenantID, docID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrKBDocumentNotFound
		}
		return nil, err
	}
	if doc.KnowledgeBaseID != kbID {
		return nil, ErrKBDocumentNotFound
	}
	kb, err := s.GetKB(ctx, tenantID, kbID)
	if err != nil {
		return nil, err
	}
	if err := s.repo.DeleteChunksForDocument(ctx, doc.ID); err != nil {
		return nil, err
	}
	_ = s.repo.UpdateDocumentStatus(ctx, doc.ID, "processing", nil, 0, 0)
	// Reindex has no end-user context (may be admin/background); indexDocument
	// falls back to the tenant marker for the embedding egress identity.
	if err := s.indexDocument(ctx, kb, doc, uuid.Nil); err != nil {
		slog.Warn("kb reindex failed", "doc_id", doc.ID.String(), "err", err)
	}
	return s.repo.GetDocument(ctx, tenantID, doc.ID)
}

// ---------------------------------------------------------------------------
// Indexing pipeline
// ---------------------------------------------------------------------------

// indexDocument runs extract → chunk → embed → store. On any failure
// the document's status flips to "failed" with the error message
// surfaced for the UI; the function still returns the error for
// telemetry but the caller treats it as informational.
func (s *KnowledgeService) indexDocument(
	ctx context.Context, kb *model.KnowledgeBase, doc *model.KnowledgeDocument,
	userID uuid.UUID,
) error {
	// FIX132 B — embeddings come from kb-models on this machine; the text
	// does not leave the host (kb_models_client.go).
	_ = userID
	fail := func(msg string) error {
		e := msg
		_ = s.repo.UpdateDocumentStatus(ctx, doc.ID, "failed", &e, 0, 0)
		_ = s.repo.RecomputeKBStats(ctx, kb.ID)
		return errors.New(msg)
	}

	if s.models == nil {
		return fail("the knowledge base models are not configured (KB_MODELS_URL)")
	}

	text, ok := s.extractor.ExtractTextForStorageKey(ctx, doc.StoragePath, doc.ContentType)
	if !ok || strings.TrimSpace(text) == "" {
		return fail("text extraction failed for this file type")
	}

	chunks := ChunkText(text, kb.ChunkSize, kb.ChunkOverlap)
	if len(chunks) == 0 {
		return fail("no chunks produced (document too small or empty after extraction)")
	}

	// Embed in batches and stream into the chunks table to keep peak
	// memory bounded for large documents (hundreds of chunks).
	batchSize := s.knobs.EmbedBatchSize
	total := 0
	for i := 0; i < len(chunks); i += batchSize {
		end := i + batchSize
		if end > len(chunks) {
			end = len(chunks)
		}
		batch := chunks[i:end]
		embs, err := s.models.Embed(ctx, batch, false, KBPriorityIndex)
		if err != nil {
			return fail("embedding failed: " + err.Error())
		}
		rows := make([]model.KnowledgeChunk, 0, len(batch))
		for j, c := range batch {
			emb := embs[j].Dense
			rows = append(rows, model.KnowledgeChunk{
				DocumentID:      doc.ID,
				KnowledgeBaseID: kb.ID,
				TenantID:        doc.TenantID,
				Content:         c,
				Embedding:       emb,
				ChunkIndex:      i + j,
				Metadata: map[string]any{
					"filename": doc.OriginalFilename,
				},
			})
		}
		if err := s.repo.InsertChunkBatch(ctx, rows); err != nil {
			return fail("chunk insert failed: " + err.Error())
		}
		total += len(rows)
	}

	if err := s.repo.UpdateDocumentStatus(ctx, doc.ID, "indexed", nil, total, len(text)); err != nil {
		return err
	}
	_ = s.repo.RecomputeKBStats(ctx, kb.ID)
	return nil
}
