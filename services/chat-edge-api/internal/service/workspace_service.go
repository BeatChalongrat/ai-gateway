// WorkspaceService — Phase 4 Session 5 — file rollback + version
// compare.
//
// Sibling of ExcelService / DocService. Two jobs:
//
//	Rollback — clones the blob of an old version into a brand-new
//	           conversation_files row at version = max+1. Additive:
//	           the original old version stays on disk and in the DB.
//	           Use case: user picks v1 of a report they liked better
//	           than v4 and wants v1's content forward.
//
//	Compare  — materialises two versions side by side and shells
//	           out to scripts/file_compare.py for the structural
//	           diff. Result is the comparison JSON the frontend's
//	           ComparisonCard renders.
//
// Both operations are tenant- and conversation-scoped via the file
// repo's existing GetByID; the handler enforces caller ownership
// before invoking either.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
)

const (
	compareScript  = "scripts/file_compare.py"
	compareTimeout = 60 * time.Second
)

// ---------------------------------------------------------------------------
// Typed errors
// ---------------------------------------------------------------------------

var (
	ErrWorkspaceTargetNotFound  = errors.New("workspace: target version not found")
	ErrWorkspaceTargetMismatch  = errors.New("workspace: target version does not belong to this lineage")
	ErrWorkspaceCompareTypes    = errors.New("workspace: cannot compare files of different types")
	ErrWorkspaceCompareFailed   = errors.New("workspace: compare script failed")
	ErrWorkspaceRollbackFailed  = errors.New("workspace: rollback failed")
	ErrWorkspaceCompareNotGen   = errors.New("workspace: only generated files can be compared")
)

// ---------------------------------------------------------------------------
// Wire types — mirror file_compare.py output 1:1 so the handler can
// JSON-encode them straight to the client.
// ---------------------------------------------------------------------------

type ComparisonChange struct {
	Type   string `json:"type"`
	Detail string `json:"detail"`
	Sheet  string `json:"sheet,omitempty"`
}

type ComparisonResult struct {
	V1SizeBytes        int64              `json:"v1_size_bytes"`
	V2SizeBytes        int64              `json:"v2_size_bytes"`
	SizeChange         string             `json:"size_change"`
	FileType           string             `json:"file_type"`
	StructuralChanges  []ComparisonChange `json:"structural_changes"`
	ContentDiffSummary string             `json:"content_diff_summary"`
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

type WorkspaceService struct {
	files         *repository.FileRepo
	conversations *repository.ConversationRepo
	storage       storage.Backend

	pythonBin  string
	scriptsDir string
}

// NewWorkspaceService wires the repos + Python runtime. The
// conversations repo is used by Rollback / Compare to verify the
// caller owns the conversation before walking any lineage —
// without it a same-tenant-different-user caller could rollback or
// diff another user's generated artifacts.
func NewWorkspaceService(
	files *repository.FileRepo,
	conversations *repository.ConversationRepo,
	store storage.Backend,
	pythonBin, scriptsDir string,
) *WorkspaceService {
	if pythonBin == "" {
		pythonBin = "python3"
	}
	if scriptsDir == "" {
		scriptsDir = "scripts"
	}
	return &WorkspaceService{
		files:         files,
		conversations: conversations,
		storage:       store,
		pythonBin:     pythonBin,
		scriptsDir:    scriptsDir,
	}
}

// AssertConvOwnership exposes the conv-ownership gate to handlers
// that hit the file repo directly (e.g. WorkspaceHandler.
// ListGenerated, ExcelHandler.Versions). Returns
// ErrWorkspaceTargetNotFound on every failure mode so the upstream
// handler maps a single error to 404 without leaking existence.
func (s *WorkspaceService) AssertConvOwnership(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) error {
	return s.assertConvOwnership(ctx, tenantID, userID, convID)
}

// assertConvOwnership is the internal call. Kept separate so the
// service's own methods can use it without the public name.
func (s *WorkspaceService) assertConvOwnership(
	ctx context.Context, tenantID, userID, convID uuid.UUID,
) error {
	c, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrWorkspaceTargetNotFound
		}
		return err
	}
	if c.UserID != userID {
		return ErrWorkspaceTargetNotFound
	}
	return nil
}

func (s *WorkspaceService) scriptPath(name string) (string, error) {
	candidate := name
	if !filepath.IsAbs(candidate) {
		if !strings.Contains(name, string(filepath.Separator)) && !strings.Contains(name, "/") {
			candidate = filepath.Join(s.scriptsDir, name)
		}
		abs, err := filepath.Abs(candidate)
		if err != nil {
			return "", err
		}
		candidate = abs
	}
	if _, err := os.Stat(candidate); err != nil {
		return "", fmt.Errorf("script not found at %s: %w", candidate, err)
	}
	return candidate, nil
}

// ---------------------------------------------------------------------------
// Rollback
// ---------------------------------------------------------------------------

// RollbackInput is the typed payload from the handler.
type RollbackInput struct {
	TenantID        uuid.UUID
	UserID          uuid.UUID
	ConversationID  uuid.UUID
	LineageFileID   uuid.UUID // any file in the chain (typically the leaf)
	TargetVersionID uuid.UUID // the old version whose content to clone forward
}

// Rollback clones the blob of TargetVersionID into a new
// conversation_files row at version = (max version in lineage) + 1.
// The new row keeps the target's parent_file_id so it lives in the
// same lineage. ChangeLog records the rollback source so the
// timeline can render "Rolled back from v3 → v1".
func (s *WorkspaceService) Rollback(
	ctx context.Context, in RollbackInput,
) (*model.ConversationFile, error) {
	if err := s.assertConvOwnership(ctx, in.TenantID, in.UserID, in.ConversationID); err != nil {
		return nil, err
	}
	target, err := s.files.GetByID(ctx, in.TenantID, in.TargetVersionID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrWorkspaceTargetNotFound
		}
		return nil, err
	}
	if target.ConversationID != in.ConversationID {
		// Cross-conversation cross-link guard.
		return nil, ErrWorkspaceTargetNotFound
	}
	if target.UserID != in.UserID {
		// Belt-and-braces — the conv ownership check above already
		// covers this, but a defensive denormalised check is cheap
		// and fails closed if migration 013's backfill missed a row.
		return nil, ErrWorkspaceTargetNotFound
	}
	if !target.IsGenerated {
		return nil, fmt.Errorf("%w: target is not a generated file", ErrWorkspaceTargetMismatch)
	}

	// Pull the lineage to (a) verify the target belongs to it and
	// (b) compute the next version number.
	chain, err := s.files.ListVersions(ctx, in.TenantID, in.LineageFileID)
	if err != nil {
		return nil, err
	}
	inChain := false
	maxVersion := 0
	var leafID uuid.UUID
	for i := range chain {
		f := &chain[i]
		if f.ID == target.ID {
			inChain = true
		}
		if f.IsGenerated && f.Version > maxVersion {
			maxVersion = f.Version
			leafID = f.ID
		}
	}
	if !inChain {
		return nil, ErrWorkspaceTargetMismatch
	}
	if leafID == uuid.Nil {
		// Shouldn't happen — the target is in the chain and
		// must be generated (checked above), so at least one
		// generated row exists. Fall back to the target as
		// parent if our walk ever fails.
		leafID = target.ID
	}

	// Clone the blob. Download to a temp, then upload under a new key.
	srcPath, cleanup, err := s.storage.OpenLocalCopy(ctx, target.StoragePath)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrWorkspaceRollbackFailed, err)
	}
	defer cleanup()

	newVersion := maxVersion + 1
	newFileID := uuid.New()
	newName := buildRollbackFilename(target.OriginalFilename, newVersion)
	storageName := fmt.Sprintf("%s_%s", newFileID.String(), newName)
	newKey := storage.JoinKey(
		conversationFilesKeyPrefix,
		in.TenantID.String(),
		in.ConversationID.String(),
		storageName,
	)

	body, err := os.Open(srcPath)
	if err != nil {
		return nil, fmt.Errorf("%w: open src: %v", ErrWorkspaceRollbackFailed, err)
	}
	defer body.Close()
	info, err := body.Stat()
	if err != nil {
		return nil, fmt.Errorf("%w: stat: %v", ErrWorkspaceRollbackFailed, err)
	}
	if err := s.storage.Upload(ctx, newKey, body, target.ContentType, info.Size()); err != nil {
		return nil, fmt.Errorf("%w: upload: %v", ErrWorkspaceRollbackFailed, err)
	}

	// New version's parent is the current LEAF, not the target.
	// This keeps the lineage linear (v1→v2→v3→v4(rollback))
	// instead of branching off the target (which would make the
	// chain look like a tree and confuse the workspace's leaf
	// detector — every branch tip would show up as a separate
	// file card).
	parentID := leafID
	changeLog := fmt.Sprintf("Rolled back from v%d to v%d", maxVersion, target.Version)
	row, err := s.files.Create(ctx, &model.ConversationFile{
		ConversationID:   in.ConversationID,
		TenantID:         in.TenantID,
		UserID:           in.UserID,
		Filename:         storageName,
		OriginalFilename: newName,
		ContentType:      target.ContentType,
		FileSizeBytes:    info.Size(),
		StoragePath:      newKey,
		ExtractionStatus: "completed",
		IsGenerated:      true,
		ParentFileID:     &parentID,
		Version:          newVersion,
		ChangeLog:        &changeLog,
		InspectionJSON:   target.InspectionJSON, // identical content → identical structure
	})
	if err != nil {
		_ = s.storage.Delete(ctx, newKey)
		return nil, fmt.Errorf("%w: persist: %v", ErrWorkspaceRollbackFailed, err)
	}
	return row, nil
}

// buildRollbackFilename normalises the previous filename's _vN /
// _edited suffix and stamps the new version number, so a chain
// like "report.docx → report_edited.docx → report_v2.docx →
// (rollback to v1) → report_v3.docx" stays human-readable.
func buildRollbackFilename(prev string, newVersion int) string {
	prev = strings.TrimSpace(prev)
	if prev == "" {
		return fmt.Sprintf("rollback_v%d", newVersion)
	}
	ext := filepath.Ext(prev)
	base := strings.TrimSuffix(prev, ext)
	// Strip a trailing _vN or _edited so we don't end up with
	// "report_edited_v3.docx" or "report_v2_v3.docx".
	for _, suffix := range []string{"_edited"} {
		if strings.HasSuffix(base, suffix) {
			base = strings.TrimSuffix(base, suffix)
		}
	}
	if idx := strings.LastIndex(base, "_v"); idx > 0 {
		tail := base[idx+2:]
		stripped := true
		for _, r := range tail {
			if r < '0' || r > '9' {
				stripped = false
				break
			}
		}
		if stripped && tail != "" {
			base = base[:idx]
		}
	}
	return fmt.Sprintf("%s_v%d%s", base, newVersion, ext)
}

// ---------------------------------------------------------------------------
// Compare
// ---------------------------------------------------------------------------

type CompareInput struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	V1FileID       uuid.UUID
	V2FileID       uuid.UUID
}

type CompareOutput struct {
	V1     *model.ConversationFile
	V2     *model.ConversationFile
	Report *ComparisonResult
}

func (s *WorkspaceService) Compare(ctx context.Context, in CompareInput) (*CompareOutput, error) {
	if err := s.assertConvOwnership(ctx, in.TenantID, in.UserID, in.ConversationID); err != nil {
		return nil, err
	}
	v1, err := s.files.GetByID(ctx, in.TenantID, in.V1FileID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrWorkspaceTargetNotFound
		}
		return nil, err
	}
	v2, err := s.files.GetByID(ctx, in.TenantID, in.V2FileID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrWorkspaceTargetNotFound
		}
		return nil, err
	}
	// Conversation-scope guard — prevents two files in different
	// threads (but same tenant) from being cross-compared.
	if v1.ConversationID != in.ConversationID || v2.ConversationID != in.ConversationID {
		return nil, ErrWorkspaceTargetNotFound
	}
	// Belt-and-braces user_id guard on the file rows. The
	// assertConvOwnership above already passes only when the conv
	// belongs to in.UserID, and the conversation-scope check pins
	// both files to that conv — but a defensive denormalised check
	// fails closed if migration 013's backfill ever missed a row.
	if v1.UserID != in.UserID || v2.UserID != in.UserID {
		return nil, ErrWorkspaceTargetNotFound
	}
	if !v1.IsGenerated || !v2.IsGenerated {
		return nil, ErrWorkspaceCompareNotGen
	}
	if !strings.EqualFold(v1.ContentType, v2.ContentType) {
		return nil, fmt.Errorf("%w: %s vs %s", ErrWorkspaceCompareTypes, v1.ContentType, v2.ContentType)
	}

	abs, err := s.scriptPath(compareScript)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrWorkspaceCompareFailed, err)
	}

	// Two storage local copies, each with its own cleanup.
	p1, cleanup1, err := s.storage.OpenLocalCopy(ctx, v1.StoragePath)
	if err != nil {
		return nil, fmt.Errorf("%w: v1 download: %v", ErrWorkspaceCompareFailed, err)
	}
	defer cleanup1()
	p2, cleanup2, err := s.storage.OpenLocalCopy(ctx, v2.StoragePath)
	if err != nil {
		return nil, fmt.Errorf("%w: v2 download: %v", ErrWorkspaceCompareFailed, err)
	}
	defer cleanup2()

	cctx, cancel := context.WithTimeout(ctx, compareTimeout)
	defer cancel()
	cmd := exec.CommandContext(cctx, s.pythonBin, abs, p1, p2)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		slog.Warn("compare script failed",
			"v1", v1.ID.String(), "v2", v2.ID.String(),
			"err", err, "stderr", stderr.String())
		return nil, fmt.Errorf("%w: %v", ErrWorkspaceCompareFailed, err)
	}
	var probe map[string]any
	if err := json.Unmarshal(out, &probe); err != nil {
		return nil, fmt.Errorf("%w: decode: %v", ErrWorkspaceCompareFailed, err)
	}
	if msg, ok := probe["error"].(string); ok && msg != "" {
		return nil, fmt.Errorf("%w: %s", ErrWorkspaceCompareFailed, msg)
	}
	var report ComparisonResult
	if err := json.Unmarshal(out, &report); err != nil {
		return nil, fmt.Errorf("%w: report decode: %v", ErrWorkspaceCompareFailed, err)
	}
	return &CompareOutput{V1: v1, V2: v2, Report: &report}, nil
}

// drainAndClose is a tiny helper for cases where we have to swallow
// a reader to satisfy a defer — currently unused but referenced
// from the rollback path's debug output during dev.
func drainAndClose(r io.ReadCloser) {
	if r == nil {
		return
	}
	_, _ = io.Copy(io.Discard, r)
	_ = r.Close()
}

var _ = drainAndClose
