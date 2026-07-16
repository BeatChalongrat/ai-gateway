package sandbox

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// These tests exercise the REAL docker sandbox. They skip (not fail) when
// docker or the image is unavailable so CI without the image stays green,
// but on a machine with `ai-gateway-sandbox:latest` built they prove the
// Go execute() contract end-to-end: artifact collection, network block,
// stripped env, and wall-clock kill.
//
// Work root is a space-free path because the Windows docker CLI mis-parses
// bind-mount / seccomp paths that contain spaces.

func newTestRunner(t *testing.T) *Runner {
	t.Helper()
	workRoot := "/tmp/ai-gw-sbx-test"
	if runtime.GOOS == "windows" {
		workRoot = "C:/ai-gw-sbx-test"
	}
	if err := os.MkdirAll(workRoot, 0o777); err != nil {
		t.Fatalf("mkdir work root: %v", err)
	}
	// Copy the repo seccomp profile next to the work root so the daemon
	// can read it from a space-free path.
	seccompSrc := filepath.Join("..", "..", "sandbox", "seccomp.json")
	b, err := os.ReadFile(seccompSrc)
	if err != nil {
		t.Skipf("seccomp profile not found (%v) — run from repo", err)
	}
	seccompDst := filepath.Join(workRoot, "seccomp.json")
	if err := os.WriteFile(seccompDst, b, 0o644); err != nil {
		t.Fatalf("copy seccomp: %v", err)
	}
	r := New(Config{
		WorkDir:     workRoot,
		HostWorkDir: workRoot,
		SeccompPath: seccompDst,
	})
	if err := r.Available(context.Background()); err != nil {
		t.Skipf("sandbox unavailable: %v", err)
	}
	return r
}

func TestSandbox_ExcelPlanExec(t *testing.T) {
	r := newTestRunner(t)
	plan := `{"operation":"create_workbook","actions":[
		{"type":"create_sheet","sheet_name":"S","position":0},
		{"type":"write_table","sheet_name":"S","start_cell":"A1",
		 "headers":["A","B"],"data":[["x",1],["y",2]]}]}`
	res, err := r.Execute(context.Background(), Spec{
		Mode:   ModeScript,
		Script: "excel_executor.py",
		Args:   []string{"plan.json", "source.xlsx", "out/output.xlsx"},
		Inputs: []InputFile{
			{Name: "plan.json", Bytes: []byte(plan)},
			{Name: "source.xlsx", Bytes: []byte(plan)}, // ignored by create_workbook
		},
		Limits: Limits{Wall: 60 * time.Second},
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	defer res.Cleanup()
	if res.Exit != 0 {
		t.Fatalf("exit=%d stderr=%s", res.Exit, res.Stderr)
	}
	art := res.Artifact("output.xlsx")
	if art == nil {
		t.Fatalf("no output.xlsx artifact; artifacts=%v", res.Artifacts)
	}
	fi, err := os.Stat(art.Path)
	if err != nil || fi.Size() < 1000 {
		t.Fatalf("artifact bad: err=%v size=%d", err, sizeOf(fi))
	}
	var out map[string]any
	if err := json.Unmarshal(res.Stdout, &out); err != nil {
		t.Fatalf("stdout not JSON: %v (%s)", err, res.Stdout)
	}
	if out["success"] != true {
		t.Fatalf("executor did not report success: %v", out)
	}
}

func TestSandbox_NetworkBlockedViaGoPath(t *testing.T) {
	r := newTestRunner(t)
	code := "import socket\n" +
		"try:\n" +
		"    socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n" +
		"    print('SOCKET_OK')\n" +
		"except Exception as e:\n" +
		"    print('SOCKET_BLOCKED', type(e).__name__)\n"
	res, err := r.Execute(context.Background(), Spec{
		Mode: ModeCode, Code: code, Limits: Limits{Wall: 20 * time.Second},
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	defer res.Cleanup()
	if !strings.Contains(string(res.Stdout), "SOCKET_BLOCKED") {
		t.Fatalf("socket was NOT blocked: %s", res.Stdout)
	}
}

func TestSandbox_NoSecretsInEnv(t *testing.T) {
	r := newTestRunner(t)
	// Set a fake secret in THIS process's env; it must NOT reach the box.
	t.Setenv("DATABASE_URL", "postgres://should-not-leak")
	res, err := r.Execute(context.Background(), Spec{
		Mode: ModeCode,
		Code: "import os; print('LEAK' if 'DATABASE_URL' in os.environ else 'CLEAN')",
		Limits: Limits{Wall: 20 * time.Second},
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	defer res.Cleanup()
	if !strings.Contains(string(res.Stdout), "CLEAN") {
		t.Fatalf("secret leaked into sandbox env: %s", res.Stdout)
	}
}

func TestSandbox_WallClockKill(t *testing.T) {
	r := newTestRunner(t)
	res, err := r.Execute(context.Background(), Spec{
		Mode:   ModeCode,
		Code:   "while True:\n    x = 1\n",
		Limits: Limits{Wall: 4 * time.Second},
	})
	if err != nil {
		t.Fatalf("execute: %v", err)
	}
	defer res.Cleanup()
	if !res.TimedOut {
		t.Fatalf("expected TimedOut, got exit=%d stderr=%s", res.Exit, res.Stderr)
	}
}

func sizeOf(fi os.FileInfo) int64 {
	if fi == nil {
		return -1
	}
	return fi.Size()
}

// networkValues scans a docker argv for every `--network <value>` pair and
// returns the values in order. It is deliberately dumb (positional) so it
// mirrors exactly what the docker CLI sees.
func networkValues(args []string) []string {
	var vals []string
	for i := 0; i < len(args); i++ {
		if args[i] == "--network" {
			if i+1 < len(args) {
				vals = append(vals, args[i+1])
			} else {
				vals = append(vals, "")
			}
		}
		// Also catch the fused `--network=none` spelling if a future edit
		// switches styles, so this guard can't be trivially bypassed.
		if strings.HasPrefix(args[i], "--network=") {
			vals = append(vals, strings.TrimPrefix(args[i], "--network="))
		}
	}
	return vals
}

// TestSandbox_DockerArgsAlwaysNoNetwork is the Part C egress-invariant unit
// test: it pins the no-third-party-network guarantee at the code level. It
// needs NO docker/image (pure arg-builder assertion), so it runs in CI.
//
// The sandbox is an INTERNAL trust boundary (see docs/DLP_ARCHITECTURE.md
// "Code interpreter sandbox"): it runs on RAW user data but has no network
// egress, so results only reach a provider via the masked completion
// pipeline. If a future change removes `--network none` (or swaps it for
// bridge/host), this test fails — the egress invariant is enforced by the
// build, not by convention.
// TestSandbox_HostRunDirUsesDaemonPath pins the host-vs-container path fix
// (Bug A). When chat-edge is a container, SANDBOX_HOST_WORKDIR is the HOST
// path; the daemon's -v source must be under it, NOT the in-container
// staging path. Inverting this (HostWorkDir == the container WorkDir) is what
// gave every run_python a 503 ("no result sidecar").
func TestSandbox_HostRunDirUsesDaemonPath(t *testing.T) {
	// chat-edge-in-container: HostWorkDir is the host bind source (absolute),
	// WorkDir is the in-container mount point. The daemon must -v the HOST dir.
	got := resolveHostRunDir(
		"/sandbox-runs",                  // WorkDir (in-container)
		"/abs/host/data/sandbox-runs",    // HostWorkDir (host, as daemon sees it)
		"/sandbox-runs/RID",              // localRun (in-container)
		"RID",
	)
	if got != "/abs/host/data/sandbox-runs/RID" {
		t.Fatalf("host run dir = %q, want the DAEMON host path "+
			"/abs/host/data/sandbox-runs/RID (the -v source the daemon can resolve)", got)
	}

	// The broken config the bug had: HostWorkDir == the container WorkDir →
	// the -v source becomes the container path, which the daemon can't back.
	// Guard that this is NOT what a correct host config produces.
	if got == "/sandbox-runs/RID" {
		t.Fatal("host run dir resolved to the in-CONTAINER path — the daemon " +
			"would create an empty root-owned dir on the host (the 503 bug)")
	}

	// dev/standalone: identical paths → use localRun verbatim.
	if d := resolveHostRunDir("/work", "/work", "/work/RID", "RID"); d != "/work/RID" {
		t.Fatalf("standalone host run dir = %q, want /work/RID", d)
	}
}

func TestSandbox_DockerArgsAlwaysNoNetwork(t *testing.T) {
	r := New(Config{
		WorkDir:     "/work",
		HostWorkDir: "/work",
		SeccompPath: "/work/seccomp.json",
	})

	// Exercise the builder across default, custom, and clamp-forced limits,
	// with and without a seccomp path, so no reachable branch can widen the
	// network mode.
	cases := []struct {
		name   string
		cfg    Config
		limits Limits
	}{
		{"default", r.cfg, DefaultLimits},
		{"custom-limits", r.cfg, Limits{Wall: 30 * time.Second, MemoryMB: 256, PidsLimit: 64, CPUs: "0.5", ScratchMB: 64, FileSizeB: 1 << 20}.withDefaults()},
		{"over-cap-clamped", r.cfg, Limits{MemoryMB: 999999, PidsLimit: 999999, ScratchMB: 999999, FileSizeB: 1 << 40}.withDefaults()},
		{"no-seccomp", Config{WorkDir: "/work", HostWorkDir: "/work"}, DefaultLimits},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rr := &Runner{cfg: tc.cfg}
			if rr.cfg.Image == "" {
				rr.cfg.Image = "ai-gateway-sandbox:latest"
			}
			args := rr.dockerArgs(tc.limits, "/work/in", "/work/out", "sbx-test")

			vals := networkValues(args)
			if len(vals) != 1 {
				t.Fatalf("expected exactly one --network flag, got %d (%v) in args=%v",
					len(vals), vals, args)
			}
			if vals[0] != "none" {
				t.Fatalf("sandbox docker args set --network=%q; the egress "+
					"invariant requires 'none' (no third-party network path). args=%v",
					vals[0], args)
			}
		})
	}
}
