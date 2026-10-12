package runner

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM16 — a run says on the face of its own card when something of it was sent to a model outside the
// organisation as it was typed. The real engine in the real sandbox; the stand-in gateway answers for
// every model name, so a model the runner does not hold as in-house is a model outside.
//
// The scan is the case that was wrong before this card: a page read by an outside model left as an
// image — which nothing masks — while the run's card said "masked".

const (
	am16Scan = "s01_scan300_full_tax_invoice.pdf"
	// the name the stand-in gateway answers a page-reading call under; these tests do NOT hold it as
	// in-house (am15's do), so here it is a model outside the organisation
	am16Outside = "fake-vision"
)

// progressOf is the run's own progress card as the thread holds it.
func (e *itEnv) progressOf(run uuid.UUID) map[string]any {
	e.t.Helper()
	items, err := e.store.ThreadItemsAfter(context.Background(), e.install.ID, nil, 0, 500)
	if err != nil {
		e.t.Fatal(err)
	}
	for _, it := range items {
		if it.Kind == "progress" && it.RunID != nil && *it.RunID == run {
			var p map[string]any
			_ = json.Unmarshal(it.Payload, &p)
			return p
		}
	}
	e.t.Fatalf("run %s has no progress card", run)
	return nil
}

func (e *itEnv) am16Switches(slug string, leave, masked bool) {
	e.t.Helper()
	ctx := context.Background()
	if _, err := e.store.EnsureOrg(ctx, am.Config{}); err != nil {
		e.t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.org_settings SET documents_may_leave = true WHERE org_id = $1`, am.DefaultOrg); err != nil {
		e.t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `INSERT INTO agent_market.agent_egress (org_id, slug, documents_may_leave, masked) VALUES ($1, $2, $3, $4)
		ON CONFLICT (org_id, slug) DO UPDATE SET documents_may_leave = EXCLUDED.documents_may_leave, masked = EXCLUDED.masked`,
		am.DefaultOrg, slug, leave, masked); err != nil {
		e.t.Fatal(err)
	}
}

// visionStep is the step that read the scan by eye, or nil.
func (e *itEnv) visionStep(run uuid.UUID) *am.Step {
	for _, s := range e.steps(run) {
		if s.Name == "vision:"+am16Scan {
			x := s
			return &x
		}
	}
	return nil
}

// A scan, an agent whose documents may leave and are not masked, a model outside that reads images:
// the page goes to that model, and the run says so — beside the step, on its progress card while it is
// still waiting for the person's answers, and (the IT lane's other tests hold the files card's fields)
// nowhere does it say the page was masked away. Then the refusal: the admin turns masking on, and the
// same scan is not read by the outside model at all.
func TestAScanReadByAModelOutsideIsSaidOnTheFaceOfTheRunsCard(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	in := e.setupInvoiceInstall()
	r := e.runner("r1")
	// the only model that reads images is one the runner does not hold as in-house
	r.Model.VisionModel = am16Outside
	e.gatewayHonours.Store(true)

	// 1. the default: nothing leaves. No model may read the page (the only one is outside), so it is
	//    read by OCR on the organisation's own machine, and no card says anything left.
	run := e.invoiceRun(am16Scan)
	e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
	if s := e.visionStep(run.ID); s != nil {
		t.Fatalf("documents stay inside and the only model that reads images is outside: a %q step ran (%s)", s.Name, s.Status)
	}
	if p := e.progressOf(run.ID); p["sent_out_unmasked"] == true {
		t.Fatalf("nothing left, and the card says it did: %v", p)
	}

	// 2. may leave, not masked: the page is read by the outside model — and the run says so
	e.am16Switches(in.Slug, true, false)
	run = e.invoiceRun(am16Scan)
	got := e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
	s := e.visionStep(run.ID)
	if s == nil || s.Status != "succeeded" {
		t.Fatalf("the scan was not read by eye: %+v (run %s %s)", s, got.Status, got.StatusReason)
	}
	var v visionReading
	_ = json.Unmarshal(s.Output, &v)
	if v.Model != am16Outside {
		t.Fatalf("read by %q, want the outside model", v.Model)
	}
	if !sentOutStamped(s.Output) {
		t.Fatalf("the page went to a model outside and nothing beside the step says so: %s", s.Output)
	}
	if p := e.progressOf(run.ID); p["sent_out_unmasked"] != true {
		t.Fatalf("the run's own card does not say its documents were sent outside unmasked (run %s %s): %v", got.Status, got.StatusReason, p)
	}

	// 3. the refusal: masking on. An image cannot be masked, so the page is not given to the outside
	//    model — whatever the leave switch says — and nothing is said to have left.
	e.am16Switches(in.Slug, true, true)
	run = e.invoiceRun(am16Scan)
	e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
	if s := e.visionStep(run.ID); s != nil {
		t.Fatalf("masking is on and a page went to a model anyway: step %q %s %s", s.Name, s.Status, s.Output)
	}
	if p := e.progressOf(run.ID); p["sent_out_unmasked"] == true {
		t.Fatalf("masking is on, the page stayed, and the card says it left: %v", p)
	}
}

// A page that was SENT and never answered still left. The outside model gives no answer in time: the
// run waits for its model (AM15) — and its card already says the documents were sent outside unmasked.
// When the model is back and the step is run again, what the first attempt sent is not forgotten.
func TestAPageSentToAModelThatDidNotAnswerStillLeft(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	in := e.setupInvoiceInstall()
	ctx := context.Background()
	r := e.runner("r1")
	r.Model.VisionModel = am16Outside
	e.gatewayHonours.Store(true)
	e.am16Switches(in.Slug, true, false)

	run := e.invoiceRun(am16Scan)
	e.away.Store(true)
	got := e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
	if got.StatusReason != am.ReasonModelUnreachable {
		t.Fatalf("the model gave no answer: the run should wait for it — %s (%s)", got.Status, got.StatusReason)
	}
	s := e.visionStep(run.ID)
	if s == nil || s.Status != "failed" || !sentOutStamped(s.Output) {
		t.Fatalf("the failed step does not say the page was sent out: %+v", s)
	}
	if p := e.progressOf(run.ID); p["sent_out_unmasked"] != true {
		t.Fatalf("a run that waits for the model it sent a page to does not say the page left: %v", p)
	}
	if !strings.Contains(string(s.Output), sentOutKey) {
		t.Fatalf("the stamp: %s", s.Output)
	}

	// the model is back, and this time the page is read
	e.away.Store(false)
	if n := r.resumeModelParked(ctx); n < 1 {
		t.Fatalf("the model is back: %d resumed", n)
	}
	e.drive(r, run.ID, 6*time.Minute, parkedOrTerminal)
	s = e.visionStep(run.ID)
	if s == nil || s.Status != "succeeded" || !sentOutStamped(s.Output) {
		t.Fatalf("the step run again: %+v", s)
	}
	if p := e.progressOf(run.ID); p["sent_out_unmasked"] != true {
		t.Fatalf("the card after the step was run again: %v", p)
	}
}
