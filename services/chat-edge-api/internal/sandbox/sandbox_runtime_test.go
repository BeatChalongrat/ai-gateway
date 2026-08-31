package sandbox

// FIX14 §2 — the no-silent-fall-back gate.
//   * TestEnforceRuntime is pure and always runs: the refusal LOGIC.
//   * The *Live tests exercise real docker; run with SANDBOX_LIVE=1. They prove
//     the boundary is stated FROM A RUN (not config), and that a required-but-
//     absent runtime REFUSES rather than falling back to runc.

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
)

func TestEnforceRuntime(t *testing.T) {
	cases := []struct {
		cfgRuntime string
		inside     string
		wantErr    bool
	}{
		// Standard container (default): never refuses.
		{"", "standard", false},
		{"", "gvisor", false},
		{"", "", false},
		// runsc REQUIRED: only a from-inside "gvisor" passes.
		{"runsc", "gvisor", false},
		{"runsc", "standard", true}, // silent fall-back to runc → refuse
		{"runsc", "", true},         // couldn't self-report → treated as weak → refuse
		// Unknown hardened runtime: must at least not be the standard container.
		{"kata", "standard", true},
		{"kata", "somevm", false},
	}
	for _, c := range cases {
		r := &Runner{cfg: Config{Runtime: c.cfgRuntime}}
		err := r.enforceRuntime(c.inside)
		if (err != nil) != c.wantErr {
			t.Errorf("enforceRuntime(cfg=%q, inside=%q): err=%v want err=%v", c.cfgRuntime, c.inside, err, c.wantErr)
		}
		if err != nil && !errors.Is(err, ErrUnavailable) {
			t.Errorf("refusal must be ErrUnavailable, got %v", err)
		}
	}
}

func liveOrSkip(t *testing.T) {
	if os.Getenv("SANDBOX_LIVE") != "1" {
		t.Skip("set SANDBOX_LIVE=1 to run the live docker sandbox-runtime tests")
	}
}

// The system states the boundary from a real probe run.
func TestVerifyBoundary_StatesRuntimeFromInside_Live(t *testing.T) {
	liveOrSkip(t)
	r := New(Config{}) // Runtime "" = standard
	got, err := r.VerifyBoundary(context.Background())
	if err != nil {
		t.Fatalf("standard boundary probe failed: %v", err)
	}
	if got != "standard" {
		t.Fatalf("expected standard runtime on default runc, got %q", got)
	}
	t.Logf("boundary in effect (default runc), stated from inside a probe run: %q", got)
}

// A required runtime that is not installed REFUSES — it does not fall back.
func TestVerifyBoundary_AbsentRuntimeRefuses_Live(t *testing.T) {
	liveOrSkip(t)
	r := New(Config{Runtime: "runsc"}) // gVisor is NOT installed on this host
	_, err := r.VerifyBoundary(context.Background())
	if err == nil {
		t.Fatal("expected VerifyBoundary to FAIL for an absent runtime, got nil (silent fall-back?)")
	}
	t.Logf("absent runtime refused at boundary probe (as required): %v", err)

	// And Available() (the startup gate) refuses too, so the service will not
	// come up believing it is hardened when it is not.
	if aerr := r.Available(context.Background()); aerr == nil {
		t.Fatal("Available() must refuse when the required runtime is absent")
	}
}

// A full job REFUSES when the required runtime is absent — proven end-to-end,
// and a standard job self-reports "standard" from inside.
func TestExecute_RuntimeEnforcement_Live(t *testing.T) {
	liveOrSkip(t)
	code := "print(open('/proc/version').read().strip())"

	// Absent required runtime → the run refuses, no result.
	rHard := New(Config{Runtime: "runsc"})
	if _, err := rHard.Execute(context.Background(), Spec{Mode: ModeCode, Code: code}); err == nil {
		t.Fatal("Execute must refuse when the required runtime is absent")
	} else {
		t.Logf("job refused under required-but-absent runsc: %v", err)
	}

	// Default runc → runs, and states its runtime from inside.
	rStd := New(Config{})
	res, err := rStd.Execute(context.Background(), Spec{Mode: ModeCode, Code: code})
	if err != nil {
		t.Fatalf("standard job failed: %v", err)
	}
	defer res.Cleanup()
	if res.Runtime != "standard" {
		t.Fatalf("standard job must report runtime=standard, got %q", res.Runtime)
	}
	t.Logf("standard job ran; runtime stated from inside=%q; /proc/version=%q", res.Runtime, strings.TrimSpace(string(res.Stdout)))
}

// Representative workload timing (§1) under the current runtime — the "before".
func TestRepresentativeWorkloadTiming_Live(t *testing.T) {
	liveOrSkip(t)
	code := `import numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
df = pd.DataFrame(np.random.rand(50000, 8), columns=list('abcdefgh'))
s = df.describe().sum().sum()
plt.figure(); plt.plot(np.cumsum(np.random.rand(2000))); plt.savefig('/job/out/chart.png')
print('sum=', round(float(s), 3))`
	r := New(Config{})
	start := time.Now()
	res, err := r.Execute(context.Background(), Spec{Mode: ModeCode, Code: code})
	elapsed := time.Since(start)
	if err != nil {
		t.Fatalf("representative workload failed: %v", err)
	}
	defer res.Cleanup()
	t.Logf("REPRESENTATIVE WORKLOAD (numpy+pandas+matplotlib) under runtime=%q: %s (exit %d, %d artifact(s), stdout=%q)",
		res.Runtime, elapsed.Round(time.Millisecond), res.Exit, len(res.Artifacts), strings.TrimSpace(string(res.Stdout)))
}
