// TranscriptRetentionService — the nightly transcript sweep
// (docs/DATA_RETENTION_PLAN.md item 1).
//
// Two rules, both per-tenant (chat.tenant_retention_settings):
//
//   - retention_expired: conversations with no activity for
//     transcript_retention_days (floor 30) are hard-deleted — the
//     full cascade in ConversationEraseRepo, storage objects
//     included. Applies to archived and pinned conversations too:
//     this is a compliance window, not a preference.
//   - user_request: a user-deleted (soft-deleted) conversation is
//     hard-deleted deleted_conversation_purge_days after the user
//     deleted it. The user-visible removal was immediate; this is
//     the back-end erasure completing.
//
// Safety shape (mandatory for anything that deletes):
//   - the sweep runs only for tenants with
//     transcript_retention_enabled = true (default false);
//   - dry_run defaults TRUE: the pass counts what it WOULD delete,
//     writes a retention_dry_run audit entry with those counts, and
//     touches nothing. A human reads the numbers and flips dry_run.
//   - live deletes are per-conversation transactions that commit the
//     deletion-audit row atomically with the erasure.
//   - the floor is re-checked here: a settings row that somehow
//     carries transcript_retention_days < 30 is SKIPPED loudly, not
//     honored.
//
// Runs at 00:00 UTC (the fixed deletion time the product documents),
// never immediately on boot — a restart must not move the deletion
// hour.
package service

import (
	"context"
	"log/slog"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// maxErasuresPerTenantPerRun bounds one night's work so a first-time
// enable on a huge backlog cannot run unbounded; the remainder goes
// the following nights.
const maxErasuresPerTenantPerRun = 500

// objectStore is the slice of the storage interface the sweep needs.
type objectStore interface {
	Delete(ctx context.Context, key string) error
}

type TranscriptRetentionService struct {
	settings *repository.TenantRetentionSettingsRepo
	erase    *repository.ConversationEraseRepo
	files    *repository.FileRepo
	audit    *repository.DeletionAuditRepo
	storage  objectStore
}

func NewTranscriptRetentionService(
	settings *repository.TenantRetentionSettingsRepo,
	erase *repository.ConversationEraseRepo,
	files *repository.FileRepo,
	audit *repository.DeletionAuditRepo,
	storage objectStore,
) *TranscriptRetentionService {
	return &TranscriptRetentionService{
		settings: settings, erase: erase, files: files,
		audit: audit, storage: storage,
	}
}

// SweepStats — one pass's outcome, counts only.
type SweepStats struct {
	TenantsScanned    int            `json:"tenants_scanned"`
	DryRunTenants     int            `json:"dry_run_tenants"`
	ConversationsSeen int            `json:"conversations_seen"`
	Erased            int            `json:"erased"`
	Counts            map[string]int `json:"counts"`
	Errors            int            `json:"errors"`
}

// EnforceOnce runs one pass across every retention-enabled tenant.
func (s *TranscriptRetentionService) EnforceOnce(ctx context.Context) SweepStats {
	stats := SweepStats{Counts: map[string]int{}}
	tenants, err := s.settings.ListEnabled(ctx)
	if err != nil {
		slog.Warn("transcript retention: list tenants failed", "err", err)
		stats.Errors++
		return stats
	}
	for i := range tenants {
		t := &tenants[i]
		stats.TenantsScanned++
		s.enforceTenant(ctx, t, &stats)
	}
	if stats.ConversationsSeen > 0 || stats.Errors > 0 {
		slog.Info("transcript retention: pass complete",
			"tenants", stats.TenantsScanned,
			"seen", stats.ConversationsSeen,
			"erased", stats.Erased,
			"errors", stats.Errors)
	}
	return stats
}

// PreviewTenant runs the counting half for ONE tenant regardless of
// its enabled flag — the admin "show me the numbers before I turn
// this on" endpoint. Deletes nothing, writes no audit rows.
func (s *TranscriptRetentionService) PreviewTenant(
	ctx context.Context, tenantID uuid.UUID,
) (map[string]int, error) {
	settings, err := s.settings.Get(ctx, tenantID)
	if err != nil {
		return nil, err
	}
	expired, purgeable, err := s.candidates(ctx, settings)
	if err != nil {
		return nil, err
	}
	out := map[string]int{
		"conversations_retention_expired": len(expired),
		"conversations_user_deleted_due":  len(purgeable),
	}
	for _, c := range append(append([]repository.EraseCandidate{}, expired...), purgeable...) {
		fp, err := s.erase.CountConversationFootprint(ctx, tenantID, c.ID)
		if err != nil {
			return nil, err
		}
		for k, n := range fp {
			out[k] += n
		}
	}
	return out, nil
}

func (s *TranscriptRetentionService) candidates(
	ctx context.Context, t *model.TenantRetentionSettings,
) (expired, purgeable []repository.EraseCandidate, err error) {
	now := time.Now().UTC()
	if t.TranscriptRetentionDays != nil {
		if *t.TranscriptRetentionDays < model.TranscriptRetentionMinDays {
			slog.Error("transcript retention: days below floor — tenant skipped",
				"tenant", t.TenantID.String(),
				"days", *t.TranscriptRetentionDays,
				"floor", model.TranscriptRetentionMinDays)
		} else {
			cutoff := now.AddDate(0, 0, -*t.TranscriptRetentionDays)
			expired, err = s.erase.ListExpiredConversations(
				ctx, t.TenantID, cutoff, maxErasuresPerTenantPerRun)
			if err != nil {
				return nil, nil, err
			}
		}
	}
	purgeCutoff := now.AddDate(0, 0, -t.DeletedConversationPurgeDays)
	purgeable, err = s.erase.ListDeletedPastPurgeWindow(
		ctx, t.TenantID, purgeCutoff, maxErasuresPerTenantPerRun)
	if err != nil {
		return nil, nil, err
	}
	return expired, purgeable, nil
}

func (s *TranscriptRetentionService) enforceTenant(
	ctx context.Context, t *model.TenantRetentionSettings, stats *SweepStats,
) {
	expired, purgeable, err := s.candidates(ctx, t)
	if err != nil {
		slog.Warn("transcript retention: candidate scan failed",
			"tenant", t.TenantID.String(), "err", err)
		stats.Errors++
		return
	}
	stats.ConversationsSeen += len(expired) + len(purgeable)

	if t.DryRun {
		stats.DryRunTenants++
		counts, err := s.PreviewTenant(ctx, t.TenantID)
		if err != nil {
			slog.Warn("transcript retention: dry-run count failed",
				"tenant", t.TenantID.String(), "err", err)
			stats.Errors++
			return
		}
		// The dry-run report is itself an audited event so the admin
		// surface can show "what last night's pass would have done".
		if err := s.audit.Insert(ctx, &model.DeletionAuditEntry{
			TenantID: t.TenantID,
			Actor:    "system:transcript-retention",
			Action:   model.AuditActionRetentionDryRun,
			Scope:    "tenant",
			Rule:     model.AuditRuleDryRun,
			Counts:   counts,
		}); err != nil {
			slog.Warn("transcript retention: dry-run audit write failed",
				"tenant", t.TenantID.String(), "err", err)
			stats.Errors++
		}
		slog.Info("transcript retention: dry run",
			"tenant", t.TenantID.String(),
			"retention_expired", counts["conversations_retention_expired"],
			"user_deleted_due", counts["conversations_user_deleted_due"],
			"messages", counts["messages"])
		return
	}

	erase := func(c repository.EraseCandidate, rule string) {
		// Storage objects first — the rows carry the paths, and the
		// cascade removes the rows. A missing object is fine.
		if files, err := s.files.ListByConversation(ctx, t.TenantID, c.ID); err == nil {
			for i := range files {
				if files[i].StoragePath != "" {
					_ = s.storage.Delete(ctx, files[i].StoragePath)
				}
			}
		}
		owner := c.UserID
		counts, err := s.erase.HardDeleteConversation(
			ctx, t.TenantID, c.ID, &owner, "system:transcript-retention", rule)
		if err != nil {
			slog.Warn("transcript retention: erase failed",
				"tenant", t.TenantID.String(),
				"conversation", c.ID.String(), "err", err)
			stats.Errors++
			return
		}
		stats.Erased++
		for k, n := range counts {
			stats.Counts[k] += n
		}
	}
	for _, c := range expired {
		erase(c, model.AuditRuleRetentionExpired)
	}
	for _, c := range purgeable {
		erase(c, model.AuditRuleUserRequest)
	}
}

// secondsUntilNextMidnightUTC — the fixed run time. Exported-ish for
// tests via the service package.
func secondsUntilNextMidnightUTC(now time.Time) time.Duration {
	now = now.UTC()
	next := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC).
		AddDate(0, 0, 1)
	return next.Sub(now)
}

// StartBackground sleeps to the next 00:00 UTC, runs a pass, and
// repeats daily. No boot-time pass: the deletion hour is a
// documented, fixed time — restarts must not move it.
func (s *TranscriptRetentionService) StartBackground(ctx context.Context) {
	for {
		wait := secondsUntilNextMidnightUTC(time.Now())
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
			s.EnforceOnce(ctx)
		}
	}
}
