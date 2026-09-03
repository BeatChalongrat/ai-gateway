// Package testdb gives an integration test its own database.
//
// The problem this replaces: tests shared one database and deleted
// their rows afterwards. Cleanup code is easy to get wrong and fails
// quietly — a cleanup that ran against a closed pool discarded its
// error and left every row behind, and tests that never registered a
// cleanup at all simply grew the shared database forever.
//
// The fix is not better cleanup code, it is not needing any: each test
// gets a database cloned from a prepared template, and the whole
// database is dropped when the test ends. There is nothing to delete,
// so there is nothing to get wrong, and one test can no longer see or
// disturb another's rows.
//
// The clone is a file copy inside Postgres (CREATE DATABASE ...
// TEMPLATE), measured at roughly half a second — cheap enough per test
// and far cheaper than re-running migrations.
//
// A schema-per-test variant was considered and rejected: every query
// in this service names its schema explicitly (chat.conversations),
// so a per-test schema on the search_path would never be consulted.
// Transaction-per-test was rejected too: only some repositories can be
// re-bound to a transaction, so a service built from a mix would write
// half its rows outside it.
//
// Prepare the template once per machine:
//
//	services/chat-edge-api/scripts/make_test_template.sh
//
// Tests skip loudly when the database or the template is absent, the
// same contract the older per-file pool helpers used.
package testdb

import (
	"context"
	"fmt"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// TemplateDB is the prepared database every test database is cloned
// from. Built by scripts/make_test_template.sh.
const TemplateDB = "chat_test_template"

const defaultDSN = "postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable"

var unsafeName = regexp.MustCompile(`[^a-z0-9_]+`)

// dsn returns the base DSN tests connect with.
func dsn() string {
	if v := os.Getenv("CHAT_EDGE_TEST_DSN"); v != "" {
		return v
	}
	return defaultDSN
}

// withDatabase rewrites the database segment of a DSN.
func withDatabase(base, db string) string {
	if i := strings.LastIndex(base, "/"); i >= 0 {
		rest := ""
		if j := strings.Index(base[i:], "?"); j >= 0 {
			rest = base[i:][j:]
		}
		return base[:i+1] + db + rest
	}
	return base
}

// dbNameFor builds a legal, unique database name from the test name.
func dbNameFor(t *testing.T) string {
	name := unsafeName.ReplaceAllString(strings.ToLower(t.Name()), "_")
	if len(name) > 40 {
		name = name[:40]
	}
	return fmt.Sprintf("t_%s_%d", strings.Trim(name, "_"), time.Now().UnixNano()%1e9)
}

// Fresh returns a pool connected to a private database for this test,
// cloned from the template. The database is dropped when the test
// finishes — tests need no cleanup of their own.
//
// Ordering note: the drop is registered BEFORE the pool close, so
// cleanups (which run last-registered-first) close the pool and only
// then drop the database — Postgres refuses to drop a database that
// still has connections.
func Fresh(t *testing.T) *pgxpool.Pool {
	t.Helper()
	ctx := context.Background()
	base := dsn()

	admin, err := pgxpool.New(ctx, withDatabase(base, "postgres"))
	if err != nil {
		t.Skipf("skipping: cannot reach the database server: %v", err)
	}
	defer admin.Close()
	if err := admin.Ping(ctx); err != nil {
		t.Skipf("skipping: database unreachable (%v)", err)
	}

	var exists bool
	if err := admin.QueryRow(ctx,
		"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = $1)", TemplateDB,
	).Scan(&exists); err != nil || !exists {
		t.Skipf(
			"skipping: template database %q is missing — run "+
				"services/chat-edge-api/scripts/make_test_template.sh", TemplateDB)
	}

	name := dbNameFor(t)
	if _, err := admin.Exec(ctx,
		fmt.Sprintf(`CREATE DATABASE %q TEMPLATE %q`, name, TemplateDB),
	); err != nil {
		t.Fatalf("create test database %s: %v", name, err)
	}

	// Registered first -> runs last: after the pool below is closed.
	t.Cleanup(func() {
		dropCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		cleaner, err := pgxpool.New(dropCtx, withDatabase(base, "postgres"))
		if err != nil {
			t.Logf("test database %s left behind: %v", name, err)
			return
		}
		defer cleaner.Close()
		if _, err := cleaner.Exec(dropCtx,
			fmt.Sprintf(`DROP DATABASE IF EXISTS %q WITH (FORCE)`, name),
		); err != nil {
			t.Logf("test database %s left behind: %v", name, err)
		}
	})

	pool, err := pgxpool.New(ctx, withDatabase(base, name))
	if err != nil {
		t.Fatalf("connect to test database %s: %v", name, err)
	}
	t.Cleanup(pool.Close)
	if err := pool.Ping(ctx); err != nil {
		t.Fatalf("ping test database %s: %v", name, err)
	}
	return pool
}
