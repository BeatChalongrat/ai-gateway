package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

// AM16 — the admin names who outside the organisation an agent's documents may reach.
//
// The owner's three answers, each held here by what is refused:
//   - both the route and the company are named: a recipient is a pair, and behind a router the company
//     is the one that runs the model;
//   - "may leave" on with nothing ticked means nobody;
//   - the organisation's list is the ceiling: an agent's list may only narrow it, never widen it.
// And each recipient added or removed is its own line of the audit log, naming both.

// everyoneNamed gives every model of rm that is not in-house a recipient of its own and returns e with
// all of them named, for the organisation and for the agent — the tests from before AM16, in which
// "documents may leave" alone opened every outside model, say what they always said.
func everyoneNamed(e Egress, rm *RunnerModels) Egress {
	list := []string{}
	for _, id := range rm.all() {
		c, in := rm.find(id)
		if rm.SelfHosted[id] || (in && c.SelfHosted) {
			continue
		}
		key := "route:" + id
		if in {
			for i := range rm.Catalog {
				if rm.Catalog[i].ID == id {
					rm.Catalog[i].Recipients = []string{key}
				}
			}
		} else {
			rm.Catalog = append(rm.Catalog, CatalogModel{ID: id, Recipients: []string{key}})
		}
		list = append(list, key)
	}
	return e.WithRecipients(list, list)
}

var am16Catalogue = RunnerModels{Primary: "qwen", Fallback: "deepseek", Vision: "qwen", SelfHosted: map[string]bool{"qwen": true},
	Catalog: []CatalogModel{
		{ID: "qwen", SelfHosted: true, Vision: true},
		{ID: "deepseek", Recipients: []string{"deepseek"}},
		{ID: "google/gemini", Vision: true, Recipients: []string{"openrouter:google"}},
		{ID: "anthropic/claude", Vision: true, Recipients: []string{"openrouter:anthropic", "openrouter:amazon", "openrouter:google"}},
		{ID: "openrouter/auto", Vision: true}, // nobody can be said to run it
	}}

func TestAM16ARecipientIsARouteAndTheCompanyThatRunsTheModel(t *testing.T) {
	for key, want := range map[string][2]string{"openrouter:google": {"openrouter", "google"}, "deepseek": {"deepseek", "deepseek"},
		"openrouter:amazon": {"openrouter", "amazon"}} {
		if r, c := SplitRecipient(key); r != want[0] || c != want[1] {
			t.Errorf("%s: route %q company %q", key, r, c)
		}
	}
	// what an admin is offered to tick: every pair the catalogue names, with the models each can run —
	// and nothing for an in-house model or for one nobody can be said to run
	got := am16Catalogue.RecipientOptions()
	said := []string{}
	for _, o := range got {
		said = append(said, o.Key+"="+strings.Join(o.Models, ","))
		if o.Route == "" || o.Company == "" {
			t.Errorf("a recipient with one name only: %+v", o)
		}
	}
	want := "deepseek=deepseek openrouter:amazon=anthropic/claude openrouter:anthropic=anthropic/claude openrouter:google=anthropic/claude,google/gemini"
	if strings.Join(said, " ") != want {
		t.Fatalf("offered:\n got %s\nwant %s", strings.Join(said, " "), want)
	}
}

func TestAM16MayLeaveWithNobodyNamedIsNobodyAndAnAgentsListOnlyNarrows(t *testing.T) {
	yes := true
	m := Manifest{Category: "finance"}
	m.Capabilities.Required = []string{"model.text"}
	open := EgressOf(true, m, &yes)
	for _, c := range []struct {
		what       string
		org, agent []string
		want       string
	}{
		{"nothing ticked anywhere", nil, nil, ""},
		{"the organisation named one, the agent none", []string{"deepseek"}, nil, ""},
		{"the agent ticked one the organisation never named", nil, []string{"deepseek"}, ""},
		{"both named it", []string{"deepseek", "openrouter:google"}, []string{"deepseek"}, "deepseek"},
		{"the agent's list is wider than the organisation's: only what both hold", []string{"deepseek"},
			[]string{"deepseek", "openrouter:google", "openrouter:amazon"}, "deepseek"},
		{"blanks and repeats are not recipients", []string{" deepseek ", "", "deepseek"}, []string{"deepseek", " "}, "deepseek"},
	} {
		e := open.WithRecipients(c.org, c.agent)
		if got := strings.Join(e.Recipients, " "); got != c.want || e.Recipients == nil {
			t.Errorf("%s: effective %q (nil %v), want %q", c.what, got, e.Recipients == nil, c.want)
		}
	}
	// named, but the agent's documents may not leave: the list opens nothing by itself
	no := false
	closed := EgressOf(true, m, &no).WithRecipients([]string{"deepseek"}, []string{"deepseek"})
	if len(closed.Recipients) != 0 || len(closed.AgentRecipients) != 1 {
		t.Fatalf("documents may not leave: effective %v, the agent's own ticks kept %v", closed.Recipients, closed.AgentRecipients)
	}
}

func TestAM16TheMenuOffersAnOutsideModelOnlyWhenACompanyThatRunsItIsNamed(t *testing.T) {
	yes := true
	reads := Manifest{Category: "finance"}
	reads.Capabilities.Optional = []string{"model.vision"}
	menu := func(named ...string) (string, ModelPicker) {
		rm := am16Catalogue
		p := MenuFor(Install{ModelChoice: "google/gemini"}, reads, EgressOf(true, reads, &yes).WithRecipients(named, named), rm)
		ids := []string{}
		for _, x := range p.Models {
			ids = append(ids, x.ID)
		}
		return strings.Join(ids, " "), p
	}
	// may leave, nobody named: no outside model, the pick from before is no pick, and the menu says why
	got, p := menu()
	if got != "qwen" || !p.OutsideAbsent || p.Choice != ModelAuto {
		t.Fatalf("nobody named: %q outside_absent=%v choice=%q", got, p.OutsideAbsent, p.Choice)
	}
	for _, l := range []Lang{Thai, English} {
		why := p.BlockedReason.In(l)
		if !(strings.Contains(why, "ยังไม่ได้ระบุผู้ให้บริการ") || strings.Contains(why, "has not yet named a provider")) || LooksLikeError(why) {
			t.Fatalf("the menu says why it is shorter (%s): %q", l, why)
		}
	}
	if ok, why := p.ChoiceAllowed("google/gemini"); ok || why != p.BlockedReason {
		t.Fatalf("picking it is refused with that reason: %v %q", ok, why.String())
	}
	// Google named: Gemini, and Claude too — Google can run it; Amazon named: Claude only
	if got, p := menu("openrouter:google"); got != "anthropic/claude google/gemini qwen" || p.Choice != "google/gemini" {
		t.Fatalf("Google named: %q choice %q", got, p.Choice)
	}
	if got, _ := menu("openrouter:amazon"); got != "anthropic/claude qwen" {
		t.Fatalf("Amazon named: %q", got)
	}
	// the route alone, a company alone, a recipient of another route: nobody
	for _, k := range []string{"openrouter", "google", "deepseek"} {
		if got, _ := menu(k); got != "qwen" {
			t.Fatalf("%q named: %q", k, got)
		}
	}
	// a model nobody can be said to run is never offered, whatever is named
	if got, _ := menu("openrouter:google", "openrouter:anthropic", "openrouter:amazon", "openrouter", "deepseek"); strings.Contains(got, "openrouter/auto") {
		t.Fatalf("a model with no recipient is in the menu: %q", got)
	}
}

func TestAM16TheTwoListsThroughTheAdminDoorEachChangeItsOwnAuditLine(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	e.seedDefinition()
	detail, _ := json.Marshal(am16Catalogue)
	caps := fullCaps()
	caps["platform.models"] = Binding{Capability: "platform.models", Impl: "gateway", Detail: detail}
	e.heartbeat(caps)
	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"documents_may_leave": true})
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"documents_may_leave": true})
	log.take()
	strs := func(v any) string {
		out := []string{}
		list, _ := v.([]any)
		for _, x := range list {
			out = append(out, x.(string))
		}
		return strings.Join(out, " ")
	}
	look := func() (map[string]any, map[string]any) {
		t.Helper()
		body := e.mustAdmin(uiAdmin, 200, "GET", "/egress", nil)
		return body, am16Row(t, body, am16Slug)
	}

	// what can be ticked is what the catalogue names — both names of each; nothing is ticked, and the
	// agent, whose documents may leave, reaches nobody
	body, row := look()
	offered, _ := body["available"].([]any)
	if len(offered) != 4 {
		t.Fatalf("offered: %v", body["available"])
	}
	first, _ := offered[0].(map[string]any)
	if first["key"] != "deepseek" || first["route"] != "deepseek" || first["company"] != "deepseek" {
		t.Fatalf("a recipient is offered with both its names: %v", first)
	}
	if strs(body["org_recipients"]) != "" || row["effective"] != true || strs(row["recipients"]) != "" {
		t.Fatalf("nothing ticked: org %v, the agent's effective list %v (may leave: %v)", body["org_recipients"], row["recipients"], row["effective"])
	}

	// an agent's list may not be wider than the organisation's: refused, nothing written, nothing logged
	e.mustAdmin(uiAdmin, http.StatusConflict, "PUT", "/egress/"+am16Slug, map[string]any{"recipients": []string{"openrouter:google"}})
	if _, row := look(); strs(row["agent_recipients"]) != "" {
		t.Fatalf("a refused tick was kept: %v", row["agent_recipients"])
	}
	// a recipient the catalogue does not name cannot be put on the organisation's list: nobody types a key
	e.mustAdmin(uiAdmin, http.StatusBadRequest, "PUT", "/egress/recipients", map[string]any{"recipients": []string{"openrouter:somebody"}})
	e.mustAdmin(uiAdmin, http.StatusBadRequest, "PUT", "/egress/recipients", map[string]any{"recipients": []string{"openrouter"}})
	e.mustAdmin(uiAdmin, http.StatusBadRequest, "PUT", "/egress/recipients", map[string]any{})
	if got := log.take(); len(got) != 0 {
		t.Fatalf("refused changes were logged: %+v", got)
	}

	// the organisation names two: two lines, each naming the route and the company
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/recipients", map[string]any{"recipients": []string{"openrouter:google", "deepseek"}})
	got := log.take()
	if len(got) != 2 {
		t.Fatalf("two recipients named for the organisation: %d lines %+v", len(got), got)
	}
	x, ok := find(got, "agent_market.org.recipient_allowed")
	if !ok || x.ResourceType != "agent_market_org" || x.Details["by"] != uiAdmin.Email || x.Details["route"] == "" || x.Details["company"] == "" {
		t.Fatalf("the organisation's line: %+v", x)
	}
	// … and that alone opens nothing for the agent
	if _, row := look(); strs(row["recipients"]) != "" || strs(row["org_recipients"]) != "deepseek openrouter:google" {
		t.Fatalf("named for the organisation only: the agent reaches %v (org %v)", row["recipients"], row["org_recipients"])
	}

	// the agent ticks one of them: one line, naming the agent and both names
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"recipients": []string{"openrouter:google"}})
	got = log.take()
	x, ok = find(got, "agent_market.agent.recipient_allowed")
	if !ok || len(got) != 1 || x.ResourceID != am16Slug || x.Details["recipient"] != "openrouter:google" || x.Details["route"] != "openrouter" ||
		x.Details["company"] != "google" || x.Details["agent"] == nil {
		t.Fatalf("the agent's line: %d %+v", len(got), got)
	}
	if _, row := look(); strs(row["recipients"]) != "openrouter:google" {
		t.Fatalf("ticked by both: %v", row["recipients"])
	}
	// the same list again moves nothing and logs nothing
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"recipients": []string{"openrouter:google"}})
	if got := log.take(); len(got) != 0 {
		t.Fatalf("an unchanged list was logged: %+v", got)
	}
	// a list on its own does not make a row of switches, and does not move them
	var rows int
	_ = e.store.Pool.QueryRow(context.Background(), `SELECT count(*) FROM agent_market.agent_egress WHERE slug = $1 AND NOT documents_may_leave`, am16Slug).Scan(&rows)
	if _, row := look(); rows != 0 || row["agent"] != true || row["masked"] != false {
		t.Fatalf("naming recipients moved the switches: %v", row)
	}

	// the organisation takes Google off its list: it is gone for the agent at once — the agent's own
	// tick stays where it is, and counts for nothing
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/recipients", map[string]any{"recipients": []string{"deepseek"}})
	got = log.take()
	if x, ok := find(got, "agent_market.org.recipient_removed"); !ok || len(got) != 1 || x.Details["recipient"] != "openrouter:google" {
		t.Fatalf("taking one off the organisation's list is one line: %+v", got)
	}
	if _, row := look(); strs(row["recipients"]) != "" || strs(row["agent_recipients"]) != "openrouter:google" {
		t.Fatalf("off the organisation's list: the agent reaches %v, its own ticks %v", row["recipients"], row["agent_recipients"])
	}
	// the tick that no longer counts may be left or removed, but nothing new can be added outside the list
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"recipients": []string{"openrouter:google", "deepseek"}})
	e.mustAdmin(uiAdmin, http.StatusConflict, "PUT", "/egress/"+am16Slug, map[string]any{"recipients": []string{"openrouter:amazon"}})
	if _, row := look(); strs(row["recipients"]) != "deepseek" {
		t.Fatalf("after the organisation narrowed its list: %v", row["recipients"])
	}
	// a viewer of the admin app cannot name anybody
	e.mustAdmin(uiViewer, http.StatusForbidden, "PUT", "/egress/recipients", map[string]any{"recipients": []string{"deepseek"}})
}

// Migration 011: two tables of its own, and down takes exactly them — after which the earlier state is back.
func TestAM16Migration011AddsTheTwoListsAndDownTakesThemAway(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	tables := func() int {
		return e.countRows(`SELECT count(*) FROM information_schema.tables WHERE table_schema = 'agent_market'
			AND table_name IN ('org_recipients', 'agent_recipients')`)
	}
	if tables() != 2 {
		t.Fatalf("the two lists: %d tables", tables())
	}
	if n := e.countRows(`SELECT count(*) FROM agent_market.org_recipients`) + e.countRows(`SELECT count(*) FROM agent_market.agent_recipients`); n != 0 {
		t.Fatalf("the migration named somebody: %d rows", n)
	}
	down, err := migrationFS.ReadFile("migrations/011_am16b.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatal(err)
	}
	if tables() != 0 || e.countRows(`SELECT count(*) FROM agent_market.schema_migrations WHERE version = '011_am16b'`) != 0 {
		t.Fatalf("down left something: %d tables", tables())
	}
	if n := e.countRows(`SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market' AND table_name = 'agent_egress'
		AND column_name IN ('masked', 'leave_set')`); n != 2 {
		t.Fatalf("down took more than its own: %d of the two columns of 010 left", n)
	}
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "011_am16b" || tables() != 2 {
		t.Fatalf("up again: %v %v, %d tables", did, err, tables())
	}
}
