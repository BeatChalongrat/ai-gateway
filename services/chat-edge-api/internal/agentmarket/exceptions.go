package agentmarket

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ── Run exceptions (AM2 Phase F) ─────────────────────────────────────────
//
// A package's judgement calls that are not a question for the run — an
// expense over its limit, a missing receipt, a possible duplicate — are left
// for a person as agent_run_exceptions rows (table from AM1, unused until
// now). Nothing is rejected automatically: a row is open until a person
// picks one of its own choices, and the choice is recorded with who and
// when. An "info" row (no choices) is stored as "noted": shown, never
// waiting on anyone. The runner stores a succeeded run's exceptions from
// computed.json ("run_exceptions").

// RunException is one row.
type RunException struct {
	ID             uuid.UUID       `json:"id"`
	RunID          uuid.UUID       `json:"run_id"`
	Severity       string          `json:"severity"`
	ReasonCode     string          `json:"reason_code"`
	SourceRef      json.RawMessage `json:"source_ref"`
	ProposedAction string          `json:"proposed_action"`
	Choices        json.RawMessage `json:"choices"`
	Status         string          `json:"status"`
	ResolvedAt     *time.Time      `json:"resolved_at,omitempty"`
	CreatedAt      time.Time       `json:"created_at"`
}

// ExceptionIn is what a package writes (computed.json run_exceptions[]).
type ExceptionIn struct {
	ReasonCode     string          `json:"reason_code"`
	Reason         string          `json:"reason"`
	Severity       string          `json:"severity"`
	Subject        string          `json:"subject"`
	SourceRef      json.RawMessage `json:"source_ref"`
	Detail         string          `json:"detail"`
	ProposedAction string          `json:"proposed_action"`
	Choices        json.RawMessage `json:"choices"`
}

// ReplaceRunExceptions stores a run's exceptions (a re-run replaces them;
// decisions already taken on the same reason and subject are kept).
func (s *Store) ReplaceRunExceptions(ctx context.Context, run uuid.UUID, rows []ExceptionIn) (int, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck // rollback after commit is a no-op
	if _, err := tx.Exec(ctx, `DELETE FROM agent_market.agent_run_exceptions WHERE run_id = $1 AND status IN ('open', 'noted')`, run); err != nil {
		return 0, err
	}
	n := 0
	for _, e := range rows {
		ref := map[string]any{}
		_ = json.Unmarshal(e.SourceRef, &ref)
		ref["subject"], ref["reason"], ref["detail"] = e.Subject, e.Reason, e.Detail
		refJSON, _ := json.Marshal(ref)
		var decided bool
		_ = tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM agent_market.agent_run_exceptions
			WHERE run_id = $1 AND reason_code = $2 AND source_ref->>'subject' = $3 AND status = 'resolved')`,
			run, e.ReasonCode, e.Subject).Scan(&decided)
		if decided {
			continue
		}
		sev, status := e.Severity, "open"
		if sev == "" {
			sev = "review"
		}
		if sev == "info" {
			status = "noted"
		}
		if _, err := tx.Exec(ctx, `INSERT INTO agent_market.agent_run_exceptions
			(run_id, severity, reason_code, source_ref, proposed_action, choices, status)
			VALUES ($1, $2, $3, $4, $5, $6, $7)`, run, sev, e.ReasonCode, refJSON, e.ProposedAction, jsonOr(e.Choices, "[]"), status); err != nil {
			return n, err
		}
		if status == "open" {
			n++
		}
	}
	return n, tx.Commit(ctx)
}

const exceptionCols = `id, run_id, severity, reason_code, source_ref, proposed_action, choices, status, resolved_at, created_at`

func scanException(r pgx.Row) (RunException, error) {
	var e RunException
	err := r.Scan(&e.ID, &e.RunID, &e.Severity, &e.ReasonCode, &e.SourceRef, &e.ProposedAction, &e.Choices, &e.Status,
		&e.ResolvedAt, &e.CreatedAt)
	return e, err
}

func (a *API) runExceptions(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	ctx := r.Context()
	if _, err := a.store.GetRun(ctx, c.TenantUUID, c.UserID, id); err != nil {
		a.fail(w, err, "run")
		return
	}
	out, err := a.store.RunExceptions(ctx, id)
	if err != nil {
		a.fail(w, err, "exceptions")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"exceptions": nonNil(out)})
}

// RunExceptions lists a run's exceptions, the open ones first.
func (s *Store) RunExceptions(ctx context.Context, run uuid.UUID) ([]RunException, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+exceptionCols+` FROM agent_market.agent_run_exceptions
		WHERE run_id = $1 ORDER BY (status = 'open') DESC, created_at, id`, run)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []RunException
	for rows.Next() {
		e, err := scanException(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

func (a *API) decideException(w http.ResponseWriter, r *http.Request) {
	c, _ := caller(r)
	id, ok := pathID(r, "id")
	if !ok {
		badRequest(w, "BAD_ID")
		return
	}
	var body struct {
		Choice json.RawMessage `json:"choice"`
		Note   string          `json:"note"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&body); err != nil || len(body.Choice) == 0 {
		badRequest(w, "BAD_JSON")
		return
	}
	ctx := r.Context()
	ex, err := scanException(a.store.Pool.QueryRow(ctx, `SELECT `+exceptionCols+` FROM agent_market.agent_run_exceptions e
		WHERE e.id = $1 AND EXISTS (SELECT 1 FROM agent_market.agent_runs r WHERE r.id = e.run_id AND r.tenant_id = $2 AND r.user_id = $3)`,
		id, c.TenantUUID, c.UserID))
	if errors.Is(err, pgx.ErrNoRows) {
		a.fail(w, ErrNotFound, "exception")
		return
	}
	if err != nil {
		a.fail(w, err, "exception")
		return
	}
	var choices []struct {
		Label string          `json:"label"`
		Value json.RawMessage `json:"value"`
	}
	_ = json.Unmarshal(ex.Choices, &choices)
	label := ""
	for _, ch := range choices {
		if JSONEqual(ch.Value, body.Choice) {
			label = ch.Label
		}
	}
	if ex.Status != "open" {
		say(w, http.StatusConflict, "NOT_OPEN")
		return
	}
	if label == "" {
		// only the exception's own choices: there is no hidden "reject"
		say(w, http.StatusBadRequest, "BAD_CHOICE")
		return
	}
	if len([]rune(body.Note)) > 1000 {
		body.Note = string([]rune(body.Note)[:1000])
	}
	decision, _ := json.Marshal(map[string]any{"choice": body.Choice, "label": label, "note": body.Note})
	out, err := scanException(a.store.Pool.QueryRow(ctx, `UPDATE agent_market.agent_run_exceptions SET status = 'resolved',
			resolved_by = $2, resolved_at = now(), source_ref = source_ref || jsonb_build_object('decision', $3::jsonb)
		WHERE id = $1 AND status = 'open' RETURNING `+exceptionCols, id, c.UserID, decision))
	if errors.Is(err, pgx.ErrNoRows) { // decided a moment ago, elsewhere
		say(w, http.StatusConflict, "NOT_OPEN")
		return
	}
	if err != nil {
		a.fail(w, err, "exception")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"exception": out})
}
