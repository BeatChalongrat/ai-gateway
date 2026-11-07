package service

// FIX95 Part A — the capability statement matches the renderer that decides.
//
// Layer 5 used to seed its CANNOT list, unconditionally and before any tool
// was consulted, with:
//
//	"show charts/graphs/visualizations inline in chat — only inside
//	 downloadable files"
//
// Both halves were false. apps/web-chat/src/components/canvas/canvasUtil.ts
// (canvasTypeForBlock) is the ground truth for what the thread does with a
// fenced block, and CodeBlock.tsx is the ground truth for how: an `svg` or a
// script-free `html` fence renders INLINE in the thread inside a static
// iframe, and mermaid / jsx / tsx / react / markdown get an "Open in Canvas"
// button. Nothing is confined to a downloadable file. The model was being
// told it could not do the thing the product was built to do.
//
// This test is the derivation the clause cannot do for itself: Go cannot
// import a TypeScript function, so instead it READS the renderer and fails
// when the two stop agreeing. Cross-tree in the FIX76/86/87/94 pattern —
// a change to the renderer in the web tree fails the Go lane.
//
// What it deliberately does NOT pin: the wording of the CAN clause. Sentences
// get rewritten; the invariant is that the prompt must not DENY a rendering
// path the renderer still supports.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func canvasUtilSource(t *testing.T) string {
	t.Helper()
	p := filepath.Join("..", "..", "..", "..", "apps", "web-chat", "src", "components", "canvas", "canvasUtil.ts")
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatalf("cannot read the renderer that decides (%s): %v", p, err)
	}
	return string(b)
}

// The tags canvasTypeForBlock turns into an artifact today. If this list and
// the file disagree, the renderer changed and every claim below is suspect.
var canvasTags = []string{"html", "htm", "markdown", "md", "svg", "mermaid", "jsx", "tsx", "react"}

func TestRendererStillTurnsFencedBlocksIntoArtifacts(t *testing.T) {
	src := canvasUtilSource(t)
	if !strings.Contains(src, "export function canvasTypeForBlock") {
		t.Fatal("canvasTypeForBlock is gone from canvasUtil.ts — the capability claim in Layer 5 rests on it")
	}
	for _, tag := range canvasTags {
		if !strings.Contains(src, `"`+tag+`"`) {
			t.Errorf("canvasUtil.ts no longer mentions the %q fence. If the renderer dropped it, "+
				"the artifact dialect and the Layer 5 CAN clause both need revisiting.", tag)
		}
	}
}

func TestCapabilityStatement_DoesNotDenyWhatTheThreadRenders(t *testing.T) {
	src := canvasUtilSource(t)
	// Only meaningful while the renderer still does this.
	if !strings.Contains(src, `l === "svg"`) || !strings.Contains(src, `l === "mermaid"`) {
		t.Skip("renderer changed shape; TestRendererStillTurnsFencedBlocksIntoArtifacts owns that failure")
	}
	pc := &PromptCompiler{}
	for _, tools := range [][]string{nil, {"run_python"}, {"run_python", "web_search", "web_fetch", "generate_image"}} {
		got := pc.capabilityStatement(CompileContext{ActiveTools: tools})
		cannot := got
		if i := strings.Index(got, "You CANNOT"); i >= 0 {
			cannot = got[i:]
		} else {
			cannot = "" // no denial line at all is fine
		}
		for _, banned := range []string{
			"show charts/graphs/visualizations inline",
			"only inside downloadable files",
		} {
			if strings.Contains(cannot, banned) {
				t.Errorf("tools=%v: the CANNOT list denies %q, which the thread does render "+
					"(svg and script-free html inline; mermaid/react/markdown in the canvas).\nblock:\n%s",
					tools, banned, got)
			}
		}
	}
}

func TestCapabilityStatement_ClaimsTheRenderedKind(t *testing.T) {
	pc := &PromptCompiler{}
	got := pc.capabilityStatement(CompileContext{})
	can := got
	if i := strings.Index(got, "You CANNOT"); i >= 0 {
		can = got[:i]
	}
	for _, want := range []string{"diagrams", "interactive pages"} {
		if !strings.Contains(can, want) {
			t.Errorf("the CAN list no longer mentions %q; the model is not being told it can do "+
				"what the thread renders.\nblock:\n%s", want, got)
		}
	}
}

func TestCapabilityStatement_NeverRendersAnEmptyDenial(t *testing.T) {
	pc := &PromptCompiler{}
	for _, tools := range [][]string{nil, {"run_python", "web_search", "web_fetch", "generate_image", "generate_video"}} {
		got := pc.capabilityStatement(CompileContext{ActiveTools: tools})
		if strings.Contains(got, "You CANNOT (be honest, never pretend you can): .") {
			t.Errorf("tools=%v: empty CANNOT list rendered as a sentence denying nothing:\n%s", tools, got)
		}
	}
}
