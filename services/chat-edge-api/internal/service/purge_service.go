// PurgeService — S9 GDPR right-to-erasure.
//
// HARD deletes (not soft) every memory-related row the user owns:
// memories, memory_events, conversation_states, response_context
// _sources. Also cleans up any export files on disk and the
// data_export_requests audit rows that pointed to them.
//
// Gated on chat.tenant_memory_settings.allow_data_deletion so an
// enterprise tenant can disable the path. Caller is responsible
// for confirming the user really meant it — the handler enforces
// the literal-string confirmation gate.
package service

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// Errors
var (
	ErrDeletionDisabled = errors.New("purge: data deletion is disabled for this tenant")
)

type PurgeService struct {
	memories    *repository.MemoryRepo
	states      *repository.ConversationStateRepo
	sources     *repository.ResponseContextSourceRepo
	exports     *repository.DataExportRepo
	settings    *repository.TenantMemorySettingsRepo
	storageRoot string
}

func NewPurgeService(
	memories *repository.MemoryRepo,
	states *repository.ConversationStateRepo,
	sources *repository.ResponseContextSourceRepo,
	exports *repository.DataExportRepo,
	settings *repository.TenantMemorySettingsRepo,
	storageRoot string,
) *PurgeService {
	if storageRoot == "" {
		storageRoot = "./data"
	}
	return &PurgeService{
		memories:    memories,
		states:      states,
		sources:     sources,
		exports:     exports,
		settings:    settings,
		storageRoot: storageRoot,
	}
}

// PurgeResult is the count breakdown the handler echoes back to
// the client. Exposed as JSON via /account/purge-memories so the
// user sees "we deleted N memories + M events + ...".
type PurgeResult struct {
	MemoriesDeleted     int `json:"memories_deleted"`
	EventsDeleted       int `json:"events_deleted"`
	StatesDeleted       int `json:"states_deleted"`
	SourcesDeleted      int `json:"sources_deleted"`
	ExportsDeleted      int `json:"exports_deleted"`
	ExportFilesUnlinked int `json:"export_files_unlinked"`
}

// PurgeAllUserMemories is the right-to-erasure entry point. Order
// matters: source rows first (they reference messages), then
// states, then memories + events, then exports. Each step is
// best-effort — a single failure logs + continues so the user
// gets a partial purge rather than a complete failure on a
// transient hiccup.
func (s *PurgeService) PurgeAllUserMemories(
	ctx context.Context, userID, tenantID uuid.UUID,
) (*PurgeResult, error) {
	settings, err := s.settings.Get(ctx, tenantID)
	if err != nil {
		return nil, err
	}
	if !settings.AllowDataDeletion {
		return nil, ErrDeletionDisabled
	}

	out := &PurgeResult{}

	if n, err := s.sources.HardDeleteAllByUser(ctx, userID, tenantID); err != nil {
		slog.Warn("purge: sources cascade failed",
			"user", userID.String(), "err", err)
	} else {
		out.SourcesDeleted = n
	}
	if n, err := s.states.HardDeleteAllByUser(ctx, userID, tenantID); err != nil {
		slog.Warn("purge: states cascade failed",
			"user", userID.String(), "err", err)
	} else {
		out.StatesDeleted = n
	}
	if n, err := s.memories.HardDeleteAllEventsByUser(ctx, userID, tenantID); err != nil {
		slog.Warn("purge: events cascade failed",
			"user", userID.String(), "err", err)
	} else {
		out.EventsDeleted = n
	}
	if n, err := s.memories.HardDeleteAllByUser(ctx, userID, tenantID); err != nil {
		// This one we DO surface — a failed memory delete means the
		// user's data is still on disk. The cascade above has
		// already cleaned the dependents, so retrying the memory
		// purge is safe.
		return out, fmt.Errorf("hard delete memories: %w", err)
	} else {
		out.MemoriesDeleted = n
	}

	// Export files + rows last. Failure here doesn't compromise
	// the right-to-erasure contract because the data inside the
	// files is the same memory content we just nuked; the files
	// are just snapshots. Log + continue.
	if pastExports, err := s.exports.ListByUser(ctx, userID, tenantID); err == nil {
		for i := range pastExports {
			if pastExports[i].FilePath != "" {
				if err := os.Remove(pastExports[i].FilePath); err == nil {
					out.ExportFilesUnlinked++
				} else if !os.IsNotExist(err) {
					slog.Warn("purge: export file unlink failed",
						"path", pastExports[i].FilePath, "err", err)
				}
			}
		}
	}
	if n, err := s.exports.DeleteByUser(ctx, userID, tenantID); err != nil {
		slog.Warn("purge: export rows delete failed",
			"user", userID.String(), "err", err)
	} else {
		out.ExportsDeleted = n
	}

	// Also wipe the per-user export directory in case files were
	// orphaned (e.g. a failed Create that left a file but no row).
	exportDir := filepath.Join(s.storageRoot, "exports", tenantID.String(), userID.String())
	_ = os.RemoveAll(exportDir)

	// Audit log — counts only, never content (the whole point of
	// the purge is no content remains anywhere). Logged at warn
	// because it's a privileged + irreversible action.
	slog.Warn("user data purged",
		"user", userID.String(),
		"tenant", tenantID.String(),
		"memories_deleted", out.MemoriesDeleted,
		"events_deleted", out.EventsDeleted,
		"states_deleted", out.StatesDeleted,
		"sources_deleted", out.SourcesDeleted,
		"exports_deleted", out.ExportsDeleted,
		"export_files_unlinked", out.ExportFilesUnlinked,
	)
	return out, nil
}
