package repository

import (
	"context"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

type FeedbackRepo struct {
	db DB
}

func NewFeedbackRepo(db *pgxpool.Pool) *FeedbackRepo {
	return &FeedbackRepo{db: db}
}

func (r *FeedbackRepo) WithTx(tx pgx.Tx) *FeedbackRepo {
	return &FeedbackRepo{db: tx}
}

// Upsert writes (or overwrites) the (message_id, user_id) feedback
// row. The unique index added in migration 004 makes the
// ON CONFLICT clause atomic — toggling thumbs_up → thumbs_down
// updates the existing row in one statement, no race.
//
// Returns the persisted row with “id“ / “created_at“ populated.
// On UPDATE conflict the “created_at“ stays at the original
// insert time; the rating column gets the new value.
func (r *FeedbackRepo) Upsert(ctx context.Context, f *model.Feedback) (*model.Feedback, error) {
	const q = `
		INSERT INTO chat.feedback (message_id, tenant_id, user_id, rating)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (message_id, user_id) DO UPDATE
		SET rating = EXCLUDED.rating
		RETURNING id, created_at
	`
	err := r.db.QueryRow(ctx, q, f.MessageID, f.TenantID, f.UserID, f.Rating).Scan(
		&f.ID, &f.CreatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("upsert feedback: %w", err)
	}
	return f, nil
}

// ListRatingsByMessages returns the caller's rating for each of the
// given message ids, as a map[messageID]rating. Messages the user
// hasn't rated are simply absent from the map. Batched (one query
// via ANY($2)) so the message-list endpoint can render persisted
// thumbs state without a per-message round-trip. user-scoped: a
// different user's vote on the same message never leaks here.
func (r *FeedbackRepo) ListRatingsByMessages(
	ctx context.Context, userID uuid.UUID, msgIDs []uuid.UUID,
) (map[uuid.UUID]string, error) {
	out := make(map[uuid.UUID]string, len(msgIDs))
	if len(msgIDs) == 0 {
		return out, nil
	}
	const q = `
		SELECT message_id, rating
		FROM chat.feedback
		WHERE user_id = $1 AND message_id = ANY($2)
	`
	rows, err := r.db.Query(ctx, q, userID, msgIDs)
	if err != nil {
		return nil, fmt.Errorf("list feedback by messages: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var (
			mid    uuid.UUID
			rating string
		)
		if err := rows.Scan(&mid, &rating); err != nil {
			return nil, fmt.Errorf("scan feedback row: %w", err)
		}
		out[mid] = rating
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate feedback rows: %w", err)
	}
	return out, nil
}

// GetForMessage returns the caller's feedback on a specific
// message, or “ErrNotFound“. Used by message-list / detail views
// to render the current thumbs state in the UI.
func (r *FeedbackRepo) GetForMessage(ctx context.Context, msgID, userID uuid.UUID) (*model.Feedback, error) {
	const q = `
		SELECT id, message_id, tenant_id, user_id, rating, created_at
		FROM chat.feedback
		WHERE message_id = $1 AND user_id = $2
	`
	f := &model.Feedback{}
	err := r.db.QueryRow(ctx, q, msgID, userID).Scan(
		&f.ID, &f.MessageID, &f.TenantID, &f.UserID, &f.Rating, &f.CreatedAt,
	)
	if err != nil {
		if err.Error() == pgx.ErrNoRows.Error() {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("get feedback: %w", err)
	}
	return f, nil
}
