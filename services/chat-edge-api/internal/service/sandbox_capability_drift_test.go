package service

// FIX78 Part 2b — the sandbox's real contents, the image build, and what
// the MODEL is told must never disagree again. The old description said
// "numpy, pandas, matplotlib and openpyxl" while the image shipped
// thirteen libraries plus Thai OCR — capability paid for and switched off
// by a stale sentence (the sixth such find in this project). This test
// reads the Dockerfile's pip/apt lines and BOTH tool descriptions (this
// package's tools.go and the desktop's host-tools.ts — the FIX76
// read-the-other-surface precedent) and fails when any of them drops a
// capability the others still claim.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// capability → the package that provides it in the Dockerfile, and the
// keyword both tool descriptions must carry for the model to know.
var sandboxCapabilities = []struct {
	name        string
	dockerfile  string // must appear in Dockerfile.sandbox (pip or apt)
	description string // must appear in BOTH run_python descriptions
}{
	{"read/annotate PDFs", "pypdf", "PDF"},
	{"write Word files", "python-docx", "Word"},
	{"write PowerPoint files", "python-pptx", "PowerPoint"},
	{"read/write Excel", "openpyxl", "Excel"},
	{"OCR incl. Thai", "tesseract-ocr-tha", "OCR"},
	{"scientific computing", "scipy", "scipy"},
	{"statistical charts", "seaborn", "seaborn"},
	{"ML basics", "scikit-learn", "scikit-learn"},
	{"Thai/legacy CSV encodings", "charset-normalizer", "charset-normalizer"},
	{"Thai chart text (font default)", "Sarabun", "Thai text renders correctly"},
}

func readRepoFile(t *testing.T, parts ...string) string {
	t.Helper()
	p := filepath.Join(parts...)
	raw, err := os.ReadFile(p)
	if err != nil {
		t.Skipf("source not present at %s: %v", p, err)
	}
	return string(raw)
}

func TestSandboxCapabilities_ImageAndBothDescriptionsAgree(t *testing.T) {
	dockerfile := readRepoFile(t, "..", "..", "sandbox", "Dockerfile.sandbox")
	webDesc := toolsSource(t) // tools.go — runPythonToolDef's description lives here
	desktopDesc := readRepoFile(t, "..", "..", "..", "..", "apps", "desktop", "src", "main", "host-tools.ts")

	for _, c := range sandboxCapabilities {
		if !strings.Contains(dockerfile, c.dockerfile) {
			t.Errorf("capability %q: %q is gone from Dockerfile.sandbox — either restore it or remove the capability from BOTH run_python descriptions and this table", c.name, c.dockerfile)
		}
		if !strings.Contains(webDesc, c.description) {
			t.Errorf("capability %q: the WEB run_python description no longer mentions %q — the image ships it, so the model must be told (tools.go runPythonToolDef)", c.name, c.description)
		}
		if !strings.Contains(desktopDesc, c.description) {
			t.Errorf("capability %q: the DESKTOP run_python description no longer mentions %q (apps/desktop/src/main/host-tools.ts)", c.name, c.description)
		}
	}
}

// The conventions the descriptions state must stay true to the runner:
// /job/out is the artifact dir, Agg the backend advice.
func TestSandboxDescriptionConventionsMatchRunner(t *testing.T) {
	runner := readRepoFile(t, "..", "..", "sandbox", "runner", "sandbox_runner.py")
	if !strings.Contains(runner, `JOB_OUT = "/job/out"`) {
		t.Error("sandbox_runner.py no longer uses /job/out — every run_python description references it")
	}
	for _, src := range []string{toolsSource(t), readRepoFile(t, "..", "..", "..", "..", "apps", "desktop", "src", "main", "host-tools.ts")} {
		if !strings.Contains(src, "/job/out/") {
			t.Error("a run_python description dropped the /job/out/ convention")
		}
		if !strings.Contains(src, "matplotlib.use('Agg')") {
			t.Error("a run_python description dropped the Agg-backend advice")
		}
	}
}
