// Package db builds the service's PostgreSQL pool.
//
// FIX129 A1 — on 2026-09-22 the live backend wedged for 34 minutes: one
// abandoned FOR UPDATE sat idle in its transaction holding a row lock,
// every later writer queued behind it, and nothing ever gave up because
// all three server-side timeouts were 0. The pool built here sets them
// as session defaults on every connection it opens, so a stuck statement,
// a lock wait or an idle transaction ends on its own:
//
//	statement_timeout                    — one statement may run this long
//	lock_timeout                         — one lock wait may last this long
//	idle_in_transaction_session_timeout  — an open transaction may idle
//	                                       this long before the session
//	                                       is closed (and its locks freed)
//
// application_name is set as well so pg_stat_activity names this service.
//
// The values are env-tunable (PG_STATEMENT_TIMEOUT_MS, PG_LOCK_TIMEOUT_MS,
// PG_IDLE_IN_TRANSACTION_TIMEOUT_MS; 0 turns one off) and live in
// config.Config; DefaultTimeouts is what an unset environment gets.
//
// This is the one place the service calls pgxpool.NewWithConfig — every
// other pool (tests included) comes from here or from internal/testdb.
package db

import (
	"context"
	"fmt"
	"strconv"

	"github.com/jackc/pgx/v5/pgxpool"
)

// ApplicationName is what this service reports in pg_stat_activity.
const ApplicationName = "chat-edge"

// Timeouts are the server-side session timeouts, in milliseconds.
// Zero means the setting is not sent, so the server's own default
// (0 = disabled) applies.
type Timeouts struct {
	StatementMS         int
	LockMS              int
	IdleInTransactionMS int
}

// DefaultTimeouts are the values an unset environment gets:
// 30 s statement, 5 s lock wait, 30 s idle in transaction.
func DefaultTimeouts() Timeouts {
	return Timeouts{StatementMS: 30000, LockMS: 5000, IdleInTransactionMS: 30000}
}

// PoolConfig parses dsn and applies the timeouts and application name as
// connection runtime parameters, so every session the pool opens carries
// them from its first statement. A value the DSN itself names for one of
// these parameters is overridden: the service's policy wins.
func PoolConfig(dsn string, t Timeouts) (*pgxpool.Config, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("db: parse dsn: %w", err)
	}
	params := cfg.ConnConfig.RuntimeParams
	if params == nil {
		params = map[string]string{}
		cfg.ConnConfig.RuntimeParams = params
	}
	params["application_name"] = ApplicationName
	setMS(params, "statement_timeout", t.StatementMS)
	setMS(params, "lock_timeout", t.LockMS)
	setMS(params, "idle_in_transaction_session_timeout", t.IdleInTransactionMS)
	return cfg, nil
}

// NewPool opens a pool built from PoolConfig.
func NewPool(ctx context.Context, dsn string, t Timeouts) (*pgxpool.Pool, error) {
	cfg, err := PoolConfig(dsn, t)
	if err != nil {
		return nil, err
	}
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("db: connect: %w", err)
	}
	return pool, nil
}

// setMS writes a millisecond parameter, or removes it when v <= 0 so an
// operator's 0 reads as "not enforced" rather than as an explicit setting.
func setMS(params map[string]string, name string, v int) {
	if v <= 0 {
		delete(params, name)
		return
	}
	params[name] = strconv.Itoa(v)
}
