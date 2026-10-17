package runner

import (
	"context"
	"fmt"
	"path/filepath"

	"github.com/google/uuid"
)

// priorFiles (AM23 Part 2) are the round's own workbook, report and deck made before this render — the latest of
// each kind this render does not make again — handed to the engine to be read back beside what it makes: every file
// of a round says the same number, a deck asked of a finished round included. The customer's form and a PDF are not
// among them (the form's figures are known when it is filled; a PDF is compared with the document it prints).
func (rc *runCtx) priorFiles(ctx context.Context, round uuid.UUID, making []string) ([]map[string]any, []Input, func()) {
	var cleanups []func()
	done := func() {
		for _, c := range cleanups {
			c()
		}
	}
	arts, err := rc.r.Store.RunArtifacts(ctx, round)
	if err != nil {
		return nil, nil, done
	}
	var prior []map[string]any
	var inputs []Input
	seen := map[string]bool{}
	for _, a := range arts { // (newest version of each format first)
		if seen[a.Format] || contains(making, a.Format) || (a.Format != "xlsx" && a.Format != "docx" && a.Format != "pptx") || a.Kind == "template" {
			seen[a.Format] = true
			continue
		}
		seen[a.Format] = true
		path, clean, err := rc.r.Files.OpenLocalCopy(ctx, a.StorageKey)
		if err != nil {
			continue // (a file that cannot be opened again is not compared; the render goes on)
		}
		cleanups = append(cleanups, clean)
		input := fmt.Sprintf("prior_%d%s", len(prior), filepath.Ext(a.Name))
		inputs = append(inputs, Input{Name: input, LocalPath: path})
		prior = append(prior, map[string]any{"name": a.Name, "format": a.Format, "kind": a.Kind, "input": input})
	}
	return prior, inputs, done
}
