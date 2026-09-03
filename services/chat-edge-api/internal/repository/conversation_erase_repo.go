// ConversationEraseRepo — the hard-delete cascade for one
// conversation, in ONE transaction.
//
// This file is the single authoritative list of every chat-schema
// table a conversation's content can be sitting in. When a new
// content-bearing table gains a conversation_id column, it must be
// added HERE (and to the coverage test) or the erasure is
// incomplete — the test in conversation_erase_coverage_test.go fails
// on any chat.* table with a conversation_id column that is neither
// in the cascade nor in the documented keep-list.
//
// Deleted (content-bearing or content-referencing):
//   - chat.feedback                  (via the conversation's messages)
//   - chat.canvas_artifacts          (artifact content)
//   - chat.response_context_sources  (evidence excerpts)
//   - chat.conversation_states       (structured summaries)
//   - chat.shared_conversations      (share links into the messages)
//   - chat.cloud_scheduled_tasks     (task prompts target the conversation)
//   - chat.conversation_files        (rows; storage OBJECTS are deleted
//     by the service layer before this transaction — see
//     TranscriptRetentionService)
//   - chat.messages_masked           (the transcript itself)
//   - chat.conversations             (the row)
//
// Kept deliberately (counts/audit rows, no user content):
//   - chat.cloud_task_runs    — fire/outcome audit, content-free
//   - chat.compaction_usage   — token/cost accounting, content-free
//
// Out of this cascade's scope by design (documented in
// docs/DATA_RETENTION_PLAN.md): chat.user_memories / memory_events
// (derived facts, erased by the user-scoped purge path), backend
// request_logs (time-based retention, not conversation-addressable),
// and client-local caches (desktop TTL/wipe).
package repository

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type ConversationEraseRepo struct {
	pool *pgxpool.Pool
}

func NewConversationEraseRepo(pool *pgxpool.Pool) *ConversationEraseRepo {
	return &ConversationEraseRepo{pool: pool}
}

// eraseSteps — ordered (dependents before the conversation row).
// Every statement is tenant-scoped. Names feed the audit counts map.
var eraseSteps = []struct {
	Kind string
	SQL  string
}{
	{"feedback", `
		DELETE FROM chat.feedback f
		USING chat.messages_masked m
		WHERE f.message_id = m.id
		  AND m.conversation_id = $1 AND f.tenant_id = $2`},
	{"canvas_artifacts", `
		DELETE FROM chat.canvas_artifacts
		WHERE conversation_id = $1 AND tenant_id = $2`},
	{"response_context_sources", `
		DELETE FROM chat.response_context_sources
		WHERE conversation_id = $1 AND tenant_id = $2`},
	{"conversation_states", `
		DELETE FROM chat.conversation_states
		WHERE conversation_id = $1 AND tenant_id = $2`},
	{"shared_conversations", `
		DELETE FROM chat.shared_conversations
		WHERE conversation_id = $1 AND tenant_id = $2`},
	{"cloud_scheduled_tasks", `
		DELETE FROM chat.cloud_scheduled_tasks
		WHERE conversation_id = $1 AND tenant_id = $2`},
	{"conversation_files", `
		DELETE FROM chat.conversation_files
		WHERE conversation_id = $1 AND tenant_id = $2`},
	{"messages", `
		DELETE FROM chat.messages_masked
		WHERE conversation_id = $1 AND tenant_id = $2`},
	{"conversations", `
		DELETE FROM chat.conversations
		WHERE id = $1 AND tenant_id = $2`},
}

// EraseKinds — the cascade's table kinds, exported for the coverage
// test and the enforcer's dry-run counting.
func EraseKinds() []string {
	out := make([]string, 0, len(eraseSteps))
	for _, s := range eraseSteps {
		out = append(out, s.Kind)
	}
	return out
}

// KeptConversationTables — chat.* tables that HAVE a conversation_id
// column but are deliberately NOT erased (content-free audit or
// accounting rows). The coverage test asserts every conversation_id
// table is in exactly one of the two lists.
var KeptConversationTables = []string{
	"cloud_task_runs",
	"compaction_usage",
}

// HardDeleteConversation removes the conversation and every
// content-bearing dependent in one transaction, and writes the
// deletion-audit row IN THE SAME TRANSACTION — the erasure and its
// evidence commit or roll back together, so neither "deleted but
// unrecorded" nor "recorded but not deleted" can exist.
//
// Returns per-kind deleted counts. Storage OBJECTS for
// conversation_files must be deleted by the caller BEFORE this runs
// (the rows carry the paths).
func (r *ConversationEraseRepo) HardDeleteConversation(
	ctx context.Context, tenantID, convID uuid.UUID,
	ownerUserID *uuid.UUID, actor, rule string,
) (map[string]int, error) {
	tx, err := r.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return nil, fmt.Errorf("erase begin: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	counts := make(map[string]int, len(eraseSteps))
	for _, step := range eraseSteps {
		tag, err := tx.Exec(ctx, step.SQL, convID, tenantID)
		if err != nil {
			return nil, fmt.Errorf("erase %s: %w", step.Kind, err)
		}
		counts[step.Kind] = int(tag.RowsAffected())
	}

	countsJSON, err := json.Marshal(counts)
	if err != nil {
		return nil, fmt.Errorf("erase audit marshal: %w", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO chat.deletion_audit
			(tenant_id, user_id, actor, action, scope, resource_id, rule, counts)
		VALUES ($1, $2, $3, 'conversation_hard_deleted', 'conversation', $4, $5, $6)`,
		tenantID, ownerUserID, actor, convID, rule, countsJSON,
	); err != nil {
		return nil, fmt.Errorf("erase audit insert: %w", err)
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, fmt.Errorf("erase commit: %w", err)
	}
	return counts, nil
}

// CountConversationFootprint returns the same per-kind counts the
// cascade WOULD delete, without deleting anything — the dry-run
// number source. Runs the SELECT-count twin of each delete step.
func (r *ConversationEraseRepo) CountConversationFootprint(
	ctx context.Context, tenantID, convID uuid.UUID,
) (map[string]int, error) {
	countQueries := map[string]string{
		"feedback": `
			SELECT count(*) FROM chat.feedback f
			JOIN chat.messages_masked m ON f.message_id = m.id
			WHERE m.conversation_id = $1 AND f.tenant_id = $2`,
		"canvas_artifacts": `
			SELECT count(*) FROM chat.canvas_artifacts
			WHERE conversation_id = $1 AND tenant_id = $2`,
		"response_context_sources": `
			SELECT count(*) FROM chat.response_context_sources
			WHERE conversation_id = $1 AND tenant_id = $2`,
		"conversation_states": `
			SELECT count(*) FROM chat.conversation_states
			WHERE conversation_id = $1 AND tenant_id = $2`,
		"shared_conversations": `
			SELECT count(*) FROM chat.shared_conversations
			WHERE conversation_id = $1 AND tenant_id = $2`,
		"cloud_scheduled_tasks": `
			SELECT count(*) FROM chat.cloud_scheduled_tasks
			WHERE conversation_id = $1 AND tenant_id = $2`,
		"conversation_files": `
			SELECT count(*) FROM chat.conversation_files
			WHERE conversation_id = $1 AND tenant_id = $2`,
		"messages": `
			SELECT count(*) FROM chat.messages_masked
			WHERE conversation_id = $1 AND tenant_id = $2`,
		"conversations": `
			SELECT count(*) FROM chat.conversations
			WHERE id = $1 AND tenant_id = $2`,
	}
	counts := make(map[string]int, len(countQueries))
	for kind, q := range countQueries {
		var n int
		if err := r.pool.QueryRow(ctx, q, convID, tenantID).Scan(&n); err != nil {
			return nil, fmt.Errorf("footprint %s: %w", kind, err)
		}
		counts[kind] = n
	}
	return counts, nil
}

// eraseCandidate — a conversation the nightly sweep may erase.
type EraseCandidate struct {
	ID     uuid.UUID
	UserID uuid.UUID
}

// ListExpiredConversations — conversations (active, archived or
// pinned alike: retention is a compliance window, not a preference)
// with no activity since the cutoff. Excludes soft-deleted rows —
// those go through ListDeletedPastPurgeWindow.
func (r *ConversationEraseRepo) ListExpiredConversations(
	ctx context.Context, tenantID uuid.UUID, cutoff time.Time, limit int,
) ([]EraseCandidate, error) {
	const q = `
		SELECT id, user_id FROM chat.conversations
		WHERE tenant_id = $1 AND is_deleted = false AND updated_at < $2
		ORDER BY updated_at ASC
		LIMIT $3`
	return r.listCandidates(ctx, q, tenantID, cutoff, limit)
}

// ListDeletedPastPurgeWindow — user-deleted (soft-deleted)
// conversations whose back-end erasure window has elapsed.
func (r *ConversationEraseRepo) ListDeletedPastPurgeWindow(
	ctx context.Context, tenantID uuid.UUID, cutoff time.Time, limit int,
) ([]EraseCandidate, error) {
	const q = `
		SELECT id, user_id FROM chat.conversations
		WHERE tenant_id = $1 AND is_deleted = true AND deleted_at < $2
		ORDER BY deleted_at ASC
		LIMIT $3`
	return r.listCandidates(ctx, q, tenantID, cutoff, limit)
}

func (r *ConversationEraseRepo) listCandidates(
	ctx context.Context, q string, tenantID uuid.UUID, cutoff time.Time, limit int,
) ([]EraseCandidate, error) {
	rows, err := r.pool.Query(ctx, q, tenantID, cutoff, limit)
	if err != nil {
		return nil, fmt.Errorf("list erase candidates: %w", err)
	}
	defer rows.Close()
	out := make([]EraseCandidate, 0)
	for rows.Next() {
		var c EraseCandidate
		if err := rows.Scan(&c.ID, &c.UserID); err != nil {
			return nil, fmt.Errorf("scan erase candidate: %w", err)
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// TenantClass is how much is known about who a tenant belongs to.
//
// Retention numbers are read by people deciding whether deletion is
// safe, and this database also holds rows left behind by integration
// tests. Mixing the two makes a total unreadable — but guessing wrong
// in the other direction is worse, so the classes are deliberately
// asymmetric: a tenant is only ever called test data on POSITIVE
// evidence, and anything unproven counts as real.
type TenantClass string

const (
	// TenantProvisioned — the id was derived, not drawn at random
	// (UUID version 5). Test helpers mint version-4 ids, so a
	// version-5 id came from provisioning: real.
	TenantProvisioned TenantClass = "provisioned"
	// TenantLikelyTest — every signal of a throwaway tenant at once:
	// a random (v4) id, at most two conversations, and none of the
	// traces a person leaves behind (no saved preferences, no
	// memories). Test runs mint one such tenant per test.
	TenantLikelyTest TenantClass = "likely_test"
	// TenantUnproven — a random id that does not match the test
	// shape. Counted as real, always.
	TenantUnproven TenantClass = "unproven"
)

// ClassifyTenant reports what can be proven about a tenant's origin.
// It never deletes and never guesses toward "test".
func (r *ConversationEraseRepo) ClassifyTenant(
	ctx context.Context, tenantID uuid.UUID,
) (TenantClass, error) {
	// UUID version lives in the first nibble of the third group.
	if v := tenantID.Version(); v == 5 {
		return TenantProvisioned, nil
	}
	const q = `
		SELECT
			(SELECT count(*) FROM chat.conversations WHERE tenant_id = $1),
			(SELECT count(*) FROM chat.user_preferences WHERE tenant_id = $1),
			(SELECT count(*) FROM chat.user_memories   WHERE tenant_id = $1)`
	var convs, prefs, memories int
	if err := r.pool.QueryRow(ctx, q, tenantID).Scan(&convs, &prefs, &memories); err != nil {
		return TenantUnproven, fmt.Errorf("classify tenant: %w", err)
	}
	if convs <= 2 && prefs == 0 && memories == 0 {
		return TenantLikelyTest, nil
	}
	return TenantUnproven, nil
}
