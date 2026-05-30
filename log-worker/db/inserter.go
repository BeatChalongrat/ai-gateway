// Package db bulk-inserts LogRecords into Postgres via the COPY protocol.
//
// COPY is ~5x faster than multi-row INSERT for the batch sizes we
// see (~100 records per flush). pq.CopyIn is a thin wrapper around
// the libpq COPY ... FROM STDIN protocol that batches every Exec
// into the same transaction.
//
// Column order MUST match the proto field order in logworker.proto.
// A mismatch silently writes to the wrong column — so the column
// list and the field-extraction order are co-located in this file.
package db

import (
	"database/sql"
	"fmt"
	"time"

	"github.com/lib/pq"

	pb "github.com/beatchalongrat/ai-gateway/log-worker/pb"
)

// requestLogColumns is the column list passed to pq.CopyIn. Every
// column here is either populated from a LogRecord field, or has a
// server-side default that COPY can omit. ``created_at`` falls under
// the latter — request_logs.created_at has ``server_default=func.now()``.
var requestLogColumns = []string{
	"request_id",
	"tenant_id",
	"project_id",
	"provider",
	"model",
	"status",
	"policy_action",
	"latency_ms",
	"prompt_tokens",
	"completion_tokens",
	"total_tokens",
	"estimated_cost_usd",
	"error_code",
	"router_used",
	"fallback_used",
	"fallback_attempts",
	"original_provider",
	"original_model",
	"dlp_input_findings",
	"dlp_input_action",
	"dlp_output_findings",
	"dlp_categories",
	"policy_profile_id",
	"input_raw",
	"input_masked",
	"output_raw",
	"output_final",
	"dlp_findings_json",
	"input_raw_encrypted",
	"reasoning_tokens",
	"reasoning_cost",
}

// InsertBatch streams ``batch`` into ``request_logs`` via COPY.
//
// The whole batch lives inside one transaction. A partial failure
// (e.g. a unique-violation on one row) aborts the entire batch —
// pq.CopyIn does not support row-level error handling. The caller
// (worker.writeBatch) logs the abort + counts the records as
// dropped after exhausting its retry budget.
func InsertBatch(database *sql.DB, batch []*pb.LogRecord) error {
	if len(batch) == 0 {
		return nil
	}

	txn, err := database.Begin()
	if err != nil {
		return fmt.Errorf("begin: %w", err)
	}

	stmt, err := txn.Prepare(pq.CopyIn("request_logs", requestLogColumns...))
	if err != nil {
		_ = txn.Rollback()
		return fmt.Errorf("prepare copy: %w", err)
	}

	for _, rec := range batch {
		if _, err := stmt.Exec(rowArgs(rec)...); err != nil {
			_ = stmt.Close()
			_ = txn.Rollback()
			return fmt.Errorf("copy row request_id=%s: %w", rec.GetRequestId(), err)
		}
	}

	// Final Exec with no args flushes the COPY buffer.
	if _, err := stmt.Exec(); err != nil {
		_ = stmt.Close()
		_ = txn.Rollback()
		return fmt.Errorf("copy flush: %w", err)
	}
	if err := stmt.Close(); err != nil {
		_ = txn.Rollback()
		return fmt.Errorf("copy close: %w", err)
	}

	if err := txn.Commit(); err != nil {
		return fmt.Errorf("commit: %w", err)
	}
	return nil
}

// rowArgs extracts the column values for a single record. Order MUST
// match ``requestLogColumns`` exactly. Empty strings on optional
// columns translate to the NULL sentinel via emptyToNil — Postgres
// distinguishes '' and NULL for ``text`` columns, and request_logs
// historically uses NULL on absent fields (e.g. error_code on a
// success row).
func rowArgs(r *pb.LogRecord) []any {
	return []any{
		r.GetRequestId(),
		r.GetTenantId(),
		r.GetProjectId(),
		r.GetProvider(),
		r.GetModel(),
		r.GetStatus(),
		r.GetPolicyAction(),
		int64(r.GetLatencyMs()),
		r.GetPromptTokens(),
		r.GetCompletionTokens(),
		r.GetTotalTokens(),
		r.GetEstimatedCostUsd(),
		emptyToNil(r.GetErrorCode()),
		emptyToNil(r.GetRouterUsed()),
		r.GetFallbackUsed(),
		emptyToNil(r.GetFallbackAttemptsJson()),
		emptyToNil(r.GetOriginalProvider()),
		emptyToNil(r.GetOriginalModel()),
		r.GetDlpInputFindings(),
		emptyToNil(r.GetDlpInputAction()),
		r.GetDlpOutputFindings(),
		emptyToNil(r.GetDlpCategories()),
		emptyToNil(r.GetPolicyProfileId()),
		emptyToNil(r.GetInputRaw()),
		emptyToNil(r.GetInputMasked()),
		emptyToNil(r.GetOutputRaw()),
		emptyToNil(r.GetOutputFinal()),
		emptyToNil(r.GetDlpFindingsJson()),
		emptyToNil(r.GetInputRawEncrypted()),
		r.GetReasoningTokens(),
		r.GetReasoningCost(),
	}
}

func emptyToNil(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// CreatedAtFromUnix converts the proto's int64 ms epoch into a
// ``time.Time`` for diagnostics (worker logs end-to-end latency).
// Not part of COPY — request_logs.created_at uses server-side now().
func CreatedAtFromUnix(unixMS int64) time.Time {
	if unixMS == 0 {
		return time.Time{}
	}
	return time.UnixMilli(unixMS).UTC()
}
