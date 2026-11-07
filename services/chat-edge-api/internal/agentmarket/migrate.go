package agentmarket

import (
	"context"
	"embed"
	"fmt"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed migrations/*.sql
var migrationFS embed.FS

// Migrate applies the agent_market schema migrations that are not yet
// recorded in agent_market.schema_migrations. It is idempotent and takes a
// transaction-scoped advisory lock so the control plane and a runner
// starting together cannot race. It only ever touches schema agent_market.
func Migrate(ctx context.Context, pool *pgxpool.Pool) ([]string, error) {
	ups, err := upFiles()
	if err != nil {
		return nil, err
	}
	tx, err := pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck // rollback after commit is a no-op
	// 0x414d31 = "AM1": a lock id no other part of the platform uses.
	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(4279601)`); err != nil {
		return nil, fmt.Errorf("agentmarket: migrate lock: %w", err)
	}
	applied := map[string]bool{}
	var exists bool
	if err := tx.QueryRow(ctx, `SELECT to_regclass('agent_market.schema_migrations') IS NOT NULL`).Scan(&exists); err != nil {
		return nil, err
	}
	if exists {
		rows, err := tx.Query(ctx, `SELECT version FROM agent_market.schema_migrations`)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var v string
			if err := rows.Scan(&v); err != nil {
				rows.Close()
				return nil, err
			}
			applied[v] = true
		}
		rows.Close()
	}
	var did []string
	for _, name := range ups {
		version := strings.TrimSuffix(name, ".up.sql")
		if applied[version] {
			continue
		}
		body, err := migrationFS.ReadFile("migrations/" + name)
		if err != nil {
			return nil, err
		}
		if _, err := tx.Exec(ctx, string(body)); err != nil {
			return nil, fmt.Errorf("agentmarket: migration %s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.schema_migrations (version) VALUES ($1) ON CONFLICT DO NOTHING`, version); err != nil {
			return nil, err
		}
		did = append(did, version)
	}
	return did, tx.Commit(ctx)
}

// Down drops the whole schema — the removal path (REMOVAL.md) and tests.
func Down(ctx context.Context, pool *pgxpool.Pool) error {
	_, err := pool.Exec(ctx, DownSQL())
	return err
}

// DownSQL is the removal statement, for scripts and the removal drill.
func DownSQL() string {
	b, _ := migrationFS.ReadFile("migrations/001_agent_market.down.sql")
	return string(b)
}

func upFiles() ([]string, error) {
	entries, err := migrationFS.ReadDir("migrations")
	if err != nil {
		return nil, err
	}
	var out []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".up.sql") {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out, nil
}

// SchemaReady reports whether the schema has been created — the control
// plane refuses to serve (503) rather than 500 on a missing table.
func SchemaReady(ctx context.Context, q interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}) bool {
	var ok bool
	if err := q.QueryRow(ctx, `SELECT to_regclass('agent_market.agent_runs') IS NOT NULL`).Scan(&ok); err != nil {
		return false
	}
	return ok
}
