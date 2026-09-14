package testdb

// FIX119 Part C1 — the fence, tested without a database.

import (
	"strings"
	"testing"
)

func TestGuardDSN_RefusesTheLiveDatabases(t *testing.T) {
	for _, dsn := range []string{
		"postgres://chatedge:chatedge_pass@localhost:5433/sran_ai?sslmode=disable",
		"postgres://u:p@db.internal:5432/sran_ai",
		"postgres://u:p@localhost:5432/ai_gateway?sslmode=disable",
	} {
		if err := GuardDSN(dsn); err == nil {
			t.Fatalf("GuardDSN(%q) must refuse a live database name", dsn)
		} else if !strings.Contains(err.Error(), "live database") {
			t.Fatalf("GuardDSN(%q): unhelpful error %v", dsn, err)
		}
	}
}

func TestGuardDSN_AllowsTheMaintenanceAndCloneDatabases(t *testing.T) {
	for _, dsn := range []string{
		defaultServerDSN,
		"postgres://chatedge:chatedge_pass@localhost:5433/postgres?sslmode=disable",
		"postgres://u:p@h:1/t_some_test_123?sslmode=disable",
		"postgres://u:p@h:1/chat_test_template",
	} {
		if err := GuardDSN(dsn); err != nil {
			t.Fatalf("GuardDSN(%q) = %v, want nil", dsn, err)
		}
	}
}

func TestDefaultServerDSN_NeverNamesALiveDatabase(t *testing.T) {
	if name := DatabaseName(defaultServerDSN); name != MaintenanceDB {
		t.Fatalf("the default DSN must point at %q, not %q", MaintenanceDB, name)
	}
}

func TestWithDatabase_RewritesOnlyTheDatabaseSegment(t *testing.T) {
	got := withDatabase("postgres://u:p@h:5433/postgres?sslmode=disable", "t_x_1")
	if got != "postgres://u:p@h:5433/t_x_1?sslmode=disable" {
		t.Fatalf("withDatabase = %q", got)
	}
	if DatabaseName(got) != "t_x_1" {
		t.Fatalf("DatabaseName(%q) = %q", got, DatabaseName(got))
	}
}

// A sub-test that calls t.Fatalf fails its parent, so the fail-closed
// branch cannot be exercised in-process; what CAN be pinned is the switch
// that picks it: optional() is false unless the machine says otherwise.
func TestOptional_DefaultsToFailClosed(t *testing.T) {
	t.Setenv("CHAT_EDGE_TEST_DB_OPTIONAL", "")
	if optional() {
		t.Fatal("an absent database must fail the test by default")
	}
	for _, v := range []string{"1", "true", "YES"} {
		t.Setenv("CHAT_EDGE_TEST_DB_OPTIONAL", v)
		if !optional() {
			t.Fatalf("CHAT_EDGE_TEST_DB_OPTIONAL=%q must turn the failure into a skip", v)
		}
	}
}

func TestFresh_SkipsOnlyWhenDeclaredOptional(t *testing.T) {
	t.Setenv("CHAT_EDGE_TEST_DSN", "postgres://u:p@127.0.0.1:1/postgres?sslmode=disable&connect_timeout=1")
	t.Setenv("CHAT_EDGE_TEST_DB_OPTIONAL", "1")
	skipped := false
	t.Run("inner", func(inner *testing.T) {
		inner.Cleanup(func() { skipped = inner.Skipped() })
		_ = Fresh(inner)
	})
	if !skipped {
		t.Fatal("with CHAT_EDGE_TEST_DB_OPTIONAL=1 an absent server must skip, not fail")
	}
}
