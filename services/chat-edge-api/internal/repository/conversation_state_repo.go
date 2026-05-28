// ConversationStateRepo persists chat.conversation_states (S3).
//
// One row per conversation; conversation_id is PK. Every method
// scopes by tenant_id + user_id where applicable so a leaked
// conversation id from another user surfaces as ErrNotFound
// (matching the S0 ownership-leak audit pattern).
package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type ConversationStateRepo struct {
	db DB
}

func NewConversationStateRepo(db *pgxpool.Pool) *ConversationStateRepo {
	return &ConversationStateRepo{db: db}
}

func (r *ConversationStateRepo) WithTx(tx pgx.Tx) *ConversationStateRepo {
	return &ConversationStateRepo{db: tx}
}

const stateColumns = `
	conversation_id, tenant_id, user_id,
	current_goal,
	structured_summary, confirmed_decisions, active_artifacts,
	open_tasks, rejected_approaches, unresolved_questions,
	summarized_up_to_message_id, message_count_at_summary, summary_token_count,
	created_at, updated_at
`

func scanState(row pgx.Row, s *model.ConversationState) error {
	return row.Scan(
		&s.ConversationID, &s.TenantID, &s.UserID,
		&s.CurrentGoal,
		&s.StructuredSummary, &s.ConfirmedDecisions, &s.ActiveArtifacts,
		&s.OpenTasks, &s.RejectedApproaches, &s.UnresolvedQuestions,
		&s.SummarizedUpToMessageID, &s.MessageCountAtSummary, &s.SummaryTokenCount,
		&s.CreatedAt, &s.UpdatedAt,
	)
}

// Get returns the conversation's state row, or (nil, nil) when no
// state exists yet (NOT an error — first-time conversations land
// here every time before the auto-extraction trigger fires). The
// service layer treats nil as "no state, skip injection".
//
// tenant + user scoping makes a leaked convID from another user
// indistinguishable from a missing row.
func (r *ConversationStateRepo) Get(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) (*model.ConversationState, error) {
	q := `SELECT ` + stateColumns + `
	      FROM chat.conversation_states
	      WHERE conversation_id = $1 AND user_id = $2 AND tenant_id = $3`
	s := &model.ConversationState{}
	if err := scanState(r.db.QueryRow(ctx, q, convID, userID, tenantID), s); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		return nil, fmt.Errorf("get conversation state: %w", err)
	}
	return s, nil
}

// Upsert writes the row. JSONB columns are passed verbatim — the
// service layer is responsible for valid JSON (empty arrays = "[]"
// rather than NULL, empty object = "{}"). updated_at is bumped
// automatically by the SET clause.
//
// We use ON CONFLICT (conversation_id) so the caller doesn't have
// to know whether the row exists — every save is "make it look
// like this".
func (r *ConversationStateRepo) Upsert(
	ctx context.Context, s *model.ConversationState,
) error {
	// Normalise empty JSONB blobs so the INSERT path doesn't error
	// on NULL — the service may pass a freshly-zeroed state.
	if len(s.StructuredSummary) == 0 {
		s.StructuredSummary = json.RawMessage(`{}`)
	}
	for _, p := range []*json.RawMessage{
		&s.ConfirmedDecisions, &s.ActiveArtifacts, &s.OpenTasks,
		&s.RejectedApproaches, &s.UnresolvedQuestions,
	} {
		if len(*p) == 0 {
			*p = json.RawMessage(`[]`)
		}
	}

	const q = `
		INSERT INTO chat.conversation_states (
			conversation_id, tenant_id, user_id,
			current_goal,
			structured_summary, confirmed_decisions, active_artifacts,
			open_tasks, rejected_approaches, unresolved_questions,
			summarized_up_to_message_id, message_count_at_summary, summary_token_count
		)
		VALUES (
			$1, $2, $3,
			$4,
			$5::jsonb, $6::jsonb, $7::jsonb,
			$8::jsonb, $9::jsonb, $10::jsonb,
			$11, $12, $13
		)
		ON CONFLICT (conversation_id) DO UPDATE
		SET current_goal               = EXCLUDED.current_goal,
		    structured_summary         = EXCLUDED.structured_summary,
		    confirmed_decisions        = EXCLUDED.confirmed_decisions,
		    active_artifacts           = EXCLUDED.active_artifacts,
		    open_tasks                 = EXCLUDED.open_tasks,
		    rejected_approaches        = EXCLUDED.rejected_approaches,
		    unresolved_questions       = EXCLUDED.unresolved_questions,
		    summarized_up_to_message_id= EXCLUDED.summarized_up_to_message_id,
		    message_count_at_summary   = EXCLUDED.message_count_at_summary,
		    summary_token_count        = EXCLUDED.summary_token_count,
		    updated_at                 = NOW()
		RETURNING created_at, updated_at
	`
	if _, err := r.db.Exec(ctx, q,
		s.ConversationID, s.TenantID, s.UserID,
		s.CurrentGoal,
		s.StructuredSummary, s.ConfirmedDecisions, s.ActiveArtifacts,
		s.OpenTasks, s.RejectedApproaches, s.UnresolvedQuestions,
		s.SummarizedUpToMessageID, s.MessageCountAtSummary, s.SummaryTokenCount,
	); err != nil {
		return fmt.Errorf("upsert conversation state: %w", err)
	}
	return nil
}

// Delete removes the state row. Called from the conversation-delete
// cascade; the FK ON DELETE CASCADE already drops the row when the
// parent conversation goes away, so this is for explicit cleanup
// flows (e.g. a future "reset memory of this conversation" action).
func (r *ConversationStateRepo) Delete(
	ctx context.Context, convID, userID, tenantID uuid.UUID,
) error {
	const q = `
		DELETE FROM chat.conversation_states
		WHERE conversation_id = $1 AND user_id = $2 AND tenant_id = $3
	`
	if _, err := r.db.Exec(ctx, q, convID, userID, tenantID); err != nil {
		return fmt.Errorf("delete conversation state: %w", err)
	}
	return nil
}

// HardDeleteAllByUser is the GDPR right-to-erasure cascade for
// conversation_states. Issues a DELETE for every row belonging to
// the user — the S9 purge flow calls this alongside the memory +
// memory-event + response-source purges.
func (r *ConversationStateRepo) HardDeleteAllByUser(
	ctx context.Context, userID, tenantID uuid.UUID,
) (int, error) {
	const q = `DELETE FROM chat.conversation_states WHERE user_id = $1 AND tenant_id = $2`
	tag, err := r.db.Exec(ctx, q, userID, tenantID)
	if err != nil {
		return 0, fmt.Errorf("hard delete conversation states: %w", err)
	}
	return int(tag.RowsAffected()), nil
}
