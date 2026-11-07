package runner

// AM1c — a runner killed at the wrong moment leaves a sandbox container that
// nothing removes. These tests plant exactly what such a runner leaves
// behind next to containers that must survive, and use only the docker CLI
// and the runner itself (the label keys are spelled out, not imported).

import (
	"context"
	"fmt"
	"log/slog"
	"os/exec"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// plant creates a sandbox-image container with these labels; started ones
// sleep as a step in progress would.
func plant(t *testing.T, labels map[string]string, started bool) string {
	t.Helper()
	name := "sbx-am1c-it-" + uuid.NewString()
	args := []string{"create", "--name", name, "--network", "none"}
	if started {
		args[0] = "run"
		args = append(args, "-d")
	}
	for k, v := range labels {
		args = append(args, "--label", k+"="+v)
	}
	args = append(args, "--entrypoint", "sleep", envOr("AM_SANDBOX_IMAGE", "ai-gateway-sandbox:latest"), "600")
	if out, err := exec.Command("docker", args...).CombinedOutput(); err != nil {
		t.Fatalf("docker %s: %v %s", args[0], err, out)
	}
	t.Cleanup(func() { _ = exec.Command("docker", "rm", "-f", name).Run() })
	return name
}

func exists(name string) bool {
	out, err := exec.Command("docker", "ps", "-a", "--filter", "name=^"+name+"$", "--format", "{{.Names}}").Output()
	return err == nil && strings.TrimSpace(string(out)) == name
}

func runnerLabels(runner, instance string, deadline time.Time) map[string]string {
	return map[string]string{
		"sran.sandbox.owner":         "agent-runner",
		"sran.agent_market.run_id":   uuid.NewString(),
		"sran.agent_market.runner":   runner,
		"sran.agent_market.instance": instance,
		"sran.agent_market.call":     uuid.NewString(),
		"sran.sandbox.deadline_unix": fmt.Sprint(deadline.Unix()),
	}
}

func TestARestartedRunnerReapsWhatItsKilledProcessLeft(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	soon := time.Now().Add(5 * time.Minute)
	// Killed between `docker create` and `start`: created, never started, still
	// inside its deadline. The runner restarts in the same container, so it
	// comes back under the same runner id with a new instance.
	killedCreated := plant(t, runnerLabels("r-restarted", "dead-instance", soon), false)
	// Killed while the step ran: the container keeps going on its own.
	killedRunning := plant(t, runnerLabels("r-restarted", "dead-instance", soon), true)
	// Another runner's container, far past the deadline its own ceiling would
	// have killed it at: nobody owns it any more.
	stale := plant(t, runnerLabels("r-gone", "gone-instance", time.Now().Add(-10*time.Minute)), false)
	// A step another live runner is running right now.
	liveOther := plant(t, runnerLabels("r-other", "other-instance", soon), true)
	// A chat sandbox: no runner labels, never the runner's to touch.
	chat := plant(t, nil, true)

	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx")
	got := e.drive(e.runner("r-restarted"), run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the run itself: %s %s", got.Status, got.StatusReason)
	}
	for _, c := range []struct {
		name string
		want bool
		why  string
	}{
		{killedCreated, false, "created by the killed process, never started"},
		{killedRunning, false, "still running for the killed process"},
		{stale, false, "past its deadline"},
		{liveOther, true, "another runner's step in progress"},
		{chat, true, "a chat sandbox"},
	} {
		if exists(c.name) != c.want {
			t.Errorf("%s (%s): exists=%v, want %v", c.name, c.why, !c.want, c.want)
		}
	}
}

// Sweeping every 50 ms — this runner and a second runner at once — never
// touches a step in flight: every step runs once and the run succeeds. The
// run's containers carry every label while they exist, and none is left.
func TestTheSweepNeverTouchesAStepInFlight(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx")
	r := e.runner("r-sweeping")
	r.Reaper = NewReaper(e.sb, r.Exec.Limits.Wall, slog.Default())
	r.Reaper.Every = 50 * time.Millisecond

	stop := make(chan struct{})
	var seen atomic.Int32
	var badLabels atomic.Value
	go func() { // the other runner's sweep, and a watch on the run's containers
		other := NewReaper(e.sb, r.Exec.Limits.Wall, slog.Default())
		format := `{{.Label "sran.sandbox.owner"}}|{{.Label "sran.agent_market.runner"}}|{{.Label "sran.agent_market.instance"}}|{{.Label "sran.agent_market.call"}}|{{.Label "sran.sandbox.deadline_unix"}}`
		for {
			select {
			case <-stop:
				return
			case <-time.After(50 * time.Millisecond):
			}
			other.Sweep(context.Background(), Identity{Runner: "r-other", Instance: "i-other"}, func() map[string]bool { return nil })
			out, _ := exec.Command("docker", "ps", "-a", "--filter", "label=sran.agent_market.run_id="+run.ID.String(), "--format", format).Output()
			for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
				if line == "" {
					continue
				}
				seen.Add(1)
				f := strings.Split(line, "|")
				if len(f) != 5 || f[0] != "agent-runner" || f[1] != "r-sweeping" || f[2] == "" || f[3] == "" || f[4] == "" {
					badLabels.Store(line)
				}
			}
		}
	}()
	got := e.drive(r, run.ID, 4*time.Minute, terminal)
	close(stop)
	if got.Status != am.RunSucceeded {
		t.Fatalf("run: %s %s", got.Status, got.StatusReason)
	}
	for _, s := range e.steps(run.ID) {
		if s.Attempt != 1 {
			t.Errorf("step %s ran %d times: a sweep removed it mid-flight", s.Name, s.Attempt)
		}
	}
	if seen.Load() == 0 {
		t.Error("never saw a container labelled with the run id")
	}
	if v := badLabels.Load(); v != nil {
		t.Errorf("a container missing labels: %v", v)
	}
	if out, _ := exec.Command("docker", "ps", "-a", "--filter", "label=sran.agent_market.run_id="+run.ID.String(), "-q").Output(); strings.TrimSpace(string(out)) != "" {
		t.Errorf("containers left after the run: %s", out)
	}
}
