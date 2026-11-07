package service

// FIX127 A2 — uploads that never became part of a message expire.
//
// A file row is created the moment the bytes land, before the user presses
// send, and until FIX127 nothing ever removed a row whose message_id stayed
// NULL: a removed chip, an abandoned draft, a tab closed mid-upload. On the
// live system that was 470 such rows (104 MB of uploads, 44 MB of them
// real) — and until FIX127 A1 every one of them was read into the model's
// context on each later turn of its conversation.
//
// The rule follows the large vendors' transient-file policy (files not part
// of a conversation expire in 48 hours; deleted content is purged within 30
// days): an upload that is not attached to any sent message
// UNATTACHED_FILE_TTL after it was created is deleted — row and blob — and
// the deletion is audited. Files of a deleted conversation are the
// transcript retention sweep's job (deleted_conversation_purge_days).
//
// Modes (UNATTACHED_FILE_SWEEP_MODE):
//   dry_run  — the default. Every pass counts what it WOULD delete, per
//              account, logs it and writes one retention_dry_run audit row.
//              Nothing is removed. The first run on the live system is this
//              report; the owner turns deletion on after reading it.
//   delete   — the pass removes up to maxUnattachedPerRun candidates, oldest
//              first, each one re-checked at delete time (a send that linked
//              the row in between keeps it), and audits the count.
//   off      — the sweep does not run.

import (
	"context"
	"log/slog"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// maxUnattachedPerRun bounds one pass; the backlog drains over the next
// passes. 500 rows an hour clears the live backlog in one pass.
const maxUnattachedPerRun = 500

// DefaultUnattachedFileTTL is the vendor-shaped default: 48 hours.
const DefaultUnattachedFileTTL = 48 * time.Hour

// DefaultUnattachedSweepInterval — hourly, so a 48-hour rule is honoured
// within 49 hours, not within 72.
const DefaultUnattachedSweepInterval = time.Hour

// Sweep modes.
const (
	UnattachedSweepDryRun = "dry_run"
	UnattachedSweepDelete = "delete"
	UnattachedSweepOff    = "off"
)

// unattachedStore is the slice of FileRepo the sweep needs (an interface
// so the unit test can run it against a fake).
type unattachedStore interface {
	SummarizeUnattachedOlderThan(ctx context.Context, cutoff time.Time) ([]repository.UnattachedTotal, error)
	ListUnattachedOlderThan(ctx context.Context, cutoff time.Time, limit int) ([]model.ConversationFile, error)
	DeleteIfStillUnattached(ctx context.Context, fileID uuid.UUID) (bool, error)
}

type auditSink interface {
	Insert(ctx context.Context, e *model.DeletionAuditEntry) error
	ListByTenant(ctx context.Context, tenantID uuid.UUID, action string, limit int) ([]model.DeletionAuditEntry, error)
}

// SweepAuditTenant — FIX128: the sweep writes ONE summary row per run under
// this system tenant id (the nil UUID), with per-tenant counts in details,
// instead of one row per account. Until FIX128 a dry run wrote a row per
// tenant on every process start: 966 rows in a day, ~160 of them per start
// for synthetic test tenants.
var SweepAuditTenant = uuid.Nil

// sweepAuditActor is the actor on every row this sweep writes.
const sweepAuditActor = "system:unattached_file_sweep"

type UnattachedFileSweep struct {
	files    unattachedStore
	storage  objectStore
	audit    auditSink
	TTL      time.Duration
	Mode     string
	Interval time.Duration
	now      func() time.Time
}

// NewUnattachedFileSweep — mode is normalised; anything but "delete" or
// "off" is the safe default, dry_run.
func NewUnattachedFileSweep(
	files unattachedStore, storage objectStore, audit auditSink,
	ttl time.Duration, mode string, interval time.Duration,
) *UnattachedFileSweep {
	if ttl <= 0 {
		ttl = DefaultUnattachedFileTTL
	}
	if interval <= 0 {
		interval = DefaultUnattachedSweepInterval
	}
	mode = strings.ToLower(strings.TrimSpace(mode))
	if mode != UnattachedSweepDelete && mode != UnattachedSweepOff {
		mode = UnattachedSweepDryRun
	}
	return &UnattachedFileSweep{
		files: files, storage: storage, audit: audit,
		TTL: ttl, Mode: mode, Interval: interval, now: time.Now,
	}
}

// UnattachedSweepStats is one pass's outcome — counts only, never content.
type UnattachedSweepStats struct {
	Mode       string                       `json:"mode"`
	Cutoff     time.Time                    `json:"cutoff"`
	Candidates int                          `json:"candidates"`
	Bytes      int64                        `json:"bytes"`
	Accounts   []repository.UnattachedTotal `json:"accounts"`
	Deleted    int                          `json:"deleted"`
	Kept       int                          `json:"kept"` // linked between list and delete
	Errors     int                          `json:"errors"`
}

// RunOnce executes one pass in the configured mode.
func (s *UnattachedFileSweep) RunOnce(ctx context.Context) UnattachedSweepStats {
	cutoff := s.now().Add(-s.TTL)
	stats := UnattachedSweepStats{Mode: s.Mode, Cutoff: cutoff}
	if s.Mode == UnattachedSweepOff {
		return stats
	}
	accounts, err := s.files.SummarizeUnattachedOlderThan(ctx, cutoff)
	if err != nil {
		slog.Warn("unattached file sweep: summarize failed", "err", err)
		stats.Errors++
		return stats
	}
	stats.Accounts = accounts
	for _, a := range accounts {
		stats.Candidates += a.Count
		stats.Bytes += a.Bytes
	}
	if stats.Candidates == 0 {
		return stats
	}

	if s.Mode == UnattachedSweepDryRun {
		for _, a := range accounts {
			slog.Info("unattached file sweep: dry run",
				"tenant_id", a.TenantID.String(), "user_id", a.UserID.String(),
				"files", a.Count, "bytes", a.Bytes, "ttl", s.TTL.String())
		}
		// Audited only when the numbers moved since the last dry-run row:
		// a restart, or an hour with nothing new, writes nothing.
		if s.dryRunChanged(ctx, stats.Candidates, stats.Bytes) {
			s.auditSummary(ctx, model.AuditActionRetentionDryRun, "unattached_upload_dry_run", cutoff, stats.Candidates, stats.Bytes, perTenant(accounts), 0)
		}
		return stats
	}

	rows, err := s.files.ListUnattachedOlderThan(ctx, cutoff, maxUnattachedPerRun)
	if err != nil {
		slog.Warn("unattached file sweep: list failed", "err", err)
		stats.Errors++
		return stats
	}
	deletedPer := map[[2]uuid.UUID]int{}
	var deletedBytes int64
	for i := range rows {
		f := &rows[i]
		ok, err := s.files.DeleteIfStillUnattached(ctx, f.ID)
		if err != nil {
			stats.Errors++
			continue
		}
		if !ok {
			stats.Kept++
			continue
		}
		if s.storage != nil {
			if err := s.storage.Delete(ctx, f.StoragePath); err != nil {
				// The row is gone, so the model can never see the file
				// again; a leftover blob is storage, not a leak of context.
				slog.Warn("unattached file sweep: blob delete failed",
					"file_id", f.ID.String(), "err", err)
			}
		}
		stats.Deleted++
		deletedBytes += f.FileSizeBytes
		deletedPer[[2]uuid.UUID{f.TenantID, f.UserID}]++
	}
	var done []repository.UnattachedTotal
	for k, n := range deletedPer {
		done = append(done, repository.UnattachedTotal{TenantID: k[0], UserID: k[1], Count: n})
	}
	if stats.Deleted > 0 {
		s.auditSummary(ctx, model.AuditActionFileDeleted, "unattached_upload_expired", cutoff, stats.Deleted, deletedBytes, perTenant(done), stats.Kept)
	}
	slog.Info("unattached file sweep: pass complete",
		"candidates", stats.Candidates, "deleted", stats.Deleted,
		"kept", stats.Kept, "errors", stats.Errors, "ttl", s.TTL.String())
	return stats
}

// perTenant folds per-account totals into one row per tenant (the dry-run
// audit shape); Count is files, Bytes is bytes, and the number of accounts
// rides in UserID being zero.
func perTenant(accounts []repository.UnattachedTotal) []repository.UnattachedTotal {
	idx := map[uuid.UUID]int{}
	var out []repository.UnattachedTotal
	for _, a := range accounts {
		i, ok := idx[a.TenantID]
		if !ok {
			idx[a.TenantID] = len(out)
			out = append(out, repository.UnattachedTotal{TenantID: a.TenantID})
			i = len(out) - 1
		}
		out[i].Count += a.Count
		out[i].Bytes += a.Bytes
	}
	return out
}

// auditSummary writes the run's one audit row: counts only, never content;
// per-tenant file counts ride in details (capped).
func (s *UnattachedFileSweep) auditSummary(
	ctx context.Context, action, rule string, cutoff time.Time,
	files int, bytes int64, tenants []repository.UnattachedTotal, kept int,
) {
	if s.audit == nil {
		return
	}
	per := map[string]int{}
	for i, t := range tenants {
		if i >= 500 {
			break
		}
		per[t.TenantID.String()] = t.Count
	}
	entry := &model.DeletionAuditEntry{
		TenantID: SweepAuditTenant,
		Actor:    sweepAuditActor,
		Action:   action,
		Scope:    "file",
		Rule:     rule,
		Counts:   map[string]int{"files": files, "tenants": len(tenants)},
		Details: map[string]any{
			"bytes": bytes, "ttl": s.TTL.String(),
			"cutoff": cutoff.UTC().Format(time.RFC3339), "kept": kept,
			"per_tenant": per,
		},
	}
	if err := s.audit.Insert(ctx, entry); err != nil {
		slog.Warn("unattached file sweep: audit write failed", "err", err)
	}
}

// dryRunChanged reports whether the dry-run totals differ from the last
// dry-run summary on record (true when there is none or it cannot be read).
func (s *UnattachedFileSweep) dryRunChanged(ctx context.Context, files int, bytes int64) bool {
	if s.audit == nil {
		return false
	}
	last, err := s.audit.ListByTenant(ctx, SweepAuditTenant, model.AuditActionRetentionDryRun, 1)
	if err != nil || len(last) == 0 {
		return true
	}
	prevBytes, _ := last[0].Details["bytes"].(float64)
	return last[0].Counts["files"] != files || int64(prevBytes) != bytes
}

// StartBackground runs a pass now and then every Interval. The first pass
// runs at boot on purpose: in dry_run mode it IS the report the owner
// reads before turning deletion on.
func (s *UnattachedFileSweep) StartBackground(ctx context.Context) {
	if s.Mode == UnattachedSweepOff {
		return
	}
	t := time.NewTicker(s.Interval)
	defer t.Stop()
	s.RunOnce(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.RunOnce(ctx)
		}
	}
}
