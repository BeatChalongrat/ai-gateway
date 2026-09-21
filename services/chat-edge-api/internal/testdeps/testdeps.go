// Package testdeps decides what a test does when a dependency it needs
// (Postgres, Redis, a migrated schema) is missing.
//
// FIX126 O7 — several tests ended with t.Skipf when Redis was unreachable or
// a table was absent, so a CI run without that dependency went green having
// run none of them. Locally that is still the behaviour (a laptop without
// Redis skips the Redis tests); on CI — CI=true, which every hosted runner
// sets — or with CHAT_EDGE_REQUIRE_DEPS=1, a missing dependency FAILS the
// test with a message naming it.
//
// CHAT_EDGE_TEST_DB_OPTIONAL=1 (testdb's opt-in skip) does not override a
// requirement: on CI the database is never optional.
package testdeps

import (
	"os"
	"strings"
	"testing"
)

// Required reports whether a missing test dependency must fail the test:
// CI=true or CHAT_EDGE_REQUIRE_DEPS=1.
func Required() bool {
	return truthy(os.Getenv("CHAT_EDGE_REQUIRE_DEPS")) || truthy(os.Getenv("CI"))
}

func truthy(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// Missing ends the test because a dependency is absent: t.Fatalf when
// Required, t.Skipf otherwise. It does not return.
func Missing(t testing.TB, format string, args ...any) {
	t.Helper()
	if Required() {
		t.Fatalf("required test dependency missing (CI=true or CHAT_EDGE_REQUIRE_DEPS=1 — dependencies may not be skipped): "+format, args...)
		return
	}
	t.Skipf("skipping, dependency missing (set CHAT_EDGE_REQUIRE_DEPS=1 to fail instead): "+format, args...)
}
