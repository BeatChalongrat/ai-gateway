// DEPRECATED (Phase 9) — folders were merged into projects.
//
// migrations/031_merge_folders_into_projects.sql turned every folder into a
// project (same id) and repointed its conversations, and router.go no longer
// registers any /folders route. This code is kept UNWIRED for one release so
// the migration stays reversible; the live organiser is projects
// (project_repo.go / project_service.go / handler/project.go). Do not wire
// it back — extend projects instead.

// FolderService — CRUD over chat.folders plus conversation→folder moves,
// with (tenant_id, user_id) ownership gates. Mirrors ConversationService:
// the handler stays thin, every mutation asserts the caller owns the row(s)
// before touching the DB, and repo ErrNotFound is remapped to typed errors.
package service

import (
	"context"
	"errors"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

var (
	ErrFolderNotFound     = errors.New("folder: not found")
	ErrFolderNameRequired = errors.New("folder: name is required")
	ErrFolderNameTooLong  = errors.New("folder: name too long (max 255)")
)

const maxFolderNameLen = 255

type FolderService struct {
	folders       *repository.FolderRepo
	conversations *repository.ConversationRepo
}

func NewFolderService(f *repository.FolderRepo, c *repository.ConversationRepo) *FolderService {
	return &FolderService{folders: f, conversations: c}
}

// normalizeName trims surrounding whitespace and enforces the non-empty +
// length bounds so a bad name fails with a clean 4xx instead of a DB error.
func normalizeName(name string) (string, error) {
	n := strings.TrimSpace(name)
	if n == "" {
		return "", ErrFolderNameRequired
	}
	// VARCHAR(255) — count runes so multibyte Thai labels aren't cut by byte.
	if len([]rune(n)) > maxFolderNameLen {
		return "", ErrFolderNameTooLong
	}
	return n, nil
}

// List returns the caller's folders with live conversation counts.
func (s *FolderService) List(ctx context.Context, tenantID, userID uuid.UUID) ([]model.Folder, error) {
	return s.folders.ListByUser(ctx, tenantID, userID)
}

// Create makes a new folder at the end of the caller's ordering.
func (s *FolderService) Create(ctx context.Context, tenantID, userID uuid.UUID, name string) (*model.Folder, error) {
	n, err := normalizeName(name)
	if err != nil {
		return nil, err
	}
	return s.folders.Create(ctx, tenantID, userID, n)
}

// Rename changes a folder's name. Owner-scoped at the repo; missing/foreign
// folders surface ErrFolderNotFound.
func (s *FolderService) Rename(ctx context.Context, tenantID, userID, folderID uuid.UUID, name string) (*model.Folder, error) {
	n, err := normalizeName(name)
	if err != nil {
		return nil, err
	}
	f, err := s.folders.Update(ctx, tenantID, userID, folderID, n)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrFolderNotFound
		}
		return nil, err
	}
	return f, nil
}

// Delete removes a folder; its conversations become unfiled via the
// ON DELETE SET NULL FK.
func (s *FolderService) Delete(ctx context.Context, tenantID, userID, folderID uuid.UUID) error {
	if err := s.folders.Delete(ctx, tenantID, userID, folderID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrFolderNotFound
		}
		return err
	}
	return nil
}

// Reorder rewrites folder positions to match the given id order. Foreign /
// unknown ids are ignored by the owner-scoped UPDATE, so a partial or stale
// list never reassigns another user's folders.
func (s *FolderService) Reorder(ctx context.Context, tenantID, userID uuid.UUID, folderIDs []uuid.UUID) error {
	return s.folders.Reorder(ctx, tenantID, userID, folderIDs)
}

// MoveConversation files (folderID != nil) or unfiles (folderID == nil) a
// single conversation. Asserts the caller owns the conversation AND — when
// filing — the destination folder, so a user can neither move someone
// else's chat nor drop their chat into someone else's folder.
func (s *FolderService) MoveConversation(
	ctx context.Context, tenantID, userID, convID uuid.UUID, folderID *uuid.UUID,
) error {
	conv, err := s.conversations.GetByID(ctx, tenantID, convID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	if conv.UserID != userID {
		// Don't leak existence across users in the same tenant.
		return ErrConversationNotFound
	}
	if folderID != nil {
		if _, err := s.folders.GetByID(ctx, tenantID, userID, *folderID); err != nil {
			if errors.Is(err, repository.ErrNotFound) {
				return ErrFolderNotFound
			}
			return err
		}
	}
	if err := s.conversations.MoveToFolder(ctx, tenantID, convID, folderID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}
