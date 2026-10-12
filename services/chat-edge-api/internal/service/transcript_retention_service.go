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
// Runs for 00:00 UTC (the fixed deletion time the product documents),
// and a pass is worked out AS OF that hour, whenever it is run.
//
// It used to sleep until the next 00:00 UTC and run nothing at a start,
// so a machine that was off or asleep at that minute skipped the day's
// pass and nothing made it good: the job quietly did not happen, and
// what was due stayed until someone noticed. Now a start runs the pass
// it finds owed (the latest 00:00 UTC), and the clock is looked at every
// minute, so a machine that slept through the hour runs it on waking.
//
// A restart still does not move the deletion hour. Because a pass is as
// of its hour, running it late erases exactly what running it on time
// would have — nothing that was not yet due at a documented hour — and
// a tenant whose settings were changed after that hour waits for the
// next pass ("nothing happens at 14:00", docs/DATA_RETENTION_PLAN.md).
// Run again for an hour whose pass already ran, it finds nothing left.
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
	// TenantsWaiting — tenants this pass left alone because their
	// settings were changed after the hour the pass is for: the change
	// applies from the next pass, never from one being made good late.
	TenantsWaiting int `json:"tenants_waiting"`
}

// EnforceOnce runs one pass as of now.
func (s *TranscriptRetentionService) EnforceOnce(ctx context.Context) SweepStats {
	return s.EnforceAsOf(ctx, time.Now().UTC())
}

// EnforceAsOf runs the pass that was due at `due`, whenever it is
// called: every tenant that opted into the retention_expired rule, plus
// every tenant holding user-deleted conversations (the user_request rule
// needs no opt-in since FIX132). Every window is measured back from
// `due`, not from the clock, so a pass run hours late erases what the
// pass at its hour would have erased and nothing that fell due since.
func (s *TranscriptRetentionService) EnforceAsOf(ctx context.Context, due time.Time) SweepStats {
	due = due.UTC()
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
		s.enforceTenant(ctx, t, &stats, due)
	}

	// The deleted-data purge scans the DATA, not the settings table: a
	// tenant with no settings row gets the defaults, which purge. The
	// discovery cutoff is one day (the shortest window a row may carry);
	// each tenant's own window is applied in candidates().
	if s.DeletedPurgeMode() != model.PurgeModeOff {
		tenants, err := s.erase.ListTenantsWithDeletedBefore(
			ctx, due.AddDate(0, 0, -1))
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
			s.enforceTenant(ctx, t, &stats, due)
		}
	}

	// Said on every pass, also one that found nothing due. It used to be said only when something was
	// seen, so a day with nothing to erase left no line — and the check that looks for this line in the
	// last 26 hours (the smoke's jobs.retention) could not tell "ran, nothing due" from "did not run":
	// it was red on 11 Oct 2026 with nothing overdue, and would have been green on a day with a backlog
	// only by luck. The line is the proof that the pass ran — and `due` says for which hour, so a
	// reader can tell today's pass from yesterday's.
	slog.Info("transcript retention: pass complete",
		"due", due.Format(time.RFC3339),
		"tenants", stats.TenantsScanned,
		"tenants_waiting", stats.TenantsWaiting,
		"seen", stats.ConversationsSeen,
		"seen_test_data", stats.ConversationsSeenTestData,
		"seen_real", stats.ConversationsSeen-stats.ConversationsSeenTestData,
		"tenants_likely_test", stats.TenantsLikelyTest,
		"erased", stats.Erased,
		"errors", stats.Errors,
		"deleted_purge_mode", s.DeletedPurgeMode())
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
	expired, purgeable, err := s.candidates(ctx, settings, true, true, time.Now().UTC())
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
	ctx context.Context, t *model.TenantRetentionSettings, withExpired, withDeleted bool, asOf time.Time,
) (expired, purgeable []repository.EraseCandidate, err error) {
	now := asOf.UTC()
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
	ctx context.Context, t *model.TenantRetentionSettings, stats *SweepStats, due time.Time,
) {
	retentionMode, purgeMode := s.ruleModes(t)
	if retentionMode == model.PurgeModeOff && purgeMode == model.PurgeModeOff {
		return
	}
	// A change to a tenant's settings applies from the next pass at the
	// documented hour, never sooner. A pass that is being made good late
	// reads today's settings row, so without this it would be the thing
	// that applied a change early — a shorter window, a dry run switched
	// to live. Such a tenant waits one pass. (No settings row = the
	// defaults, which were never changed.)
	if t.UpdatedAt.After(due) {
		stats.TenantsWaiting++
		slog.Info("transcript retention: settings changed after this pass's hour — the tenant waits for the next pass",
			"tenant", t.TenantID.String(),
			"due", due.Format(time.RFC3339),
			"settings_changed", t.UpdatedAt.UTC().Format(time.RFC3339))
		return
	}
	expired, purgeable, err := s.candidates(ctx, t,
		retentionMode != model.PurgeModeOff, purgeMode != model.PurgeModeOff, due)
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

// retentionClockLook — how often the schedule looks at the clock. A
// pass is owed from 00:00 UTC; it starts within this long of that, or
// of the machine waking.
const retentionClockLook = time.Minute

// lastScheduledPass — the latest 00:00 UTC at or before now: the hour
// the newest pass is for.
func lastScheduledPass(now time.Time) time.Time {
	now = now.UTC()
	return time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
}

// runRetentionSchedule calls pass(due) once for every scheduled hour it
// sees go by, starting with the one it finds already gone by when it is
// called — the pass a start makes good. It reads the wall clock on every
// tick instead of sleeping until the next hour: a single long sleep does
// not count the time a machine is asleep, so it fired hours late or not
// at all on a machine that sleeps at night. Hours that went by unseen
// (two days asleep) are made good by one pass for the latest of them —
// a pass as of an hour covers everything due before it.
func runRetentionSchedule(
	ctx context.Context, now func() time.Time, ticks <-chan time.Time, pass func(due time.Time),
) {
	var ranFor time.Time // the hour the last pass was for; zero = none since this start
	for {
		if due := lastScheduledPass(now()); due.After(ranFor) {
			pass(due)
			ranFor = due
		}
		select {
		case <-ctx.Done():
			return
		case <-ticks:
		}
	}
}

// StartBackground runs the pass the start finds owed, then one for every
// 00:00 UTC that goes by — each as of its hour (see the file header).
func (s *TranscriptRetentionService) StartBackground(ctx context.Context) {
	t := time.NewTicker(retentionClockLook)
	defer t.Stop()
	runRetentionSchedule(ctx, time.Now, t.C, func(due time.Time) {
		if late := time.Since(due); late > 2*retentionClockLook {
			slog.Info("transcript retention: making good a pass that did not run at its hour",
				"due", due.Format(time.RFC3339),
				"late_by", late.Round(time.Second).String())
		}
		s.EnforceAsOf(ctx, due)
	})
}
