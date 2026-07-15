// Package sandbox is Phase 13 Part A — the narrow, server-enforced
// contract chat-edge uses to run model-INFLUENCED code (plan-exec
// today; the free-form run_python tool in Part B) with hard isolation.
//
// Nothing model-authored ever again runs in the secret-bearing chat-edge
// process. Every job runs in a per-run ephemeral `docker run --rm` of the
// dedicated `ai-gateway-sandbox` image, launched with the full lockdown:
//
//	--network none                 no postgres/redis/gateway/internet
//	--user 65534:65534             non-root
//	--cap-drop ALL                 no Linux capabilities
//	--security-opt no-new-privileges
//	--security-opt seccomp=<prof>  default-deny syscalls (compute allowlist,
//	                               networking syscalls removed)
//	--read-only                    immutable rootfs
//	--tmpfs /tmp                    the only scratch, wiped each run
//	--memory / --memory-swap       hard RAM cap (no swap escape)
//	--pids-limit                   fork-bomb containment
//	--cpus                         CPU share cap
//	--ulimit fsize / nofile        output-size + fd caps
//	NO --env passthrough           none of chat-edge's secrets
//	-v <run>/in:/job/in:ro         ONLY the specific input files, read-only
//	-v <run>/out:/job/out          per-run writable output dir
//
// Limits are SERVER-enforced constants (see Limits + DefaultLimits); a
// caller may only tighten within a capped request, never widen.
//
// DOCKER-SOCKET PRIVILEGE. Spawning a run needs access to the Docker
// daemon. That access is held ONLY by this trusted Go code: the model's
// influence is confined to (a) a plan/code delivered as a FILE inside the
// no-privilege container and (b) input file *contents*. No model input
// ever reaches the `docker run` argv — every flag here is a server
// constant and every path is a server-derived basename. See the package
// README / final report for the prod-hardening path (a minimal broker so
// a chat-edge RCE still cannot issue arbitrary docker commands).
package sandbox

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
)

// Mode selects how the runner interprets the job.
type Mode string

const (
	// ModeScript runs one of the baked-in plan-exec scripts (Part A).
	ModeScript Mode = "script"
	// ModeCode runs free-form model python (Part B seam). The runner
	// executes it under the identical lockdown; Part C's DLP egress gate
	// inspects the returned stdout/artifacts.
	ModeCode Mode = "code"
)

// InputFile is a single read-only input placed under /job/in. Name is the
// basename the job.json args reference; exactly one of LocalPath / Bytes
// supplies the content.
type InputFile struct {
	Name      string
	LocalPath string
	Bytes     []byte
}

// Limits are the server-enforced resource ceilings. Zero fields fall back
// to DefaultLimits. Clamp() prevents a caller widening past the hard caps.
type Limits struct {
	Wall         time.Duration
	MemoryMB     int64
	PidsLimit    int64
	CPUs         string
	MaxOutputB   int64 // per-stream stdout/stderr cap (runner-enforced)
	FileSizeB    int64 // ulimit fsize
	ScratchMB    int64 // tmpfs /tmp size
}

// DefaultLimits are the conservative defaults for plan-exec. Part B's
// run_python can pass its own (still Clamp()ed) Limits.
var DefaultLimits = Limits{
	Wall:       90 * time.Second,
	MemoryMB:   512,
	PidsLimit:  128,
	CPUs:       "1.0",
	MaxOutputB: 4 << 20,   // 4 MiB
	FileSizeB:  128 << 20, // 128 MiB per file
	ScratchMB:  256,
}

// hard ceilings — a caller can never exceed these regardless of request.
const (
	maxWall      = 300 * time.Second
	maxMemoryMB  = 2048
	maxPids      = 512
	maxScratchMB = 1024
	maxFileSizeB = 512 << 20
)

func (l Limits) withDefaults() Limits {
	d := DefaultLimits
	if l.Wall > 0 {
		d.Wall = l.Wall
	}
	if l.MemoryMB > 0 {
		d.MemoryMB = l.MemoryMB
	}
	if l.PidsLimit > 0 {
		d.PidsLimit = l.PidsLimit
	}
	if strings.TrimSpace(l.CPUs) != "" {
		d.CPUs = l.CPUs
	}
	if l.MaxOutputB > 0 {
		d.MaxOutputB = l.MaxOutputB
	}
	if l.FileSizeB > 0 {
		d.FileSizeB = l.FileSizeB
	}
	if l.ScratchMB > 0 {
		d.ScratchMB = l.ScratchMB
	}
	return d.clamp()
}

func (l Limits) clamp() Limits {
	if l.Wall > maxWall {
		l.Wall = maxWall
	}
	if l.MemoryMB > maxMemoryMB {
		l.MemoryMB = maxMemoryMB
	}
	if l.PidsLimit > maxPids {
		l.PidsLimit = maxPids
	}
	if l.ScratchMB > maxScratchMB {
		l.ScratchMB = maxScratchMB
	}
	if l.FileSizeB > maxFileSizeB {
		l.FileSizeB = maxFileSizeB
	}
	return l
}

// Spec is one sandbox job. Args mirror the plan-exec scripts' positional
// contract; "out/<name>" args are writable targets, everything else is
// resolved read-only under /job/in (see sandbox_runner.py).
type Spec struct {
	Mode    Mode
	Script  string // ModeScript: baked-in script basename
	Code    string // ModeCode: free-form python
	Args    []string
	Inputs  []InputFile
	Limits  Limits
}

// Artifact is a file the job produced under /job/out (excluding the __*
// result sidecars). Path is valid on the host until Result.Cleanup runs.
type Artifact struct {
	Name string
	Path string
}

// Result is the execute() return value.
type Result struct {
	Stdout    []byte
	Stderr    []byte
	Exit      int
	Artifacts []Artifact
	Truncated bool
	TimedOut  bool
	cleanup   func()
}

// Cleanup removes the per-run directory. Always call it (defer).
func (r *Result) Cleanup() {
	if r != nil && r.cleanup != nil {
		r.cleanup()
	}
}

// Artifact returns the produced artifact with the given basename, or nil.
func (r *Result) Artifact(name string) *Artifact {
	for i := range r.Artifacts {
		if r.Artifacts[i].Name == name {
			return &r.Artifacts[i]
		}
	}
	return nil
}

// Config wires the runner to the host environment.
type Config struct {
	// DockerBin is the docker CLI (default "docker").
	DockerBin string
	// Image is the sandbox image tag.
	Image string
	// SeccompPath is the profile path AS THE DOCKER DAEMON READS IT.
	SeccompPath string
	// WorkDir is the per-run staging root as THIS process writes it.
	WorkDir string
	// HostWorkDir is the same staging root AS THE DAEMON sees it for -v
	// bind mounts. Equal to WorkDir when docker runs on the same host
	// namespace (dev / standalone). When chat-edge is itself a container,
	// set this to the host path the shared volume is bind-mounted from.
	HostWorkDir string
}

// Runner executes sandbox jobs. Safe for concurrent use — each Execute
// stages an isolated per-run directory.
type Runner struct {
	cfg Config
}

var (
	// ErrUnavailable means the sandbox can't run (docker/image/config
	// missing). Callers may fall back to a legacy path ONLY in dev.
	ErrUnavailable = errors.New("sandbox: unavailable")
	// ErrJobFailed means the container ran but the runner reported an
	// internal failure (bad job spec, unknown script).
	ErrJobFailed = errors.New("sandbox: job failed")
)

// New constructs a Runner, applying defaults.
func New(cfg Config) *Runner {
	if cfg.DockerBin == "" {
		cfg.DockerBin = "docker"
	}
	if cfg.Image == "" {
		cfg.Image = "ai-gateway-sandbox:latest"
	}
	if cfg.WorkDir == "" {
		cfg.WorkDir = filepath.Join(os.TempDir(), "ai-gateway-sandbox")
	}
	if cfg.HostWorkDir == "" {
		cfg.HostWorkDir = cfg.WorkDir
	}
	return &Runner{cfg: cfg}
}

// Available reports whether the docker CLI and sandbox image are present.
// Used at startup so the service can log a loud warning if the sandbox is
// misconfigured rather than failing the first plan-exec.
func (r *Runner) Available(ctx context.Context) error {
	if _, err := exec.LookPath(r.cfg.DockerBin); err != nil {
		return fmt.Errorf("%w: docker cli not found: %v", ErrUnavailable, err)
	}
	cctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	out, err := exec.CommandContext(cctx, r.cfg.DockerBin, "image", "inspect", r.cfg.Image).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%w: image %q not present: %s", ErrUnavailable, r.cfg.Image, strings.TrimSpace(string(out)))
	}
	return nil
}

// runnerMeta mirrors sandbox_runner.py's __meta.json sidecar.
type runnerMeta struct {
	Exit      int    `json:"exit"`
	Truncated bool   `json:"truncated"`
	TimedOut  bool   `json:"timed_out"`
	Error     string `json:"error"`
}

// jobSpec is the JSON the runner reads from /job/in/job.json.
type jobSpec struct {
	Mode         string   `json:"mode"`
	Script       string   `json:"script,omitempty"`
	Code         string   `json:"code,omitempty"`
	Args         []string `json:"args,omitempty"`
	WallTimeout  int      `json:"wall_timeout_sec"`
	MaxOutputByt int64    `json:"max_output_bytes"`
}

// Execute stages inputs, launches the locked-down container, and collects
// results. This IS the execute() contract Part B's run_python calls with
// Mode=ModeCode.
func (r *Runner) Execute(ctx context.Context, spec Spec) (*Result, error) {
	limits := spec.Limits.withDefaults()

	runID := uuid.NewString()
	localRun := filepath.Join(r.cfg.WorkDir, runID)
	localIn := filepath.Join(localRun, "in")
	localOut := filepath.Join(localRun, "out")
	if err := os.MkdirAll(localIn, 0o700); err != nil {
		return nil, fmt.Errorf("sandbox: mkdir in: %w", err)
	}
	if err := os.MkdirAll(localOut, 0o777); err != nil {
		return nil, fmt.Errorf("sandbox: mkdir out: %w", err)
	}
	// The container's uid 65534 must be able to write /job/out.
	_ = os.Chmod(localOut, 0o777)
	cleanup := func() { _ = os.RemoveAll(localRun) }

	// Stage inputs (basename only — model-supplied names are sanitised
	// to a basename so nothing points outside /job/in).
	for _, in := range spec.Inputs {
		name := filepath.Base(in.Name)
		if name == "" || name == "." || name == ".." {
			cleanup()
			return nil, fmt.Errorf("sandbox: bad input name %q", in.Name)
		}
		dst := filepath.Join(localIn, name)
		if err := stageInput(dst, in); err != nil {
			cleanup()
			return nil, err
		}
	}

	// Write job.json.
	job := jobSpec{
		Mode:         string(spec.Mode),
		Script:       filepath.Base(spec.Script),
		Code:         spec.Code,
		Args:         spec.Args,
		WallTimeout:  int(limits.Wall / time.Second),
		MaxOutputByt: limits.MaxOutputB,
	}
	if spec.Mode == "" {
		job.Mode = string(ModeScript)
	}
	jb, err := json.Marshal(job)
	if err != nil {
		cleanup()
		return nil, fmt.Errorf("sandbox: marshal job: %w", err)
	}
	if err := os.WriteFile(filepath.Join(localIn, "job.json"), jb, 0o644); err != nil {
		cleanup()
		return nil, fmt.Errorf("sandbox: write job: %w", err)
	}

	// Bind-mount source paths as the DAEMON sees them.
	hostRun := r.cfg.HostWorkDir
	if hostRun != r.cfg.WorkDir {
		hostRun = joinHost(r.cfg.HostWorkDir, runID)
	} else {
		hostRun = localRun
	}
	hostIn := joinHost(hostRun, "in")
	hostOut := joinHost(hostRun, "out")

	containerName := "sbx-" + runID
	args := r.dockerArgs(limits, hostIn, hostOut, containerName)

	// Hard host-side ceiling: the runner self-kills at limits.Wall; give
	// docker startup + teardown grace, then SIGKILL the whole run.
	cctx, cancel := context.WithTimeout(ctx, limits.Wall+30*time.Second)
	defer cancel()
	// Reap the container unconditionally: if the host ceiling fires it
	// only kills the `docker run` CLI, NOT the container — an orphaned
	// infinite-loop run would keep burning a CPU. --rm removes it on a
	// clean exit; this `rm -f` catches the timed-out / CLI-killed case.
	defer func() {
		rctx, rcancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer rcancel()
		_ = exec.CommandContext(rctx, r.cfg.DockerBin, "rm", "-f", containerName).Run()
	}()
	cmd := exec.CommandContext(cctx, r.cfg.DockerBin, args...)
	// Explicitly hand the container an EMPTY app env: docker already does
	// not inherit the parent env, but we set Env to nil to be unmistakable.
	cmd.Env = minimalEnv()
	var dockerErr strings.Builder
	cmd.Stderr = &dockerErr
	_ = cmd.Run() // container/runner failures are read from the out sidecars

	// Read result sidecars.
	meta := readMeta(localOut)
	stdout, _ := os.ReadFile(filepath.Join(localOut, "__stdout"))
	stderrb, _ := os.ReadFile(filepath.Join(localOut, "__stderr"))

	if meta == nil {
		// The runner never wrote meta — a docker-level failure (image
		// missing, daemon unreachable, OOM-killed before sidecar write).
		cleanup()
		return nil, fmt.Errorf("%w: no result sidecar (docker stderr: %s)",
			ErrUnavailable, strings.TrimSpace(dockerErr.String()))
	}
	if meta.Error != "" {
		res := &Result{Stdout: stdout, Stderr: stderrb, Exit: meta.Exit,
			Truncated: meta.Truncated, TimedOut: meta.TimedOut, cleanup: cleanup}
		return res, fmt.Errorf("%w: %s", ErrJobFailed, meta.Error)
	}

	arts := collectArtifacts(localOut, hostOut)
	return &Result{
		Stdout:    stdout,
		Stderr:    stderrb,
		Exit:      meta.Exit,
		Artifacts: arts,
		Truncated: meta.Truncated,
		TimedOut:  meta.TimedOut,
		cleanup:   cleanup,
	}, nil
}

// dockerArgs builds the immutable lockdown flag set + mounts. Every value
// here is a server constant or a server-derived limit — no model input.
func (r *Runner) dockerArgs(l Limits, hostIn, hostOut, name string) []string {
	mem := strconv.FormatInt(l.MemoryMB, 10) + "m"
	args := []string{
		"run", "--rm",
		"--name", name,
		"--network", "none",
		"--user", "65534:65534",
		"--cap-drop", "ALL",
		"--security-opt", "no-new-privileges",
	}
	if r.cfg.SeccompPath != "" {
		args = append(args, "--security-opt", "seccomp="+r.cfg.SeccompPath)
	}
	args = append(args,
		"--read-only",
		"--tmpfs", fmt.Sprintf("/tmp:rw,noexec,nosuid,size=%dm,mode=1777", l.ScratchMB),
		"--memory", mem, "--memory-swap", mem,
		"--pids-limit", strconv.FormatInt(l.PidsLimit, 10),
		"--cpus", l.CPUs,
		"--ulimit", fmt.Sprintf("fsize=%d:%d", l.FileSizeB, l.FileSizeB),
		"--ulimit", "nofile=256:256",
		"-v", hostIn+":/job/in:ro",
		"-v", hostOut+":/job/out",
		r.cfg.Image,
	)
	return args
}

func stageInput(dst string, in InputFile) error {
	if in.LocalPath != "" {
		src, err := os.Open(in.LocalPath)
		if err != nil {
			return fmt.Errorf("sandbox: open input %q: %w", in.Name, err)
		}
		defer src.Close()
		out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
		if err != nil {
			return fmt.Errorf("sandbox: stage input %q: %w", in.Name, err)
		}
		defer out.Close()
		if _, err := io.Copy(out, src); err != nil {
			return fmt.Errorf("sandbox: copy input %q: %w", in.Name, err)
		}
		return nil
	}
	if err := os.WriteFile(dst, in.Bytes, 0o644); err != nil {
		return fmt.Errorf("sandbox: write input %q: %w", in.Name, err)
	}
	return nil
}

func readMeta(outDir string) *runnerMeta {
	b, err := os.ReadFile(filepath.Join(outDir, "__meta.json"))
	if err != nil {
		return nil
	}
	var m runnerMeta
	if err := json.Unmarshal(b, &m); err != nil {
		return nil
	}
	return &m
}

func collectArtifacts(localOut, hostOut string) []Artifact {
	entries, err := os.ReadDir(localOut)
	if err != nil {
		return nil
	}
	var arts []Artifact
	for _, e := range entries {
		if e.IsDir() || strings.HasPrefix(e.Name(), "__") {
			continue
		}
		arts = append(arts, Artifact{
			Name: e.Name(),
			Path: filepath.Join(localOut, e.Name()),
		})
	}
	_ = hostOut
	return arts
}

// joinHost joins path segments with forward slashes so a Linux daemon
// path stays valid regardless of the OS this process runs on.
func joinHost(base string, parts ...string) string {
	b := strings.TrimRight(base, "/")
	for _, p := range parts {
		b += "/" + strings.Trim(p, "/")
	}
	return b
}

// minimalEnv gives the docker CLI just what it needs to reach the daemon,
// deliberately NOT the chat-edge secret env. DOCKER_* + PATH only.
func minimalEnv() []string {
	keep := []string{"PATH", "DOCKER_HOST", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "HOME"}
	var env []string
	for _, k := range keep {
		if v, ok := os.LookupEnv(k); ok {
			env = append(env, k+"="+v)
		}
	}
	return env
}
