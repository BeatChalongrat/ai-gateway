package runner

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM5 phase E — a package's completion gate. The bank reconciliation's
// computed.json says whether its two adjusted balances are equal. When they
// are not, the working paper is still delivered (as a draft) and the round is
// not a success: it ends failed with the gate's code, and what the person
// reads is the package's sentence, "ยังไม่ลงตัว ต่างกัน X บาท".

const notTied = `{"schema":"bank.reconciliation.v1","gate":{"passed":false,"code":"not_tied","difference":"-21435.00",
"what":"ยังไม่ลงตัว ต่างกัน 21,435.00 บาท","next":"ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร",
"reasons":["ยอดคงเหลือในรายการเดินบัญชีไม่ต่อเนื่อง 1 จุด อาจมีรายการขาดหาย"]}}`

func TestTheGateOfAComputedTable(t *testing.T) {
	g, err := gateOf([]byte(notTied))
	if err != nil || g == nil {
		t.Fatalf("%v %v", g, err)
	}
	if g.Passed || g.Code != "not_tied" || g.What != "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท" || !strings.HasPrefix(g.Next, "ขอรายการเดินบัญชี") {
		t.Fatalf("%+v", g)
	}
	tied, _ := gateOf([]byte(`{"gate":{"passed":true,"code":"tied","what":"ลงตัว"}}`))
	if tied == nil || !tied.Passed {
		t.Fatalf("%+v", tied)
	}
	// a package that sets no gate has none; one that says it has and gives none is an error, not a pass
	if none, err := gateOf([]byte(`{"totals":{}}`)); none != nil || err != nil {
		t.Fatalf("no gate: %v %v", none, err)
	}
	for _, bad := range []string{`{"gate":{"code":"tied"}}`, `{"gate":"yes"}`, `{"gate":{"passed":"true"}}`} {
		if g, err := gateOf([]byte(bad)); err == nil {
			t.Fatalf("%s read as %+v", bad, g)
		}
	}
	// the code becomes the run's reason: only a code, never a sentence or a path
	for in, want := range map[string]string{"not_tied": "not_tied", "": "gate_not_passed", "Not Tied!": "gate_not_passed",
		"../x": "gate_not_passed", strings.Repeat("a", 60): "gate_not_passed"} {
		g, _ := gateOf([]byte(`{"gate":{"passed":false,"code":` + string(mustJSON(in)) + `,"what":"x","next":"y"}}`))
		if g.Reason() != want {
			t.Fatalf("%q → %q, want %q", in, g.Reason(), want)
		}
	}
}

func TestARoundWhoseGateDoesNotPassIsNotASuccess(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	run := e.round(am.KindRun, true)
	g, _ := gateOf([]byte(notTied))
	_ = e.r.fail(ctx, run, g.Reason(), &gateErr{gate: *g, draft: true})
	got, _ := e.store.RunByID(ctx, run.ID)
	if got.Status != am.RunFailed || got.StatusReason != "not_tied" {
		t.Fatalf("not a success: %s %s", got.Status, got.StatusReason)
	}
	text, f := lastFailure(t, e, run)
	if f["what"] != "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท" || !strings.HasPrefix(text, "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท") {
		t.Fatalf("it says so: %q %v", text, f)
	}
	if f["next"] != "ขอรายการเดินบัญชีฉบับเต็มของงวดนี้จากธนาคาร" || f["can_retry"] != false {
		t.Fatalf("and what to do next — not to try the same files again: %v", f)
	}
	if s, _ := f["files"].(string); !strings.Contains(s, "ร่าง") {
		t.Fatalf("the draft is there: %v", f["files"])
	}
	readable(t, "a round that does not tie", e.seen())
	// the step that says so is on the run, for the run page
	var gate *am.Step
	for _, s := range e.steps(run) {
		if s.Name == "stopped" || s.Name == "gate" {
			s := s
			gate = &s
		}
	}
	if gate == nil || gate.Status != "failed" || !strings.Contains(gate.Error, "not_tied") {
		t.Fatalf("the run keeps why: %+v", gate)
	}
}

func (e *failEnv) steps(run am.Run) []am.Step {
	e.t.Helper()
	steps, err := e.store.ListSteps(context.Background(), run.ID)
	if err != nil {
		e.t.Fatal(err)
	}
	return steps
}

func TestTheProgressCardShowsTheGate(t *testing.T) {
	now := time.Now()
	start := now.Add(-time.Minute)
	run := am.Run{Kind: am.KindRun, Status: am.RunFailed, StatusReason: "not_tied", StartedAt: &start, FinishedAt: &now}
	out := json.RawMessage(`{"passed":false,"code":"not_tied","what":"ยังไม่ลงตัว ต่างกัน 21,435.00 บาท"}`)
	steps := []am.Step{{Name: "compute", Status: "succeeded"}, {Name: "render", Status: "succeeded"},
		{Name: "gate", Status: "failed", Output: out, Error: "gate: not_tied"}}
	p := BuildProgress(run, steps, now)
	var line *am.ProgressStep
	for i := range p.Steps {
		if p.Steps[i].Key == "gate" {
			line = &p.Steps[i]
		}
	}
	if line == nil || line.Status != "failed" || line.Detail != "ยังไม่ลงตัว ต่างกัน 21,435.00 บาท" || line.Label == "" {
		t.Fatalf("%+v", p.Steps)
	}
	if strings.Contains(line.Label+line.Detail, "not_tied") || strings.Contains(line.Label+line.Detail, "gate") {
		t.Fatalf("in words: %+v", line)
	}
	// a run without a gate has no such line
	for _, s := range BuildProgress(run, steps[:2], now).Steps {
		if s.Key == "gate" {
			t.Fatal("a line for a gate the package does not have")
		}
	}
	ok := BuildProgress(am.Run{Kind: am.KindRun, Status: am.RunSucceeded}, []am.Step{{Name: "gate", Status: "succeeded",
		Output: json.RawMessage(`{"passed":true,"code":"tied","what":"ลงตัว ยอดหลังปรับปรุงของธนาคารและของบัญชีเท่ากัน"}`)}}, now)
	for _, s := range ok.Steps {
		if s.Key == "gate" && (s.Status != "done" || !strings.HasPrefix(s.Detail, "ลงตัว")) {
			t.Fatalf("%+v", s)
		}
	}
}

func TestTheRunnerBindsTheMatchingEngine(t *testing.T) {
	r := &Runner{Engine: &Engine{Checksum: strings.Repeat("a", 64)}, Model: &ModelClient{Primary: "p", Fallback: "f"},
		SandboxImage: "img"}
	if _, ok := r.Capabilities()["document.match.e3"]; ok {
		t.Fatal("offered while the sandbox it runs in is not there")
	}
	r.SandboxReady = true
	b, ok := r.Capabilities()["document.match.e3"]
	if !ok || !strings.HasPrefix(b.Impl, "engine:e3") || b.Version == "" {
		t.Fatalf("document.match.e3: %+v", b)
	}
}
