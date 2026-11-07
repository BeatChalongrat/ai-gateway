package repository

// FIX132 B — knowledge-base retrieval candidates.
//
// Two arms, each an EXACT scan over the knowledge base's chunks (btree
// idx_kc_kb narrows to the KB; no ANN index — at pilot scale an exact scan
// is milliseconds and never misses; see docs/ops/knowledge-base.md for when
// to add HNSW), and only over chunks of documents whose status is 'indexed':
//
//   - dense:  ORDER BY embedding <=> q LIMIT n            (cosine distance)
//   - sparse: ORDER BY sparse <#> q_sparse LIMIT n, kept only when < 0
//     (<#> is the NEGATIVE inner product: bge-m3's lexical score is
//     Σ q_w·d_w over shared tokens, so a chunk sharing no token scores 0)
//
// For every chunk in the union of the two arms BOTH scores are returned
// (cosine = 1 − distance, lexical = −(sparse <#> q)), so the service can
// fuse by score (cosine + w·lexical — measured better than RRF on the
// FIX132 eval set) without a second round trip.

import (
	"context"
	"fmt"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// KBCandidate is one chunk found by either arm.
type KBCandidate struct {
	ChunkID    uuid.UUID
	DocumentID uuid.UUID
	Filename   string
	ChunkIndex int
	Content    string
	Cosine     float64 // 1 − cosine distance
	Lexical    float64 // bge-m3 sparse inner product (0 when no shared token)
	DenseRank  int     // 1-based rank in the dense arm; 0 = not in it
	SparseRank int     // 1-based rank in the sparse arm; 0 = not in it
}

// SearchCandidates runs the enabled arms (top n each) over the knowledge
// base's indexed chunks and returns the union with both scores.
func (r *KnowledgeRepo) SearchCandidates(
	ctx context.Context, tenantID, kbID uuid.UUID,
	dense []float32, sparse *model.SparseVector, n int, useDense, useSparse bool,
) ([]KBCandidate, error) {
	if n <= 0 {
		n = 50
	}
	dn, sn := 0, 0
	if useDense && len(dense) > 0 {
		dn = n
	}
	if useSparse && sparse != nil && len(sparse.Indices) > 0 {
		sn = n
	}
	if dn == 0 && sn == 0 {
		return nil, nil
	}
	qd := encodeVector(dense)
	if len(dense) == 0 {
		qd = encodeVector(make([]float32, 1024))
	}
	qs := "{}/" + fmt.Sprint(SparseDim)
	if s := encodeSparse(sparse); s != nil {
		qs = *s
	}
	const q = `
		WITH q AS (SELECT $3::vector AS qd, $4::sparsevec AS qs),
		d AS (
			SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> q.qd, c.id) AS rnk
			FROM chat.knowledge_chunks c
			JOIN chat.knowledge_documents doc ON doc.id = c.document_id AND doc.status = 'indexed'
			CROSS JOIN q
			WHERE c.knowledge_base_id = $1 AND c.tenant_id = $2 AND c.embedding IS NOT NULL
			ORDER BY c.embedding <=> q.qd, c.id
			LIMIT $5
		),
		s AS (
			SELECT c.id, row_number() OVER (ORDER BY c.sparse <#> q.qs, c.id) AS rnk
			FROM chat.knowledge_chunks c
			JOIN chat.knowledge_documents doc ON doc.id = c.document_id AND doc.status = 'indexed'
			CROSS JOIN q
			WHERE c.knowledge_base_id = $1 AND c.tenant_id = $2 AND c.sparse IS NOT NULL
			  AND (c.sparse <#> q.qs) < 0
			ORDER BY c.sparse <#> q.qs, c.id
			LIMIT $6
		),
		u AS (SELECT id FROM d UNION SELECT id FROM s)
		SELECT c.id, c.document_id, doc.original_filename, COALESCE(c.chunk_index, 0), c.content,
		       CASE WHEN c.embedding IS NULL THEN 0 ELSE 1 - (c.embedding <=> q.qd) END,
		       CASE WHEN c.sparse IS NULL THEN 0 ELSE -(c.sparse <#> q.qs) END,
		       COALESCE(d.rnk, 0), COALESCE(s.rnk, 0)
		FROM u
		JOIN chat.knowledge_chunks c ON c.id = u.id
		JOIN chat.knowledge_documents doc ON doc.id = c.document_id
		CROSS JOIN q
		LEFT JOIN d ON d.id = u.id
		LEFT JOIN s ON s.id = u.id
	`
	rows, err := r.db.Query(ctx, q, kbID, tenantID, qd, qs, dn, sn)
	if err != nil {
		return nil, fmt.Errorf("kb search: %w", err)
	}
	defer rows.Close()
	out := make([]KBCandidate, 0, dn+sn)
	for rows.Next() {
		var c KBCandidate
		var dr, sr int64
		if err := rows.Scan(&c.ChunkID, &c.DocumentID, &c.Filename, &c.ChunkIndex, &c.Content,
			&c.Cosine, &c.Lexical, &dr, &sr); err != nil {
			return nil, fmt.Errorf("kb search scan: %w", err)
		}
		c.DenseRank, c.SparseRank = int(dr), int(sr)
		out = append(out, c)
	}
	return out, rows.Err()
}
