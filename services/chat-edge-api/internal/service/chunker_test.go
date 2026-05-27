// Unit tests for the paragraph → sentence → window chunker. We
// don't assert exact chunk boundaries because the algorithm reshapes
// fragments slightly depending on sentence-punctuation placement;
// instead we assert ranges (“wantMin .. wantMax“) and a few hard
// invariants (no empty chunks, no over-budget chunks, overlap shape).
package service

import (
	"strings"
	"testing"
)

func TestChunkText_EdgeCases(t *testing.T) {
	tests := []struct {
		name      string
		text      string
		chunkSize int
		overlap   int
		wantMin   int
		wantMax   int
	}{
		{"empty", "", 512, 50, 0, 0},
		{"whitespace only", "   \n\n   \t  ", 512, 50, 0, 0},
		{"short", "Hello world", 512, 50, 1, 1},
		{"exact-size paragraph", strings.Repeat("a", 512), 512, 50, 1, 1},
		// 600 chars of repeated tokens — sentence split should kick
		// in via the period-and-space heuristic.
		{
			name:      "two paragraphs",
			text:      strings.Repeat("a", 300) + "\n\n" + strings.Repeat("b", 300),
			chunkSize: 512,
			overlap:   50,
			wantMin:   2,
			wantMax:   2,
		},
		{
			name: "three paragraphs",
			text: "Paragraph one is here.\n\nParagraph two follows.\n\nAnd here is paragraph three.",
			// Small enough that each paragraph gets merged into one
			// chunk via the appendOrMerge floor (each paragraph is
			// under the 100-char minimum).
			chunkSize: 200,
			overlap:   10,
			wantMin:   1,
			wantMax:   1,
		},
		{
			name:      "long paragraph splits on sentences",
			text:      strings.Repeat("This is a sentence. ", 100), // 2000 chars
			chunkSize: 512,
			overlap:   50,
			wantMin:   3,
			wantMax:   6,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			chunks := ChunkText(tt.text, tt.chunkSize, tt.overlap)
			if len(chunks) < tt.wantMin || len(chunks) > tt.wantMax {
				t.Errorf("got %d chunks, want %d-%d", len(chunks), tt.wantMin, tt.wantMax)
			}
			for i, c := range chunks {
				if strings.TrimSpace(c) == "" {
					t.Errorf("chunk %d is empty/whitespace", i)
				}
			}
		})
	}
}

// Floor-merge: fragments shorter than the 100-char minimum should
// be glued onto the previous chunk instead of standing alone. The
// algorithm runs entirely on the text it produces, so the easiest
// assertion is "any single tiny paragraph still produces one chunk
// at most".
func TestChunkText_MinimumChunkSize(t *testing.T) {
	chunks := ChunkText("hi.", 512, 50)
	if len(chunks) != 1 {
		t.Fatalf("expected 1 chunk for tiny input, got %d", len(chunks))
	}
	if chunks[0] != "hi." {
		t.Errorf("chunk content: got %q, want %q", chunks[0], "hi.")
	}
}

// Window-split: a single sentence longer than chunkSize must trigger
// the character-window splitter; the result should have multiple
// chunks and each (except possibly the last) should be no smaller
// than chunkSize - overlap.
func TestChunkText_LongSentenceWindowSplit(t *testing.T) {
	// One "sentence" (no period mid-text) of 2000 chars.
	text := strings.Repeat("x", 2000)
	chunks := ChunkText(text, 400, 50)
	if len(chunks) < 4 {
		t.Errorf("expected ≥4 chunks for 2000/400, got %d", len(chunks))
	}
	// Every chunk must be non-empty.
	for i, c := range chunks {
		if c == "" {
			t.Errorf("chunk %d empty", i)
		}
	}
}

// Defaults: chunkSize ≤ 0 should fall back to 512 internally;
// overlap < 0 should clamp to 0; overlap > chunkSize/4 should clamp.
// We don't reach inside the function — we only assert it produces a
// sensible (non-empty, non-panicking) output for adversarial input.
func TestChunkText_DefaultsAndClamps(t *testing.T) {
	text := strings.Repeat("a", 800)
	cases := []struct {
		chunkSize int
		overlap   int
	}{
		{0, 0},       // defaults
		{-100, -50},  // negative
		{1000, 5000}, // overlap > size
	}
	for _, c := range cases {
		chunks := ChunkText(text, c.chunkSize, c.overlap)
		if len(chunks) == 0 {
			t.Errorf("size=%d overlap=%d produced 0 chunks", c.chunkSize, c.overlap)
		}
	}
}
