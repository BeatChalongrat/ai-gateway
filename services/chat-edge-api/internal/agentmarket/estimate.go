package agentmarket

import (
	"context"
	"math"
	"net/http"
)

// ── How long a run takes, said before it starts (AM6 · AM11b) ────────────
//
// AM6 put a line where a run starts: what a run of this agent usually costs,
// how long it takes, and when it is charged. AM11b: a person using an agent
// never sees credits or baht — what a run costs is the admin's business and
// has its page (admin app › Agent Market › Spend). What is left is the reason
// the line exists at all: waiting without knowing how long is unkind. So the
// line is the time and nothing else ("ราว 20 วินาที"), and when nothing has
// been measured there is no line.
//
// The time is the median of the organisation's own delivered runs of the
// agent over the last 90 days (a run that finished with its gate open is a
// delivered run) once there are three of them; until then the catalog's
// sample run; with neither, nothing is claimed.

// MinEstimateRuns is how many delivered runs of its own an organisation
// needs before its estimate comes from them.
const MinEstimateRuns = 3

// Where an estimate comes from.
const (
	EstimateOwnRuns = "own_runs"
	EstimateSample  = "sample"
	EstimateNone    = "none"
)

// Estimate is how long a run of an installed agent takes. It carries no
// figure of cost: this is what the chat app is sent.
type Estimate struct {
	Seconds   *float64   `json:"seconds"` // nil: nothing is known yet
	Basis     string     `json:"basis"`
	BasisRuns int        `json:"basis_runs"`
	Line      PersonText `json:"line"` // the one line the workspace shows before a run starts ("" when nothing is known)
}

// EstimateFor measures one agent for one organisation.
func (s *Store) EstimateFor(ctx context.Context, org, slug string) (Estimate, error) {
	e := Estimate{Basis: EstimateNone}
	var n int
	var secs *float64
	err := s.Pool.QueryRow(ctx, `
		SELECT count(*)::int,
			percentile_cont(0.5) WITHIN GROUP (ORDER BY
				greatest(extract(epoch FROM r.finished_at - r.started_at) - r.awaiting_ms / 1000.0, 0))::float8
		FROM agent_market.agent_runs r JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE i.org_id = $1 AND i.slug = $2 AND i.status <> 'catalog' AND r.kind = 'run'
			AND (r.status = 'succeeded' OR (r.status = 'failed' AND r.gate_open))
			AND r.created_at >= now() - interval '90 days' AND r.started_at IS NOT NULL AND r.finished_at IS NOT NULL`,
		orgOr(org), slug).Scan(&n, &secs)
	if err != nil {
		return e, err
	}
	switch {
	case n >= MinEstimateRuns && secs != nil:
		e.Basis, e.BasisRuns, e.Seconds = EstimateOwnRuns, n, secs
	default:
		if smp, err := s.CatalogSampleFor(ctx, slug); err == nil && smp.Status == RunSucceeded {
			e.Basis, e.BasisRuns, e.Seconds = EstimateSample, 1, smp.Seconds
		}
	}
	if e.Seconds != nil {
		v := math.Round(*e.Seconds*10) / 10
		e.Seconds = &v
	}
	e.Line = estimateLine(e)
	return e, nil
}

// estimateLine is the estimate as the one line a person reads: the time, and
// only the time. Nothing measured: no line.
func estimateLine(e Estimate) PersonText {
	if e.Seconds == nil || *e.Seconds <= 0 {
		return PersonText{}
	}
	return Text("ราว %s", Words(aboutSeconds(*e.Seconds)))
}

// aboutSeconds says a duration roughly: to five seconds under a minute and a
// half, to the minute above.
func aboutSeconds(s float64) PersonText {
	if s < 90 {
		n := int(math.Round(s/5) * 5)
		if n < 5 {
			n = 5
		}
		return Text("%s วินาที", Count(n))
	}
	return Text("%s นาที", Count(int(math.Round(s/60))))
}

// estimate: GET /installs/{id}/estimate.
func (a *API) estimate(w http.ResponseWriter, r *http.Request) {
	in, _, ok := a.ownedInstall(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	web := false
	if def, err := a.store.DefinitionByID(ctx, in.DefinitionID); err == nil {
		if m, err := ParseManifest(def.Manifest); err == nil {
			web = m.UsesWeb()
		}
	}
	e, err := a.store.EstimateFor(ctx, in.OrgID, in.Slug)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if web && e.Basis == EstimateSample {
		// AM12: the sample of an agent on the web runs on pages kept with the package — it searches nothing,
		// opens nothing and asks no model, so it says nothing of how long a real run takes. Until the
		// organisation has runs of its own, nothing is claimed.
		e = Estimate{Basis: EstimateNone}
	}
	writeJSON(w, http.StatusOK, e)
}
