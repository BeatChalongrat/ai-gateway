package service

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

// ---- fakes -----------------------------------------------------------------

type fakeSandboxRunner struct {
	calls int
	spec  sandbox.Spec
	res   *sandbox.Result
	err   error
}

func (f *fakeSandboxRunner) Execute(_ context.Context, spec sandbox.Spec) (*sandbox.Result, error) {
	f.calls++
	f.spec = spec
	return f.res, f.err
}

type fakeCodeResolver struct {
	calls      int
	err        error
	path, name string
}

func (f *fakeCodeResolver) OpenConversationFileCopy(
	_ context.Context, _, _, _, _ uuid.UUID,
) (string, string, func(), error) {
	f.calls++
	if f.err != nil {
		return "", "", nil, f.err
	}
	return f.path, f.name, func() {}, nil
}

type fakeGeneratedFileSaver struct {
	calls  int
	inputs []GeneratedFileInput
}

func (f *fakeGeneratedFileSaver) SaveGeneratedFile(
	_ context.Context, in GeneratedFileInput,
) (*model.ConversationFile, error) {
	f.calls++
	f.inputs = append(f.inputs, in)
	return &model.ConversationFile{ID: uuid.New()}, nil
}

func newCodeExecutor(runner sandboxRunner, resolver codeFileResolver, saver generatedFileSaver) *ToolExecutor {
	e := &ToolExecutor{}
	e.SetCodeInterpreter(NewCodeInterpreter(runner), resolver, saver)
	return e
}

func baseExecCtx() ToolExecContext {
	return ToolExecContext{
		TenantID:       uuid.New(),
		UserID:         uuid.New(),
		ConversationID: uuid.New(),
		MessageID:      uuid.New(),
	}
}

// ---- tests -----------------------------------------------------------------

// run_python must reject a file the resolver denies (cross-user /
// cross-conversation) BEFORE the sandbox ever runs.
func TestExecRunPython_DeniesForeignFileBeforeSandbox(t *testing.T) {
	runner := &fakeSandboxRunner{res: &sandbox.Result{}}
	resolver := &fakeCodeResolver{err: ErrFileNotFound}
	saver := &fakeGeneratedFileSaver{}
	e := newCodeExecutor(runner, resolver, saver)

	foreign := uuid.New().String()
	out := e.execRunPython(context.Background(), "call-1",
		`{"code":"print(1)","files":["`+foreign+`"]}`, baseExecCtx())

	if runner.calls != 0 {
		t.Fatalf("sandbox was called despite a denied file (calls=%d)", runner.calls)
	}
	if !strings.Contains(out, "can't access") {
		t.Fatalf("expected an access-denied message, got: %s", out)
	}
	if resolver.calls != 1 {
		t.Fatalf("resolver should have been consulted once, got %d", resolver.calls)
	}
}

// Happy path: runs ModeCode with the SERVER limits, returns stdout, and
// persists a png artifact as a generated file attached to the message.
func TestExecRunPython_RunsAndSavesArtifacts(t *testing.T) {
	tmp := t.TempDir()
	pngPath := filepath.Join(tmp, "chart.png")
	if err := os.WriteFile(pngPath, []byte("\x89PNGfakebytes"), 0o644); err != nil {
		t.Fatal(err)
	}
	runner := &fakeSandboxRunner{res: &sandbox.Result{
		Stdout:    []byte("pandas 2.2.0\n"),
		Exit:      0,
		Artifacts: []sandbox.Artifact{{Name: "chart.png", Path: pngPath}},
	}}
	saver := &fakeGeneratedFileSaver{}
	e := newCodeExecutor(runner, &fakeCodeResolver{}, saver)
	ec := baseExecCtx()

	out := e.execRunPython(context.Background(), "call-1",
		`{"code":"import pandas; print(pandas.__version__)"}`, ec)

	if runner.calls != 1 {
		t.Fatalf("expected 1 sandbox run, got %d", runner.calls)
	}
	if runner.spec.Mode != sandbox.ModeCode {
		t.Fatalf("expected ModeCode, got %q", runner.spec.Mode)
	}
	if !strings.Contains(runner.spec.Code, "pandas.__version__") {
		t.Fatalf("model code not forwarded to sandbox: %q", runner.spec.Code)
	}
	// Server-enforced limits — never client-supplied.
	if runner.spec.Limits != codeInterpreterLimits {
		t.Fatalf("sandbox limits were not the server constants: %+v", runner.spec.Limits)
	}
	if saver.calls != 1 {
		t.Fatalf("expected the png artifact to be saved once, got %d", saver.calls)
	}
	got := saver.inputs[0]
	if got.ContentType != "image/png" || got.OriginalFilename != "chart.png" {
		t.Fatalf("artifact saved with wrong metadata: ct=%q name=%q", got.ContentType, got.OriginalFilename)
	}
	if got.MessageID != ec.MessageID {
		t.Fatalf("artifact not attached to the assistant message")
	}
	if !strings.Contains(out, "Exit code: 0") || !strings.Contains(out, "pandas 2.2.0") {
		t.Fatalf("result text missing stdout/exit: %s", out)
	}
	if !strings.Contains(out, "chart.png") {
		t.Fatalf("result text should note the attached image: %s", out)
	}
	if !strings.Contains(out, "file_id=") || !strings.Contains(out, "run_python files") {
		t.Fatalf("the next tool call needs a persisted file reference: %s", out)
	}
}

func TestExecRunPython_EmptyCodeNoRun(t *testing.T) {
	runner := &fakeSandboxRunner{res: &sandbox.Result{}}
	e := newCodeExecutor(runner, &fakeCodeResolver{}, &fakeGeneratedFileSaver{})

	out := e.execRunPython(context.Background(), "call-1", `{"code":"   "}`, baseExecCtx())

	if runner.calls != 0 {
		t.Fatalf("sandbox ran on empty code")
	}
	if !strings.Contains(out, "need Python code") {
		t.Fatalf("unexpected message: %s", out)
	}
}

// On the non-stream path (no assistant message id) the run still executes
// but artifacts can't be attached.
func TestExecRunPython_NonStreamSkipsArtifactSave(t *testing.T) {
	tmp := t.TempDir()
	pngPath := filepath.Join(tmp, "chart.png")
	_ = os.WriteFile(pngPath, []byte("bytes"), 0o644)
	runner := &fakeSandboxRunner{res: &sandbox.Result{
		Stdout:    []byte("ok"),
		Artifacts: []sandbox.Artifact{{Name: "chart.png", Path: pngPath}},
	}}
	saver := &fakeGeneratedFileSaver{}
	e := newCodeExecutor(runner, &fakeCodeResolver{}, saver)
	ec := baseExecCtx()
	ec.MessageID = uuid.Nil // non-stream

	out := e.execRunPython(context.Background(), "call-1", `{"code":"print('ok')"}`, ec)

	if runner.calls != 1 {
		t.Fatalf("expected the run to execute, calls=%d", runner.calls)
	}
	if saver.calls != 0 {
		t.Fatalf("artifacts must not be saved without a message id")
	}
	if !strings.Contains(out, "can't be attached") {
		t.Fatalf("expected a no-attach note, got: %s", out)
	}
}

// run_python must not be offered when the interpreter isn't wired.
func TestToolDefs_RunPythonHiddenWhenUnwired(t *testing.T) {
	e := &ToolExecutor{}
	for _, d := range e.ToolDefs(&model.Conversation{}) {
		if d.Function.Name == "run_python" {
			t.Fatal("run_python offered without a wired interpreter")
		}
	}
	e.SetCodeInterpreter(NewCodeInterpreter(&fakeSandboxRunner{}), &fakeCodeResolver{}, &fakeGeneratedFileSaver{})
	found := false
	for _, d := range e.ToolDefs(&model.Conversation{}) {
		if d.Function.Name == "run_python" {
			found = true
		}
	}
	if !found {
		t.Fatal("run_python not offered after wiring")
	}
}
