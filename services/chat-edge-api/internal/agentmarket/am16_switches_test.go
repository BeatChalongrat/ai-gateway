package agentmarket

import (
	"context"
	"strings"
	"testing"
)

// AM16 — masking is the admin's own switch per agent, beside "documents may leave": two switches, both
// off by default, each change of either in the audit log.

const am16Slug = "sran.financial-report"

func am16Row(t *testing.T, body map[string]any, slug string) map[string]any {
	t.Helper()
	list, _ := body["agents"].([]any)
	for _, x := range list {
		if m, _ := x.(map[string]any); m != nil && m["slug"] == slug {
			return m
		}
	}
	t.Fatalf("no row for %s in %v", slug, body)
	return nil
}

func TestAM16TheTwoSwitchesAreSeparateBothOffByDefaultAndBothAudited(t *testing.T) {
	e, log := auditEnv(t)
	if e == nil {
		return
	}
	e.seedDefinition()
	e.mustAdmin(uiAdmin, 200, "PUT", "/org", map[string]any{"documents_may_leave": true})
	log.take()
	look := func() map[string]any {
		t.Helper()
		return am16Row(t, e.mustAdmin(uiAdmin, 200, "GET", "/egress", nil), am16Slug)
	}
	want := func(what string, row map[string]any, agent, agentDefault, masked, effective, pages bool) {
		t.Helper()
		if row["agent"] != agent || row["agent_default"] != agentDefault || row["masked"] != masked || row["effective"] != effective || row["pages_may_leave"] != pages {
			t.Fatalf("%s: agent=%v default=%v masked=%v effective=%v pages_may_leave=%v — want %v %v %v %v %v", what,
				row["agent"], row["agent_default"], row["masked"], row["effective"], row["pages_may_leave"], agent, agentDefault, masked, effective, pages)
		}
	}
	// nobody has set anything: nothing leaves, nothing is masked — though the organisation allows leaving
	want("never set", look(), false, true, false, false, false)

	// masking alone is turned on: the leave switch is still nobody's (its default), and that is one entry
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"masked": true})
	want("masking on, leaving never set", look(), false, true, true, false, false)
	got := log.take()
	x, ok := find(got, "agent_market.agent.masked")
	if !ok || len(got) != 1 {
		t.Fatalf("turning masking on is one audit entry of its own: %+v", got)
	}
	if x.ResourceType != "agent_market_agent" || x.ResourceID != am16Slug || x.Details["setting"] != "masked" ||
		x.Details["from"] != false || x.Details["to"] != true || x.Details["by"] != uiAdmin.Email || x.Details["documents_may_leave"] != false {
		t.Fatalf("the masking entry: %+v", x)
	}
	if lbl, _ := x.Details["label"].(string); !strings.Contains(lbl, "ปิดบัง") {
		t.Fatalf("the masking entry's label: %q", lbl)
	}

	// the same value again moves nothing and logs nothing
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"masked": true})
	if got := log.take(); len(got) != 0 {
		t.Fatalf("an unchanged masking switch was logged: %+v", got)
	}

	// documents may leave: masking stays as the admin left it — the two do not move each other. Text may
	// go out (masked); a page may not, because an image cannot be masked.
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"documents_may_leave": true})
	want("may leave, masked", look(), true, false, true, true, false)
	got = log.take()
	if x, ok := find(got, "agent_market.agent.documents_may_leave"); !ok || len(got) != 1 || x.Details["to"] != true || x.Details["masked"] != true {
		t.Fatalf("letting documents leave is one entry, and it says how they leave (masked): %+v", got)
	}

	// masking off: now what leaves is sent as typed, pages too — and that is the entry an auditor looks for
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"masked": false})
	want("may leave, not masked", look(), true, false, false, true, true)
	x, ok = find(log.take(), "agent_market.agent.masked")
	if !ok || x.Details["from"] != true || x.Details["to"] != false || x.Details["documents_may_leave"] != true {
		t.Fatalf("turning masking off for an agent whose documents leave: %+v", x)
	}

	// both in one request: two entries
	e.mustAdmin(uiAdmin, 200, "PUT", "/egress/"+am16Slug, map[string]any{"documents_may_leave": false, "masked": true})
	want("back inside, masked", look(), false, false, true, false, false)
	if got := log.take(); len(got) != 2 {
		t.Fatalf("two switches moved in one request: %d entries %+v", len(got), got)
	}

	// a request that names neither is not a change
	e.mustAdmin(uiAdmin, 400, "PUT", "/egress/"+am16Slug, map[string]any{})
}

// The model menu: an agent that reads pages is given their images, and an image cannot be masked — so
// with masking on an outside model is not in its menu, and the menu says why. An agent whose model
// writes from figures (text) keeps its outside models: its text goes out masked.
func TestAM16AnAgentThatReadsPagesHasNoOutsideModelWhileItIsMasked(t *testing.T) {
	rm := RunnerModels{Primary: "qwen3.8-27b-fp8", Fallback: "deepseek-v4-flash", Vision: "qwen3.8-27b-fp8",
		SelfHosted: map[string]bool{"qwen3.8-27b-fp8": true},
		Catalog:    []CatalogModel{{ID: "qwen3.8-27b-fp8", Vision: true, SelfHosted: true}, {ID: "google/gemini-2.5-flash", Vision: true}, {ID: "deepseek-v4-flash"}}}
	yes := true
	reads := Manifest{Category: "finance"}
	reads.Capabilities.Optional = []string{"model.vision"}
	writes := Manifest{Category: "finance"}
	writes.Capabilities.Required = []string{"model.text"}
	has := func(p ModelPicker, id string) bool {
		for _, x := range p.Models {
			if x.ID == id {
				return x.Allowed
			}
		}
		return false
	}

	open := EgressOf(true, reads, &yes).WithMasked(false)
	p := MenuFor(Install{ModelChoice: ModelAuto}, reads, open, rm)
	if !has(p, "google/gemini-2.5-flash") || !open.PagesMayLeave {
		t.Fatalf("may leave, not masked: an outside model that reads images is in the menu: %+v", p.Models)
	}

	masked := EgressOf(true, reads, &yes).WithMasked(true)
	p = MenuFor(Install{ModelChoice: "google/gemini-2.5-flash"}, reads, masked, rm)
	if has(p, "google/gemini-2.5-flash") || !has(p, "qwen3.8-27b-fp8") || masked.PagesMayLeave {
		t.Fatalf("may leave, masked: a page stays inside — the menu is %+v", p.Models)
	}
	if p.Choice != ModelAuto {
		t.Fatalf("a choice made before masking was turned on is no choice now: %q", p.Choice)
	}
	for _, l := range []Lang{Thai, English} {
		why := p.BlockedReason.In(l)
		if !(strings.Contains(why, "ปิดบัง") || strings.Contains(why, "masked")) || LooksLikeError(why) {
			t.Fatalf("the menu says why it is shorter (%s): %q", l, why)
		}
	}
	if ok, why := p.ChoiceAllowed("google/gemini-2.5-flash"); ok || why != p.BlockedReason {
		t.Fatalf("picking the outside model is refused with that reason: %v %q", ok, why.String())
	}

	// text: masked or not, the outside model stays — what it is sent is masked text
	p = MenuFor(Install{ModelChoice: ModelAuto}, writes, EgressOf(true, writes, &yes).WithMasked(true), rm)
	if !has(p, "deepseek-v4-flash") {
		t.Fatalf("an agent whose model writes from figures keeps its outside model while masked: %+v", p.Models)
	}
}

// Migration 010: nothing changes for an agent. A row an admin had made with the leave switch on was
// masked until now and stays masked; one with it off is not masked. Down takes both columns away, and
// with them a row that was made for masking alone.
func TestAM16Migration010KeepsWhatWasAndDownUndoesIt(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	down, err := migrationFS.ReadFile("migrations/010_am16.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_egress (org_id, slug, documents_may_leave, leave_set, masked)
		VALUES ('default', 'masking.only', false, false, true)`); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, string(down)); err != nil {
		t.Fatalf("down: %v", err)
	}
	cols := e.countRows(`SELECT count(*) FROM information_schema.columns WHERE table_schema = 'agent_market'
		AND table_name = 'agent_egress' AND column_name IN ('masked', 'leave_set')`)
	rec := e.countRows(`SELECT count(*) FROM agent_market.schema_migrations WHERE version = '010_am16'`)
	left := e.countRows(`SELECT count(*) FROM agent_market.agent_egress WHERE slug = 'masking.only'`)
	if cols != 0 || rec != 0 || left != 0 {
		t.Fatalf("down leaves columns %d, record %d, the masking-only row %d", cols, rec, left)
	}
	// the table as it was before AM16, with what an admin had set
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_egress (org_id, slug, documents_may_leave)
		VALUES ('default', 'was.open', true), ('default', 'was.closed', false)`); err != nil {
		t.Fatal(err)
	}
	did, err := Migrate(ctx, e.store.Pool)
	if err != nil || len(did) != 1 || did[0] != "010_am16" {
		t.Fatalf("up again: %v %v", did, err)
	}
	for slug, wantMasked := range map[string]bool{"was.open": true, "was.closed": false} {
		set, masked, err := e.store.AgentSwitches(ctx, e.store.Pool, DefaultOrg, slug)
		if err != nil || set == nil || *set != (slug == "was.open") || masked != wantMasked {
			t.Fatalf("%s after the migration: leave=%v masked=%v err=%v — want masked=%v, the leave switch as it was", slug, set, masked, err, wantMasked)
		}
	}
	// an agent nobody set: no row, both off
	if set, masked, err := e.store.AgentSwitches(ctx, e.store.Pool, DefaultOrg, "never.set"); err != nil || set != nil || masked {
		t.Fatalf("an agent nobody set: leave=%v masked=%v err=%v", set, masked, err)
	}
}
