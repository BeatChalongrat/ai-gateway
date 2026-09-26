package agentmarket

import (
	"context"
	"time"

	"github.com/google/uuid"
)

// IndexEntry is one recorded document in an install's duplicate index
// (migration 002): what a package's duplicate check compares.
type IndexEntry struct {
	DocKey         string  `json:"doc_key"`
	Name           string  `json:"name"`
	SHA256         string  `json:"sha256"`
	SellerTaxID    *string `json:"seller_tax_id"`
	DocumentNumber *string `json:"document_number"`
	DocumentDate   *string `json:"document_date"`
	Total          *string `json:"total"`
}

// DocumentIndex is the install's index as the package reads it (newest
// first, at most limit rows), leaving out the rows of run exceptRun — a
// resumed run must not find its own documents as duplicates.
func (s *Store) DocumentIndex(ctx context.Context, install, exceptRun uuid.UUID, limit int) ([]IndexEntry, error) {
	rows, err := s.Pool.Query(ctx, `SELECT doc_key, name, sha256, seller_tax_id, document_number, document_date::text, total::text
		FROM agent_market.document_index
		WHERE install_id = $1 AND run_id <> $2 AND expires_at > now()
		ORDER BY created_at DESC, id DESC LIMIT $3`, install, exceptRun, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []IndexEntry{}
	for rows.Next() {
		var e IndexEntry
		if err := rows.Scan(&e.DocKey, &e.Name, &e.SHA256, &e.SellerTaxID, &e.DocumentNumber, &e.DocumentDate, &e.Total); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// AddDocumentIndex records a succeeded run's documents; a repeat (the same
// run and document) is ignored, so it is safe to call again after a crash.
func (s *Store) AddDocumentIndex(ctx context.Context, run Run, entries []IndexEntry, retention time.Duration) (int64, error) {
	var n int64
	for _, e := range entries {
		tag, err := s.Pool.Exec(ctx, `INSERT INTO agent_market.document_index
			(install_id, tenant_id, user_id, run_id, doc_key, name, sha256, seller_tax_id, document_number, document_date, total, expires_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::date, $11::numeric, now() + make_interval(secs => $12::double precision))
			ON CONFLICT (install_id, run_id, doc_key) DO NOTHING`,
			run.InstallID, run.TenantID, run.UserID, run.ID, e.DocKey, e.Name, e.SHA256, e.SellerTaxID, e.DocumentNumber,
			e.DocumentDate, e.Total, retention.Seconds())
		if err != nil {
			return n, err
		}
		n += tag.RowsAffected()
	}
	return n, nil
}

// PurgeExpiredIndex deletes index rows past their retention.
func (s *Store) PurgeExpiredIndex(ctx context.Context) (int64, error) {
	tag, err := s.Pool.Exec(ctx, `DELETE FROM agent_market.document_index WHERE expires_at <= now()`)
	return tag.RowsAffected(), err
}
