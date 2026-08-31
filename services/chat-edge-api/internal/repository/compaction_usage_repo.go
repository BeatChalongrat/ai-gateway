// CompactionUsageRepo persists chat.compaction_usage (FIX33 A-W8,
// migration 038): one row per state-extraction call, so the cost of
// compacting a conversation is visible separately from the cost of
// answering in it.
package repository

import (
	"context"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

type CompactionUsageRepo struct {
	db DB
}

func NewCompactionUsageRepo(db *pgxpool.Pool) *CompactionUsageRepo {
	return &CompactionUsageRepo{db: db}
}

// CompactionUsageRow is the insert shape (mirrors service.CompactionUsage
// without importing the service package).
type CompactionUsageRow struct {
	ConversationID uuid.UUID
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ModelID        string
	Trigger        string
	PromptTokens   int
	OutputTokens   int
	CostUSD        float64
	MessagesFolded int
}

func (r *CompactionUsageRepo) Insert(ctx context.Context, u CompactionUsageRow) error {
	const q = `
		INSERT INTO chat.compaction_usage (
			conversation_id, tenant_id, user_id, model_id, trigger,
			prompt_tokens, output_tokens, cost_usd, messages_folded
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
	`
	if _, err := r.db.Exec(ctx, q,
		u.ConversationID, u.TenantID, u.UserID, u.ModelID, u.Trigger,
		u.PromptTokens, u.OutputTokens, u.CostUSD, u.MessagesFolded,
	); err != nil {
		return fmt.Errorf("insert compaction usage: %w", err)
	}
	return nil
}

// CompactionUsageTotals is what GET /usage/me reports for the period.
type CompactionUsageTotals struct {
	Calls        int     `json:"calls"`
	PromptTokens int64   `json:"prompt_tokens"`
	OutputTokens int64   `json:"output_tokens"`
	Tokens       int64   `json:"tokens"`
	CostUSD      float64 `json:"cost_usd"`
	Manual       int     `json:"manual_calls"`
}

// TotalsForUser sums the user's compaction rows since `since`.
func (r *CompactionUsageRepo) TotalsForUser(
	ctx context.Context, tenantID, userID uuid.UUID, since time.Time,
) (CompactionUsageTotals, error) {
	const q = `
		SELECT COUNT(*)::int,
		       COALESCE(SUM(prompt_tokens), 0)::bigint,
		       COALESCE(SUM(output_tokens), 0)::bigint,
		       COALESCE(SUM(cost_usd), 0)::double precision,
		       COALESCE(SUM(CASE WHEN trigger = 'manual' THEN 1 ELSE 0 END), 0)::int
		FROM chat.compaction_usage
		WHERE tenant_id = $1 AND user_id = $2 AND created_at >= $3
	`
	var t CompactionUsageTotals
	if err := r.db.QueryRow(ctx, q, tenantID, userID, since).Scan(
		&t.Calls, &t.PromptTokens, &t.OutputTokens, &t.CostUSD, &t.Manual,
	); err != nil {
		return t, fmt.Errorf("compaction usage totals: %w", err)
	}
	t.Tokens = t.PromptTokens + t.OutputTokens
	return t, nil
}

// TotalsForConversation sums one conversation's compaction rows.
func (r *CompactionUsageRepo) TotalsForConversation(
	ctx context.Context, tenantID, convID uuid.UUID,
) (CompactionUsageTotals, error) {
	const q = `
		SELECT COUNT(*)::int,
		       COALESCE(SUM(prompt_tokens), 0)::bigint,
		       COALESCE(SUM(output_tokens), 0)::bigint,
		       COALESCE(SUM(cost_usd), 0)::double precision,
		       COALESCE(SUM(CASE WHEN trigger = 'manual' THEN 1 ELSE 0 END), 0)::int
		FROM chat.compaction_usage
		WHERE tenant_id = $1 AND conversation_id = $2
	`
	var t CompactionUsageTotals
	if err := r.db.QueryRow(ctx, q, tenantID, convID).Scan(
		&t.Calls, &t.PromptTokens, &t.OutputTokens, &t.CostUSD, &t.Manual,
	); err != nil {
		return t, fmt.Errorf("compaction usage conv totals: %w", err)
	}
	t.Tokens = t.PromptTokens + t.OutputTokens
	return t, nil
}
