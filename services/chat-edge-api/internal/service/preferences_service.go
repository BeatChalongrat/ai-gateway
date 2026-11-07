package service

import (
	"context"
	"errors"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// PreferencesService is a thin convenience layer over PreferencesRepo.
// Its only non-trivial method is BuildInstructionsBlock, which
// renders the two custom-instruction fields into the markdown
// preamble that the message handler prepends to the system prompt.
type PreferencesService struct {
	prefs *repository.PreferencesRepo
}

func NewPreferencesService(p *repository.PreferencesRepo) *PreferencesService {
	return &PreferencesService{prefs: p}
}

// BuildInstructionsBlock renders the markdown preamble. Empty
// string when no preferences are stored (or both fields are blank),
// so the message-service can detect "nothing to inject" with a
// simple len()==0 check.
func (s *PreferencesService) BuildInstructionsBlock(
	ctx context.Context, tenantID, userID uuid.UUID,
) string {
	p, err := s.prefs.Get(ctx, tenantID, userID)
	if err != nil {
		if !errors.Is(err, repository.ErrNotFound) {
			// Treat read failures as "no preferences" — the message
			// still goes out, just without persona shaping.
			return ""
		}
		return ""
	}
	about := strings.TrimSpace(p.CustomInstructionsAbout)
	respond := strings.TrimSpace(p.CustomInstructionsRespond)
	if about == "" && respond == "" {
		return ""
	}
	var b strings.Builder
	if about != "" {
		b.WriteString("## About the user\n")
		b.WriteString(about)
		b.WriteString("\n")
	}
	if respond != "" {
		if b.Len() > 0 {
			b.WriteString("\n")
		}
		b.WriteString("## Response style\n")
		b.WriteString(respond)
		b.WriteString("\n")
	}
	return b.String()
}
