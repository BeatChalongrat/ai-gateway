// Chunker — paragraph → sentence → fixed-window text splitter for
// RAG indexing.
//
// Splitting strategy (largest-first):
//
//   1. Split the text on blank-line paragraph breaks.
//   2. For each paragraph: if it fits inside the chunk size, keep it
//      whole (best retrieval quality — semantically coherent unit).
//   3. If it overflows: split on sentence boundaries. A sentence
//      that's still too large gets chunked by character window.
//   4. Each character-window chunk overlaps the previous one by
//      ``overlap`` characters so a thought that straddles the
//      boundary still survives one chunk's worth of context.
//
// 100-char minimum: very-short fragments make poor retrieval anchors
// and dilute the index — they get appended to the previous chunk
// instead of standing alone.
package service

import (
	"strings"
	"unicode"
)

const (
	minChunkChars     = 100
	maxChunkSizeCeil  = 4096 // sanity cap so a misconfigured KB doesn't OOM
	maxOverlapDivisor = 4    // overlap is clamped to chunkSize / 4
)

// ChunkText slices ``text`` into RAG-ready chunks.
//
// chunkSize is the target character count per chunk (NOT a hard
// ceiling — paragraphs slightly over are kept whole). Overlap is the
// number of trailing characters from the previous chunk prepended to
// each character-window split.
func ChunkText(text string, chunkSize, overlap int) []string {
	text = strings.TrimSpace(text)
	if text == "" {
		return nil
	}
	if chunkSize <= 0 {
		chunkSize = 512
	}
	if chunkSize > maxChunkSizeCeil {
		chunkSize = maxChunkSizeCeil
	}
	if overlap < 0 {
		overlap = 0
	}
	if overlap > chunkSize/maxOverlapDivisor {
		overlap = chunkSize / maxOverlapDivisor
	}

	// Normalise whitespace runs inside paragraphs but keep the
	// blank-line structure so the paragraph splitter sees the breaks.
	normalised := normaliseWhitespace(text)
	paragraphs := splitParagraphs(normalised)

	out := make([]string, 0, len(paragraphs))
	for _, p := range paragraphs {
		if len(p) <= chunkSize {
			appendOrMerge(&out, p, minChunkChars)
			continue
		}
		// Paragraph too big — split on sentence boundaries.
		sentences := splitSentences(p)
		var buf strings.Builder
		for _, s := range sentences {
			if len(s) > chunkSize {
				// Flush whatever's in the buffer before window-splitting
				// this sentence so order is preserved.
				if buf.Len() > 0 {
					appendOrMerge(&out, strings.TrimSpace(buf.String()), minChunkChars)
					buf.Reset()
				}
				for _, w := range windowSplit(s, chunkSize, overlap) {
					appendOrMerge(&out, w, minChunkChars)
				}
				continue
			}
			// Greedy pack: keep adding sentences until the next one
			// would overflow.
			if buf.Len()+len(s)+1 > chunkSize && buf.Len() > 0 {
				appendOrMerge(&out, strings.TrimSpace(buf.String()), minChunkChars)
				buf.Reset()
			}
			if buf.Len() > 0 {
				buf.WriteByte(' ')
			}
			buf.WriteString(s)
		}
		if buf.Len() > 0 {
			appendOrMerge(&out, strings.TrimSpace(buf.String()), minChunkChars)
		}
	}
	return out
}

// appendOrMerge keeps the chunk if it meets the floor; otherwise
// it appends the fragment to the previous chunk so we don't emit
// tiny shards that pollute the index.
func appendOrMerge(out *[]string, s string, floor int) {
	s = strings.TrimSpace(s)
	if s == "" {
		return
	}
	if len(s) < floor && len(*out) > 0 {
		(*out)[len(*out)-1] = (*out)[len(*out)-1] + " " + s
		return
	}
	*out = append(*out, s)
}

// normaliseWhitespace collapses runs of spaces / tabs (but NOT
// newlines, because paragraph splitting wants the line breaks).
func normaliseWhitespace(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	prevSpace := false
	for _, r := range s {
		switch {
		case r == '\n':
			b.WriteByte('\n')
			prevSpace = false
		case unicode.IsSpace(r):
			if !prevSpace {
				b.WriteByte(' ')
				prevSpace = true
			}
		default:
			b.WriteRune(r)
			prevSpace = false
		}
	}
	return b.String()
}

// splitParagraphs cuts on runs of newlines (≥1 blank line between
// paragraphs). A run of N newlines collapses to a single split.
func splitParagraphs(text string) []string {
	raw := strings.Split(text, "\n\n")
	out := make([]string, 0, len(raw))
	for _, p := range raw {
		p = strings.TrimSpace(p)
		// Collapse single \n within a paragraph back to spaces so
		// line-wrapped text reads naturally inside one chunk.
		p = strings.ReplaceAll(p, "\n", " ")
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// splitSentences cuts on sentence-final punctuation followed by
// whitespace. Cheap-and-cheerful — not a full grammar — but good
// enough for chunk boundaries (the worst case is a slightly
// imperfect split, which retrieval still tolerates well).
func splitSentences(p string) []string {
	out := make([]string, 0)
	var cur strings.Builder
	runes := []rune(p)
	for i, r := range runes {
		cur.WriteRune(r)
		if r != '.' && r != '!' && r != '?' {
			continue
		}
		// Look ahead: is the next rune a space (or end of input)?
		if i+1 >= len(runes) || unicode.IsSpace(runes[i+1]) {
			s := strings.TrimSpace(cur.String())
			if s != "" {
				out = append(out, s)
			}
			cur.Reset()
		}
	}
	tail := strings.TrimSpace(cur.String())
	if tail != "" {
		out = append(out, tail)
	}
	return out
}

// windowSplit slides a fixed-size window over a too-long string,
// keeping ``overlap`` characters of the previous window prepended.
func windowSplit(s string, size, overlap int) []string {
	if size <= 0 {
		return []string{s}
	}
	step := size - overlap
	if step <= 0 {
		step = size
	}
	out := make([]string, 0)
	for i := 0; i < len(s); i += step {
		end := i + size
		if end > len(s) {
			end = len(s)
		}
		out = append(out, strings.TrimSpace(s[i:end]))
		if end == len(s) {
			break
		}
	}
	return out
}
