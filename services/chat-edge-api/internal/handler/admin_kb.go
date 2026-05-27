// Admin knowledge-base endpoints — cross-tenant visibility for the
// admin-ui. Mirrors the tenant-scoped handler in knowledge.go but
// drops the tenant filter (the entire point of the admin role) and
// adds a small "sample chunks" peek for the admin UI's relevance-
// debugging panel.
//
// Mutations (delete + reindex) still go through the same
// KnowledgeService a tenant user would hit — the admin handler just
// looks up the row's tenant_id first, then forwards. That keeps the
// service-layer rules (cascade, statss recompute) authoritative
// regardless of who triggered the action.
package handler

import (
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

type adminKBRow struct {
	ID             string    `json:"id"`
	TenantID       string    `json:"tenant_id"`
	TenantName     string    `json:"tenant_name,omitempty"`
	Name           string    `json:"name"`
	Description    string    `json:"description"`
	EmbeddingModel string    `json:"embedding_model"`
	ChunkSize      int       `json:"chunk_size"`
	ChunkOverlap   int       `json:"chunk_overlap"`
	IsActive       bool      `json:"is_active"`
	DocumentCount  int       `json:"document_count"`
	TotalChunks    int       `json:"total_chunks"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

type adminKBDocRow struct {
	ID              string    `json:"id"`
	KnowledgeBaseID string    `json:"knowledge_base_id"`
	Filename        string    `json:"filename"`
	ContentType     string    `json:"content_type"`
	FileSizeBytes   int64     `json:"file_size_bytes"`
	Status          string    `json:"status"`
	ChunkCount      int       `json:"chunk_count"`
	CharCount       int       `json:"char_count"`
	ErrorMessage    *string   `json:"error_message,omitempty"`
	CreatedAt       time.Time `json:"created_at"`
	UpdatedAt       time.Time `json:"updated_at"`
}

type adminKBSampleChunk struct {
	ID         string `json:"id"`
	DocumentID string `json:"document_id"`
	Filename   string `json:"filename"`
	ChunkIndex int    `json:"chunk_index"`
	Content    string `json:"content"`
}

type adminKBDetail struct {
	KnowledgeBase adminKBRow           `json:"knowledge_base"`
	Documents     []adminKBDocRow      `json:"documents"`
	SampleChunks  []adminKBSampleChunk `json:"sample_chunks"`
}

// ---------------------------------------------------------------------------
// GET /admin/knowledge-bases
// ---------------------------------------------------------------------------

// ListKnowledgeBases returns every KB the admin can see, optionally
// filtered by “tenant_id“ or partial name match (“q“). Paginated
// by limit + offset like the analytics conversation listing.
func (h *AdminHandler) ListKnowledgeBases(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	tenantFilter := r.URL.Query().Get("tenant_id")
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
	if offset < 0 {
		offset = 0
	}

	// Tenant join is LEFT — orphaned KBs (tenant deleted, KB still
	// referenced) still surface so an admin can clean them up
	// instead of vanishing from the UI.
	sql := `
		SELECT kb.id, kb.tenant_id, COALESCE(t.name, ''),
		       kb.name, kb.description, kb.embedding_model, kb.chunk_size,
		       kb.chunk_overlap, kb.is_active, kb.document_count,
		       kb.total_chunks, kb.created_at, kb.updated_at
		FROM chat.knowledge_bases kb
		LEFT JOIN identity.tenants t ON t.id = kb.tenant_id
		WHERE ($1 = '' OR kb.name ILIKE '%' || $1 || '%')
		  AND ($2::uuid IS NULL OR kb.tenant_id = $2::uuid)
		ORDER BY kb.created_at DESC
		LIMIT $3 OFFSET $4
	`
	var tenantArg any
	if tenantFilter != "" {
		if tid, err := uuid.Parse(tenantFilter); err == nil {
			tenantArg = tid
		} else {
			WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid tenant_id")
			return
		}
	}
	rows, err := h.pool.Query(r.Context(), sql, q, tenantArg, limit, offset)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "list kbs: "+err.Error())
		return
	}
	defer rows.Close()
	out := make([]adminKBRow, 0)
	for rows.Next() {
		var row adminKBRow
		if err := rows.Scan(
			&row.ID, &row.TenantID, &row.TenantName,
			&row.Name, &row.Description, &row.EmbeddingModel,
			&row.ChunkSize, &row.ChunkOverlap, &row.IsActive,
			&row.DocumentCount, &row.TotalChunks, &row.CreatedAt, &row.UpdatedAt,
		); err != nil {
			WriteError(w, http.StatusInternalServerError, CodeInternalError, "scan: "+err.Error())
			return
		}
		out = append(out, row)
	}

	// Total count for pagination. Same predicates, no LIMIT.
	var total int
	if err := h.pool.QueryRow(r.Context(), `
		SELECT COUNT(*) FROM chat.knowledge_bases kb
		WHERE ($1 = '' OR kb.name ILIKE '%' || $1 || '%')
		  AND ($2::uuid IS NULL OR kb.tenant_id = $2::uuid)
	`, q, tenantArg).Scan(&total); err != nil {
		// Soft-fail: ship the rows we have without a total.
		total = len(out)
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"knowledge_bases": out,
		"total":           total,
		"limit":           limit,
		"offset":          offset,
	})
}

// ---------------------------------------------------------------------------
// GET /admin/knowledge-bases/{id}
// ---------------------------------------------------------------------------

// KnowledgeBaseDetail returns the full KB row + every document + a
// sample of up to 5 chunks. Sample chunks are picked by lowest
// chunk_index so the admin sees the "top" content of each KB — useful
// for spot-checking what was indexed.
func (h *AdminHandler) KnowledgeBaseDetail(w http.ResponseWriter, r *http.Request) {
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}

	var kb adminKBRow
	err = h.pool.QueryRow(r.Context(), `
		SELECT kb.id, kb.tenant_id, COALESCE(t.name, ''),
		       kb.name, kb.description, kb.embedding_model, kb.chunk_size,
		       kb.chunk_overlap, kb.is_active, kb.document_count,
		       kb.total_chunks, kb.created_at, kb.updated_at
		FROM chat.knowledge_bases kb
		LEFT JOIN identity.tenants t ON t.id = kb.tenant_id
		WHERE kb.id = $1
	`, kbID).Scan(
		&kb.ID, &kb.TenantID, &kb.TenantName,
		&kb.Name, &kb.Description, &kb.EmbeddingModel,
		&kb.ChunkSize, &kb.ChunkOverlap, &kb.IsActive,
		&kb.DocumentCount, &kb.TotalChunks, &kb.CreatedAt, &kb.UpdatedAt,
	)
	if err != nil {
		if err == pgx.ErrNoRows {
			WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "fetch kb: "+err.Error())
		return
	}

	// Documents — newest first, full list (admin needs everything,
	// not a paginated slice).
	docs := make([]adminKBDocRow, 0)
	docRows, err := h.pool.Query(r.Context(), `
		SELECT id, knowledge_base_id, original_filename, content_type,
		       file_size_bytes, status, chunk_count, char_count,
		       error_message, created_at, updated_at
		FROM chat.knowledge_documents
		WHERE knowledge_base_id = $1
		ORDER BY created_at DESC
	`, kbID)
	if err == nil {
		defer docRows.Close()
		for docRows.Next() {
			var d adminKBDocRow
			if err := docRows.Scan(
				&d.ID, &d.KnowledgeBaseID, &d.Filename, &d.ContentType,
				&d.FileSizeBytes, &d.Status, &d.ChunkCount, &d.CharCount,
				&d.ErrorMessage, &d.CreatedAt, &d.UpdatedAt,
			); err == nil {
				docs = append(docs, d)
			}
		}
	}

	// Sample chunks — 5 lowest chunk_index across the KB, joined to
	// the document for filename. Bounded ``LEFT(content, 600)`` so
	// the admin UI doesn't render a 20kB blob on screen.
	samples := make([]adminKBSampleChunk, 0)
	sampleRows, err := h.pool.Query(r.Context(), `
		SELECT c.id, c.document_id, d.original_filename, c.chunk_index,
		       LEFT(c.content, 600)
		FROM chat.knowledge_chunks c
		JOIN chat.knowledge_documents d ON d.id = c.document_id
		WHERE c.knowledge_base_id = $1
		ORDER BY c.chunk_index ASC
		LIMIT 5
	`, kbID)
	if err == nil {
		defer sampleRows.Close()
		for sampleRows.Next() {
			var s adminKBSampleChunk
			if err := sampleRows.Scan(
				&s.ID, &s.DocumentID, &s.Filename, &s.ChunkIndex, &s.Content,
			); err == nil {
				samples = append(samples, s)
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(adminKBDetail{
		KnowledgeBase: kb,
		Documents:     docs,
		SampleChunks:  samples,
	})
}

// ---------------------------------------------------------------------------
// DELETE /admin/knowledge-bases/{id}
// ---------------------------------------------------------------------------

// DeleteKnowledgeBase routes through KnowledgeService.DeleteKB so
// the on-disk / S3 blobs get cleaned up, not just the DB rows. Looks
// up the KB's tenant_id first because DeleteKB is tenant-scoped.
func (h *AdminHandler) DeleteKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	if h.kb.DeleteKB == nil {
		WriteError(w, http.StatusServiceUnavailable, "KB_SINK_UNCONFIGURED",
			"knowledge-base service not wired for admin")
		return
	}
	var tenantID uuid.UUID
	if err := h.pool.QueryRow(r.Context(),
		`SELECT tenant_id FROM chat.knowledge_bases WHERE id = $1`, kbID,
	).Scan(&tenantID); err != nil {
		if err == pgx.ErrNoRows {
			WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "lookup tenant: "+err.Error())
		return
	}
	if err := h.kb.DeleteKB(r.Context(), tenantID, kbID); err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "delete kb: "+err.Error())
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// ---------------------------------------------------------------------------
// POST /admin/knowledge-bases/{id}/reindex
// ---------------------------------------------------------------------------

// ReindexKnowledgeBase walks every document in the KB and triggers a
// reindex through KnowledgeService. The current ReindexDocument
// implementation is synchronous and can take seconds per document;
// for a KB with many docs the call may take a while. The handler
// returns a summary once everything has finished.
func (h *AdminHandler) ReindexKnowledgeBase(w http.ResponseWriter, r *http.Request) {
	kbID, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, CodeBadRequest, "invalid kb id")
		return
	}
	if h.kb.ReindexDocument == nil {
		WriteError(w, http.StatusServiceUnavailable, "KB_SINK_UNCONFIGURED",
			"knowledge-base service not wired for admin")
		return
	}
	var tenantID uuid.UUID
	if err := h.pool.QueryRow(r.Context(),
		`SELECT tenant_id FROM chat.knowledge_bases WHERE id = $1`, kbID,
	).Scan(&tenantID); err != nil {
		if err == pgx.ErrNoRows {
			WriteError(w, http.StatusNotFound, CodeNotFound, "knowledge base not found")
			return
		}
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "lookup tenant: "+err.Error())
		return
	}

	docRows, err := h.pool.Query(r.Context(),
		`SELECT id FROM chat.knowledge_documents WHERE knowledge_base_id = $1`, kbID,
	)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, CodeInternalError, "list docs: "+err.Error())
		return
	}
	defer docRows.Close()
	ids := make([]uuid.UUID, 0)
	for docRows.Next() {
		var id uuid.UUID
		if err := docRows.Scan(&id); err == nil {
			ids = append(ids, id)
		}
	}

	var succeeded, failed int
	for _, id := range ids {
		if err := h.kb.ReindexDocument(r.Context(), tenantID, kbID, id); err != nil {
			failed++
			continue
		}
		succeeded++
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"reindexed": succeeded,
		"failed":    failed,
		"total":     len(ids),
	})
}
