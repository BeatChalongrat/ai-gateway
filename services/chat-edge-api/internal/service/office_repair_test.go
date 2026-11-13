package service

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	amrunner "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket/runner"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

type repairConverter struct {
	initial, candidate amrunner.CheckResult
	calls              int
}

func (f *repairConverter) Available() bool { return true }
func (f *repairConverter) Convert(_ context.Context, _ string, raw []byte, _ string) ([]byte, error) {
	return raw, nil
}
func (f *repairConverter) CheckWithFirstPNG(_ context.Context, _ []byte) (amrunner.CheckResult, error) {
	f.calls++
	if f.calls == 1 {
		return f.initial, nil
	}
	return f.candidate, nil
}

type repairRunner struct {
	path  string
	t     *testing.T
	calls int
}

func (r *repairRunner) Execute(_ context.Context, spec sandbox.Spec) (*sandbox.Result, error) {
	r.calls++
	if spec.Args[2] != "out/repaired.pptx" {
		r.t.Fatal("output must use sandbox writable argument syntax")
	}
	return &sandbox.Result{Artifacts: []sandbox.Artifact{{Name: "repaired.pptx", Path: r.path}}}, nil
}

func TestOfficeRepairAcceptsOnlyMeasuredImprovement(t *testing.T) {
	base := amrunner.CheckResult{Pages: 1, RenderedPages: 1, RawText: "all original words", PageInk: []float64{.1}, MarginInk: []float64{.1}}
	cases := []struct {
		name     string
		edge     float64
		pages    int
		text     string
		ink      float64
		accepted bool
	}{
		{"improves", 0, 1, "all original words", .1, true},
		{"worse", .2, 1, "all original words", .1, false},
		{"lost rendered words", 0, 1, "words", .1, false},
		{"changed pages", 0, 2, "all original words", .1, false},
		{"became blank", 0, 1, "all original words", 0, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "candidate.pptx")
			if err := os.WriteFile(path, []byte("candidate"), 0600); err != nil {
				t.Fatal(err)
			}
			converter := &repairConverter{initial: base, candidate: amrunner.CheckResult{Pages: tc.pages, RenderedPages: tc.pages, RawText: tc.text, PageInk: []float64{tc.ink}, MarginInk: []float64{tc.edge}}}
			runner := &repairRunner{path: path, t: t}
			q := &OfficeQuality{converter: converter, repairRunner: runner}
			result, err := q.review(context.Background(), "deck.pptx", []byte("original"))
			if err != nil {
				t.Fatal(err)
			}
			if result.Report.Repaired != tc.accepted || result.Report.RepairAttempts > 2 {
				t.Fatalf("unexpected report %+v", result.Report)
			}
			if !tc.accepted && string(result.Data) != "original" {
				t.Fatal("unproven correction replaced original")
			}
		})
	}
}
