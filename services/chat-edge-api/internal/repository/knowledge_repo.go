// KnowledgeRepo persists the RAG storage layer:
//
//	chat.knowledge_bases    one library per owner (FIX132 B: user_id)
//	chat.knowledge_documents one row per uploaded file
//	chat.knowledge_chunks    passages: bge-m3 vector(1024) + sparsevec
//
// pgvector wire format note: the “vector“ column round-trips as a
// textual “[f1,f2,...]“ form on both insert and read. We format /
// parse manually so we don't pull in the pgvector-go adapter just
// to push float32 slices through pgx. Float32 is the native pgvector
// type; using float64 here would force a precision-losing convert
// inside the database.
package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type KnowledgeRepo struct {
	db DB
	// pool opens the short transactions the indexer needs (FIX132 B);
	// nil on a WithTx copy.
	pool *pgxpool.Pool
}

func NewKnowledgeRepo(db *pgxpool.Pool) *KnowledgeRepo {
	return &KnowledgeRepo{db: db, pool: db}
}

func (r *KnowledgeRepo) WithTx(tx pgx.Tx) *KnowledgeRepo {
	return &KnowledgeRepo{db: tx}
}

// ---------------------------------------------------------------------------
// Knowledge bases
// ---------------------------------------------------------------------------

func (r *KnowledgeRepo) CreateKB(ctx context.Context, kb *model.KnowledgeBase) (*model.KnowledgeBase, error) {
	const q = `
		INSERT INTO chat.knowledge_bases
			(tenant_id, name, description, embedding_model, chunk_size, chunk_overlap, user_id)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		RETURNING id, is_active, document_count, total_chunks, created_at, updated_at
	`
	if kb.EmbeddingModel == "" {
		kb.EmbeddingModel = "bge-m3" // FIX132 B — kb-models, on this machine
	}
	if kb.ChunkSize == 0 {
		kb.ChunkSize = 512
	}
	if kb.ChunkOverlap == 0 {
		kb.ChunkOverlap = 50
	}
	err := r.db.QueryRow(ctx, q,
		kb.TenantID, kb.Name, kb.Description,
		kb.EmbeddingModel, kb.ChunkSize, kb.ChunkOverlap, kb.UserID,
	).Scan(&kb.ID, &kb.IsActive, &kb.DocumentCount, &kb.TotalChunks, &kb.CreatedAt, &kb.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("create kb: %w", err)
	}
	return kb, nil
}

func (r *KnowledgeRepo) ListKBs(ctx context.Context, tenantID uuid.UUID) ([]model.KnowledgeBase, error) {
	q := `SELECT ` + kbColumns + `
		FROM chat.knowledge_bases
		WHERE tenant_id = $1
		ORDER BY created_at DESC
	`
	rows, err := r.db.Query(ctx, q, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list kbs: %w", err)
	}
	defer rows.Close()
	out := make([]model.KnowledgeBase, 0)
	for rows.Next() {
		var kb model.KnowledgeBase
		if err := scanKB(rows, &kb); err != nil {
			return nil, fmt.Errorf("list kbs scan: %w", err)
		}
		out = append(out, kb)
	}
	return out, rows.Err()
}

func (r *KnowledgeRepo) GetKB(ctx context.Context, tenantID, id uuid.UUID) (*model.KnowledgeBase, error) {
	q := `SELECT ` + kbColumns + `
		FROM chat.knowledge_bases
		WHERE id = $1 AND tenant_id = $2
	`
	kb := &model.KnowledgeBase{}
	err := scanKB(r.db.QueryRow(ctx, q, id, tenantID), kb)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get kb: %w", err)
	}
	return kb, nil
}

// UpdateKB applies a sparse patch. Each pointer that's non-nil
// becomes a SET clause. Returns ErrNotFound if no row matched.
func (r *KnowledgeRepo) UpdateKB(
	ctx context.Context, tenantID, id uuid.UUID,
	name, description *string, isActive *bool,
) error {
	sets := make([]string, 0, 4)
	args := make([]any, 0, 6)
	args = append(args, id, tenantID)
	idx := 3
	if name != nil {
		sets = append(sets, fmt.Sprintf("name = $%d", idx))
		args = append(args, *name)
		idx++
	}
	if description != nil {
		sets = append(sets, fmt.Sprintf("description = $%d", idx))
		args = append(args, *description)
		idx++
	}
	if isActive != nil {
		sets = append(sets, fmt.Sprintf("is_active = $%d", idx))
		args = append(args, *isActive)
		idx++
	}
	if len(sets) == 0 {
		return nil
	}
	sets = append(sets, "updated_at = NOW()")
	q := fmt.Sprintf(
		`UPDATE chat.knowledge_bases SET %s WHERE id = $1 AND tenant_id = $2`,
		strings.Join(sets, ", "),
	)
	tag, err := r.db.Exec(ctx, q, args...)
	if err != nil {
		return fmt.Errorf("update kb: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

func (r *KnowledgeRepo) DeleteKB(ctx context.Context, tenantID, id uuid.UUID) error {
	const q = `DELETE FROM chat.knowledge_bases WHERE id = $1 AND tenant_id = $2`
	tag, err := r.db.Exec(ctx, q, id, tenantID)
	if err != nil {
		return fmt.Errorf("delete kb: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// RecomputeKBStats refreshes the denormalised counters by reading
// the current document + chunk counts. Cheap (two COUNTs) and called
// after every document index / delete / reindex so the sidebar stays
// consistent without an aggregate join on every list query.
func (r *KnowledgeRepo) RecomputeKBStats(ctx context.Context, kbID uuid.UUID) error {
	const q = `
		UPDATE chat.knowledge_bases
		SET document_count = (
		      SELECT COUNT(*) FROM chat.knowledge_documents WHERE knowledge_base_id = $1
		    ),
		    -- FIX132 B: only searchable chunks count (a document being indexed
		    -- has some of its chunks already stored).
		    total_chunks = (
		      SELECT COUNT(*) FROM chat.knowledge_chunks c
		      JOIN chat.knowledge_documents d ON d.id = c.document_id
		      WHERE c.knowledge_base_id = $1 AND d.status = 'indexed'
		    ),
		    updated_at = NOW()
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, kbID); err != nil {
		return fmt.Errorf("recompute kb stats: %w", err)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Knowledge documents
// ---------------------------------------------------------------------------

func (r *KnowledgeRepo) CreateDocument(ctx context.Context, d *model.KnowledgeDocument) (*model.KnowledgeDocument, error) {
	const q = `
		INSERT INTO chat.knowledge_documents
			(id, knowledge_base_id, tenant_id, filename, original_filename,
			 content_type, file_size_bytes, storage_path, status, uploaded_by)
		VALUES (COALESCE($1::uuid, gen_random_uuid()), $2, $3, $4, $5, $6, $7, $8, $9, $10)
		RETURNING id, chunk_count, char_count, created_at, updated_at
	`
	if d.Status == "" {
		d.Status = "pending"
	}
	var id any
	if d.ID != uuid.Nil {
		id = d.ID
	}
	err := r.db.QueryRow(ctx, q, id,
		d.KnowledgeBaseID, d.TenantID, d.Filename, d.OriginalFilename,
		d.ContentType, d.FileSizeBytes, d.StoragePath, d.Status, d.UploadedBy,
	).Scan(&d.ID, &d.ChunkCount, &d.CharCount, &d.CreatedAt, &d.UpdatedAt)
	if err != nil {
		return nil, fmt.Errorf("create document: %w", err)
	}
	return d, nil
}

func (r *KnowledgeRepo) ListDocuments(ctx context.Context, tenantID, kbID uuid.UUID) ([]model.KnowledgeDocument, error) {
	q := `SELECT ` + kbDocColumns + `
		FROM chat.knowledge_documents
		WHERE knowledge_base_id = $1 AND tenant_id = $2
		ORDER BY created_at DESC
	`
	rows, err := r.db.Query(ctx, q, kbID, tenantID)
	if err != nil {
		return nil, fmt.Errorf("list documents: %w", err)
	}
	defer rows.Close()
	out := make([]model.KnowledgeDocument, 0)
	for rows.Next() {
		var d model.KnowledgeDocument
		if err := scanDoc(rows, &d); err != nil {
			return nil, fmt.Errorf("list documents scan: %w", err)
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

func (r *KnowledgeRepo) GetDocument(ctx context.Context, tenantID, id uuid.UUID) (*model.KnowledgeDocument, error) {
	q := `SELECT ` + kbDocColumns + `
		FROM chat.knowledge_documents
		WHERE id = $1 AND tenant_id = $2
	`
	d := &model.KnowledgeDocument{}
	err := scanDoc(r.db.QueryRow(ctx, q, id, tenantID), d)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get document: %w", err)
	}
	return d, nil
}

// UpdateDocumentStatus updates a document after indexing. errMsg
// stores the failure reason (nil for success); chunkCount/charCount
// are the post-index totals.
func (r *KnowledgeRepo) UpdateDocumentStatus(
	ctx context.Context, docID uuid.UUID,
	status string, errMsg *string, chunkCount, charCount int,
) error {
	const q = `
		UPDATE chat.knowledge_documents
		SET status = $2, error_message = $3, chunk_count = $4, char_count = $5,
		    updated_at = NOW()
		WHERE id = $1
	`
	if _, err := r.db.Exec(ctx, q, docID, status, errMsg, chunkCount, charCount); err != nil {
		return fmt.Errorf("update document status: %w", err)
	}
	return nil
}

func (r *KnowledgeRepo) DeleteDocument(ctx context.Context, tenantID, id uuid.UUID) error {
	const q = `DELETE FROM chat.knowledge_documents WHERE id = $1 AND tenant_id = $2`
	tag, err := r.db.Exec(ctx, q, id, tenantID)
	if err != nil {
		return fmt.Errorf("delete document: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// DeleteChunksForDocument removes every chunk for a doc. Called
// during reindex (wipe-and-rewrite) and on the soft-delete cascade
// fallback path. The FK on knowledge_chunks already cascades from
// document deletes; this exists for reindex flows that keep the
// document row.
func (r *KnowledgeRepo) DeleteChunksForDocument(ctx context.Context, docID uuid.UUID) error {
	const q = `DELETE FROM chat.knowledge_chunks WHERE document_id = $1`
	if _, err := r.db.Exec(ctx, q, docID); err != nil {
		return fmt.Errorf("delete chunks for document: %w", err)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Knowledge chunks
// ---------------------------------------------------------------------------

// InsertChunkBatch bulk-inserts a slice of chunks. We build one
// multi-VALUES statement so the round-trip count is constant in the
// chunk count. Caller is responsible for batching at ~20 chunks at a
// time so a single statement doesn't blow up the parser arg cap.
func (r *KnowledgeRepo) InsertChunkBatch(ctx context.Context, chunks []model.KnowledgeChunk) error {
	if len(chunks) == 0 {
		return nil
	}
	const cols = 8
	args := make([]any, 0, len(chunks)*cols)
	rowsSQL := make([]string, 0, len(chunks))
	for i, c := range chunks {
		base := i*cols + 1
		// embedding goes in as the literal ``[f1,f2,...]`` string the
		// pgvector input parser accepts; sparse as ``{i:v,...}/dim``.
		emb := encodeVector(c.Embedding)
		md := c.Metadata
		if md == nil {
			md = map[string]any{}
		}
		mdJSON, _ := json.Marshal(md)
		rowsSQL = append(rowsSQL, fmt.Sprintf(
			"($%d, $%d, $%d, $%d, $%d::vector, $%d::sparsevec, $%d, $%d::jsonb)",
			base, base+1, base+2, base+3, base+4, base+5, base+6, base+7,
		))
		args = append(args,
			c.DocumentID, c.KnowledgeBaseID, c.TenantID,
			c.Content, emb, encodeSparse(c.Sparse), c.ChunkIndex, string(mdJSON),
		)
	}
	q := fmt.Sprintf(`
		INSERT INTO chat.knowledge_chunks
			(document_id, knowledge_base_id, tenant_id, content,
			 embedding, sparse, chunk_index, metadata)
		VALUES %s
	`, strings.Join(rowsSQL, ", "))
	if _, err := r.db.Exec(ctx, q, args...); err != nil {
		return fmt.Errorf("insert chunk batch: %w", err)
	}
	return nil
}

// SearchSimilarChunks does a vector similarity search inside a KB.
// Returns (chunk, score, filename) tuples ordered by similarity
// descending. “threshold“ is the minimum cosine similarity to
// include in the result (1.0 == identical; 0.0 == orthogonal).
//
// pgvector exposes the cosine-distance operator “<=>“ so the
// equivalent similarity is “1 - distance“. The query computes
// both, filters by similarity, and orders by distance ascending
// (== similarity descending).
type RetrievedChunkRow struct {
	Chunk            model.KnowledgeChunk
	Similarity       float64
	DocumentFilename string
}

func (r *KnowledgeRepo) SearchSimilarChunks(
	ctx context.Context, tenantID, kbID uuid.UUID,
	embedding []float32, topK int, threshold float64,
) ([]RetrievedChunkRow, error) {
	if topK <= 0 {
		topK = 5
	}
	emb := encodeVector(embedding)
	const q = `
		SELECT c.id, c.document_id, c.knowledge_base_id, c.tenant_id,
		       c.content, c.chunk_index, c.metadata, c.created_at,
		       1 - (c.embedding <=> $3::vector) AS similarity,
		       d.original_filename
		FROM chat.knowledge_chunks c
		JOIN chat.knowledge_documents d ON d.id = c.document_id
		WHERE c.knowledge_base_id = $1
		  AND c.tenant_id = $2
		  AND c.embedding IS NOT NULL
		ORDER BY c.embedding <=> $3::vector ASC
		LIMIT $4
	`
	rows, err := r.db.Query(ctx, q, kbID, tenantID, emb, topK)
	if err != nil {
		return nil, fmt.Errorf("search similar chunks: %w", err)
	}
	defer rows.Close()
	out := make([]RetrievedChunkRow, 0, topK)
	for rows.Next() {
		var rc RetrievedChunkRow
		var md []byte
		if err := rows.Scan(
			&rc.Chunk.ID, &rc.Chunk.DocumentID, &rc.Chunk.KnowledgeBaseID, &rc.Chunk.TenantID,
			&rc.Chunk.Content, &rc.Chunk.ChunkIndex, &md, &rc.Chunk.CreatedAt,
			&rc.Similarity, &rc.DocumentFilename,
		); err != nil {
			return nil, fmt.Errorf("search scan: %w", err)
		}
		if len(md) > 0 {
			_ = json.Unmarshal(md, &rc.Chunk.Metadata)
		}
		if rc.Similarity < threshold {
			continue
		}
		out = append(out, rc)
	}
	return out, rows.Err()
}

// ---------------------------------------------------------------------------
// Vector helpers
// ---------------------------------------------------------------------------

// encodeVector turns “[]float32“ into the textual pgvector input
// form: “[0.1,0.2,...]“. Empty / nil → empty literal (still valid
// for a vector(1536) column when the column is nullable).
func encodeVector(v []float32) string {
	if len(v) == 0 {
		return "[]"
	}
	var b strings.Builder
	b.Grow(len(v) * 12)
	b.WriteByte('[')
	for i, f := range v {
		if i > 0 {
			b.WriteByte(',')
		}
		// 6-digit precision is plenty for cosine search; smaller text
		// = faster round-trips.
		b.WriteString(strconv.FormatFloat(float64(f), 'f', 6, 32))
	}
	b.WriteByte(']')
	return b.String()
}

// SparseDim is the sparsevec dimension of knowledge_chunks.sparse — bge-m3's
// vocabulary (migration 049).
const SparseDim = 250002

// encodeSparse turns bge-m3 lexical weights into pgvector's sparsevec input
// form. pgvector's indices are 1-BASED, token ids are 0-based: token 5 with
// weight 0.3 is "{6:0.3}". Zero weights are dropped. nil -> NULL.
func encodeSparse(v *model.SparseVector) *string {
	if v == nil {
		return nil
	}
	var b strings.Builder
	b.WriteByte('{')
	n := 0
	for i, id := range v.Indices {
		if i >= len(v.Values) || v.Values[i] == 0 || id < 0 || id >= SparseDim {
			continue
		}
		if n > 0 {
			b.WriteByte(',')
		}
		b.WriteString(strconv.Itoa(id + 1))
		b.WriteByte(':')
		b.WriteString(strconv.FormatFloat(float64(v.Values[i]), 'g', 7, 32))
		n++
	}
	b.WriteString("}/")
	b.WriteString(strconv.Itoa(SparseDim))
	s := b.String()
	return &s
}

// kbColumns / scanKB — one column list for every knowledge_bases read.
const kbColumns = `id, tenant_id, user_id, name, COALESCE(description, ''),
	COALESCE(embedding_model, ''), COALESCE(chunk_size, 0), COALESCE(chunk_overlap, 0),
	COALESCE(is_active, TRUE), COALESCE(document_count, 0), COALESCE(total_chunks, 0),
	created_at, updated_at`

func scanKB(row pgx.Row, kb *model.KnowledgeBase) error {
	return row.Scan(
		&kb.ID, &kb.TenantID, &kb.UserID, &kb.Name, &kb.Description, &kb.EmbeddingModel,
		&kb.ChunkSize, &kb.ChunkOverlap, &kb.IsActive,
		&kb.DocumentCount, &kb.TotalChunks, &kb.CreatedAt, &kb.UpdatedAt,
	)
}

// kbDocColumns / scanDoc — one column list for every knowledge_documents read.
const kbDocColumns = `id, knowledge_base_id, tenant_id, filename, original_filename,
	COALESCE(content_type, ''), COALESCE(file_size_bytes, 0), COALESCE(storage_path, ''),
	COALESCE(status, 'pending'), COALESCE(chunk_count, 0), COALESCE(char_count, 0),
	error_message, created_at, updated_at,
	COALESCE(progress_done, 0), COALESCE(progress_total, 0), error_code, uploaded_by,
	indexed_at, COALESCE(attempts, 0), next_attempt_at, claimed_at`

func scanDoc(row pgx.Row, d *model.KnowledgeDocument) error {
	return row.Scan(
		&d.ID, &d.KnowledgeBaseID, &d.TenantID, &d.Filename, &d.OriginalFilename,
		&d.ContentType, &d.FileSizeBytes, &d.StoragePath, &d.Status,
		&d.ChunkCount, &d.CharCount, &d.ErrorMessage, &d.CreatedAt, &d.UpdatedAt,
		&d.ProgressDone, &d.ProgressTotal, &d.ErrorCode, &d.UploadedBy,
		&d.IndexedAt, &d.Attempts, &d.NextAttemptAt, &d.ClaimedAt,
	)
}
