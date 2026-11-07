package service

// FIX126 F4 — a file made from a plan card belongs to the message that wrote
// the plan.
//
// POST /conversations/{id}/document/execute (and /excel/execute) stored the
// generated file with message_id NULL. The page draws a message's files from
// GET /messages (files are grouped by message_id), so after a reload the
// assistant message that wrote the plan had no file: the plan card came back
// as if nothing had been made, and the file showed up only in the header's
// "made 1 file" chip. The generated row now carries the plan's message:
//
//   - the id the client names (message_id in the execute body) when it is
//     an assistant message of this conversation owned by the caller;
//   - otherwise the newest assistant message whose text carries the plan's
//     fence (:::doc-plan / :::pptx-plan / :::excel-plan), preferring one
//     that names the plan's output filename — today's page does not send
//     message_id, so this is what links its files.
//
// No owner found → NULL, exactly as before.

import (
	"context"
	"errors"
	"log/slog"
	"strings"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
)

// PlanOwnerResolver finds the message a plan execution belongs to.
type PlanOwnerResolver struct {
	messages *repository.MessageRepo
}

func NewPlanOwnerResolver(messages *repository.MessageRepo) *PlanOwnerResolver {
	return &PlanOwnerResolver{messages: messages}
}

// PlanFenceForFormat maps an execute call to the fence its plan card came
// from: excel → excel-plan, pptx → pptx-plan, docx/pdf → doc-plan.
func PlanFenceForFormat(format string) string {
	switch strings.ToLower(strings.TrimSpace(format)) {
	case "excel", "xlsx":
		return "excel-plan"
	case "pptx":
		return "pptx-plan"
	default:
		return "doc-plan"
	}
}

// Resolve returns the owning message id, or nil when none can be named.
// requested is the client's message_id (uuid.Nil when absent); hint is the
// plan's output filename ("" when none).
func (r *PlanOwnerResolver) Resolve(
	ctx context.Context, tenantID, userID, convID, requested uuid.UUID, fence, hint string,
) *uuid.UUID {
	if r == nil || r.messages == nil {
		return nil
	}
	if requested != uuid.Nil {
		m, err := r.messages.GetByID(ctx, tenantID, requested)
		if err == nil && m.ConversationID == convID && m.UserID == userID && m.Role == "assistant" {
			id := m.ID
			return &id
		}
		if err != nil && !errors.Is(err, repository.ErrNotFound) {
			slog.Warn("plan owner: message lookup failed", "conversation_id", convID.String(), "err", err)
		}
		// A foreign or unknown id is ignored, never trusted: fall through
		// to the fence search inside this conversation.
	}
	id, err := r.messages.FindPlanOwner(ctx, tenantID, userID, convID, fence, strings.TrimSpace(hint))
	if err != nil {
		if !errors.Is(err, repository.ErrNotFound) {
			slog.Warn("plan owner: search failed", "conversation_id", convID.String(), "err", err)
		}
		return nil
	}
	return &id
}
