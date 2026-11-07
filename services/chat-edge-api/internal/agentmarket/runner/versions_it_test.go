package runner

// AM2 Phase G end to end: a pinned install runs with the engine it was
// released with; an upgrade runs the new version's self-test and switches
// only when it passes; a changed package cannot overwrite a released version
// (AGENT_MARKET_IT=1).

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// variantEngine is base with some files replaced (a different checksum).
func variantEngine(t *testing.T, base *Engine, edits map[string]func([]byte) []byte) *Engine {
	t.Helper()
	zr, err := zip.NewReader(bytes.NewReader(base.zipBytes), int64(len(base.zipBytes)))
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for _, f := range zr.File {
		rc, _ := f.Open()
		b, _ := io.ReadAll(rc)
		rc.Close()
		if edit, ok := edits[f.Name]; ok {
			b = edit(b)
		}
		w, _ := zw.CreateHeader(&zip.FileHeader{Name: f.Name, Method: zip.Deflate, Modified: f.Modified})
		_, _ = w.Write(b)
	}
	_ = zw.Close()
	tmp, err := os.CreateTemp(t.TempDir(), "variant-*.zip")
	if err != nil {
		t.Fatal(err)
	}
	_, _ = tmp.Write(buf.Bytes())
	tmp.Close()
	e, err := LoadEngine(tmp.Name(), "")
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func setVersion(v string) func([]byte) []byte {
	return func(b []byte) []byte {
		var m map[string]any
		_ = json.Unmarshal(b, &m)
		m["version"] = v
		out, _ := json.Marshal(m)
		return out
	}
}

const frManifest = "agent_market/packages/financial_report/agent.json"

func TestAChangedPackageNeverOverwritesAReleasedVersion(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	r := e.runner("r1")
	if err := r.Register(ctx); err != nil {
		t.Fatal(err)
	}
	before, err := e.store.DefinitionBySlugVersion(ctx, "sran.financial-report", r.Engine.Packages["sran.financial-report"].Version)
	if err != nil {
		t.Fatal(err)
	}
	changed := variantEngine(t, e.engine, map[string]func([]byte) []byte{
		"agent_market/packages/financial_report/AGENT.md": func(b []byte) []byte { return append(b, []byte("\nchanged\n")...) },
	})
	r2 := e.runner("r2")
	r2.Engine, r2.Exec.Engine = changed, changed
	if err := r2.Register(ctx); err != nil {
		t.Fatal(err)
	}
	after, _ := e.store.DefinitionBySlugVersion(ctx, "sran.financial-report", before.Version)
	if after.Checksum != before.Checksum || after.EngineChecksum != before.EngineChecksum {
		t.Fatal("a released version was overwritten by a changed package")
	}
	if ok, _ := e.files.Exists(ctx, am.EngineKey(changed.Build)); !ok {
		t.Fatal("every runner stores its engine")
	}
}

func TestPinnedInstallRunsItsOwnEngineAndUpgradesOnlyWhenItsSelfTestPasses(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	cur := e.runner("current")
	if err := cur.Register(ctx); err != nil {
		t.Fatal(err)
	}
	// an older release of the financial report, from another engine
	old := variantEngine(t, e.engine, map[string]func([]byte) []byte{frManifest: setVersion("0.9.0")})
	ro := e.runner("old")
	ro.Engine, ro.Exec.Engine = old, old
	if err := ro.Register(ctx); err != nil {
		t.Fatal(err)
	}
	oldDef, err := e.store.DefinitionBySlugVersion(ctx, "sran.financial-report", "0.9.0")
	if err != nil || oldDef.EngineChecksum != old.Build {
		t.Fatalf("old release: %+v %v", oldDef, err)
	}
	e.setupInstall(3_000_000)
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_installs SET definition_id = $2, definition_version = '0.9.0',
		checksum = $3 WHERE id = $1`, e.install.ID, oldDef.ID, oldDef.Checksum); err != nil {
		t.Fatal(err)
	}
	e.install, _ = e.store.InstallByID(ctx, e.install.ID)

	// 1 · the pinned run executes with the old engine, loaded from storage by the current runner
	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx")
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET checksum = $2, engine_checksum = $3, definition_version = '0.9.0'
		WHERE id = $1`, run.ID, oldDef.Checksum, oldDef.EngineChecksum)
	if got := e.drive(cur, run.ID, 5*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("pinned run: %s %s", got.Status, got.StatusReason)
	}
	if cur.engines.byCk[old.Build] == nil {
		t.Fatal("the run was executed with its own engine, loaded from storage")
	}

	// 2 · upgrade to the current version: its self-test passes, the install switches
	newDef, _ := e.store.DefinitionBySlugVersion(ctx, "sran.financial-report", cur.Engine.Packages["sran.financial-report"].Version)
	upgrade := func(to am.Definition) am.Run {
		params, _ := json.Marshal(map[string]any{"upgrade_to": to.ID})
		r, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: e.install.ID, TenantID: e.install.TenantID,
			UserID: e.install.UserID, Kind: am.KindSelfTest, Message: "[AM2] upgrade", Params: params, DefinitionVersion: to.Version,
			Checksum: to.Checksum, EngineChecksum: to.EngineChecksum}, "")
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	up := upgrade(newDef)
	if got := e.drive(cur, up.ID, 5*time.Minute, terminal); got.Status != am.RunSucceeded {
		t.Fatalf("upgrade self-test: %s %s", got.Status, got.StatusReason)
	}
	in, _ := e.store.InstallByID(ctx, e.install.ID)
	if in.DefinitionID != newDef.ID || in.PreviousDefinitionID == nil || *in.PreviousDefinitionID != oldDef.ID {
		t.Fatalf("switched: %+v", in)
	}

	// 3 · a version whose self-test misses its own expectations is not taken
	bad := variantEngine(t, e.engine, map[string]func([]byte) []byte{
		frManifest: setVersion("9.9.0"),
		"agent_market/packages/financial_report/self-test.json": func(b []byte) []byte {
			return []byte(strings.Replace(string(b), `"period": "2026-09"`, `"period": "2030-01"`, 1))
		},
	})
	rb := e.runner("bad")
	rb.Engine, rb.Exec.Engine = bad, bad
	if err := rb.Register(ctx); err != nil {
		t.Fatal(err)
	}
	badDef, _ := e.store.DefinitionBySlugVersion(ctx, "sran.financial-report", "9.9.0")
	up2 := upgrade(badDef)
	e.drive(cur, up2.ID, 5*time.Minute, terminal)
	in, _ = e.store.InstallByID(ctx, e.install.ID)
	if in.DefinitionID != newDef.ID {
		t.Fatal("a version that misses its self-test expectations must not be installed")
	}
	items, _ := e.store.ItemsAfter(ctx, e.install.ID, 0, 500)
	said := ""
	for _, it := range items {
		said += string(it.Payload)
	}
	if !strings.Contains(said, "ยังไม่อัปเกรดเป็นรุ่น 9.9.0") || !strings.Contains(said, "2030-01") {
		t.Fatal("the person is told why the upgrade was not taken")
	}
	_ = uuid.Nil
}
