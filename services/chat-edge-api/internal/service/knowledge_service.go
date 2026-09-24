// KnowledgeService — knowledge-base CRUD and document upload / delete /
// re-index. Indexing itself (extract → chunk → embed → store) runs in the
// background (knowledge_worker.go).
//
// FIX132 B — an upload stores the file and a 'pending' row and returns at
// once (202); a worker picks the document up, reports progress per batch,
// and marks it indexed or failed with a closed-set error code. Until
// FIX132 the upload request itself extracted and embedded every chunk, one
// HTTP call per chunk, so a 50-page PDF held the request for minutes.
//
// Storage layout mirrors FileService (one directory per tenant per KB) so
// the on-disk shape stays predictable across products.
package service

import (
	"context"
	"errors"
	"fmt"
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

// TextExtractor is the slice of FileService the KB indexer needs: the whole
// text of a stored document, or why it could not be read (FileService's
// closed set: encrypted / scanned / damaged / unknown, or "unsupported").
type TextExtractor interface {
	ExtractKnowledgeText(ctx context.Context, key, contentType string) (text, reason string, ok bool)
}

// KnowledgeServiceKnobs is the runtime config bag for the KB pipeline.
// StorageRoot is retained for backwards-compat only — the active path goes
// through the storage.Backend instance.
type KnowledgeServiceKnobs struct {
	StorageRoot      string
	MaxFileSizeBytes int64
	// EmbedBatchSize — chunks per kb-models embeddings call (≤ 16).
	EmbedBatchSize int
	// ChunkSize / ChunkOverlap — the chunker defaults in runes
	// (KB_CHUNK_SIZE / KB_CHUNK_OVERLAP; 0 = DefaultKBChunkSize/Overlap).
	ChunkSize    int
	ChunkOverlap int
}

// indexControl is the background indexer as the service sees it: wake it
// after an upload, stop its work on a document that was deleted or reset.
type indexControl interface {
	Notify()
	Cancel(docID uuid.UUID)
}

type KnowledgeService struct {
	repo      *repository.KnowledgeRepo
	models    *KBModelsClient
	extractor TextExtractor
	storage   storage.Backend
	knobs     KnowledgeServiceKnobs
	indexer   indexControl
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
	if knobs.ChunkSize <= 0 {
		knobs.ChunkSize = DefaultKBChunkSize
	}
	if knobs.ChunkOverlap < 0 {
		knobs.ChunkOverlap = DefaultKBChunkOverlap
	}
	return &KnowledgeService{
		repo:      repo,
		models:    models,
		extractor: extractor,
		storage:   store,
		knobs:     knobs,
	}
}

// SetIndexer wires the background indexer (knowledge_worker.go).
func (s *KnowledgeService) SetIndexer(c indexControl) { s.indexer = c }

func (s *KnowledgeService) notify() {
	if s.indexer != nil {
		s.indexer.Notify()
	}
}

func (s *KnowledgeService) cancelIndexing(docID uuid.UUID) {
	if s.indexer != nil {
		s.indexer.Cancel(docID)
	}
}

// ---------------------------------------------------------------------------
// Knowledge bases
// ---------------------------------------------------------------------------

type CreateKBInput struct {
	TenantID uuid.UUID
	// UserID is the owner (FIX132 B): a knowledge base is private to the
	// user who created it.
	UserID       uuid.UUID
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
	size, overlap := s.knobs.ChunkSize, s.knobs.ChunkOverlap
	if in.ChunkSize > 0 {
		size, overlap = in.ChunkSize, in.ChunkOverlap
	}
	size, overlap = clampChunking(size, overlap)
	owner := in.UserID
	return s.repo.CreateKB(ctx, &model.KnowledgeBase{
		TenantID:     in.TenantID,
		UserID:       &owner,
		Name:         in.Name,
		Description:  in.Description,
		ChunkSize:    size,
		ChunkOverlap: overlap,
	})
}

func (s *KnowledgeService) ListKBs(ctx context.Context, tenantID uuid.UUID) ([]model.KnowledgeBase, error) {
	return s.repo.ListKBs(ctx, tenantID)
}

// IndexingCounts returns, per knowledge base, how many documents are
// pending / processing / failed.
func (s *KnowledgeService) IndexingCounts(ctx context.Context, kbIDs []uuid.UUID) (map[uuid.UUID]repository.IndexingCounts, error) {
	return s.repo.CountIndexing(ctx, kbIDs)
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
	for _, d := range docs {
		s.cancelIndexing(d.ID)
	}
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
	// UserID is the uploading user (recorded as uploaded_by).
	UserID       uuid.UUID
	OriginalName string
	ContentType  string
	Size         int64
	Body         multipart.File
}

// UploadDocument validates and stores the file, creates the row as
// 'pending', wakes the indexer and returns at once — the handler answers
// 202. The row's progress_done / progress_total and status tell the client
// how indexing is going.
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

	var uploader *uuid.UUID
	if in.UserID != uuid.Nil {
		u := in.UserID
		uploader = &u
	}
	doc, err := s.repo.CreateDocument(ctx, &model.KnowledgeDocument{
		ID:               docID,
		KnowledgeBaseID:  kb.ID,
		TenantID:         in.TenantID,
		Filename:         storageName,
		OriginalFilename: clean,
		ContentType:      strings.ToLower(in.ContentType),
		FileSizeBytes:    in.Size,
		StoragePath:      key,
		Status:           "pending",
		UploadedBy:       uploader,
	})
	if err != nil {
		_ = s.storage.Delete(ctx, key)
		return nil, err
	}
	_ = s.repo.RecomputeKBStats(ctx, kb.ID)
	s.notify()
	if fresh, err := s.repo.GetDocument(ctx, in.TenantID, doc.ID); err == nil {
		return fresh, nil
	}
	return doc, nil
}

// GetDocument returns one document of the knowledge base.
func (s *KnowledgeService) GetDocument(ctx context.Context, tenantID, kbID, docID uuid.UUID) (*model.KnowledgeDocument, error) {
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
	return doc, nil
}

// DeleteDocument stops any indexing of the document, removes the row (its
// chunks cascade) and the blob, then recomputes the KB's counters. A worker
// in another process notices at its next batch: its fenced write matches no
// row. (Cancel first: cancelling after would race a fresh claim.)
func (s *KnowledgeService) DeleteDocument(ctx context.Context, tenantID, kbID, docID uuid.UUID) error {
	doc, err := s.GetDocument(ctx, tenantID, kbID, docID)
	if err != nil {
		return err
	}
	s.cancelIndexing(docID)
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

// ReindexDocument deletes the document's chunks and puts it back in the
// queue as 'pending' (a re-index after a model or chunking change, or a
// retry after a failure). Any indexing of it in progress stops.
func (s *KnowledgeService) ReindexDocument(ctx context.Context, tenantID, kbID, docID uuid.UUID) (*model.KnowledgeDocument, error) {
	if _, err := s.GetDocument(ctx, tenantID, kbID, docID); err != nil {
		return nil, err
	}
	s.cancelIndexing(docID)
	if err := s.repo.ResetDocumentForReindex(ctx, tenantID, docID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrKBDocumentNotFound
		}
		return nil, err
	}
	_ = s.repo.RecomputeKBStats(ctx, kbID)
	s.notify()
	return s.repo.GetDocument(ctx, tenantID, docID)
}
