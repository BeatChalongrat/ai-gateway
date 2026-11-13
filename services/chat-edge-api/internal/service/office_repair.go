package service

import (
	"context"
	"encoding/json"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
	"path/filepath"
	"strings"
	"time"
)

func (q *OfficeQuality) SetRepairRunner(r sandboxRunner) { q.repairRunner = r }

// Pixel feedback is deliberately narrow: near-edge PPTX text. A candidate
// must preserve text (script invariant), page count, and improve measured edge
// ink. Unproven candidates are discarded; this is not semantic vision review.
func (q *OfficeQuality) review(ctx context.Context, name string, data []byte) (officeReviewed, error) {
	ctx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	best, err := q.reviewOnce(ctx, name, data)
	if err != nil || q == nil || q.repairRunner == nil || strings.ToLower(filepath.Ext(name)) != ".pptx" || best.Report.EdgeInk <= 0 {
		return best, err
	}
	attempts := 0
	accepted := false
	for i := 1; i <= 2 && best.Report.EdgeInk > 0; i++ {
		if ctx.Err() != nil {
			break
		}
		attempts++
		params, _ := json.Marshal(map[string]any{"mode": "repair", "attempt": i})
		res, e := q.repairRunner.Execute(ctx, sandbox.Spec{Mode: sandbox.ModeScript, Script: "file_editor.py", Args: []string{"source.pptx", "request.json", "out/repaired.pptx"}, Inputs: []sandbox.InputFile{{Name: "source.pptx", Bytes: best.Data}, {Name: "request.json", Bytes: params}}, Limits: sandbox.Limits{Wall: 45 * time.Second, MaxOutputB: 1 << 20}})
		if e != nil || res == nil {
			if res != nil {
				res.Cleanup()
			}
			break
		}
		art := res.Artifact("repaired.pptx")
		if res.Exit != 0 || res.TimedOut || art == nil {
			res.Cleanup()
			break
		}
		candidate, e := ReadArtifact(art.Path)
		res.Cleanup()
		if e != nil {
			break
		}
		checked, e := q.reviewOnce(ctx, name, candidate)
		if e == nil && checked.Report.Pages == best.Report.Pages && checked.Report.EdgeInk < best.Report.EdgeInk && checked.BlankPages <= best.BlankPages && len(strings.Fields(checked.RenderText)) >= len(strings.Fields(best.RenderText)) {
			best = checked
			accepted = true
		}
	}
	best.Report.RepairAttempts = attempts
	best.Report.Repaired = accepted
	if !accepted {
		best.Report.Warnings = append(best.Report.Warnings, "Automatic PPTX repair did not prove an improvement; original layout retained")
	}
	return best, nil
}
