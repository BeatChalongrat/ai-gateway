package agentmarket

import (
	"context"

	"github.com/google/uuid"
)

// SetRunLang writes the language a round is made in, when the words typed with it ask for one (AM18): the
// run's params carry it as they do for a run started from a screen in that language (paramsIn), and every
// step, card and sentence of the run after that is in it. Only a run of its own takes it — a job done for
// a run keeps its parent's.
func (s *Store) SetRunLang(ctx context.Context, run uuid.UUID, l Lang) error {
	var params []byte
	if err := s.Pool.QueryRow(ctx, `SELECT params FROM agent_market.agent_runs WHERE id = $1`, run).Scan(&params); err != nil {
		return err
	}
	_, err := s.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET params = $2, updated_at = now() WHERE id = $1`,
		run, jsonOr(paramsIn(params, l), "{}"))
	return err
}
