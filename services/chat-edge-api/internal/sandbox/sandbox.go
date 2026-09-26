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
	"sort"
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
	Wall       time.Duration
	MemoryMB   int64
	PidsLimit  int64
	CPUs       string
	MaxOutputB int64 // per-stream stdout/stderr cap (runner-enforced)
	FileSizeB  int64 // ulimit fsize
	ScratchMB  int64 // tmpfs /tmp size
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
	Mode   Mode
	Script string // ModeScript: baked-in script basename
	Code   string // ModeCode: free-form python
	Args   []string
	Inputs []InputFile
	Limits Limits
	// Labels are docker labels for the container, so its owner can find it
	// again (the agent runner reaps what a killed process left behind). With
	// labels the container also carries LabelDeadline; without them the
	// docker arguments are unchanged.
	Labels map[string]string
}

// HostGrace is how long past its wall limit a job may live before the host
// ceiling kills the docker CLI and removes the container.
const HostGrace = 30 * time.Second

// LabelDeadline is the unix second by which the host ceiling has removed the
// container: a labelled container still present after it has no live owner.
const LabelDeadline = "sran.sandbox.deadline_unix"

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
	// Runtime (FIX14 §2) is the isolation boundary this job ACTUALLY ran
	// under, as the job reported from inside itself ("gvisor" | "standard").
	// The system states this from the run, not from configuration.
	Runtime string
	cleanup func()
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
	// Runtime (FIX14 §2) is the REQUIRED OCI runtime — e.g. "runsc" (gVisor)
	// for a VM-grade boundary. Empty = the default runc (standard container,
	// backward-compatible). When set, every run passes `--runtime=<Runtime>`
	// AND the result is refused unless the job self-reports (from inside) that
	// it actually executed under that runtime — so a silent fall-back to the
	// weaker runtime is impossible, never merely assumed from config. Env:
	// SANDBOX_RUNTIME.
	Runtime string
}

// expectedInsideRuntime maps the requested OCI runtime to the from-inside
// signature `sandbox_runner.py._detect_runtime` reports. "" (runc) → "standard";
// "runsc" → "gvisor". Any other non-empty runtime must at least NOT be
// "standard" (some hardened boundary), which the caller enforces.
func expectedInsideRuntime(runtime string) string {
	switch runtime {
	case "":
		return "standard"
	case "runsc":
		return "gvisor"
	default:
		return "" // unknown: enforce only "must not be standard"
	}
}

// Runner executes sandbox jobs. Safe for concurrent use — each Execute
// stages an isolated per-run directory.
type Runner struct {
	cfg Config
}

var (
	// ErrUnavailable means the sandbox can't run (docker/image/config
	// missing). Security-sensitive callers must fail closed.
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

// DockerBin is the docker CLI this runner calls.
func (r *Runner) DockerBin() string { return r.cfg.DockerBin }

// WorkDir is the staging root as this process writes it; each job's
// workspace is WorkDir/<the container name without "sbx-">.
func (r *Runner) WorkDir() string { return r.cfg.WorkDir }

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
	// FIX14 §2 — the startup boundary check. When a hardened runtime is
	// REQUIRED, prove at boot (not from config) that it is the one actually in
	// effect by running a probe under it and reading the runtime from inside.
	// If it is unavailable or fell back, the sandbox is UNAVAILABLE — the
	// service logs the boundary loudly and every job refuses, rather than
	// running under a weaker boundary the operator believes is stronger.
	if r.cfg.Runtime != "" {
		actual, verr := r.VerifyBoundary(ctx)
		if verr != nil {
			return fmt.Errorf("%w: required runtime %q not verifiable at startup: %v — %s", ErrUnavailable, r.cfg.Runtime, verr, runtimeFixHint(r.cfg.Runtime))
		}
		if berr := r.enforceRuntime(actual); berr != nil {
			return fmt.Errorf("%w — %s", berr, runtimeFixHint(r.cfg.Runtime))
		}
	}
	return nil
}

// runtimeFixHint (FIX17 §5) turns the startup refusal from "this is
// misconfigured" into "here is how to fix it": it names the script that
// installs the runtime on the Docker host, the one that checks the boundary
// from inside a container, and the deliberate alternative (dev mode).
func runtimeFixHint(runtime string) string {
	if runtime == "runsc" {
		return "to fix: install gVisor on the Docker host with `sudo bash scripts/install-runsc.sh`, confirm with `bash scripts/verify-sandbox-runtime.sh --expect gvisor`, then restart this service; or unset SANDBOX_RUNTIME to run the standard container boundary (dev mode). See docs/DEPLOY_SANDBOX.md"
	}
	return fmt.Sprintf("to fix: install and register runtime %q with the Docker daemon on this host (scripts/install-runsc.sh covers runsc/gVisor), confirm with `bash scripts/verify-sandbox-runtime.sh`, then restart this service; or unset SANDBOX_RUNTIME to run the standard container boundary (dev mode). See docs/DEPLOY_SANDBOX.md", runtime)
}

// VerifyBoundary runs a minimal probe container under the configured runtime
// and returns the isolation boundary as measured FROM INSIDE it ("gvisor" |
// "standard"). This is how the system states which runtime is in effect from a
// real run rather than from configuration. An error means the runtime could
// not be exercised at all (e.g. an unknown/uninstalled runtime name — docker
// rejects `--runtime`), which the callers treat as "refuse", never "fall back".
func (r *Runner) VerifyBoundary(ctx context.Context) (string, error) {
	cctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	args := []string{"run", "--rm"}
	if r.cfg.Runtime != "" {
		args = append(args, "--runtime", r.cfg.Runtime)
	}
	args = append(args,
		"--network", "none",
		"--user", "65534:65534",
		"--cap-drop", "ALL",
		"--security-opt", "no-new-privileges",
		"--read-only",
		"--entrypoint", "cat",
		r.cfg.Image, "/proc/version",
	)
	out, err := exec.CommandContext(cctx, r.cfg.DockerBin, args...).CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("boundary probe failed: %s", strings.TrimSpace(string(out)))
	}
	if strings.Contains(strings.ToLower(string(out)), "gvisor") {
		return "gvisor", nil
	}
	return "standard", nil
}

// runnerMeta mirrors sandbox_runner.py's __meta.json sidecar.
type runnerMeta struct {
	Exit      int    `json:"exit"`
	Truncated bool   `json:"truncated"`
	TimedOut  bool   `json:"timed_out"`
	Error     string `json:"error"`
	// Runtime (FIX14 §2) — the boundary the job measured from inside itself.
	Runtime string `json:"runtime"`
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
	if err := os.MkdirAll(localIn, 0o755); err != nil {
		return nil, fmt.Errorf("sandbox: mkdir in: %w", err)
	}
	if err := os.MkdirAll(localOut, 0o777); err != nil {
		return nil, fmt.Errorf("sandbox: mkdir out: %w", err)
	}
	// The container runs --user 65534:65534, so it must be able to
	// TRAVERSE+READ /job/in (0755: it only reads the read-only inputs) and
	// WRITE /job/out (0777). Chmod after MkdirAll defeats the process umask
	// (MkdirAll applies mode & ~umask, which can strip the group/other bits
	// uid-65534 needs). Bug 2: 'in' at 0700 made job.json unreadable ->
	// 'Permission denied: /job/in/job.json' -> 502 on every run.
	_ = os.Chmod(localIn, 0o755)
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
	hostRun := resolveHostRunDir(r.cfg.WorkDir, r.cfg.HostWorkDir, localRun, runID)
	hostIn := joinHost(hostRun, "in")
	hostOut := joinHost(hostRun, "out")

	containerName := "sbx-" + runID
	var labels map[string]string
	if len(spec.Labels) > 0 {
		labels = make(map[string]string, len(spec.Labels)+1)
		for k, v := range spec.Labels {
			labels[k] = v
		}
		labels[LabelDeadline] = strconv.FormatInt(time.Now().Add(limits.Wall+HostGrace).Unix(), 10)
	}
	args := r.dockerArgs(limits, hostIn, hostOut, containerName, labels)

	// Hard host-side ceiling: the runner self-kills at limits.Wall; give
	// docker startup + teardown grace, then SIGKILL the whole run.
	cctx, cancel := context.WithTimeout(ctx, limits.Wall+HostGrace)
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
	// FIX14 §2 — no silent fall-back. When a hardened runtime is REQUIRED,
	// the job must have executed under it, proven from INSIDE the run (the
	// meta.Runtime the job measured), not from the --runtime flag we passed or
	// any deployment assumption. A mismatch means we would believe the boundary
	// is stronger than it is — refuse the result rather than return it.
	if boundaryErr := r.enforceRuntime(meta.Runtime); boundaryErr != nil {
		cleanup()
		return nil, boundaryErr
	}

	if meta.Error != "" {
		res := &Result{Stdout: stdout, Stderr: stderrb, Exit: meta.Exit,
			Truncated: meta.Truncated, TimedOut: meta.TimedOut, Runtime: meta.Runtime, cleanup: cleanup}
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
		Runtime:   meta.Runtime,
		cleanup:   cleanup,
	}, nil
}

// enforceRuntime is the no-silent-fall-back gate (FIX14 §2). Given the runtime
// the job reported from inside itself, it returns an error (refusing the run)
// when a REQUIRED hardened runtime was not the one that actually executed.
// runc/default (Runtime=="") never refuses — it is the standard boundary.
func (r *Runner) enforceRuntime(insideActual string) error {
	if r.cfg.Runtime == "" {
		return nil // standard container: nothing to enforce
	}
	if insideActual == "" {
		insideActual = "standard" // a job that couldn't self-report is treated as the weak case
	}
	expected := expectedInsideRuntime(r.cfg.Runtime)
	if expected != "" {
		if insideActual != expected {
			return fmt.Errorf("%w: required runtime %q (expected in-job %q) but the job executed under %q — refusing rather than run under a weaker boundary",
				ErrUnavailable, r.cfg.Runtime, expected, insideActual)
		}
		return nil
	}
	// Unknown hardened runtime: we can't name its signature, but it must not be
	// the plain standard container — that would be a fall-back to runc.
	if insideActual == "standard" {
		return fmt.Errorf("%w: required runtime %q but the job executed under a standard container — refusing (silent fall-back)",
			ErrUnavailable, r.cfg.Runtime)
	}
	return nil
}

// dockerArgs builds the immutable lockdown flag set + mounts. Every value
// here is a server constant or a server-derived limit — no model input.
func (r *Runner) dockerArgs(l Limits, hostIn, hostOut, name string, labels map[string]string) []string {
	mem := strconv.FormatInt(l.MemoryMB, 10) + "m"
	args := []string{
		"run", "--rm",
		"--name", name,
	}
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		args = append(args, "--label", k+"="+labels[k])
	}
	// FIX14 §2 — the hardened OCI runtime, when configured. docker itself
	// errors on an unknown runtime name (it does NOT silently fall back), and
	// the from-inside meta check below catches any other divergence.
	if r.cfg.Runtime != "" {
		args = append(args, "--runtime", r.cfg.Runtime)
	}
	args = append(args,
		"--network", "none",
		"--user", "65534:65534",
		"--cap-drop", "ALL",
		"--security-opt", "no-new-privileges",
	)
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

// resolveHostRunDir maps THIS process's per-run staging dir (localRun, under
// WorkDir) to the path the DOCKER DAEMON must bind-mount with -v.
//
// This is the subtle, easy-to-invert distinction that broke the sandbox
// (Section 5 / run_python 503): SANDBOX_HOST_WORKDIR must be the HOST path the
// shared volume is bind-mounted FROM (as the daemon sees it), NOT the
// in-container staging path (== WorkDir). If HostWorkDir is set to the
// container path, this returns localRun and the daemon -v's a path that only
// exists inside chat-edge — it then creates an empty root-owned dir on the
// host, the uid-65534 sandbox can't write __meta.json, and Execute reports
// "no result sidecar" (503).
//
//   - HostWorkDir == WorkDir  (dev/standalone: docker shares this process's
//     mount namespace) → the paths are identical; use localRun as-is.
//   - HostWorkDir != WorkDir  (chat-edge is itself a container) → the daemon's
//     -v source is joinHost(HostWorkDir, runID), i.e. the HOST inode that
//     backs this container's localRun via the shared bind mount.
func resolveHostRunDir(workDir, hostWorkDir, localRun, runID string) string {
	if hostWorkDir != workDir {
		return joinHost(hostWorkDir, runID)
	}
	return localRun
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
