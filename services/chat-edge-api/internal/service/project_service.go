package service

// ProjectService — Phase 4 Projects. Validation + ownership assertions
// over ProjectRepo, mirroring FolderService's shape. A project carries
// shared context (instructions + optional KB) that member conversations
// inherit at prompt-compile time.

import (
	"context"
	"errors"
	"log/slog"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

const (
	maxProjectNameLen = 255
	// maxProjectInstructionsLen caps the free-text instructions well below
	// anything that could crowd the context budget (~2k tokens of Thai).
	maxProjectInstructionsLen = 8000
)

var (
	ErrProjectNotFound        = errors.New("project not found")
	ErrProjectNameInvalid     = errors.New("project name must be 1-255 characters")
	ErrProjectInstructionsBig = errors.New("project instructions too long")
	ErrProjectKBNotFound      = errors.New("knowledge base not found")
)

type ProjectService struct {
	projects      *repository.ProjectRepo
	conversations *repository.ConversationRepo
	// memories — FIX58. Optional; when wired, deleting a project also
	// erases the memories scoped to it, which are unreachable once the
	// project is gone. nil leaves the pre-FIX58 behaviour.
	memories *repository.MemoryRepo
	// FIX22 §1a — optional write-time prompt guard for the instructions
	// text (it is folded verbatim into every member conversation's prompt).
	guard PromptGuard
}

// SetMemoryRepo wires the project-memory eraser used on delete (FIX58).
func (s *ProjectService) SetMemoryRepo(m *repository.MemoryRepo) { s.memories = m }

// SetPromptGuard wires the FIX22 write-time guard (nil = unchecked).
func (s *ProjectService) SetPromptGuard(g PromptGuard) { s.guard = g }

func NewProjectService(
	projects *repository.ProjectRepo, conversations *repository.ConversationRepo,
) *ProjectService {
	return &ProjectService{projects: projects, conversations: conversations}
}

func validProjectName(name string) (string, error) {
	n := strings.TrimSpace(name)
	if n == "" || len(n) > maxProjectNameLen {
		return "", ErrProjectNameInvalid
	}
	return n, nil
}

func (s *ProjectService) Create(
	ctx context.Context, tenantID, userID uuid.UUID, name, instructions string,
) (*model.Project, error) {
	n, err := validProjectName(name)
	if err != nil {
		return nil, err
	}
	instructions = strings.TrimSpace(instructions)
	if len(instructions) > maxProjectInstructionsLen {
		return nil, ErrProjectInstructionsBig
	}
	if err := guardPromptText(ctx, s.guard, tenantID, userID, instructions, "the project instructions"); err != nil {
		return nil, err
	}
	return s.projects.Create(ctx, tenantID, userID, n, instructions)
}

func (s *ProjectService) List(
	ctx context.Context, tenantID, userID uuid.UUID,
) ([]model.Project, error) {
	return s.projects.ListByUser(ctx, tenantID, userID)
}

func (s *ProjectService) Get(
	ctx context.Context, tenantID, userID, projectID uuid.UUID,
) (*model.Project, error) {
	p, err := s.projects.GetByID(ctx, tenantID, userID, projectID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrProjectNotFound
		}
		return nil, err
	}
	return p, nil
}

// Update patches name and/or instructions; nil keeps the current value.
func (s *ProjectService) Update(
	ctx context.Context, tenantID, userID, projectID uuid.UUID,
	name, instructions *string,
) (*model.Project, error) {
	if name != nil {
		n, err := validProjectName(*name)
		if err != nil {
			return nil, err
		}
		name = &n
	}
	if instructions != nil {
		trimmed := strings.TrimSpace(*instructions)
		if len(trimmed) > maxProjectInstructionsLen {
			return nil, ErrProjectInstructionsBig
		}
		if err := guardPromptText(ctx, s.guard, tenantID, userID, trimmed, "the project instructions"); err != nil {
			return nil, err
		}
		instructions = &trimmed
	}
	p, err := s.projects.Update(ctx, tenantID, userID, projectID, name, instructions)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return nil, ErrProjectNotFound
		}
		return nil, err
	}
	return p, nil
}

// SetKnowledgeBase attaches (nil detaches) the project KB. The repo's
// EXISTS guard enforces the KB's tenant scope; RowsAffected==0 therefore
// means "project not yours OR KB not yours" — both surface as not-found
// so existence isn't probeable.
func (s *ProjectService) SetKnowledgeBase(
	ctx context.Context, tenantID, userID, projectID uuid.UUID, kbID *uuid.UUID,
) (*model.Project, error) {
	p, err := s.projects.SetKnowledgeBase(ctx, tenantID, userID, projectID, kbID)
	if err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			if kbID != nil {
				// Distinguish for the caller only after the project itself
				// is known to exist — one extra owner-scoped read.
				if _, gerr := s.projects.GetByID(ctx, tenantID, userID, projectID); gerr == nil {
					return nil, ErrProjectKBNotFound
				}
			}
			return nil, ErrProjectNotFound
		}
		return nil, err
	}
	return p, nil
}

// Delete removes the project and erases the memories scoped to it.
//
// Member conversations survive: conversations.project_id is ON DELETE
// SET NULL, so the chats fall back to being general chats. Their
// memories cannot follow them, because a project memory was saved under
// a separation the user asked for — silently promoting it into the
// global set would move one client's facts into every other chat, which
// is the leak this round exists to close, arriving by the back door.
//
// The memories are erased BEFORE the project row goes, so a failure
// leaves the project (and therefore its memories) still reachable
// rather than leaving orphans nothing can read or delete.
func (s *ProjectService) Delete(
	ctx context.Context, tenantID, userID, projectID uuid.UUID,
) error {
	if s.memories != nil {
		ids, err := s.memories.SoftDeleteByProject(ctx, projectID, userID, tenantID)
		if err != nil {
			return err
		}
		for _, id := range ids {
			mid := id
			if err := s.memories.LogEvent(ctx, &model.MemoryEvent{
				MemoryID:  &mid,
				TenantID:  tenantID,
				UserID:    userID,
				Action:    "deleted",
				ActorType: "system",
				Reason:    "project deleted",
				Metadata:  map[string]any{"project_id": projectID.String()},
			}); err != nil {
				// The erase is the part that matters and it already
				// committed; a missing audit line must not resurrect
				// the row or fail the user's delete.
				slog.Warn("project delete: memory audit log failed",
					"memory_id", mid.String(), "err", err)
			}
		}
	}
	if err := s.projects.Delete(ctx, tenantID, userID, projectID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrProjectNotFound
		}
		return err
	}
	return nil
}

// MoveConversation assigns (nil un-assigns) a conversation to a project.
// Ownership assertions mirror FolderService.MoveConversation: conversation
// must belong to the caller, and the target project must too.
func (s *ProjectService) MoveConversation(
	ctx context.Context, tenantID, userID, convID uuid.UUID, projectID *uuid.UUID,
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
	if projectID != nil {
		if _, err := s.projects.GetByID(ctx, tenantID, userID, *projectID); err != nil {
			if errors.Is(err, repository.ErrNotFound) {
				return ErrProjectNotFound
			}
			return err
		}
	}
	if err := s.projects.MoveConversationToProject(ctx, tenantID, convID, projectID); err != nil {
		if errors.Is(err, repository.ErrNotFound) {
			return ErrConversationNotFound
		}
		return err
	}
	return nil
}
