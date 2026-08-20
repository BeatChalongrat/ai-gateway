package service

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// The corpus is SHARED with the desktop client's estimator
// (SranDesktop/src/SranDesktop.Tests/Agent/TokenEstimatorCorpusTests.cs
// reads this same file). Both suites assert the same expectations, so
// changing one implementation's model breaks the other's tests instead of
// letting the two clients silently disagree about what fits in a context
// window — which is exactly how the desktop side came to under-count Thai
// by ~2.7x.
const corpusRelPath = "../../../../tests/fixtures/token_estimator_corpus.json"

type corpusCase struct {
	Name   string `json:"name"`
	Text   string `json:"text"`
	Tokens int    `json:"tokens"`
	Note   string `json:"note"`
}

type corpusFile struct {
	Cases []corpusCase `json:"cases"`
}

func loadCorpus(t *testing.T) []corpusCase {
	t.Helper()
	path := filepath.FromSlash(corpusRelPath)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read shared corpus %s: %v", path, err)
	}
	var parsed corpusFile
	if err := json.Unmarshal(raw, &parsed); err != nil {
		t.Fatalf("parse shared corpus: %v", err)
	}
	if len(parsed.Cases) == 0 {
		t.Fatal("shared corpus has no cases — wrong file?")
	}
	return parsed.Cases
}

func TestEstimateTokens_SharedCorpus(t *testing.T) {
	for _, c := range loadCorpus(t) {
		t.Run(c.Name, func(t *testing.T) {
			if got := EstimateTokens(c.Text); got != c.Tokens {
				t.Errorf("EstimateTokens(%q) = %d, corpus says %d\n%s",
					c.Text, got, c.Tokens, c.Note)
			}
		})
	}
}

// The corpus must keep covering the scripts the product actually serves;
// a future edit that quietly drops the Thai or the astral-emoji case would
// remove the guard rather than fail it.
func TestSharedCorpus_CoversTheScriptsWeCareAbout(t *testing.T) {
	seen := map[string]bool{}
	for _, c := range loadCorpus(t) {
		seen[c.Name] = true
	}
	for _, required := range []string{
		"empty", "pure_ascii", "pure_thai", "mixed_thai_ascii",
		"thai_numerals_and_punctuation", "emoji", "cjk",
	} {
		if !seen[required] {
			t.Errorf("shared corpus is missing the %q case", required)
		}
	}
}
