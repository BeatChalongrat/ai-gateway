// AccountDeletionService — FIX132 C: a user deletes their own account.
//
// The backend (portal) owns the account and orchestrates. It calls two
// internal endpoints here (handler/internal_account.go, gated by
// INTERNAL_SERVICE_SECRET like /internal/auth/revoke):
//
//  1. RequestDeletion — the moment the user confirms. Every live chat of
//     the user is hidden (soft-deleted, deleted_at = now, so the nightly
//     deleted-chat purge erases it 30 days later like any deleted chat),
//     their scheduled cloud tasks stop, parked questions close, and every
//     access token issued to them so far is refused here from now on.
//     Idempotent.
//  2. Purge — when the backend's 30-day window has passed. Everything the
//     user owns in this database is erased: each conversation through the
//     one-transaction cascade (file bytes first), then every user-keyed
//     table in PurgeUserTables, the export files, the identity rows. What
//     is kept is listed in KeptUserTables and why. Idempotent: a second
//     call finds nothing and erases nothing.
//
// Both write a chat.deletion_audit row with ids and counts only.
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// Audit vocabulary for account deletion.
const (
	AuditActionAccountDeletionRequested = "account_deletion_requested"
	AuditActionAccountPurged            = "account_purged"
	accountPurgeActor                   = "system:account-purge"
)

// userRevoker is the slice of auth.Revocations the request step needs.
type userRevoker interface {
	RevokeUserBefore(ctx context.Context, sub string, at time.Time) error
}

// streamCanceller stops a conversation's running turn (MessageService).
type streamCanceller interface {
	CancelStream(convID uuid.UUID) bool
}

// PurgeUserTables — every table outside the conversation cascade that a
// user's rows live in, in delete order (children first), with the
// column that names the user. The coverage test
// (fix132_account_purge_test.go) fails when a chat.* or identity.* table
// carries a user column and is in neither this list, the conversation
// cascade, nor KeptUserTables.
var PurgeUserTables = []struct {
	Kind   string
	Table  string
	Column string
}{
	{"feedback", "chat.feedback", "user_id"},
	{"canvas_artifacts", "chat.canvas_artifacts", "user_id"},
	{"response_context_sources", "chat.response_context_sources", "user_id"},
	{"conversation_states", "chat.conversation_states", "user_id"},
	{"pending_questions", "chat.pending_questions", "user_id"},
	{"cloud_task_runs", "chat.cloud_task_runs", "user_id"},
	{"cloud_scheduled_tasks", "chat.cloud_scheduled_tasks", "user_id"},
	{"conversation_files", "chat.conversation_files", "user_id"},
	{"memory_events", "chat.memory_events", "user_id"},
	{"user_memories", "chat.user_memories", "user_id"},
	{"projects", "chat.projects", "user_id"},
	{"folders", "chat.folders", "user_id"},
	{"user_preferences", "chat.user_preferences", "user_id"},
	{"user_skills", "chat.user_skills", "user_id"},
	{"data_export_requests", "chat.data_export_requests", "user_id"},
	{"refresh_tokens", "identity.refresh_tokens", "user_id"},
	{"invitation_tokens", "identity.invitation_tokens", "created_by"},
}

// KeptUserTables — tables with a user column the purge deliberately
// leaves, and why. Both hold ids and numbers only, no content.
var KeptUserTables = map[string]string{
	"chat.deletion_audit":   "the evidence of the deletions themselves: ids, kinds and counts",
	"chat.compaction_usage": "token and cost accounting for summaries already billed; content-free",
}

type AccountDeletionService struct {
	pool       *pgxpool.Pool
	erase      *repository.ConversationEraseRepo
	files      *repository.FileRepo
	audit      *repository.DeletionAuditRepo
	storage    objectStore
	exportRoot string
	revoker    userRevoker
	streams    streamCanceller
	now        func() time.Time
}

func NewAccountDeletionService(
	pool *pgxpool.Pool,
	erase *repository.ConversationEraseRepo,
	files *repository.FileRepo,
	audit *repository.DeletionAuditRepo,
	storage objectStore,
	exportRoot string,
) *AccountDeletionService {
	if exportRoot == "" {
		exportRoot = "./data"
	}
	return &AccountDeletionService{
		pool: pool, erase: erase, files: files, audit: audit,
		storage: storage, exportRoot: exportRoot, now: time.Now,
	}
}

// SetRevoker wires the token revocation store (Redis in production).
func (s *AccountDeletionService) SetRevoker(r userRevoker) { s.revoker = r }

// SetStreamCanceller wires the running-turn canceller.
func (s *AccountDeletionService) SetStreamCanceller(c streamCanceller) { s.streams = c }

// DeletionRequestResult — what RequestDeletion did, counts only.
type DeletionRequestResult struct {
	ConversationsHidden int  `json:"conversations_hidden"`
	TasksStopped        int  `json:"tasks_stopped"`
	SessionsRevoked     bool `json:"sessions_revoked"`
}

// RequestDeletion hides everything of the user's at once and ends their
// sessions here. See the file comment.
func (s *AccountDeletionService) RequestDeletion(
	ctx context.Context, userID uuid.UUID,
) (*DeletionRequestResult, error) {
	if userID == uuid.Nil {
		return nil, fmt.Errorf("account deletion: empty user id")
	}
	res := &DeletionRequestResult{}
	hiddenByTenant := map[uuid.UUID]int{}
	var hidden []uuid.UUID

	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return nil, fmt.Errorf("account deletion begin: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	rows, err := tx.Query(ctx, `
		UPDATE chat.conversations
		SET is_deleted = true, deleted_at = NOW(), updated_at = NOW()
		WHERE user_id = $1 AND is_deleted = false
		RETURNING id, tenant_id`, userID)
	if err != nil {
		return nil, fmt.Errorf("hide conversations: %w", err)
	}
	for rows.Next() {
		var id, tenant uuid.UUID
		if err := rows.Scan(&id, &tenant); err != nil {
			rows.Close()
			return nil, fmt.Errorf("hide conversations scan: %w", err)
		}
		hidden = append(hidden, id)
		hiddenByTenant[tenant]++
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("hide conversations: %w", err)
	}
	res.ConversationsHidden = len(hidden)

	tag, err := tx.Exec(ctx, `
		UPDATE chat.cloud_scheduled_tasks
		SET enabled = false, paused_reason = 'account_deleted', updated_at = NOW()
		WHERE user_id = $1 AND enabled = true`, userID)
	if err != nil {
		return nil, fmt.Errorf("stop cloud tasks: %w", err)
	}
	res.TasksStopped = int(tag.RowsAffected())
	if _, err := tx.Exec(ctx, `
		UPDATE chat.pending_questions SET status = 'cancelled', closed_at = NOW()
		WHERE user_id = $1 AND status = 'pending'`, userID); err != nil {
		return nil, fmt.Errorf("close pending questions: %w", err)
	}

	// One audit row per tenant the user had chats in (the audit table is
	// tenant-keyed); a user with no chats still gets one, under the tenant
	// their identity row names.
	if len(hiddenByTenant) == 0 {
		hiddenByTenant[s.identityTenant(ctx, tx, userID)] = 0
	}
	uid := userID
	for tenant, n := range hiddenByTenant {
		countsJSON, _ := json.Marshal(map[string]int{
			"conversations_hidden": n, "cloud_tasks_stopped": res.TasksStopped,
		})
		if _, err := tx.Exec(ctx, `
			INSERT INTO chat.deletion_audit
				(tenant_id, user_id, actor, action, scope, resource_id, rule, counts)
			VALUES ($1, $2, $3, $4, 'user', $2, $5, $6)`,
			tenant, uid, "user:"+userID.String(), AuditActionAccountDeletionRequested,
			model.AuditRuleUserRequest, countsJSON,
		); err != nil {
			return nil, fmt.Errorf("account deletion audit: %w", err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, fmt.Errorf("account deletion commit: %w", err)
	}

	// Every token issued up to now is refused here from the next request.
	// A store failure is reported, not fatal: the chats are already hidden,
	// the backend refuses the account everywhere else, and the token's own
	// expiry (60 minutes) bounds the window.
	if s.revoker != nil {
		if err := s.revoker.RevokeUserBefore(ctx, userID.String(), s.now().UTC()); err != nil {
			slog.Error("account deletion: session revocation failed", "user", userID.String(), "err", err)
		} else {
			res.SessionsRevoked = true
		}
	}
	if s.streams != nil {
		for _, id := range hidden {
			s.streams.CancelStream(id)
		}
	}
	slog.Warn("account deletion requested",
		"user", userID.String(),
		"conversations_hidden", res.ConversationsHidden,
		"tasks_stopped", res.TasksStopped,
		"sessions_revoked", res.SessionsRevoked)
	return res, nil
}

func (s *AccountDeletionService) identityTenant(ctx context.Context, q pgx.Tx, userID uuid.UUID) uuid.UUID {
	var tenant uuid.UUID
	if err := q.QueryRow(ctx,
		`SELECT tenant_id FROM identity.users WHERE id = $1`, userID).Scan(&tenant); err != nil {
		return uuid.Nil
	}
	return tenant
}

// Purge erases everything the user owns in this database. See the file
// comment. Returns per-kind counts.
func (s *AccountDeletionService) Purge(ctx context.Context, userID uuid.UUID) (map[string]int, error) {
	if userID == uuid.Nil {
		return nil, fmt.Errorf("account purge: empty user id")
	}
	counts := map[string]int{}
	owner := userID

	// 1. Every conversation (hidden or not, any tenant) through the
	//    existing cascade, file objects first.
	rows, err := s.pool.Query(ctx,
		`SELECT id, tenant_id FROM chat.conversations WHERE user_id = $1`, userID)
	if err != nil {
		return nil, fmt.Errorf("list user conversations: %w", err)
	}
	type convRef struct{ id, tenant uuid.UUID }
	var convs []convRef
	for rows.Next() {
		var c convRef
		if err := rows.Scan(&c.id, &c.tenant); err != nil {
			rows.Close()
			return nil, fmt.Errorf("scan user conversation: %w", err)
		}
		convs = append(convs, c)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for _, c := range convs {
		if files, err := s.files.ListByConversation(ctx, c.tenant, c.id); err == nil {
			for i := range files {
				if files[i].StoragePath != "" {
					if err := s.storage.Delete(ctx, files[i].StoragePath); err == nil {
						counts["file_objects"]++
					}
				}
			}
		}
		n, err := s.erase.HardDeleteConversation(ctx, c.tenant, c.id, &owner,
			accountPurgeActor, model.AuditRuleUserRequest)
		if err != nil {
			return counts, fmt.Errorf("erase conversation %s: %w", c.id, err)
		}
		for k, v := range n {
			counts[k] += v
		}
	}

	// 2. File objects of any row the cascade did not reach.
	frows, err := s.pool.Query(ctx,
		`SELECT storage_path FROM chat.conversation_files WHERE user_id = $1`, userID)
	if err != nil {
		return counts, fmt.Errorf("list leftover files: %w", err)
	}
	var leftover []string
	for frows.Next() {
		var p string
		if err := frows.Scan(&p); err == nil && p != "" {
			leftover = append(leftover, p)
		}
	}
	frows.Close()
	for _, p := range leftover {
		if err := s.storage.Delete(ctx, p); err == nil {
			counts["file_objects"]++
		}
	}

	// 3. Export files on disk, before their rows go.
	erows, err := s.pool.Query(ctx,
		`SELECT DISTINCT tenant_id, COALESCE(file_path, '') FROM chat.data_export_requests WHERE user_id = $1`, userID)
	if err != nil {
		return counts, fmt.Errorf("list exports: %w", err)
	}
	exportDirs := map[string]bool{}
	for erows.Next() {
		var tenant uuid.UUID
		var path string
		if err := erows.Scan(&tenant, &path); err != nil {
			continue
		}
		if path != "" {
			if err := os.Remove(path); err == nil {
				counts["export_files"]++
			}
		}
		exportDirs[filepath.Join(s.exportRoot, "exports", tenant.String(), userID.String())] = true
	}
	erows.Close()
	for dir := range exportDirs {
		_ = os.RemoveAll(dir)
	}

	// 4. Every user-keyed table, then the identity row, in one transaction
	//    with the audit row.
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return counts, fmt.Errorf("account purge begin: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var identityTenant uuid.UUID
	var email string
	_ = tx.QueryRow(ctx, `SELECT tenant_id, email FROM identity.users WHERE id = $1`, userID).
		Scan(&identityTenant, &email)

	for _, t := range PurgeUserTables {
		tag, err := tx.Exec(ctx, `DELETE FROM `+t.Table+` WHERE `+t.Column+` = $1`, userID)
		if err != nil {
			return counts, fmt.Errorf("purge %s: %w", t.Table, err)
		}
		counts[t.Kind] += int(tag.RowsAffected())
	}
	if _, err := tx.Exec(ctx,
		`UPDATE identity.invitation_tokens SET used_by = NULL WHERE used_by = $1`, userID); err != nil {
		return counts, fmt.Errorf("purge invitation redemptions: %w", err)
	}
	tag, err := tx.Exec(ctx, `DELETE FROM identity.users WHERE id = $1`, userID)
	if err != nil {
		return counts, fmt.Errorf("purge identity user: %w", err)
	}
	counts["identity_users"] = int(tag.RowsAffected())
	// The workspace name chat-edge derived from the email ("<local>'s
	// workspace") goes too when it is the one this user's sign-in wrote.
	if email != "" {
		if _, err := tx.Exec(ctx, `
			UPDATE identity.tenants SET name = 'workspace', updated_at = NOW()
			WHERE id = $1 AND name = $2`, identityTenant, deriveTenantName(email, userID)); err != nil {
			return counts, fmt.Errorf("purge workspace name: %w", err)
		}
	}

	countsJSON, _ := json.Marshal(counts)
	if _, err := tx.Exec(ctx, `
		INSERT INTO chat.deletion_audit
			(tenant_id, user_id, actor, action, scope, resource_id, rule, counts)
		VALUES ($1, $2, $3, $4, 'user', $2, $5, $6)`,
		identityTenant, owner, accountPurgeActor, AuditActionAccountPurged,
		model.AuditRuleUserRequest, countsJSON,
	); err != nil {
		return counts, fmt.Errorf("account purge audit: %w", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return counts, fmt.Errorf("account purge commit: %w", err)
	}
	slog.Warn("account purged", "user", userID.String(), "counts", counts)
	return counts, nil
}
