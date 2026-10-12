package runner

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// AM15 — a test that needs the sandbox runs only where a lane names it.
//
// The integration tests of this package skip unless AGENT_MARKET_IT=1, and the gate's integration lane
// is lists of names in chunks (scripts/probes/*/it_lane.sh). A test that no chunk names is skipped by
// the unit lane and never asked for by the other: it fails nowhere. A test that was renamed leaves its
// old name in a chunk, which then matches nothing — and `go test -run` of nothing is "ok".
//
// So this test, which every unit lane runs, holds the two lists against each other: every test of this
// package that starts the integration environment (newIT) is named in a chunk of a lane the gate runs,
// and every name in those chunks is a test that exists.

// The lanes `scripts/gate.sh it` runs, in its order: two it names itself and the one it runs last
// (GATE_IT's default — AM16's since that card), with the lanes that one runs first (AM15's, which
// runs AM13's).
var gateLanes = []string{
	"scripts/probes/AM10/it_lane.sh",
	"scripts/probes/AM12/it_lane.sh",
	"scripts/probes/AM13/it_lane.sh",
	"scripts/probes/AM15/it_lane.sh",
	"scripts/probes/AM16/it_lane.sh",
}

// startsIT is the call that starts the integration environment (written in two parts, so that this
// file's own test is not read as one that makes it).
const startsIT = "newIT" + "(t)"

func TestEveryIntegrationTestIsInALaneOfTheGate(t *testing.T) {
	root := repoRoot(t)
	read := func(rel string) string {
		b, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil {
			t.Fatalf("%s: %v", rel, err)
		}
		return string(b)
	}
	// the gate runs these lanes: its own words
	gate := read("scripts/gate.sh")
	for _, want := range []string{"scripts/probes/AM10/it_lane.sh", "scripts/probes/AM12/it_lane.sh web", "GATE_IT:-scripts/probes/AM16/it_lane.sh"} {
		if !strings.Contains(gate, want) {
			t.Fatalf("scripts/gate.sh it no longer runs %q — this test reads the lanes it runs; bring the two together", want)
		}
	}
	if !strings.Contains(read("scripts/probes/AM16/it_lane.sh"), "scripts/probes/AM15/it_lane.sh\" |") {
		t.Fatal("scripts/probes/AM16/it_lane.sh no longer runs AM15's lane first")
	}
	if !strings.Contains(read("scripts/probes/AM15/it_lane.sh"), "scripts/probes/AM13/it_lane.sh\" asked") {
		t.Fatal("scripts/probes/AM15/it_lane.sh no longer runs AM13's lane first")
	}
	// every name in a chunk
	chunk := regexp.MustCompile(`(?m)^\s*'\^\((.*)\)\$'`)
	named := map[string]string{}
	for _, lane := range gateLanes {
		for _, m := range chunk.FindAllStringSubmatch(read(lane), -1) {
			for _, name := range strings.Split(m[1], "|") {
				named[name] = lane
			}
		}
	}
	if len(named) < 50 {
		t.Fatalf("only %d test names were read from the lanes — the reader of the lanes is broken", len(named))
	}
	// every test of this package, and whether it starts the integration environment
	files, _ := filepath.Glob("*_test.go")
	fn := regexp.MustCompile(`(?m)^func (Test[A-Za-z0-9_]+)\(t \*testing\.T\) \{`)
	integration := map[string]string{}
	exists := map[string]bool{}
	for _, f := range files {
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		text := string(src)
		at := fn.FindAllStringSubmatchIndex(text, -1)
		for i, m := range at {
			name := text[m[2]:m[3]]
			end := len(text)
			if i+1 < len(at) {
				end = at[i+1][0]
			}
			exists[name] = true
			if strings.Contains(text[m[1]:end], startsIT) {
				integration[name] = f
			}
		}
	}
	if len(integration) < 50 {
		t.Fatalf("only %d integration tests were found in this package — the reader of the source is broken", len(integration))
	}
	var unrun, gone []string
	for name, f := range integration {
		if _, ok := named[name]; !ok {
			unrun = append(unrun, name+" ("+f+")")
		}
	}
	for name, lane := range named {
		if !exists[name] {
			gone = append(gone, name+" ("+lane+")")
		}
	}
	sort.Strings(unrun)
	sort.Strings(gone)
	if len(unrun) > 0 {
		t.Errorf("%d integration test(s) are named in no chunk of the gate's lanes — nothing runs them:\n  %s\nadd each to a chunk of scripts/probes/AM15/it_lane.sh (or the lane of the card that writes it, added to gateLanes)",
			len(unrun), strings.Join(unrun, "\n  "))
	}
	if len(gone) > 0 {
		t.Errorf("%d name(s) in a chunk are not a test of this package — the chunk runs nothing for them and passes:\n  %s", len(gone), strings.Join(gone, "\n  "))
	}
}
