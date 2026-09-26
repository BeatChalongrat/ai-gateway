package runner

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

// Labels on every sandbox container the runner starts (AM1c). A runner killed
// between `docker create` and `start`, or while a step runs, leaves its
// container behind: nothing else would ever remove it.
const (
	LabelOwner    = "sran.sandbox.owner"
	LabelRun      = "sran.agent_market.run_id"
	LabelRunner   = "sran.agent_market.runner"   // Runner.ID
	LabelInstance = "sran.agent_market.instance" // one Runner.Start
	LabelCall     = "sran.agent_market.call"     // one Executor.Run
	OwnerRunner   = "agent-runner"
)

// Box is one labelled sandbox container as docker lists it.
type Box struct {
	ID, Name, State              string
	Owner, Run, Runner, Instance string
	Call                         string
	Deadline                     time.Time // zero when the label is missing
}

// Identity is who is sweeping: the runner id and this process's instance.
type Identity struct{ Runner, Instance string }

// Reap is one container to remove and why. OwnerGone is true when the
// process that started it is gone, so its workspace is garbage too; a
// returned call of this process cleans its own workspace.
type Reap struct {
	Box       Box
	Reason    string
	OwnerGone bool
}

// Orphans picks the runner's containers no live step owns. inflight is this
// process's calls in progress, read AFTER the containers were listed (a call
// registers before its container can exist, so a listed container of a call
// in progress is always in it). Never touched: a container without the
// runner's owner label (chat's sandboxes), a call in progress here, and
// another runner's container before its deadline.
func Orphans(boxes []Box, me Identity, inflight map[string]bool, now time.Time, grace time.Duration) []Reap {
	var out []Reap
	for _, b := range boxes {
		if b.Owner != OwnerRunner || (b.Call != "" && inflight[b.Call]) {
			continue
		}
		switch {
		case me.Instance != "" && b.Instance == me.Instance:
			// Ours and its call has returned: the create raced the call's own rm -f.
			out = append(out, Reap{b, "its call has returned", false})
		case me.Runner != "" && b.Runner == me.Runner:
			// Same runner id, another instance: the process that started it was
			// replaced (a runner restarts in the same container, same hostname).
			out = append(out, Reap{b, "left by this runner's previous process", true})
		case !b.Deadline.IsZero() && now.After(b.Deadline.Add(grace)):
			// Past the moment its own host ceiling removes it: no live owner.
			out = append(out, Reap{b, "past its deadline", true})
		}
	}
	return out
}

// Reaper removes orphaned sandbox containers and their workspaces, once at
// runner start and then every Every.
type Reaper struct {
	Docker  string
	WorkDir string        // the executor's staging root (workspaces are WorkDir/<exec id>)
	Grace   time.Duration // past a container's deadline before it is reaped
	Every   time.Duration
	DirAge  time.Duration // a workspace older than this with no container is removed
	Log     *slog.Logger
}

// NewReaper sweeps with the sandbox's own docker CLI and work dir: every
// minute, a minute past a container's deadline.
func NewReaper(sb *sandbox.Runner, wall time.Duration, log *slog.Logger) *Reaper {
	return &Reaper{Docker: sb.DockerBin(), WorkDir: sb.WorkDir(), Grace: time.Minute, Every: time.Minute,
		DirAge: 2*(wall+sandbox.HostGrace) + time.Minute, Log: log}
}

func (x *Reaper) list(ctx context.Context) ([]Box, error) {
	format := strings.Join([]string{"{{.ID}}", "{{.Names}}", "{{.State}}",
		`{{.Label "` + LabelOwner + `"}}`, `{{.Label "` + LabelRun + `"}}`, `{{.Label "` + LabelRunner + `"}}`,
		`{{.Label "` + LabelInstance + `"}}`, `{{.Label "` + LabelCall + `"}}`, `{{.Label "` + sandbox.LabelDeadline + `"}}`}, "\t")
	out, err := exec.CommandContext(ctx, x.Docker, "ps", "-a", "--no-trunc",
		"--filter", "label="+LabelOwner+"="+OwnerRunner, "--format", format).Output()
	if err != nil {
		return nil, fmt.Errorf("docker ps: %w", err)
	}
	var boxes []Box
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		f := strings.Split(strings.TrimRight(line, "\r"), "\t")
		if len(f) != 9 {
			continue
		}
		b := Box{ID: f[0], Name: f[1], State: f[2], Owner: f[3], Run: f[4], Runner: f[5], Instance: f[6], Call: f[7]}
		if sec, err := strconv.ParseInt(f[8], 10, 64); err == nil {
			b.Deadline = time.Unix(sec, 0)
		}
		boxes = append(boxes, b)
	}
	return boxes, nil
}

// Sweep removes the orphans Orphans picks, and workspaces nothing uses.
func (x *Reaper) Sweep(ctx context.Context, me Identity, inflight func() map[string]bool) int {
	boxes, err := x.list(ctx)
	if err != nil {
		x.Log.Warn("sandbox sweep: list failed", "err", err)
		return 0
	}
	reaped := 0
	for _, r := range Orphans(boxes, me, inflight(), time.Now(), x.Grace) {
		if err := exec.CommandContext(ctx, x.Docker, "rm", "-f", r.Box.ID).Run(); err != nil {
			x.Log.Warn("sandbox sweep: remove failed", "container", r.Box.Name, "err", err)
			continue
		}
		reaped++
		x.Log.Info("orphaned sandbox container removed", "container", r.Box.Name, "state", r.Box.State,
			"run", r.Box.Run, "runner", r.Box.Runner, "reason", r.Reason)
		if r.OwnerGone {
			x.removeWorkspace(strings.TrimPrefix(r.Box.Name, "sbx-"))
		}
	}
	x.sweepWorkspaces()
	return reaped
}

func (x *Reaper) removeWorkspace(execID string) {
	if x.WorkDir == "" {
		return
	}
	if _, err := uuid.Parse(execID); err != nil {
		return
	}
	_ = os.RemoveAll(filepath.Join(x.WorkDir, execID))
}

// sweepWorkspaces removes step workspaces older than DirAge: a live step's
// workspace is younger (its host ceiling plus the time to read its files).
func (x *Reaper) sweepWorkspaces() {
	if x.WorkDir == "" || x.DirAge <= 0 {
		return
	}
	entries, err := os.ReadDir(x.WorkDir)
	if err != nil {
		return
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		if _, err := uuid.Parse(e.Name()); err != nil {
			continue
		}
		info, err := e.Info()
		if err != nil || time.Since(info.ModTime()) < x.DirAge {
			continue
		}
		if os.RemoveAll(filepath.Join(x.WorkDir, e.Name())) == nil {
			x.Log.Info("orphaned sandbox workspace removed", "dir", e.Name())
		}
	}
}

// ── labels through the context ──────────────────────────────────────────

type labelsKey struct{}

func withSandboxLabels(ctx context.Context, labels map[string]string) context.Context {
	return context.WithValue(ctx, labelsKey{}, labels)
}

func sandboxLabels(ctx context.Context) map[string]string {
	m, _ := ctx.Value(labelsKey{}).(map[string]string)
	return m
}
