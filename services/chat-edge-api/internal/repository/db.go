// Common DB-querier interface used by every repo.
//
// Both “*pgxpool.Pool“ and “pgx.Tx“ satisfy this interface, so
// repos can be reused inside a transaction by swapping the backing
// executor without changing query code. The pattern is:
//
//	tx, _ := pool.BeginTx(ctx, pgx.TxOptions{})
//	defer tx.Rollback(ctx)
//	if _, err := repo.WithTx(tx).CreateThing(ctx, ...); err != nil { ... }
//	tx.Commit(ctx)
package repository

import (
	"context"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

type DB interface {
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
}
