package agentmarket

import (
	"context"
	"encoding/json"
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
	// Detail (AM6, migration 005) is what the package keeps of the document
	// beyond the four values the duplicate check compares: an invoice's lines
	// and the PO lines they billed, a receipt a person confirmed in this
	// system. A confirmed receipt has no total and no seller's tax ID.
	Detail json.RawMessage `json:"detail,omitempty"`
}

// DocumentIndex is the install's index as the package reads it (newest
// first, at most limit rows), leaving out the rows of run exceptRun — a
// resumed run must not find its own documents as duplicates.
func (s *Store) DocumentIndex(ctx context.Context, install, exceptRun uuid.UUID, limit int) ([]IndexEntry, error) {
	rows, err := s.Pool.Query(ctx, `SELECT doc_key, name, sha256, seller_tax_id, document_number, document_date::text, total::text,
			detail::text
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
		var detail *string
		if err := rows.Scan(&e.DocKey, &e.Name, &e.SHA256, &e.SellerTaxID, &e.DocumentNumber, &e.DocumentDate, &e.Total, &detail); err != nil {
			return nil, err
		}
		if detail != nil {
			e.Detail = json.RawMessage(*detail)
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// AddDocumentIndex records a succeeded run's documents. It is safe to call
// again after a crash: an entry recorded as it is now is not written again.
// AM6: it records what the run's computed table lists
// now — a round that computes again (a document restored, a receipt
// confirmed) replaces what it recorded before: its entries are brought up to
// date, and the ones its new table no longer lists are removed. Another
// run's entries are never touched.
func (s *Store) AddDocumentIndex(ctx context.Context, run Run, entries []IndexEntry, retention time.Duration) (int64, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck // rollback after commit is a no-op
	var n int64
	keys := make([]string, 0, len(entries))
	for _, e := range entries {
		var detail *string
		if len(e.Detail) > 0 && string(e.Detail) != "null" {
			d := string(e.Detail)
			detail = &d
		}
		tag, err := tx.Exec(ctx, `INSERT INTO agent_market.document_index
			(install_id, tenant_id, user_id, run_id, doc_key, name, sha256, seller_tax_id, document_number, document_date, total, expires_at, detail)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::date, $11::numeric, now() + make_interval(secs => $12::double precision), $13::jsonb)
			ON CONFLICT (install_id, run_id, doc_key) DO UPDATE SET name = EXCLUDED.name, sha256 = EXCLUDED.sha256,
				seller_tax_id = EXCLUDED.seller_tax_id, document_number = EXCLUDED.document_number,
				document_date = EXCLUDED.document_date, total = EXCLUDED.total, detail = EXCLUDED.detail, expires_at = EXCLUDED.expires_at
			WHERE (document_index.name, document_index.sha256, document_index.seller_tax_id, document_index.document_number,
				document_index.document_date, document_index.total, document_index.detail)
				IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.sha256, EXCLUDED.seller_tax_id, EXCLUDED.document_number,
				EXCLUDED.document_date, EXCLUDED.total, EXCLUDED.detail)`,
			run.InstallID, run.TenantID, run.UserID, run.ID, e.DocKey, e.Name, e.SHA256, e.SellerTaxID, e.DocumentNumber,
			e.DocumentDate, e.Total, retention.Seconds(), detail)
		if err != nil {
			return n, err
		}
		n += tag.RowsAffected()
		keys = append(keys, e.DocKey)
	}
	if _, err := tx.Exec(ctx, `DELETE FROM agent_market.document_index WHERE install_id = $1 AND run_id = $2
		AND NOT (doc_key = ANY($3::text[]))`, run.InstallID, run.ID, keys); err != nil {
		return n, err
	}
	return n, tx.Commit(ctx)
}

// PurgeExpiredIndex deletes index rows past their retention.
func (s *Store) PurgeExpiredIndex(ctx context.Context) (int64, error) {
	tag, err := s.Pool.Exec(ctx, `DELETE FROM agent_market.document_index WHERE expires_at <= now()`)
	return tag.RowsAffected(), err
}
