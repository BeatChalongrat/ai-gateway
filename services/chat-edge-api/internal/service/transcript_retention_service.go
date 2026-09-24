// TranscriptRetentionService — the nightly transcript sweep
// (docs/DATA_RETENTION_PLAN.md item 1, docs/ops/user-data.md).
//
// Two rules, each with its own switch:
//
//   - user_request (FIX132: ON for every tenant by default): a user-deleted
//     (soft-deleted) conversation is hard-deleted
//     deleted_conversation_purge_days (30) after the user deleted it —
//     rows AND file bytes. The user-visible removal was immediate; this is
//     the back-end erasure completing. Governed by the env ceiling
//     DELETED_DATA_PURGE_MODE (on | dry_run | off) and the tenant's
//     deleted_purge_enabled / deleted_purge_dry_run (migration 050); no
//     settings row = on. Until FIX132 this rule only ran for tenants that
//     had opted into the rule below, so on the defaults nothing was ever
//     erased.
//   - retention_expired (per-tenant opt-in, unchanged): conversations with
//     no activity for transcript_retention_days (floor 30) are
//     hard-deleted — archived and pinned ones too: this is a compliance
//     window, not a preference. Runs only for tenants with
//     transcript_retention_enabled = true; dry_run defaults TRUE.
//
// Safety shape (mandatory for anything that deletes):
//   - a dry-run pass counts what it WOULD delete, writes a
//     retention_dry_run audit entry with those counts, and touches
//     nothing;
//   - live deletes are per-conversation transactions that commit the
//     deletion-audit row atomically with the erasure; the file objects
//     are deleted first (the rows carry their keys);
//   - the floor is re-checked here: a settings row that somehow
//     carries transcript_retention_days < 30 is SKIPPED loudly, not
//     honored;
//   - at most maxErasuresPerTenantPerRun per tenant per night.
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
	// purgeEnvMode — DELETED_DATA_PURGE_MODE, the ceiling for the
	// user_request rule. "" = on (the default).
	purgeEnvMode string
}

// SetDeletedPurgeMode sets the env ceiling for the deleted-data purge
// (on | dry_run | off; anything else is treated as dry_run).
func (s *TranscriptRetentionService) SetDeletedPurgeMode(mode string) {
	s.purgeEnvMode = mode
}

// DeletedPurgeMode reports the env ceiling in its normalised form.
func (s *TranscriptRetentionService) DeletedPurgeMode() string {
	return model.NormalizePurgeMode(s.purgeEnvMode)
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
	// Both totals, side by side and never merged: ConversationsSeen
	// counts everything, ConversationsSeenTestData counts the part
	// belonging to tenants proven to be integration-test leftovers.
	// Real minus test is the number a person should act on; the total
	// is kept so nothing is hidden.
	ConversationsSeenTestData int `json:"conversations_seen_test_data"`
	TenantsLikelyTest         int `json:"tenants_likely_test"`
}

// EnforceOnce runs one pass: every tenant that opted into the
// retention_expired rule, plus every tenant holding user-deleted
// conversations (the user_request rule needs no opt-in since FIX132).
func (s *TranscriptRetentionService) EnforceOnce(ctx context.Context) SweepStats {
	stats := SweepStats{Counts: map[string]int{}}
	enabled, err := s.settings.ListEnabled(ctx)
	if err != nil {
		slog.Warn("transcript retention: list tenants failed", "err", err)
		stats.Errors++
		enabled = nil
	}
	seen := make(map[uuid.UUID]bool, len(enabled))
	for i := range enabled {
		t := &enabled[i]
		seen[t.TenantID] = true
		stats.TenantsScanned++
		s.enforceTenant(ctx, t, &stats)
	}

	// The deleted-data purge scans the DATA, not the settings table: a
	// tenant with no settings row gets the defaults, which purge. The
	// discovery cutoff is one day (the shortest window a row may carry);
	// each tenant's own window is applied in candidates().
	if s.DeletedPurgeMode() != model.PurgeModeOff {
		tenants, err := s.erase.ListTenantsWithDeletedBefore(
			ctx, time.Now().UTC().AddDate(0, 0, -1))
		if err != nil {
			slog.Warn("transcript retention: list tenants with deleted chats failed", "err", err)
			stats.Errors++
		}
		for _, tid := range tenants {
			if seen[tid] {
				continue
			}
			t, err := s.settings.Get(ctx, tid)
			if err != nil {
				slog.Warn("transcript retention: settings lookup failed",
					"tenant", tid.String(), "err", err)
				stats.Errors++
				continue
			}
			stats.TenantsScanned++
			s.enforceTenant(ctx, t, &stats)
		}
	}

	if stats.ConversationsSeen > 0 || stats.Errors > 0 {
		slog.Info("transcript retention: pass complete",
			"tenants", stats.TenantsScanned,
			"seen", stats.ConversationsSeen,
			"seen_test_data", stats.ConversationsSeenTestData,
			"seen_real", stats.ConversationsSeen-stats.ConversationsSeenTestData,
			"tenants_likely_test", stats.TenantsLikelyTest,
			"erased", stats.Erased,
			"errors", stats.Errors,
			"deleted_purge_mode", s.DeletedPurgeMode())
	}
	return stats
}

// ruleModes — what each rule does for this tenant on this pass.
func (s *TranscriptRetentionService) ruleModes(
	t *model.TenantRetentionSettings,
) (retention, purge string) {
	retention = model.PurgeModeOff
	if t.TranscriptRetentionEnabled {
		retention = model.PurgeModeOn
		if t.DryRun {
			retention = model.PurgeModeDryRun
		}
	}
	return retention, model.EffectiveDeletedPurgeMode(s.purgeEnvMode, t)
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
	// The preview counts both rules whatever their switches say — it is
	// the "what would a live pass do" number an admin reads first.
	expired, purgeable, err := s.candidates(ctx, settings, true, true)
	if err != nil {
		return nil, err
	}
	out := map[string]int{
		"conversations_retention_expired": len(expired),
		"conversations_user_deleted_due":  len(purgeable),
	}
	// Say whose data these numbers describe. This database also holds
	// rows left by integration tests, and a total that silently mixes
	// them is unreadable to whoever has to decide if deletion is safe.
	// Only positive evidence marks a tenant as test data; anything
	// unproven is reported as real.
	class, err := s.erase.ClassifyTenant(ctx, tenantID)
	if err != nil {
		return nil, err
	}
	out["counts_are_test_data"] = 0
	if class == repository.TenantLikelyTest {
		out["counts_are_test_data"] = 1
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
	ctx context.Context, t *model.TenantRetentionSettings, withExpired, withDeleted bool,
) (expired, purgeable []repository.EraseCandidate, err error) {
	now := time.Now().UTC()
	if withExpired && t.TranscriptRetentionDays != nil {
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
	if withDeleted {
		days := t.DeletedConversationPurgeDays
		if days < 1 {
			days = model.DefaultDeletedPurgeDays
		}
		purgeCutoff := now.AddDate(0, 0, -days)
		purgeable, err = s.erase.ListDeletedPastPurgeWindow(
			ctx, t.TenantID, purgeCutoff, maxErasuresPerTenantPerRun)
		if err != nil {
			return nil, nil, err
		}
	}
	return expired, purgeable, nil
}

func (s *TranscriptRetentionService) enforceTenant(
	ctx context.Context, t *model.TenantRetentionSettings, stats *SweepStats,
) {
	retentionMode, purgeMode := s.ruleModes(t)
	if retentionMode == model.PurgeModeOff && purgeMode == model.PurgeModeOff {
		return
	}
	expired, purgeable, err := s.candidates(ctx, t,
		retentionMode != model.PurgeModeOff, purgeMode != model.PurgeModeOff)
	if err != nil {
		slog.Warn("transcript retention: candidate scan failed",
			"tenant", t.TenantID.String(), "err", err)
		stats.Errors++
		return
	}
	seen := len(expired) + len(purgeable)
	stats.ConversationsSeen += seen
	// Keep the test-data share as its own running total so the pass
	// can report both numbers instead of one blended figure.
	if seen > 0 {
		if class, err := s.erase.ClassifyTenant(ctx, t.TenantID); err == nil &&
			class == repository.TenantLikelyTest {
			stats.TenantsLikelyTest++
			stats.ConversationsSeenTestData += seen
		}
	}

	// A rule in dry-run counts instead of deleting. One audited report
	// per tenant per pass (both rules' numbers side by side) so the admin
	// surface can show "what last night's pass would have done". A tenant
	// whose only dry-run rule is the purge and who has nothing due writes
	// nothing — hundreds of empty reports a night would bury the real ones.
	if retentionMode == model.PurgeModeDryRun ||
		(purgeMode == model.PurgeModeDryRun && len(purgeable) > 0) {
		stats.DryRunTenants++
		counts, err := s.PreviewTenant(ctx, t.TenantID)
		if err != nil {
			slog.Warn("transcript retention: dry-run count failed",
				"tenant", t.TenantID.String(), "err", err)
			stats.Errors++
		} else {
			if err := s.audit.Insert(ctx, &model.DeletionAuditEntry{
				TenantID: t.TenantID,
				Actor:    "system:transcript-retention",
				Action:   model.AuditActionRetentionDryRun,
				Scope:    "tenant",
				Rule:     model.AuditRuleDryRun,
				Counts:   counts,
				Details: map[string]any{
					model.AuditRuleRetentionExpired: retentionMode,
					model.AuditRuleUserRequest:      purgeMode,
				},
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
		}
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
	if retentionMode == model.PurgeModeOn {
		for _, c := range expired {
			erase(c, model.AuditRuleRetentionExpired)
		}
	}
	if purgeMode == model.PurgeModeOn {
		for _, c := range purgeable {
			erase(c, model.AuditRuleUserRequest)
		}
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
