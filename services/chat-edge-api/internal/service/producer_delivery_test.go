package service

// FIX81 — the rule, pinned: a thing the assistant produced is a durable,
// previewable, downloadable file in the conversation ("Files remain
// available for download throughout your conversation"). FIX80 found six
// delivery paths instead of one rule; this test is the thing that stops a
// SEVENTH producer from being wrong.
//
// Mechanism: a registry of every tool-executor function that produces
// user-facing bytes, each required to call its SaveGenerated* door. Then
// the tripwire: ANY exec* function whose body touches byte-producing
// markers must be in the registry (or the documented card-only list) —
// so adding a new media/file producer without wiring persistence fails
// here with instructions, instead of shipping another data URI that dies
// with the session.

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// producers: exec function → the delivery door its body must call.
var producerDeliveries = map[string]string{
	"execGenerateImage": "SaveGeneratedImage",
	"execGenerateVideo": "SaveGeneratedVideo",
	"execRunPython":     "SaveGeneratedFile",
}

// byteMarkers — evidence a function produces user-facing bytes.
var byteMarkers = []string{
	"DataURI", "data:image", "base64.StdEncoding", "res.Artifacts",
	"FetchVideoOutput", "ImageB64", "SaveGenerated",
}

func toolsSource(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile("tools.go")
	if err != nil {
		t.Fatalf("read tools.go: %v", err)
	}
	return string(raw)
}

// execBodies splits tools.go into (name → body) for every ToolExecutor
// method. Crude on purpose: bodies run to the next top-level func, which
// over-includes trailing helpers — fine for marker scanning (it can only
// widen the net, the safe direction).
func execBodies(src string) map[string]string {
	out := map[string]string{}
	re := regexp.MustCompile(`(?m)^func \(e \*ToolExecutor\) (exec\w+)\(`)
	locs := re.FindAllStringSubmatchIndex(src, -1)
	for i, loc := range locs {
		name := src[loc[2]:loc[3]]
		end := len(src)
		if i+1 < len(locs) {
			end = locs[i+1][0]
		}
		out[name] = src[loc[0]:end]
	}
	return out
}

func TestEveryRegisteredProducerCallsItsDeliveryDoor(t *testing.T) {
	bodies := execBodies(toolsSource(t))
	for fn, door := range producerDeliveries {
		body, ok := bodies[fn]
		if !ok {
			t.Errorf("%s is registered as a producer but no longer exists in tools.go — update producerDeliveries", fn)
			continue
		}
		if !strings.Contains(body, door) {
			t.Errorf("%s no longer calls %s — a produced output must persist as a ConversationFile (the FIX81 rule); route it through the SaveGenerated* pipeline", fn, door)
		}
	}
}

func TestTripwire_NewByteProducingToolMustRegisterADeliveryPath(t *testing.T) {
	bodies := execBodies(toolsSource(t))
	for fn, body := range bodies {
		if _, registered := producerDeliveries[fn]; registered {
			continue
		}
		for _, marker := range byteMarkers {
			if strings.Contains(body, marker) {
				t.Errorf("%s touches %q but is not in producerDeliveries — a new producer of user-facing bytes must persist through SaveGeneratedFile/Image/Video (one pipeline, FIX81) and be registered here. If it deliberately produces NO user-kept output, remove the marker use or extend this test with a documented exemption.", fn, marker)
				break
			}
		}
	}
}

// The /code/run door (the desktop's run_python) lives in the handler
// package; its persistence is pinned by handler/code_fix80_test.go. This
// cross-check only ensures the door itself still exists.
func TestCodeRunDoorStillPersists(t *testing.T) {
	raw, err := os.ReadFile("../handler/code.go")
	if err != nil {
		t.Fatalf("read handler/code.go: %v", err)
	}
	if !strings.Contains(string(raw), "SaveGeneratedFile") {
		t.Error("handler/code.go no longer persists artifacts via SaveGeneratedFile — the desktop's run_python delivery path (FIX80) is gone")
	}
}
