package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

// AM9 (D): per-agent egress. The organisation's switch stays the ceiling;
// each agent has a setting under it that can only be stricter, off by
// default for an agent that touches accounting. Both levels are shown where
// a person picks a model, and every change is in the admin audit log.

func TestAM9AnAgentsOwnSettingOnlyNarrowsTheOrganisations(t *testing.T) {
	fin, ops := Manifest{Category: "finance"}, Manifest{Category: "operations"}
	yes, no := true, false
	for _, c := range []struct {
		name           string
		org            bool
		m              Manifest
		set            *bool
		agent, eff, df bool
	}{
		{"organisation off, the agent set on", false, ops, &yes, true, false, false},
		{"organisation off, an accounting agent set on", false, fin, &yes, true, false, false},
		{"organisation on, an accounting agent never set", true, fin, nil, false, false, true},
		{"organisation on, an accounting agent an admin opened", true, fin, &yes, true, true, false},
		{"organisation on, another agent closed", true, ops, &no, false, false, false},
		{"organisation on, another agent never set", true, ops, nil, true, true, true},
	} {
		e := EgressOf(c.org, c.m, c.set)
		if e.Org != c.org || e.Agent != c.agent || e.Effective != c.eff || e.AgentDefault != c.df || e.Words.Empty() {
			t.Fatalf("%s: %+v", c.name, e)
		}
	}
}

func TestAM9TheModelMenuSaysBothLevelsAndLocksOutsideModelsForAnAccountingAgent(t *testing.T) {
	m := Manifest{Category: "finance"}
	m.Capabilities.Required = []string{"model.text"}
	rm := RunnerModels{Primary: "deepseek-v4-flash", Fallback: "qwen3.8-27b-fp8", SelfHosted: map[string]bool{"qwen3.8-27b-fp8": true}}
	p := MenuFor(Install{ModelChoice: ModelAuto}, m, EgressOf(true, m, nil), rm)
	allowed := map[string]bool{}
	for _, x := range p.Models {
		allowed[x.ID] = x.Allowed
	}
	if allowed["deepseek-v4-flash"] || !allowed["qwen3.8-27b-fp8"] {
		t.Fatalf("an accounting agent's documents stay in: %+v", p.Models)
	}
	if !p.Egress.Org || p.Egress.Agent || !strings.Contains(p.BlockedReason.String(), "ข้อมูลบัญชี") {
		t.Fatalf("the menu says the organisation allows it and this agent does not: %+v / %q", p.Egress, p.BlockedReason.String())
	}
	if ok, why := p.ChoiceAllowed("deepseek-v4-flash"); ok || why != p.BlockedReason {
		t.Fatalf("choosing the outside model is refused with the reason shown: %v %q", ok, why.String())
	}
	yes := true
	p = MenuFor(Install{ModelChoice: ModelAuto}, m, EgressOf(true, m, &yes), rm)
	if ok, _ := p.ChoiceAllowed("deepseek-v4-flash"); !ok {
		t.Fatal("an admin opened this agent: the outside model may be chosen")
	}
	p = MenuFor(Install{ModelChoice: ModelAuto}, m, EgressOf(false, m, &yes), rm)
	if ok, why := p.ChoiceAllowed("deepseek-v4-flash"); ok || why.String() != reasonOutside {
		t.Fatalf("the organisation's switch is the ceiling: %v %q", ok, why.String())
	}
}

// seedAccountingDefinition registers a newer version of the test agent that
// touches accounting (category finance).
func (e *testEnv) seedAccountingDefinition() {
	e.t.Helper()
	man := strings.Replace(testManifest, `"name":"รายงานการเงิน",`, `"name":"รายงานการเงิน","category":"finance",`, 1)
	man = strings.Replace(man, `"version":"1.0.0"`, `"version":"1.1.0"`, 1)
	if _, err := e.store.UpsertDefinition(context.Background(), Definition{Slug: "sran.financial-report", Version: "1.1.0",
		Name: "รายงานการเงิน", Manifest: json.RawMessage(man), Checksum: "pkgsum2", EngineChecksum: "engsum"}); err != nil {
		e.t.Fatal(err)
	}
}

func egressRow(t *testing.T, body map[string]any, slug string) map[string]any {
	t.Helper()
	for _, x := range body["agents"].([]any) {
		if r := x.(map[string]any); r["slug"] == slug {
			return r
		}
	}
	t.Fatalf("no %s in %v", slug, body)
	return nil
}

func TestAM9AnAdminSetsAnAgentsEgressUnderTheCeilingAndTheLogKeepsIt(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.seedDefinition()
	e.seedAccountingDefinition()
	const slug = "sran.financial-report"
	m := Manifest{Category: "finance"}

	r := egressRow(t, e.mustAdmin(uiAdmin, 200, "GET", "/egress", nil), slug)
	if r["org"] != false || r["accounting"] != true || r["agent"] != false || r["agent_default"] != true || r["effective"] != false {
		t.Fatalf("a new organisation: nothing leaves, and the agent's own default is off: %v", r)
	}
	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"documents_may_leave": true})
	log.take()
	if got, _ := e.store.EgressFor(ctx, DefaultOrg, slug, m); got.Effective {
		t.Fatalf("the organisation allowing it does not open an accounting agent: %+v", got)
	}

	// the agent's own setting: an admin's change, logged with what it was and what it is
	out := e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+slug, map[string]any{"documents_may_leave": true})
	if eg := out["egress"].(map[string]any); eg["effective"] != true || eg["agent_default"] != false {
		t.Fatalf("opened: %v", out)
	}
	x, ok := find(log.take(), "agent_market.agent.documents_may_leave")
	if !ok || x.ResourceType != "agent_market_agent" || x.ResourceID != slug || x.Actor != "session:"+uiAdmin.ID ||
		x.Details["from"] != false || x.Details["from_default"] != true || x.Details["to"] != true || x.Details["org_allows"] != true {
		t.Fatalf("the entry: %+v", x)
	}
	if got, _ := e.store.EgressFor(ctx, DefaultOrg, slug, m); !got.Effective {
		t.Fatalf("both allow it: %+v", got)
	}
	// the same value again is no change and no entry
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+slug, map[string]any{"documents_may_leave": true})
	if got := log.take(); len(got) != 0 {
		t.Fatalf("logged a value that did not move: %+v", got)
	}

	// the organisation's switch is the ceiling: off there is off for the agent, whatever its own says
	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"documents_may_leave": false})
	if got, _ := e.store.EgressFor(ctx, DefaultOrg, slug, m); got.Effective || !got.Agent {
		t.Fatalf("the ceiling holds: %+v", got)
	}

	// a publisher may not touch it; a change the log cannot take does not happen
	if code, _ := e.adminDo(uiPublisher, "PUT", "/egress/"+slug, map[string]any{"documents_may_leave": false}); code != 403 {
		t.Fatalf("a publisher changed egress: %d", code)
	}
	if code, _ := e.adminDo(uiPublisher, "GET", "/egress", nil); code != 403 {
		t.Fatalf("a publisher read egress: %d", code)
	}
	log.take()
	log.setFail(true)
	if code, _ := e.adminDo(uiAdmin, "PUT", "/egress/"+slug, map[string]any{"documents_may_leave": false}); code != 503 {
		t.Fatalf("an unlogged change was accepted: %d", code)
	}
	log.setFail(false)
	if set, _ := e.store.AgentEgressSetting(ctx, e.store.Pool, DefaultOrg, slug); set == nil || !*set {
		t.Fatalf("the refused change moved the row: %v", set)
	}
}

func TestAM9Migration007DownUndoesAll(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_egress (org_id, slug, documents_may_leave) VALUES ('default', 'x', true)`); err != nil {
		t.Fatal(err)
	}
	down, err := migrationFS.ReadFile("migrations/007_am9.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	tables := e.countRows(`SELECT count(*) FROM information_schema.tables WHERE table_schema = 'agent_market' AND table_name = 'agent_egress'`)
	cols := e.countRows(`SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market'
		AND table_name = 'email_sends' AND column_name = 'transport'`)
	rec := e.countRows(`SELECT count(*) FROM agent_market.schema_migrations WHERE version = '007_am9'`)
	if tables != 0 || cols != 0 || rec != 0 {
		t.Fatalf("down leaves table %d, column %d, record %d", tables, cols, rec)
	}
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "007_am9" {
		t.Fatalf("up again: %v %v", did, err)
	}
}
