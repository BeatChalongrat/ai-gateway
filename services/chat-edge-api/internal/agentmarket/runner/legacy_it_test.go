package runner

// AM2 deploy: installs released before AM2 are pinned to an engine recorded by
// its AM1 checksum and never stored. The deploy stores that engine (taken from
// the running AM1c runner image) under its checksum; this proves the AM2
// runner, with the AM2 sandbox image, still runs the AM1c engine for them —
// the engine built from git at AM_LEGACY_ENGINE_REF (default af24b360, the
// AM1c tip) — and that without it the run fails honestly (AGENT_MARKET_IT=1).

import (
	"archive/zip"
	"context"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// legacyEngine builds the engine of a past commit from git.
func (e *itEnv) legacyEngine(ref string) *Engine {
	e.t.Helper()
	out := filepath.Join(e.t.TempDir(), "legacy.zip")
	cmd := exec.Command("git", "archive", "--format=zip", "-o", out, ref, "backend/agent_market")
	cmd.Dir = e.root
	if b, err := cmd.CombinedOutput(); err != nil {
		e.t.Skipf("git archive %s: %v %s", ref, err, b)
	}
	zr, err := zip.OpenReader(out)
	if err != nil {
		e.t.Fatal(err)
	}
	defer zr.Close()
	dst := e.t.TempDir()
	for _, f := range zr.File {
		if f.FileInfo().IsDir() {
			continue
		}
		p := filepath.Join(dst, filepath.FromSlash(f.Name))
		_ = os.MkdirAll(filepath.Dir(p), 0o755)
		rc, _ := f.Open()
		b, _ := io.ReadAll(rc)
		rc.Close()
		if err := os.WriteFile(p, b, 0o644); err != nil {
			e.t.Fatal(err)
		}
	}
	eng, err := LoadEngine("", filepath.Join(dst, "backend", "agent_market"))
	if err != nil {
		e.t.Fatal(err)
	}
	return eng
}

func TestAnInstallFromBeforeAM2RunsItsOwnAM1Engine(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	ref := envOr("AM_LEGACY_ENGINE_REF", "af24b360")
	old := e.legacyEngine(ref)
	p := old.Packages["sran.financial-report"]
	if p == nil || p.Version != "1.0.0" {
		t.Fatalf("the AM1c engine holds the financial report 1.0.0: %+v", p)
	}
	cur := e.runner("current")
	if err := cur.Register(ctx); err != nil {
		t.Fatal(err)
	}
	// what AM1c registered: its version, pinned to its engine by the AM1 checksum
	def, err := e.store.InsertDefinition(ctx, am.Definition{Slug: p.Slug, Version: p.Version, Name: p.Manifest.Name,
		Manifest: p.RawManifest, Instructions: p.Instructions, Checksum: p.Checksum, EngineChecksum: old.Checksum})
	if err != nil {
		t.Fatal(err)
	}
	e.setupInstall(3_000_000)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET definition_id = $2, definition_version = $3,
		checksum = $4 WHERE id = $1`, e.install.ID, def.ID, def.Version, def.Checksum); err != nil {
		t.Fatal(err)
	}
	e.install, _ = e.store.InstallByID(ctx, e.install.ID)
	pinned := func() am.Run {
		run := e.newRun("[AM2] ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx",
			"sample-data/งบประมาณ_2569.xlsx")
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET engine_checksum = $2 WHERE id = $1`,
			run.ID, old.Checksum); err != nil {
			t.Fatal(err)
		}
		return run
	}

	// 1 · not stored: the run fails and says why, nothing else runs it
	missing := e.drive(cur, pinned().ID, 3*time.Minute, terminal)
	if missing.Status != am.RunFailed || !strings.Contains(missing.StatusReason, "engine") {
		t.Fatalf("an engine nobody stored: %s %s", missing.Status, missing.StatusReason)
	}

	// 2 · the deploy step (agent-runner store-engine <the AM1c zip>): kept under its AM1
	// checksum for the released version only; the pinned run executes with it
	lines, err := StoreLegacyEngine(ctx, e.store, e.files, old)
	if err != nil {
		t.Fatal(err)
	}
	said := strings.Join(lines, "\n")
	if !strings.Contains(said, "sran.financial-report 1.0.0: its engine is kept at "+am.EngineKey(old.Checksum)) {
		t.Fatalf("store-engine: %s", said)
	}
	if again, _ := StoreLegacyEngine(ctx, e.store, e.files, old); strings.Join(again, "\n") != said {
		t.Fatalf("a second store changes nothing: %v", again)
	}
	// the current engine is not a legacy engine of anything released with another
	curLines, _ := StoreLegacyEngine(ctx, e.store, e.files, e.engine)
	if strings.Contains(strings.Join(curLines, "\n"), "sran.financial-report 1.0.0: its engine") {
		t.Fatalf("another engine is never kept for 1.0.0: %v", curLines)
	}
	run := pinned()
	got := e.drive(cur, run.ID, 5*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the AM1c engine under the AM2 runner: %s %s", got.Status, got.StatusReason)
	}
	if cur.engines.byCk[old.Checksum] == nil {
		t.Fatal("the run executed with the stored AM1c engine")
	}
	if cards, _ := e.store.RunCards(ctx, run.ID); len(cards) != 0 {
		t.Fatalf("the clean sample asks nothing under AM1c either: %d cards", len(cards))
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 2 {
		t.Fatalf("its report and deck: %d", len(arts))
	}

	// 3 · a stored engine whose package is not the one pinned is never used
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET checksum = 'another' WHERE id = $1`, e.install.ID); err != nil {
		t.Fatal(err)
	}
	e.install.Checksum = "another"
	fresh := e.runner("fresh") // no cache
	wrong := e.drive(fresh, pinned().ID, 3*time.Minute, terminal)
	if wrong.Status != am.RunFailed {
		t.Fatalf("a package checksum that does not match: %s %s", wrong.Status, wrong.StatusReason)
	}
	_ = uuid.Nil
}
