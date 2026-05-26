// Shared helpers used across every repository in this package.
//
// Kept in one file so the unique-constraint detection logic and
// the case-normalisation helper can evolve without copy/paste.
package repository

import (
	"strings"

	"github.com/jackc/pgx/v5/pgconn"
)

// isUniqueViolation matches PostgreSQL's SQLSTATE 23505 (UNIQUE
// violation) using pgx's typed error. Any other DB error is
// returned untouched so the caller can log + propagate.
func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	if err == nil {
		return false
	}
	// pgx returns a *PgError wrapped under fmt.Errorf in some paths.
	for cause := err; cause != nil; {
		if pe, ok := cause.(*pgconn.PgError); ok {
			pgErr = pe
			break
		}
		// Climb the wrapped chain.
		u, ok := cause.(interface{ Unwrap() error })
		if !ok {
			break
		}
		cause = u.Unwrap()
	}
	return pgErr != nil && pgErr.Code == "23505"
}

// lower is a tiny helper so callsites don't need to import strings
// just to LOWERCASE one field.
func lower(s string) string { return strings.ToLower(s) }
