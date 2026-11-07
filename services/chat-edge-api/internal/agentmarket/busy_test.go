package agentmarket

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http/httptest"
	"testing"

	"github.com/jackc/pgx/v5/pgconn"
)

// Agent Market's pools exhausted or its tables locked is a temporary state:
// the person is told to try again shortly (503), not shown an internal error.
func TestDatabaseBusyIsA503NotA500(t *testing.T) {
	a := &API{log: slog.Default()}
	for _, err := range []error{
		fmt.Errorf("install: %w", &pgconn.PgError{Code: "55P03"}), // lock_timeout
		&pgconn.PgError{Code: "57014"},                            // statement_timeout
		&pgconn.PgError{Code: "53300"},                            // too many connections
		fmt.Errorf("acquire: %w", context.DeadlineExceeded),
	} {
		w := httptest.NewRecorder()
		a.fail(w, err, "install")
		if w.Code != 503 || w.Header().Get("Retry-After") == "" {
			t.Fatalf("%v: got %d (Retry-After %q), want 503 with Retry-After", err, w.Code, w.Header().Get("Retry-After"))
		}
	}
	w := httptest.NewRecorder()
	a.fail(w, errors.New("boom"), "install")
	if w.Code != 500 {
		t.Fatalf("a real fault stays a 500, got %d", w.Code)
	}
}
