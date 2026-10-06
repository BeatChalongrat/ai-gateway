package agentmarket

import (
	"context"
	"math"
	"net/http"
)

// ── What a round costs, said before it starts (AM6) ──────────────────────
//
// AM5 charged 1,134.58 credits for a round that ended "ยังไม่ลงตัว", and the
// person found out afterwards. The rule is now said where a round starts:
// what a round of this agent usually costs, and when it is charged — when
// the work is finished and its files delivered, whether or not the numbers
// agree; never for a round the system failed.
//
// The figure is the median of the organisation's own delivered rounds of the
// agent over the last 90 days (a round that finished with its gate open is a
// delivered round) once there are three of them; until then the catalog's
// sample run; with neither, nothing is claimed.

// MinEstimateRuns is how many delivered rounds of its own an organisation
// needs before its estimate comes from them.
const MinEstimateRuns = 3

// Where an estimate comes from.
const (
	EstimateOwnRuns = "own_runs"
	EstimateSample  = "sample"
	EstimateNone    = "none"
)

// Estimate is what a round of an installed agent costs.
type Estimate struct {
	Credits   *float64   `json:"credits"` // nil: nothing is known yet
	THB       *float64   `json:"thb"`
	Seconds   *float64   `json:"seconds"`
	Basis     string     `json:"basis"`
	BasisRuns int        `json:"basis_runs"`
	Line      PersonText `json:"line"` // the one sentence the workspace shows before a round starts
}

const (
	chargedWhen     = "คิดเครดิตเมื่องานเสร็จและส่งไฟล์ให้แล้ว"
	chargedWhenOpen = "คิดเครดิตเมื่องานเสร็จและส่งไฟล์ให้แล้ว ไม่ว่ายอดจะลงตัวหรือไม่"
	neverCharged    = "รอบที่ระบบขัดข้องไม่คิดเครดิต"
)

// EstimateFor measures one agent for one organisation. gated: the agent has a
// completion gate, so a round of it can finish without its numbers agreeing.
func (s *Store) EstimateFor(ctx context.Context, cfg Config, org, slug string, gated bool) (Estimate, error) {
	e := Estimate{Basis: EstimateNone}
	var n int
	var credits, secs *float64
	err := s.Pool.QueryRow(ctx, `
		SELECT count(*)::int,
			percentile_cont(0.5) WITHIN GROUP (ORDER BY r.credits_spent)::float8,
			percentile_cont(0.5) WITHIN GROUP (ORDER BY
				greatest(extract(epoch FROM r.finished_at - r.started_at) - r.awaiting_ms / 1000.0, 0))::float8
		FROM agent_market.agent_runs r JOIN agent_market.agent_installs i ON i.id = r.install_id
		WHERE i.org_id = $1 AND i.slug = $2 AND i.status <> 'catalog' AND r.kind = 'run'
			AND (r.status = 'succeeded' OR (r.status = 'failed' AND r.gate_open))
			AND r.created_at >= now() - interval '90 days' AND r.started_at IS NOT NULL AND r.finished_at IS NOT NULL`,
		orgOr(org), slug).Scan(&n, &credits, &secs)
	if err != nil {
		return e, err
	}
	switch {
	case n >= MinEstimateRuns && credits != nil:
		e.Basis, e.BasisRuns, e.Credits, e.Seconds = EstimateOwnRuns, n, credits, secs
	default:
		if smp, err := s.CatalogSampleFor(ctx, slug); err == nil && smp.Status == RunSucceeded {
			c := smp.Credits
			e.Basis, e.BasisRuns, e.Credits, e.Seconds = EstimateSample, 1, &c, smp.Seconds
		}
	}
	if e.Seconds != nil {
		v := math.Round(*e.Seconds*10) / 10
		e.Seconds = &v
	}
	if e.Credits != nil && cfg.CreditsPerUSD > 0 {
		thb := math.Round(*e.Credits/cfg.CreditsPerUSD*cfg.THBPerUSD*100) / 100
		e.THB = &thb
	}
	e.Line = estimateLine(e, gated)
	return e, nil
}

// estimateLine is the estimate as one sentence.
func estimateLine(e Estimate, gated bool) PersonText {
	when := Text(chargedWhen)
	if gated {
		when = Text(chargedWhenOpen)
	}
	if e.Credits == nil {
		return Text("ยังไม่มีข้อมูลค่าใช้จ่ายของ agent นี้ · %s · %s", Words(Text(chargedWhen)), Words(Text(neverCharged)))
	}
	cost := Text("รอบหนึ่งใช้ประมาณ %s เครดิต", Credits(about(*e.Credits)))
	if e.THB != nil {
		switch {
		case *e.THB > 0 && *e.THB < 0.01:
			cost = Text("%s (น้อยกว่า 0.01 บาท)", Words(cost))
		default:
			cost = Text("%s (≈ %s บาท)", Words(cost), Baht(*e.THB))
		}
	}
	if e.Seconds != nil && *e.Seconds > 0 {
		cost = Text("%s ราว %s", Words(cost), Words(aboutSeconds(*e.Seconds)))
	}
	return Text("%s · %s · %s", Words(cost), Words(when), Words(Text(neverCharged)))
}

// about rounds a figure to three digits that mean something: an estimate
// says "ประมาณ 1,130", not "1,134.58".
func about(v float64) float64 {
	if v <= 0 {
		return 0
	}
	if v < 100 {
		return math.Round(v*10) / 10
	}
	p := math.Pow(10, math.Floor(math.Log10(v))-2)
	return math.Round(v/p) * p
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
	gated, web := false, false
	if def, err := a.store.DefinitionByID(ctx, in.DefinitionID); err == nil {
		if m, err := ParseManifest(def.Manifest); err == nil {
			gated, web = m.CompletionGate, m.UsesWeb()
		}
	}
	e, err := a.store.EstimateFor(ctx, a.cfg, in.OrgID, in.Slug, gated)
	if err != nil {
		a.fail(w, err, "install")
		return
	}
	if web && e.Basis == EstimateSample {
		// AM12: the sample of an agent on the web runs on pages kept with the package — it searches nothing,
		// opens nothing and asks no model, so it says nothing of what a real run costs. Until the
		// organisation has runs of its own, the cost is said to be not known yet.
		e = Estimate{Basis: EstimateNone}
		e.Line = estimateLine(e, gated)
	}
	writeJSON(w, http.StatusOK, e)
}
