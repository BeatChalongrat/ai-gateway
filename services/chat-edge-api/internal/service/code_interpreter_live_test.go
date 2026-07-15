package service

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

// These tests exercise the REAL docker sandbox through the run_python
// executor. They SKIP (not fail) when docker or the ai-gateway-sandbox
// image is unavailable — same guard as sandbox_test.go — so CI without the
// image stays green, but on a machine with the image built they prove the
// full path: model code → ModeCode container → stdout + png artifact →
// SaveGeneratedFile.

func newLiveInterpreter(t *testing.T) *CodeInterpreter {
	t.Helper()
	workRoot := "/tmp/ai-gw-sbx-code-test"
	if runtime.GOOS == "windows" {
		workRoot = "C:/ai-gw-sbx-code-test"
	}
	if err := os.MkdirAll(workRoot, 0o777); err != nil {
		t.Fatalf("mkdir work root: %v", err)
	}
	// Copy the repo seccomp profile to a space-free path the daemon can read.
	seccompSrc := filepath.Join("..", "..", "sandbox", "seccomp.json")
	b, err := os.ReadFile(seccompSrc)
	if err != nil {
		t.Skipf("seccomp profile not found (%v) — run from repo", err)
	}
	seccompDst := filepath.Join(workRoot, "seccomp.json")
	if err := os.WriteFile(seccompDst, b, 0o644); err != nil {
		t.Fatalf("copy seccomp: %v", err)
	}
	r := sandbox.New(sandbox.Config{
		WorkDir:     workRoot,
		HostWorkDir: workRoot,
		SeccompPath: seccompDst,
	})
	if err := r.Available(context.Background()); err != nil {
		t.Skipf("sandbox unavailable: %v", err)
	}
	return NewCodeInterpreter(r)
}

// End-to-end: pandas import + a matplotlib savefig into /job/out come back
// as printed stdout AND a persisted png artifact.
func TestRunPython_Live_PandasAndMatplotlibArtifact(t *testing.T) {
	ci := newLiveInterpreter(t)
	saver := &fakeGeneratedFileSaver{}
	e := &ToolExecutor{}
	e.SetCodeInterpreter(ci, &fakeCodeResolver{}, saver)

	code := "import pandas as pd\n" +
		"import matplotlib\n" +
		"matplotlib.use('Agg')\n" +
		"import matplotlib.pyplot as plt\n" +
		"print('PANDAS', pd.__version__)\n" +
		"plt.plot([1,2,3],[3,1,2])\n" +
		"plt.savefig('/job/out/chart.png')\n"

	ec := ToolExecContext{
		TenantID:       uuid.New(),
		UserID:         uuid.New(),
		ConversationID: uuid.New(),
		MessageID:      uuid.New(),
	}
	argsJSON, _ := json.Marshal(map[string]any{"code": code})
	out := e.execRunPython(context.Background(), string(argsJSON), ec)

	if !strings.Contains(out, "PANDAS") {
		t.Fatalf("expected pandas version in stdout, got:\n%s", out)
	}
	if !strings.Contains(out, "Exit code: 0") {
		t.Fatalf("expected a clean exit, got:\n%s", out)
	}
	if saver.calls != 1 {
		t.Fatalf("expected the chart.png artifact to be saved once, got %d", saver.calls)
	}
	if saver.inputs[0].ContentType != "image/png" {
		t.Fatalf("artifact should be image/png, got %q", saver.inputs[0].ContentType)
	}
	if len(saver.inputs[0].Data) < 100 {
		t.Fatalf("png artifact suspiciously small: %d bytes", len(saver.inputs[0].Data))
	}
}
