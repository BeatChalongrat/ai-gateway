package runner

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

// bootstrap is the only code the sandbox runs: it puts the engine zip and
// the shared generator scripts on the path and hands over to the engine.
const bootstrap = `import sys
sys.path[0:0] = ['/job/in/am_engine.zip', '/sandbox/scripts']
from agent_market.engine.step import main
sys.exit(main())
`

// Executor runs one engine op in a fresh sandbox container — the stock
// run_python image, reused as a library (internal/sandbox). Every call gets
// its own workspace (in/ read-only, out/ writable), --network none, hard
// CPU/memory/pids/wall limits, and the workspace is deleted afterwards.
type Executor struct {
	SB     *sandbox.Runner
	Engine *Engine
	Limits sandbox.Limits

	mu    sync.Mutex
	calls map[string]bool // calls in progress (their containers' LabelCall)
}

// InFlight is a snapshot of the calls in progress.
func (x *Executor) InFlight() map[string]bool {
	x.mu.Lock()
	defer x.mu.Unlock()
	m := make(map[string]bool, len(x.calls))
	for k := range x.calls {
		m[k] = true
	}
	return m
}

func (x *Executor) track(call string, on bool) {
	x.mu.Lock()
	defer x.mu.Unlock()
	if x.calls == nil {
		x.calls = map[string]bool{}
	}
	if on {
		x.calls[call] = true
	} else {
		delete(x.calls, call)
	}
}

// Input is one file handed to a step.
type Input struct {
	Name      string // basename inside /job/in
	LocalPath string
	Bytes     []byte
}

// StepResult is the engine's JSON result plus the files it wrote.
type StepResult struct {
	Result    map[string]json.RawMessage
	Artifacts map[string]string // name → local path (valid until Cleanup)
	Elapsed   time.Duration
	Runtime   string
	cleanup   func()
}

// Cleanup removes the step's workspace.
func (r *StepResult) Cleanup() {
	if r != nil && r.cleanup != nil {
		r.cleanup()
	}
}

// Decode reads one result field.
func (r *StepResult) Decode(field string, v any) error {
	raw, ok := r.Result[field]
	if !ok {
		return fmt.Errorf("result has no %q", field)
	}
	return json.Unmarshal(raw, v)
}

// ErrStep is a step the engine itself reported as failed.
var ErrStep = errors.New("engine step failed")

// Run executes one op.
func (x *Executor) Run(ctx context.Context, request any, inputs []Input) (*StepResult, error) {
	req, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	in := []sandbox.InputFile{
		{Name: "am_engine.zip", LocalPath: x.Engine.ZipPath},
		{Name: "request.json", Bytes: req},
	}
	for _, i := range inputs {
		in = append(in, sandbox.InputFile{Name: i.Name, LocalPath: i.LocalPath, Bytes: i.Bytes})
	}
	// Every container carries the runner's labels and this call's own token,
	// registered before the container can exist (see Orphans).
	labels := map[string]string{LabelOwner: OwnerRunner}
	for k, v := range sandboxLabels(ctx) {
		labels[k] = v
	}
	call := uuid.NewString()
	labels[LabelCall] = call
	x.track(call, true)
	defer x.track(call, false)
	start := time.Now()
	res, err := x.SB.Execute(ctx, sandbox.Spec{Mode: sandbox.ModeCode, Code: bootstrap, Inputs: in, Limits: x.Limits, Labels: labels})
	if err != nil && res == nil {
		return nil, fmt.Errorf("sandbox: %w", err)
	}
	out := &StepResult{Artifacts: map[string]string{}, Elapsed: time.Since(start), cleanup: res.Cleanup}
	out.Runtime = res.Runtime
	if res.TimedOut {
		res.Cleanup()
		return nil, fmt.Errorf("%w: the step ran past its time limit (%s)", ErrStep, x.Limits.Wall)
	}
	line := lastJSONLine(res.Stdout)
	if line == nil {
		msg := strings.TrimSpace(string(res.Stderr))
		if len(msg) > 600 {
			msg = msg[len(msg)-600:]
		}
		res.Cleanup()
		return nil, fmt.Errorf("%w: no result (exit %d): %s", ErrStep, res.Exit, msg)
	}
	if err := json.Unmarshal(line, &out.Result); err != nil {
		res.Cleanup()
		return nil, fmt.Errorf("%w: bad result: %v", ErrStep, err)
	}
	var ok bool
	_ = json.Unmarshal(out.Result["ok"], &ok)
	if !ok {
		var msg string
		_ = json.Unmarshal(out.Result["error"], &msg)
		res.Cleanup()
		return nil, fmt.Errorf("%w: %s", ErrStep, msg)
	}
	for _, a := range res.Artifacts {
		out.Artifacts[a.Name] = a.Path
	}
	return out, nil
}

func lastJSONLine(b []byte) []byte {
	var last []byte
	sc := bufio.NewScanner(bytes.NewReader(b))
	sc.Buffer(make([]byte, 1<<20), 8<<20)
	for sc.Scan() {
		t := bytes.TrimSpace(sc.Bytes())
		if len(t) > 1 && t[0] == '{' {
			last = append([]byte(nil), t...)
		}
	}
	return last
}

// readArtifact loads a produced file.
func readArtifact(r *StepResult, name string) ([]byte, error) {
	p, ok := r.Artifacts[name]
	if !ok {
		return nil, fmt.Errorf("step produced no %s", name)
	}
	return os.ReadFile(p)
}
