package runner

// AM9 (A) end to end: each working paper is printed through the doc-converter
// image and every row with an amount is found beside its description on one
// rendered page (AGENT_MARKET_IT=1; the image is AM_CONVERTER_IMAGE).

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

func (e *itEnv) printedSelfTest(in am.Install) gateCheck {
	e.t.Helper()
	ctx := context.Background()
	def, _ := e.store.DefinitionByID(ctx, in.DefinitionID)
	run, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID,
		Kind: am.KindSelfTest, Message: "ทดสอบกับข้อมูลตัวอย่าง", DefinitionVersion: def.Version, Checksum: def.Checksum,
		EngineChecksum: def.EngineChecksum}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	r := e.runner("r1")
	r.Converter = startConverter(e.t)
	got := e.drive(r, run.ID, 8*time.Minute, func(x am.Run) bool { return terminal(x) || parked(x) })
	if got.Status != am.RunSucceeded {
		e.t.Fatalf("%s self-test: %s %s", in.Slug, got.Status, got.StatusReason)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	for _, a := range arts {
		if a.Format != "xlsx" {
			continue
		}
		var q struct {
			Passed bool        `json:"passed"`
			Checks []gateCheck `json:"checks"`
		}
		_ = json.Unmarshal(a.Validation, &q)
		for _, c := range q.Checks {
			if c.Name == "xlsx.printed" {
				e.t.Logf("%s %s: ok=%v %s", in.Slug, a.Name, c.OK, c.Detail)
				if !q.Passed {
					e.t.Fatalf("the file passed its gate: %+v", q)
				}
				return c
			}
		}
		e.t.Fatalf("%s: no print proof on %s: %s", in.Slug, a.Name, a.Validation)
	}
	e.t.Fatalf("%s: no working paper delivered", in.Slug)
	return gateCheck{}
}

func TestTheBankPaperPrintsEachRowsDescriptionBesideItsAmounts(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	if c := e.printedSelfTest(e.setupBankInstall()); !c.OK {
		t.Fatalf("printed: %+v", c)
	}
}

func TestThePOPaperPrintsEachRowsDescriptionBesideItsAmounts(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	if c := e.printedSelfTest(e.setupPOInstall()); !c.OK {
		t.Fatalf("printed: %+v", c)
	}
}

func TestTheExpensePaperPrintsEachRowsDescriptionBesideItsAmounts(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	if c := e.printedSelfTest(e.setupExpenseInstall()); !c.OK {
		t.Fatalf("printed: %+v", c)
	}
}
