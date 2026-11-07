package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"path"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

// ── Export / delete (AM2 Phase G) ────────────────────────────────────────
//
// AccountProvider is Agent Market's part of "export all my data" and of
// account deletion. It satisfies service.AccountDataProvider and is
// registered by chat-edge only while AGENT_MARKET_ENABLED is on (the
// seam: one registration line) — the core never names this schema.
//
//	Export  agent-market/: installs and their settings, runs, steps,
//	        questions and answers, approvals, exceptions, files, artifacts,
//	        the workspace thread, credits spent, the duplicate index, and
//	        the bytes of every uploaded and generated file.
//	Stop    at the deletion request: queued, waiting and running runs are
//	        cancelled, open cards closed.
//	Purge   the person's part (AM4): conversations, settings, unused files,
//	        failed rounds, preferences, reports, shares, access row. A
//	        finished round's evidence is the organisation's accounting record
//	        and stays for its retention; an install left with none is erased
//	        whole. Kept too: the workspace's theme (it belongs to the tenant;
//	        its creator link is cleared).
//
// A user deleted while the flag was off is swept when it is on again
// (SweepDeletedUsers).
type AccountProvider struct {
	store *Store
	files storage.Backend
}

// NewAccountProvider builds the provider over the control plane's store.
func NewAccountProvider(store *Store, files storage.Backend) *AccountProvider {
	return &AccountProvider{store: store, files: files}
}

// Name is the export folder.
func (p *AccountProvider) Name() string { return "agent-market" }

var exportDocs = []struct {
	file, query string
}{
	{"installs.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.agent_installs t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"settings.json", `SELECT COALESCE(json_agg(t), '[]') FROM agent_market.agent_install_defaults t
		WHERE t.install_id IN (SELECT id FROM agent_market.agent_installs WHERE tenant_id = $1 AND user_id = $2)`},
	{"runs.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.agent_runs t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"steps.json", `SELECT COALESCE(json_agg(t ORDER BY t.started_at), '[]') FROM agent_market.agent_run_steps t
		WHERE t.run_id IN (SELECT id FROM agent_market.agent_runs WHERE tenant_id = $1 AND user_id = $2)`},
	{"clarifications.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.agent_run_clarifications t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"approvals.json", `SELECT COALESCE(json_agg(t), '[]') FROM agent_market.agent_run_approvals t
		WHERE t.run_id IN (SELECT id FROM agent_market.agent_runs WHERE tenant_id = $1 AND user_id = $2)`},
	{"exceptions.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.agent_run_exceptions t
		WHERE t.run_id IN (SELECT id FROM agent_market.agent_runs WHERE tenant_id = $1 AND user_id = $2)`},
	{"files.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.agent_files t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"artifacts.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.agent_output_artifacts t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"thread.json", `SELECT COALESCE(json_agg(t ORDER BY t.rev), '[]') FROM agent_market.agent_thread_items t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"budgets.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.agent_budget_ledger t
		WHERE t.install_id IN (SELECT id FROM agent_market.agent_installs WHERE tenant_id = $1 AND user_id = $2)`},
	{"document_index.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.document_index t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	// AM3: the person's place on the access list, their rail preferences,
	// the sign-offs they gave or their runs received, the shares they made or
	// opened, and the problems they reported.
	{"membership.json", `SELECT COALESCE(json_agg(t), '[]') FROM agent_market.org_members t
		WHERE $1::uuid IS NOT NULL AND t.user_id = $2`},
	{"preferences.json", `SELECT COALESCE(json_agg(t), '[]') FROM agent_market.user_prefs t
		WHERE $1::uuid IS NOT NULL AND t.user_id = $2`},
	{"reviews.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.run_reviews t
		WHERE t.reviewer_user_id = $2 OR t.run_id IN (SELECT id FROM agent_market.agent_runs WHERE tenant_id = $1 AND user_id = $2)`},
	{"shares.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.run_shares t
		WHERE $1::uuid IS NOT NULL AND (t.owner_user_id = $2 OR t.recipient_user_id = $2)`},
	{"problem_reports.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.problem_reports t
		WHERE $1::uuid IS NOT NULL AND t.user_id = $2`},
	// AM6: reminders, notifications, e-mail drafts, and the sends of the person's rounds or that they approved
	{"reminders.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.reminders t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"notifications.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.notifications t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"email_drafts.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.email_drafts t
		WHERE t.tenant_id = $1 AND t.user_id = $2`},
	{"email_sends.json", `SELECT COALESCE(json_agg(t ORDER BY t.created_at), '[]') FROM agent_market.email_sends t
		WHERE t.approved_by_user_id = $2 OR t.run_id IN (SELECT id FROM agent_market.agent_runs WHERE tenant_id = $1 AND user_id = $2)`},
}

// Export writes the user's Agent Market data in this tenant.
func (p *AccountProvider) Export(ctx context.Context, tenantID, userID uuid.UUID, add func(path string, r io.Reader) error) ([]string, error) {
	for _, d := range exportDocs {
		var doc json.RawMessage
		if err := p.store.Pool.QueryRow(ctx, d.query, tenantID, userID).Scan(&doc); err != nil {
			return nil, fmt.Errorf("%s: %w", d.file, err)
		}
		if err := add(d.file, bytes.NewReader(doc)); err != nil {
			return nil, err
		}
	}
	type obj struct{ dir, id, name, key string }
	var objs []obj
	rows, err := p.store.Pool.Query(ctx, `SELECT 'files/' || install_id::text, id::text, name, storage_key FROM agent_market.agent_files
		WHERE tenant_id = $1 AND user_id = $2 AND status <> 'removed'
		UNION ALL
		SELECT 'outputs/' || run_id::text, id::text, name, storage_key FROM agent_market.agent_output_artifacts
		WHERE tenant_id = $1 AND user_id = $2`, tenantID, userID)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var o obj
		if err := rows.Scan(&o.dir, &o.id, &o.name, &o.key); err != nil {
			rows.Close()
			return nil, err
		}
		objs = append(objs, o)
	}
	rows.Close()
	missing := 0
	firstErr := ""
	for _, o := range objs {
		body, _, _, err := p.files.Download(ctx, o.key)
		if err != nil {
			missing++
			if firstErr == "" {
				firstErr = err.Error()
			}
			continue
		}
		err = add(o.dir+"/"+o.id[:8]+"_"+SafeName(o.name), body)
		body.Close()
		if err != nil {
			return nil, err
		}
	}
	lines := []string{"agent-market/  agent ที่ติดตั้ง การตั้งค่า รอบทำงาน ขั้นตอน คำถามและคำตอบ ข้อยกเว้น ไฟล์ที่ส่งเข้าไปและไฟล์ที่ agent สร้าง" +
		" / installed agents, settings, runs, steps, questions and answers, exceptions, files in and out", DeletionNote}
	if missing > 0 {
		lines = append(lines, fmt.Sprintf("agent-market/  อ่านไฟล์ไม่ได้ %d ไฟล์ / %d files could not be read (%s)", missing, missing, firstErr))
	}
	return lines, nil
}

// Stop cancels the user's queued, waiting and running runs and closes their open cards.
func (p *AccountProvider) Stop(ctx context.Context, userID uuid.UUID) (map[string]int, error) {
	tag, err := p.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET cancel_requested = true,
		status = CASE WHEN status IN ('queued', 'awaiting_input') THEN 'cancelled' ELSE status END,
		status_reason = CASE WHEN status IN ('queued', 'awaiting_input') THEN 'account_deletion' ELSE status_reason END,
		finished_at = CASE WHEN status IN ('queued', 'awaiting_input') THEN now() ELSE finished_at END
		WHERE user_id = $1 AND status IN ('queued', 'running', 'awaiting_input')`, userID)
	if err != nil {
		return nil, err
	}
	// AM6: a round that was stopped delivered nothing, and is charged nothing
	if _, err := p.store.Pool.Exec(ctx, fmt.Sprintf(settleSQL, `r.user_id = $1`), userID); err != nil {
		return nil, err
	}
	cards, err := p.store.Pool.Exec(ctx, `UPDATE agent_market.agent_run_clarifications SET status = 'cancelled'
		WHERE user_id = $1 AND status IN ('open', 'interpreting')`, userID)
	if err != nil {
		return nil, err
	}
	// AM6: no reminder of theirs falls due any more, and nothing is shown to them any more
	reminders, err := p.store.Pool.Exec(ctx, `UPDATE agent_market.reminders SET status = 'stopped', updated_at = now()
		WHERE user_id = $1 AND status = 'active'`, userID)
	if err != nil {
		return nil, err
	}
	if _, err := p.store.Pool.Exec(ctx, `UPDATE agent_market.notifications SET dismissed_at = now()
		WHERE user_id = $1 AND dismissed_at IS NULL`, userID); err != nil {
		return nil, err
	}
	return map[string]int{"runs_stopped": int(tag.RowsAffected()), "cards_closed": int(cards.RowsAffected()),
		"reminders_stopped": int(reminders.RowsAffected())}, nil
}

// Purge erases what Agent Market keeps for the PERSON, in every tenant: their
// conversations, their installs' settings and remembered answers, files never
// used in a round, failed or cancelled rounds, preferences, problem reports,
// shares and their place on the access list (AM4). A finished round's
// evidence — the files sent in, the files made and the computed table behind
// them — is the organisation's accounting record, not the person's: it stays
// for the organisation's retention (retention.go), and removing it earlier is
// an admin's decision with a reason (access.go). Their installs are marked
// removed; an install with no such record left is erased whole.
func (p *AccountProvider) Purge(ctx context.Context, userID uuid.UUID) (map[string]int, error) {
	counts := map[string]int{}
	installs, err := p.store.ids(ctx, `SELECT id FROM agent_market.agent_installs WHERE user_id = $1`, userID)
	if err != nil {
		return counts, err
	}
	if len(installs) > 0 {
		counts["objects"], err = p.store.purgePersonal(ctx, p.files, installs)
		if err != nil {
			return counts, err
		}
		if _, err := p.store.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET status = 'removed',
			removed_at = COALESCE(removed_at, now()), pinned_at = NULL WHERE id = ANY($1) AND status <> 'removed'`, installs); err != nil {
			return counts, err
		}
		empty, err := p.store.ids(ctx, `SELECT i.id FROM agent_market.agent_installs i WHERE i.id = ANY($1)
			AND NOT EXISTS (SELECT 1 FROM agent_market.agent_runs r WHERE r.install_id = i.id AND r.kind = 'run'
				AND `+kept("r")+` AND r.evidence_removed_at IS NULL)`, installs)
		if err != nil {
			return counts, err
		}
		if len(empty) > 0 {
			objs, n, err := p.store.PurgeInstalls(ctx, p.files, empty)
			counts["objects"] += objs
			counts["installs"] = n
			if err != nil {
				return counts, err
			}
		}
		var records int
		_ = p.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs WHERE install_id = ANY($1) AND kind = 'run'
			AND `+kept("")+` AND evidence_removed_at IS NULL`, installs).Scan(&records)
		counts["org_records_kept"] = records
	}
	for _, q := range []struct{ kind, sql string }{
		{"thread_items", `DELETE FROM agent_market.agent_thread_items WHERE user_id = $1
			AND NOT (kind IN ('progress', 'outputs') AND run_id IN (SELECT id FROM agent_market.agent_runs WHERE ` + kept("") + `))`},
		{"theme_creator_cleared", `UPDATE agent_market.agent_themes SET created_by = NULL WHERE created_by = $1`},
		// AM3: their preferences, problem reports, the shares addressed to them
		// or made by them, and their access row. A sign-off they gave on someone
		// else's run stays on that run's record (the name is printed on the
		// signed files) without their email or id.
		{"preferences", `DELETE FROM agent_market.user_prefs WHERE user_id = $1`},
		{"problem_reports", `DELETE FROM agent_market.problem_reports WHERE user_id = $1`},
		{"shares_received", `DELETE FROM agent_market.run_shares WHERE recipient_user_id = $1 OR owner_user_id = $1`},
		{"reviews_anonymised", `UPDATE agent_market.run_reviews SET reviewer_user_id = '00000000-0000-0000-0000-000000000000',
			reviewer_email = '' WHERE reviewer_user_id = $1`},
		{"membership", `DELETE FROM agent_market.org_members WHERE user_id = $1`},
		// AM6: their reminders, notifications and e-mail drafts. What was sent, to whom and with which
		// files is the organisation's record of what left it: it stays with its round, and no longer
		// names the person who approved it by id or address (as a sign-off does not).
		{"reminders", `DELETE FROM agent_market.reminders WHERE user_id = $1`},
		{"notifications", `DELETE FROM agent_market.notifications WHERE user_id = $1`},
		{"email_drafts", `DELETE FROM agent_market.email_drafts WHERE user_id = $1`},
		{"email_sends_anonymised", `UPDATE agent_market.email_sends SET approved_by_user_id = '00000000-0000-0000-0000-000000000000',
			approved_by_email = '' WHERE approved_by_user_id = $1`},
	} {
		tag, err := p.store.Pool.Exec(ctx, q.sql, userID)
		if err != nil {
			return counts, fmt.Errorf("%s: %w", q.kind, err)
		}
		counts[q.kind] += int(tag.RowsAffected())
	}
	return counts, nil
}

// DeletionNote is what a person reads about their Agent Market data when
// they delete their account (the export's README carries it too).
const DeletionNote = "agent-market/  เมื่อลบบัญชี แชทกับ agent การตั้งค่า และไฟล์ที่ไม่ได้ใช้จะถูกลบ ส่วนไฟล์ที่ส่งเข้าและไฟล์ผลลัพธ์ของรอบที่สำเร็จ" +
	"เป็นหลักฐานทางบัญชีขององค์กร เก็บไว้ตามระยะเวลาที่องค์กรกำหนด การลบก่อนกำหนดเป็นการตัดสินใจของผู้ดูแล" +
	" / on account deletion, your agent chats, settings and unused files are deleted; the files in and out of finished rounds" +
	" are the organisation's accounting records and are kept for its retention period — only an admin can remove them earlier"

func collectKeys(v any, keys map[string]bool) {
	switch t := v.(type) {
	case string:
		if strings.HasPrefix(t, StoragePrefix+"/") && path.Clean(t) == t {
			keys[t] = true
		}
	case []any:
		for _, x := range t {
			collectKeys(x, keys)
		}
	case map[string]any:
		for _, x := range t {
			collectKeys(x, keys)
		}
	}
}

// SweepDeletedUsers catches up with account deletions that happened while
// the flag was off, using only what the core recorded itself
// (chat.deletion_audit, read by user id; no key crosses schemas): a user
// whose account was purged gets their Agent Market data purged; a user
// whose deletion was requested and not yet purged gets their work stopped.
// It never deletes on a guess (a user merely missing from a table).
// Returns how many users it purged.
func (p *AccountProvider) SweepDeletedUsers(ctx context.Context) (int, error) {
	var audit bool
	if err := p.store.Pool.QueryRow(ctx, `SELECT to_regclass('chat.deletion_audit') IS NOT NULL`).Scan(&audit); err != nil || !audit {
		return 0, err
	}
	ids := func(q string) ([]uuid.UUID, error) {
		rows, err := p.store.Pool.Query(ctx, q)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		var out []uuid.UUID
		for rows.Next() {
			var id uuid.UUID
			if rows.Scan(&id) == nil {
				out = append(out, id)
			}
		}
		return out, rows.Err()
	}
	purged, err := ids(`SELECT DISTINCT i.user_id FROM agent_market.agent_installs i
		WHERE EXISTS (SELECT 1 FROM chat.deletion_audit a WHERE a.user_id = i.user_id AND a.action = 'account_purged')`)
	if err != nil {
		return 0, err
	}
	for _, id := range purged {
		if _, err := p.Purge(ctx, id); err != nil {
			return 0, err
		}
	}
	requested, err := ids(`SELECT DISTINCT r.user_id FROM agent_market.agent_runs r
		WHERE r.status IN ('queued', 'running', 'awaiting_input')
		  AND EXISTS (SELECT 1 FROM chat.deletion_audit a WHERE a.user_id = r.user_id AND a.action = 'account_deletion_requested')`)
	if err != nil {
		return len(purged), err
	}
	for _, id := range requested {
		if _, err := p.Stop(ctx, id); err != nil {
			return len(purged), err
		}
	}
	return len(purged), nil
}
