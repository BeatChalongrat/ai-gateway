package db_test

// FIX129 A1 — the pool must carry the three PostgreSQL timeouts.
//
// Two layers: the config the pool is built from names the runtime
// parameters (unit), and a session opened through the pool actually has
// them (integration, against a private clone of the test template).
// Before this card main.go built the pool with no parameters at all, so
// SHOW idle_in_transaction_session_timeout answered 0 — the setting that
// let an abandoned FOR UPDATE hold the live backend for 34 minutes.

import (
	"context"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/db"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

const testDSN = "postgres://u:p@localhost:5432/x?sslmode=disable"

func TestPoolConfig_CarriesTimeoutsAndApplicationName(t *testing.T) {
	cfg, err := db.PoolConfig(testDSN, db.DefaultTimeouts())
	if err != nil {
		t.Fatalf("PoolConfig: %v", err)
	}
	params := cfg.ConnConfig.RuntimeParams
	want := map[string]string{
		"statement_timeout":                   "30000",
		"lock_timeout":                        "5000",
		"idle_in_transaction_session_timeout": "30000",
		"application_name":                    "chat-edge",
	}
	for k, v := range want {
		if got := params[k]; got != v {
			t.Errorf("RuntimeParams[%q] = %q, want %q", k, got, v)
		}
	}
}

// 0 turns a timeout off: the parameter is not sent, so the server's own
// default (0 = disabled) applies, rather than an explicit "0" that would
// read as a deliberate setting in the logs.
func TestPoolConfig_ZeroLeavesTheParameterUnset(t *testing.T) {
	cfg, err := db.PoolConfig(testDSN, db.Timeouts{StatementMS: 0, LockMS: 0, IdleInTransactionMS: 0})
	if err != nil {
		t.Fatalf("PoolConfig: %v", err)
	}
	params := cfg.ConnConfig.RuntimeParams
	for _, k := range []string{"statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout"} {
		if _, set := params[k]; set {
			t.Errorf("RuntimeParams[%q] must be absent when the timeout is 0, got %q", k, params[k])
		}
	}
	if params["application_name"] != "chat-edge" {
		t.Errorf("application_name must always be set, got %q", params["application_name"])
	}
}

// A session opened through the helper has the timeouts in force.
func TestNewPool_SessionHasTimeouts(t *testing.T) {
	// testdb.Fresh hands out a private database; its connection string is
	// the only DSN a test may use (internal/testhygiene).
	scratch := testdb.Fresh(t)
	dsn := scratch.Config().ConnString()
	ctx := context.Background()

	pool, err := db.NewPool(ctx, dsn, db.DefaultTimeouts())
	if err != nil {
		t.Fatalf("NewPool: %v", err)
	}
	t.Cleanup(pool.Close)

	show := func(name string) string {
		t.Helper()
		var v string
		if err := pool.QueryRow(ctx, "SHOW "+name).Scan(&v); err != nil {
			t.Fatalf("SHOW %s: %v", name, err)
		}
		return v
	}
	want := map[string]string{
		"idle_in_transaction_session_timeout": "30s",
		"statement_timeout":                   "30s",
		"lock_timeout":                        "5s",
		"application_name":                    "chat-edge",
	}
	for name, v := range want {
		if got := show(name); got != v {
			t.Errorf("SHOW %s = %q, want %q", name, got, v)
		}
	}
}
