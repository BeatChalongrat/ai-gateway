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
