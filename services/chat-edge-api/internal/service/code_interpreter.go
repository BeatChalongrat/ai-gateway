// Code interpreter — Phase 13 Part B.
//
// The model-facing run_python tool runs free-form model-authored python
// inside Part A's hardened sandbox (internal/sandbox): --network none,
// non-root, cap-drop ALL, seccomp default-deny, read-only rootfs,
// stripped env, resource-capped. Nothing model-authored ever runs in the
// secret-bearing chat-edge process.
//
// This file holds the SHARED core both callers use:
//   - the run_python chat tool (tools.go execRunPython) on the web/chat
//     path, and
//   - the POST /code/run HTTP endpoint (handler/code.go) the desktop
//     agent's thin-client RunPythonTool calls.
//
// The core is deliberately dumb: it clamps to SERVER-enforced limits,
// runs ModeCode, and hands back the raw Result. File resolution
// (ownership-checked) and artifact persistence live in the callers, which
// differ (a chat tool attaches artifacts to the assistant message; the
// desktop endpoint returns them inline). Part C's DLP egress gate
// inspects the returned stdout/artifacts — the runner stays dumb.
package service

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

// sandboxRunner is the slice of *sandbox.Runner the code interpreter
// needs. An interface (not the concrete Runner) so the executor + endpoint
// are unit-testable without docker — the live tests use the real Runner.
type sandboxRunner interface {
	Execute(ctx context.Context, spec sandbox.Spec) (*sandbox.Result, error)
}

// Server-enforced run_python ceilings. These are constants — a caller
// (tool args OR the desktop request) can NEVER widen them; the sandbox's
// own Clamp() is a second belt on top. run_python gets a bit more room
// than plan-exec's DefaultLimits (compute workloads load pandas/numpy),
// but stays well under the sandbox hard caps (maxWall 300s / maxMem 2GiB).
// FIX78 — limits measured against THIS host, not copied from Anthropic's
// page: their sandbox gives 5 GiB RAM / 5 GiB disk / 90 s; our Docker VM
// has 15.2 GiB total shared with the whole 20-container stack, so what
// this machine can honestly serve is 2 GiB × maxConcurrentCodeRuns = 6 GiB
// worst-case (tmpfs scratch pages are charged to the same cgroup memory
// cap, so scratch cannot add on top). Wall goes to Anthropic's 90 s per
// cell. A real server with ≥32 GiB dedicated could raise MemoryMB toward
// their 5 GiB — raise maxConcurrentCodeRuns and this together, never one
// alone.
var codeInterpreterLimits = sandbox.Limits{
	Wall:       90 * time.Second,
	MemoryMB:   2048,
	PidsLimit:  256,
	CPUs:       "1.0",
	MaxOutputB: 256 << 10, // 256 KiB per stream — the model doesn't need more
	FileSizeB:  64 << 20,  // 64 MiB per produced artifact
	ScratchMB:  1024,
}

// maxConcurrentCodeRuns bounds simultaneous sandbox containers. Before
// this there was NO limit — N users meant N containers, and the ceiling
// that matters is limit × concurrency, not limit. 3 × 2 GiB = 6 GiB of
// the 15.2 GiB VM. Waiters queue on the caller's context (a cancelled
// turn stops waiting).
const maxConcurrentCodeRuns = 3

var codeRunSlots = make(chan struct{}, maxConcurrentCodeRuns)

const (
	// maxCodeBytes bounds the model-authored source shipped to the sandbox.
	maxCodeBytes = 256 << 10
	// maxArtifacts caps how many /job/out files we surface per run.
	maxArtifacts = 8
	// maxArtifactBytes caps a single artifact we read back + persist.
	maxArtifactBytes = 16 << 20 // 16 MiB
)

// CodeInterpreter runs model-authored python in the hardened sandbox with
// server-enforced limits. Safe for concurrent use (the underlying Runner
// stages an isolated per-run dir).
type CodeInterpreter struct {
	runner sandboxRunner
}

// NewCodeInterpreter wires the interpreter to a sandbox runner. Callers
// build this ONLY when the sandbox is available (SANDBOX_ENABLED=1) —
// run_python must never fall back to in-process execution.
func NewCodeInterpreter(runner sandboxRunner) *CodeInterpreter {
	return &CodeInterpreter{runner: runner}
}

// Run executes the given python under the identical Part-A lockdown, with
// the supplied read-only inputs mounted at /job/in. Limits are the
// server constants — the caller supplies NO limits. The returned Result
// MUST have Cleanup() called by the caller (defer) once artifacts are
// consumed.
func (c *CodeInterpreter) Run(
	ctx context.Context, code string, inputs []sandbox.InputFile,
) (*sandbox.Result, error) {
	// FIX78 — the concurrency gate that makes the 2 GiB per-run cap an
	// actual bound on the host (limit × concurrency, see the constant).
	select {
	case codeRunSlots <- struct{}{}:
		defer func() { <-codeRunSlots }()
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	return c.runner.Execute(ctx, sandbox.Spec{
		Mode:   sandbox.ModeCode,
		Code:   code,
		Inputs: inputs,
		Limits: codeInterpreterLimits,
	})
}

// artifactContentType maps a produced artifact's basename to a MIME type
// for persistence. Images get an image/* type so the existing
// message_files SSE path renders them inline (same as generate_image);
// everything else gets a best-effort office/data type and shows as a
// downloadable file chip.
func ArtifactContentType(name string) string {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".png":
		return "image/png"
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".gif":
		return "image/gif"
	case ".webp":
		return "image/webp"
	case ".csv":
		return "text/csv"
	case ".json":
		return "application/json"
	case ".txt", ".log":
		return "text/plain"
	case ".md", ".markdown":
		return "text/markdown"
	case ".html", ".htm":
		return "text/html"
	case ".pdf":
		return "application/pdf"
	case ".xlsx":
		return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
	case ".docx":
		return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
	case ".pptx":
		return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
	default:
		return "application/octet-stream"
	}
}

// IsImageArtifact reports whether an artifact should render inline as an
// image (vs a download chip).
func IsImageArtifact(name string) bool {
	return strings.HasPrefix(ArtifactContentType(name), "image/")
}

// readArtifact reads a sandbox artifact off the per-run scratch dir,
// capped at maxArtifactBytes so a runaway output file can't blow memory.
// The fsize ulimit already bounds the file on the sandbox side; this is a
// belt on the read.
func ReadArtifact(path string) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxArtifactBytes))
	if err != nil {
		return nil, err
	}
	if len(data) == 0 {
		return nil, fmt.Errorf("empty artifact")
	}
	return data, nil
}
