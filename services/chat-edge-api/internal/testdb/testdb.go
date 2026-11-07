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
// FIX119 Part C1 — fail-closed. Until FIX119 seven test files opened
// their own pool with a default DSN that named the LIVE database
// (sran_ai) and fell back to it whenever CHAT_EDGE_TEST_DSN was unset —
// which is every developer's machine. FIX118 counted the result: 176
// fixture rows in the production table. Now:
//
//   - Fresh is the only way a test gets a database
//     (internal/testhygiene fails the build on any other pgxpool.New in
//     a _test.go);
//   - the server DSN's own database segment is ignored — Fresh always
//     connects to the maintenance database `postgres` and to the clone
//     it created — and a DSN that names a live database is refused
//     outright (LiveDatabases), so misconfiguration cannot point a test
//     at production;
//   - an unreachable server or a missing template FAILS the test
//     (t.Fatalf), it does not skip: a lane that quietly ran zero
//     database tests reported green for months. Set
//     CHAT_EDGE_TEST_DB_OPTIONAL=1 on a machine that has no Postgres at
//     all to turn that failure into a loud skip.
package testdb

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdeps"
)

// TemplateDB is the prepared database every test database is cloned
// from. Built by scripts/make_test_template.sh.
const TemplateDB = "chat_test_template"

// MaintenanceDB is the database Fresh connects to in order to create
// and drop clones. It is never written to.
const MaintenanceDB = "postgres"

// defaultServerDSN names the SERVER only. The database segment is
// deliberately the maintenance database, never a live one.
const defaultServerDSN = "postgres://chatedge:chatedge_pass@localhost:5433/postgres?sslmode=disable"

// LiveDatabases are names a test must never be allowed to connect to,
// whatever the environment says.
var LiveDatabases = map[string]bool{"sran_ai": true, "ai_gateway": true}

var unsafeName = regexp.MustCompile(`[^a-z0-9_]+`)

// dsn returns the server DSN tests connect with.
func dsn() string {
	if v := os.Getenv("CHAT_EDGE_TEST_DSN"); v != "" {
		return v
	}
	return defaultServerDSN
}

// optional reports whether an absent database turns into a skip
// instead of a failure (CHAT_EDGE_TEST_DB_OPTIONAL=1).
func optional() bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv("CHAT_EDGE_TEST_DB_OPTIONAL")))
	return v == "1" || v == "true" || v == "yes"
}

// DatabaseName returns the database segment of a DSN ("" when absent).
func DatabaseName(base string) string {
	u, err := url.Parse(base)
	if err != nil {
		if i := strings.LastIndex(base, "/"); i >= 0 {
			rest := base[i+1:]
			if j := strings.Index(rest, "?"); j >= 0 {
				rest = rest[:j]
			}
			return rest
		}
		return ""
	}
	return strings.TrimPrefix(u.Path, "/")
}

// GuardDSN refuses a server DSN that names a live database. It is the
// code-level fence the card asked for: even a CHAT_EDGE_TEST_DSN that
// points at production cannot make a test connect to it.
func GuardDSN(base string) error {
	if name := DatabaseName(base); LiveDatabases[name] {
		return fmt.Errorf("testdb: refusing DSN that names the live database %q — tests only ever connect to %q and to their own clones", name, MaintenanceDB)
	}
	return nil
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

// absent ends the test when no test database can be had: a failure by
// default, a skip only when the machine declares it has no Postgres.
//
// FIX126 O7 — on CI (CI=true) or with CHAT_EDGE_REQUIRE_DEPS=1 the database
// is never optional: CHAT_EDGE_TEST_DB_OPTIONAL is ignored and it fails.
func absent(t testing.TB, format string, args ...any) {
	t.Helper()
	if optional() && !testdeps.Required() {
		t.Skipf("skipping (CHAT_EDGE_TEST_DB_OPTIONAL=1): "+format, args...)
		return
	}
	t.Fatalf("no test database (set CHAT_EDGE_TEST_DB_OPTIONAL=1 to skip instead): "+format, args...)
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
	if err := GuardDSN(base); err != nil {
		t.Fatal(err)
	}

	admin, err := pgxpool.New(ctx, withDatabase(base, MaintenanceDB))
	if err != nil {
		absent(t, "cannot reach the database server: %v", err)
		return nil
	}
	defer admin.Close()
	if err := admin.Ping(ctx); err != nil {
		absent(t, "database server unreachable (%v)", err)
		return nil
	}

	var exists bool
	if err := admin.QueryRow(ctx,
		"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = $1)", TemplateDB,
	).Scan(&exists); err != nil || !exists {
		absent(t, "template database %q is missing — run services/chat-edge-api/scripts/make_test_template.sh", TemplateDB)
		return nil
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
		cleaner, err := pgxpool.New(dropCtx, withDatabase(base, MaintenanceDB))
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
