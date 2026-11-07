package service

import (
	"regexp"
	"strings"
	"testing"
)

// FIX132 B (live finding) — the gateway's system-prompt echo guard
// (backend/app/services/prompt_echo_guard.py) fingerprints INSTRUCTION text
// and stops at the first DATA section, which it recognises by a markdown
// heading (_DATA_HEADING_RE: "# retrieved…", "# knowledge…", "# attached…",
// …). A knowledge-base block that opened with "<knowledge_base name=…>" was
// read as instructions, so an answer quoting a passage word for word — what a
// grounded answer does — was cut as a "system prompt echo" (live: "The HX-4410
// impeller is part number 7731-A" vanished, leaving "."). The block must open
// with a data heading the guard knows.
var echoGuardDataHeading = regexp.MustCompile(`(?i)^#{1,6}\s*(retrieved|knowledge)`)

func TestFIX132_KnowledgeBlockOpensAsDataForTheEchoGuard(t *testing.T) {
	cases := map[string]KnowledgeBlockInput{
		"passages":    {KBName: "นโยบาย", Chunks: []RetrievedChunk{{Filename: "HX-4410-maintenance-manual.pdf", Content: "The HX-4410 impeller is part number 7731-A."}}},
		"pending":     {KBName: "นโยบาย", Pending: 2, PendingNames: []string{"a.pdf", "b.docx"}},
		"unavailable": {KBName: "นโยบาย", Unavailable: true},
	}
	for name, in := range cases {
		block, _ := BuildKnowledgeBlock(in)
		if block == "" {
			t.Fatalf("%s: empty block", name)
		}
		first := strings.TrimSpace(strings.SplitN(strings.TrimLeft(block, "\n"), "\n", 2)[0])
		if !echoGuardDataHeading.MatchString(first) {
			t.Errorf("%s: the block opens with %q — the echo guard would fingerprint the passages as instructions", name, first)
		}
	}
}
