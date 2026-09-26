package runner

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// AM2 — after a Docker restart the runner came up while the engine was still
// booting: its one sandbox check (docker image inspect) timed out, it logged
// "sandbox unavailable — installs refused and runs fail until it is fixed"
// and stayed that way until it was restarted by hand. It now retries the
// check while the sandbox is not ready and takes installs and runs as soon as
// it passes.
func TestARunnerThatStartedBeforeDockerRecoversItsSandbox(t *testing.T) {
	var calls atomic.Int32
	r := &Runner{
		Log:          slog.New(slog.NewTextHandler(io.Discard, nil)),
		Engine:       &Engine{Checksum: strings.Repeat("e", 64)},
		Model:        &ModelClient{Primary: "p", Fallback: "f"},
		SandboxImage: "ai-gateway-sandbox:latest",
		SandboxCheck: func(context.Context) error {
			if calls.Add(1) < 3 {
				return errors.New(`sandbox: unavailable: image "ai-gateway-sandbox:latest" not present: `)
			}
			return nil
		},
		SandboxRecheck: 5 * time.Millisecond,
	}
	if r.Sandbox() {
		t.Fatal("sandbox ready before any check passed")
	}
	if _, ok := r.Capabilities()["document.read.pdf"]; ok {
		t.Fatal("sandbox capabilities offered while the sandbox is unavailable")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	ready := make(chan struct{})
	go r.watchSandbox(ctx, func() { close(ready) })
	select {
	case <-ready:
	case <-ctx.Done():
		t.Fatalf("the runner never noticed the sandbox came back (%d checks)", calls.Load())
	}
	if !r.Sandbox() {
		t.Fatal("check passed but the runner still refuses runs")
	}
	if _, ok := r.Capabilities()["document.read.pdf"]; !ok {
		t.Fatal("sandbox capabilities missing after recovery")
	}
	if n := calls.Load(); n != 3 {
		t.Fatalf("checks = %d, want 3 (it stops checking once ready)", n)
	}
}

// A runner whose sandbox was ready at start never runs the check again.
func TestAReadySandboxIsNotRechecked(t *testing.T) {
	var calls atomic.Int32
	r := &Runner{Log: slog.New(slog.NewTextHandler(io.Discard, nil)), SandboxReady: true,
		SandboxCheck: func(context.Context) error { calls.Add(1); return nil }, SandboxRecheck: time.Millisecond}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	r.watchSandbox(ctx, nil)
	if calls.Load() != 0 {
		t.Fatalf("checks = %d, want 0", calls.Load())
	}
}
