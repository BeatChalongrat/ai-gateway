package repository_test

// FIX132 B — migration 049 and the chunk storage it adds (bge-m3 1024-d
// dense + sparse lexical weights), on a real pgvector database (a private
// clone of the test template).

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

func migration049(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "migrations", "049_kb_local_hybrid.sql"))
	if err != nil {
		t.Fatalf("migration 049: %v", err)
	}
	return string(b)
}

func colType(t *testing.T, pool *pgxpool.Pool, table, col string) string {
	t.Helper()
	var typ *string
	_ = pool.QueryRow(context.Background(), `
		SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
		WHERE a.attrelid = $1::regclass AND a.attname = $2 AND NOT a.attisdropped`,
		table, col).Scan(&typ)
	if typ == nil {
		return ""
	}
	return *typ
}

func TestFIX132_Migration049Shape(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()
	want := map[string]string{
		"chat.knowledge_chunks.embedding":          "vector(1024)",
		"chat.knowledge_chunks.sparse":             "sparsevec(250002)",
		"chat.knowledge_chunks.content_tsv":        "", // measured out: no full-text arm
		"chat.knowledge_documents.progress_done":   "integer",
		"chat.knowledge_documents.progress_total":  "integer",
		"chat.knowledge_documents.error_code":      "character varying(40)",
		"chat.knowledge_documents.uploaded_by":     "uuid",
		"chat.knowledge_documents.indexed_at":      "timestamp with time zone",
		"chat.knowledge_documents.attempts":        "integer",
		"chat.knowledge_documents.next_attempt_at": "timestamp with time zone",
		"chat.knowledge_documents.claimed_at":      "timestamp with time zone",
		"chat.knowledge_bases.user_id":             "uuid",
	}
	for k, w := range want {
		i := strings.LastIndex(k, ".")
		if got := colType(t, pool, k[:i], k[i+1:]); got != w {
			t.Errorf("%s: got %q, want %q", k, got, w)
		}
	}
	var def string
	_ = pool.QueryRow(ctx, `SELECT column_default FROM information_schema.columns
		WHERE table_schema='chat' AND table_name='knowledge_bases' AND column_name='embedding_model'`).Scan(&def)
	if !strings.Contains(def, "bge-m3") {
		t.Errorf("embedding_model default: %q", def)
	}
	var ivf, gin int
	_ = pool.QueryRow(ctx, `SELECT count(*) FROM pg_indexes WHERE schemaname='chat' AND indexname='idx_kc_embedding'`).Scan(&ivf)
	_ = pool.QueryRow(ctx, `SELECT count(*) FROM pg_indexes WHERE schemaname='chat' AND indexname='idx_kc_content_tsv'`).Scan(&gin)
	if ivf != 0 || gin != 0 {
		t.Errorf("want no ivfflat index and no full-text index, got ivfflat=%d gin=%d", ivf, gin)
	}
}

// The old 1536-d vectors are deleted and their documents go back to pending;
// running the migration again changes nothing and does not fail.
func TestFIX132_Migration049ReplacesOldVectorsAndIsIdempotent(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()
	// Put the table back in its pre-049 shape (plus the full-text column an
	// earlier draft of 049 added) and add a 1536-d chunk.
	if _, err := pool.Exec(ctx, `ALTER TABLE chat.knowledge_chunks ALTER COLUMN embedding TYPE vector(1536);
		ALTER TABLE chat.knowledge_chunks ADD COLUMN content_tsv tsvector;
		CREATE INDEX idx_kc_content_tsv ON chat.knowledge_chunks USING GIN (content_tsv)`); err != nil {
		t.Fatal(err)
	}
	tenant := uuid.New()
	var kbID, docID uuid.UUID
	if err := pool.QueryRow(ctx, `INSERT INTO chat.knowledge_bases (tenant_id, name, embedding_model) VALUES ($1,'old','text-embedding-3-small') RETURNING id`, tenant).Scan(&kbID); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, `INSERT INTO chat.knowledge_documents (knowledge_base_id, tenant_id, filename, original_filename, status, chunk_count, indexed_at)
		VALUES ($1,$2,'f','f.pdf','indexed',1,NOW()) RETURNING id`, kbID, tenant).Scan(&docID); err != nil {
		t.Fatal(err)
	}
	old := "[" + strings.TrimSuffix(strings.Repeat("0.01,", 1536), ",") + "]"
	if _, err := pool.Exec(ctx, `INSERT INTO chat.knowledge_chunks (document_id, knowledge_base_id, tenant_id, content, embedding) VALUES ($1,$2,$3,'x',$4::vector)`, docID, kbID, tenant, old); err != nil {
		t.Fatal(err)
	}

	for run := 1; run <= 2; run++ {
		if _, err := pool.Exec(ctx, migration049(t)); err != nil {
			t.Fatalf("049 run %d: %v", run, err)
		}
	}
	if got := colType(t, pool, "chat.knowledge_chunks", "embedding"); got != "vector(1024)" {
		t.Fatalf("embedding column: %s", got)
	}
	if got := colType(t, pool, "chat.knowledge_chunks", "content_tsv"); got != "" {
		t.Fatalf("the draft's full-text column must be dropped, got %s", got)
	}
	var chunks int
	var status string
	var chunkCount int
	var indexedAt *string
	_ = pool.QueryRow(ctx, `SELECT count(*) FROM chat.knowledge_chunks WHERE document_id=$1`, docID).Scan(&chunks)
	_ = pool.QueryRow(ctx, `SELECT status, chunk_count, indexed_at::text FROM chat.knowledge_documents WHERE id=$1`, docID).Scan(&status, &chunkCount, &indexedAt)
	if chunks != 0 || status != "pending" || chunkCount != 0 || indexedAt != nil {
		t.Fatalf("old vectors must go and the document be indexed again: chunks=%d status=%s chunk_count=%d indexed_at=%v", chunks, status, chunkCount, indexedAt)
	}
	var em string
	_ = pool.QueryRow(ctx, `SELECT embedding_model FROM chat.knowledge_bases WHERE id=$1`, kbID).Scan(&em)
	if em != "bge-m3" {
		t.Fatalf("existing knowledge bases now say bge-m3, got %s", em)
	}
}

func TestFIX132_ChunkStoresDenseAndSparse(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()
	repo := repository.NewKnowledgeRepo(pool)
	tenant, owner := uuid.New(), uuid.New()
	kb, err := repo.CreateKB(ctx, &model.KnowledgeBase{TenantID: tenant, UserID: &owner, Name: "คู่มือ"})
	if err != nil {
		t.Fatal(err)
	}
	if kb.EmbeddingModel != "bge-m3" {
		t.Fatalf("new knowledge bases are bge-m3, got %q", kb.EmbeddingModel)
	}
	got, err := repo.GetKB(ctx, tenant, kb.ID)
	if err != nil || got.UserID == nil || *got.UserID != owner {
		t.Fatalf("owner stored: %+v %v", got, err)
	}
	doc, err := repo.CreateDocument(ctx, &model.KnowledgeDocument{
		KnowledgeBaseID: kb.ID, TenantID: tenant, Filename: "a", OriginalFilename: "ราคา.pdf",
		ContentType: "application/pdf", StoragePath: "k", UploadedBy: &owner,
	})
	if err != nil {
		t.Fatal(err)
	}
	if doc.Status != "pending" {
		t.Fatalf("a new document is pending, got %s", doc.Status)
	}
	dense := make([]float32, 1024)
	dense[3] = 1
	err = repo.InsertChunkBatch(ctx, []model.KnowledgeChunk{{
		DocumentID: doc.ID, KnowledgeBaseID: kb.ID, TenantID: tenant,
		Content: "ราคาห้องพัก 1200 บาท", Embedding: dense,
		Sparse: &model.SparseVector{Indices: []int{0, 5, 250001}, Values: []float32{0.25, 1.5, 0.5}},
	}})
	if err != nil {
		t.Fatal(err)
	}
	var dims int
	var sparse string
	var ip float64
	err = pool.QueryRow(ctx, `
		SELECT vector_dims(embedding), sparse::text,
		       -(sparse <#> '{6:2}/250002'::sparsevec)
		FROM chat.knowledge_chunks WHERE document_id = $1`, doc.ID).Scan(&dims, &sparse, &ip)
	if err != nil {
		t.Fatal(err)
	}
	if dims != 1024 {
		t.Errorf("dense dims %d", dims)
	}
	// token ids are 0-based, pgvector's sparse indices 1-based.
	if sparse != "{1:0.25,6:1.5,250002:0.5}/250002" {
		t.Errorf("sparse stored as %s", sparse)
	}
	if ip < 2.99 || ip > 3.01 {
		t.Errorf("sparse inner product = %v, want 1.5*2", ip)
	}
	d, err := repo.GetDocument(ctx, tenant, doc.ID)
	if err != nil || d.UploadedBy == nil || *d.UploadedBy != owner || d.ProgressTotal != 0 || d.ErrorCode != nil {
		t.Fatalf("document columns: %+v %v", d, err)
	}
}
