// Chunker — splits a document's text into retrieval passages.
//
// FIX132 B rewrite. The old chunker counted BYTES (512 bytes is ~170 Thai
// characters) and cut an over-long sentence by byte offset, which could split
// a Thai character in half and produce invalid UTF-8. Now:
//
//   - sizes are counted in runes (characters), never bytes;
//   - the text is split recursively, largest boundary first — blank line,
//     line break, sentence end (". " "? " "! "), space — the way
//     LangChain's RecursiveCharacterTextSplitter (the splitter behind most
//     vendor "file search" defaults) does;
//   - a run with none of those boundaries that is still longer than the
//     chunk (Thai writes a whole paragraph without spaces) is split only
//     BETWEEN WORDS, using kb-models /v1/segment (PyThaiNLP newmm) — never
//     inside a word, never inside a character. The original characters are
//     kept: the segmenter only tells us where the words end;
//   - the pieces are packed greedily up to `size` runes, and each chunk
//     starts with the previous chunk's trailing pieces, up to `overlap`
//     runes, so a thought that straddles a boundary survives in both.
//
// Only if the segmenter cannot place a boundary (a single "word" longer than
// a whole chunk — a URL, a base64 blob) is a run cut by length, and then
// never before a combining mark (a Thai vowel or tone mark stays with its
// consonant).
//
// Defaults are 1,000 runes with 150 of overlap (OpenAI file search uses
// 800 tokens / 400 overlap; Anthropic's contextual-retrieval study used
// ~800-token chunks — 1,000 Thai characters is roughly 300–500 bge-m3
// tokens, well inside the reranker's 512-token window).
package service

import (
	"context"
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	// DefaultKBChunkSize / DefaultKBChunkOverlap are the chunker defaults in
	// runes (KB_CHUNK_SIZE / KB_CHUNK_OVERLAP override them).
	DefaultKBChunkSize    = 1000
	DefaultKBChunkOverlap = 150
	// legacyChunkSize / legacyChunkOverlap are migration 011's column
	// defaults (512 / 50, meant as bytes). A knowledge base still carrying
	// exactly those values uses the current defaults instead.
	legacyChunkSize    = 512
	legacyChunkOverlap = 50
	minChunkSizeRunes  = 100
	maxChunkSizeCeil   = 4000 // sanity cap so a misconfigured KB doesn't OOM
	// segWindowRunes bounds one /v1/segment text; a longer unbroken run is
	// segmented window by window (see wordBoundariesWindowed).
	segWindowRunes = 8000
)

// Segmenter splits texts into words: each output is the input with its
// words separated by single spaces (kb-models /v1/segment).
type Segmenter interface {
	Segment(ctx context.Context, texts []string) ([]string, error)
}

// EffectiveChunking maps a knowledge base's stored chunk_size/chunk_overlap
// to the values the chunker uses: legacy 512/50 rows and unset values take
// the defaults; everything is clamped to sane bounds.
func EffectiveChunking(size, overlap, defSize, defOverlap int) (int, int) {
	if defSize <= 0 {
		defSize = DefaultKBChunkSize
	}
	if defOverlap < 0 {
		defOverlap = DefaultKBChunkOverlap
	}
	if size <= 0 || (size == legacyChunkSize && overlap == legacyChunkOverlap) {
		size, overlap = defSize, defOverlap
	}
	return clampChunking(size, overlap)
}

func clampChunking(size, overlap int) (int, int) {
	if size <= 0 {
		size = DefaultKBChunkSize
	}
	if size < minChunkSizeRunes {
		size = minChunkSizeRunes
	}
	if size > maxChunkSizeCeil {
		size = maxChunkSizeCeil
	}
	if overlap < 0 {
		overlap = 0
	}
	if overlap > size/3 {
		overlap = size / 3
	}
	return size, overlap
}

// ChunkText is ChunkTextRunes without a segmenter: an unbroken run longer
// than the chunk is cut by length (at a safe character boundary). Kept for
// callers with no kb-models at hand; the indexer uses ChunkTextRunes.
func ChunkText(text string, chunkSize, overlap int) []string {
	out, _ := ChunkTextRunes(context.Background(), text, chunkSize, overlap, nil)
	return out
}

// ChunkTextRunes splits text into passages of at most size runes (see the
// file comment). seg may be nil (no word segmentation available). The only
// error is the segmenter's, so the caller can retry when kb-models is back.
func ChunkTextRunes(ctx context.Context, text string, size, overlap int, seg Segmenter) ([]string, error) {
	norm, spans, err := chunkSpans(ctx, text, size, overlap, seg)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(spans))
	for _, s := range spans {
		if c := strings.TrimSpace(norm[s.start:s.end]); c != "" {
			out = append(out, c)
		}
	}
	return out, nil
}

// span is a byte range [start, end) of the normalised text.
type span struct{ start, end int }

// chunkSpans returns the normalised text and the chunk ranges within it.
func chunkSpans(ctx context.Context, text string, size, overlap int, seg Segmenter) (string, []span, error) {
	size, overlap = clampChunking(size, overlap)
	norm := NormalizeKBText(text)
	if norm == "" {
		return "", nil, nil
	}
	units, err := splitUnits(ctx, norm, size, seg)
	if err != nil {
		return "", nil, err
	}
	return norm, packUnits(norm, units, size, overlap), nil
}

// NormalizeKBText makes extracted text safe to store and split: invalid
// UTF-8 and NUL bytes (Postgres text rejects both) are dropped, line endings
// become \n, runs of spaces/tabs inside a line become one space, trailing
// spaces go, and three or more line breaks collapse to a blank line.
func NormalizeKBText(text string) string {
	text = strings.ToValidUTF8(text, "")
	text = strings.ReplaceAll(text, "\x00", "")
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	var b strings.Builder
	b.Grow(len(text))
	newlines := 0
	pendingSpace := false
	for _, r := range text {
		switch {
		case r == '\n':
			pendingSpace = false
			newlines++
			continue
		case unicode.IsSpace(r):
			pendingSpace = true
			continue
		}
		if newlines > 0 {
			if b.Len() > 0 {
				if newlines >= 2 {
					b.WriteString("\n\n")
				} else {
					b.WriteByte('\n')
				}
			}
			newlines = 0
			pendingSpace = false
		}
		if pendingSpace && b.Len() > 0 {
			b.WriteByte(' ')
		}
		pendingSpace = false
		b.WriteRune(r)
	}
	return b.String()
}

// ---------------------------------------------------------------------------
// Units: contiguous pieces, each at most size runes, that tile the text.
// ---------------------------------------------------------------------------

// unitLevels are the recursive boundaries, largest first. Each piece keeps
// its boundary at its END, so the pieces concatenate back to the text.
var unitLevels = []func(s string) []int{
	func(s string) []int { return cutsAfter(s, "\n\n") },
	func(s string) []int { return cutsAfter(s, "\n") },
	sentenceCuts,
	spaceCuts,
}

type pendingUnit struct {
	span
	needsWords bool
}

func splitUnits(ctx context.Context, text string, size int, seg Segmenter) ([]span, error) {
	var items []pendingUnit
	var walk func(sp span, level int)
	walk = func(sp span, level int) {
		piece := text[sp.start:sp.end]
		if utf8.RuneCountInString(piece) <= size {
			items = append(items, pendingUnit{span: sp})
			return
		}
		for ; level < len(unitLevels); level++ {
			cuts := unitLevels[level](piece)
			if len(cuts) == 0 {
				continue
			}
			prev := 0
			for _, c := range append(cuts, len(piece)) {
				if c > prev {
					walk(span{sp.start + prev, sp.start + c}, level+1)
				}
				prev = c
			}
			return
		}
		items = append(items, pendingUnit{span: sp, needsWords: true})
	}
	walk(span{0, len(text)}, 0)

	// Runs with no boundary left: split between words.
	var long []int
	for i, it := range items {
		if it.needsWords {
			long = append(long, i)
		}
	}
	words := map[int][]span{}
	if len(long) > 0 {
		var short []int // runs small enough for one /v1/segment text
		for _, i := range long {
			if utf8.RuneCountInString(text[items[i].start:items[i].end]) <= segWindowRunes {
				short = append(short, i)
			}
		}
		if seg != nil && len(short) > 0 {
			texts := make([]string, len(short))
			for k, i := range short {
				texts[k] = text[items[i].start:items[i].end]
			}
			segged, err := seg.Segment(ctx, texts)
			if err != nil {
				return nil, fmt.Errorf("chunker: word segmentation: %w", err)
			}
			if len(segged) != len(texts) {
				return nil, fmt.Errorf("chunker: word segmentation returned %d texts for %d", len(segged), len(texts))
			}
			for k, i := range short {
				if b, ok := wordBoundaries(texts[k], segged[k]); ok {
					words[i] = boundariesToSpans(items[i].start, b)
				}
			}
		}
		for _, i := range long {
			if _, done := words[i]; done || seg == nil ||
				utf8.RuneCountInString(text[items[i].start:items[i].end]) <= segWindowRunes {
				continue
			}
			// Longer than one segmentation window: segment window by window.
			b, err := wordBoundariesWindowed(ctx, text[items[i].start:items[i].end], seg)
			if err != nil {
				return nil, err
			}
			if b != nil {
				words[i] = boundariesToSpans(items[i].start, b)
			}
		}
	}

	out := make([]span, 0, len(items))
	for i, it := range items {
		if !it.needsWords {
			out = append(out, it.span)
			continue
		}
		ws, ok := words[i]
		if !ok { // no segmenter / no usable answer: whole run, cut by length below
			ws = []span{it.span}
		}
		for _, w := range ws {
			if utf8.RuneCountInString(text[w.start:w.end]) <= size {
				out = append(out, w)
				continue
			}
			out = append(out, hardSplit(text, w, size)...)
		}
	}
	return out, nil
}

// cutsAfter returns the offsets just after every occurrence of sep.
func cutsAfter(s, sep string) []int {
	var cuts []int
	for i := 0; ; {
		j := strings.Index(s[i:], sep)
		if j < 0 {
			return cuts
		}
		i += j + len(sep)
		if i < len(s) {
			cuts = append(cuts, i)
		}
	}
}

// sentenceCuts: after ". ", "? ", "! " (the space stays with the sentence).
func sentenceCuts(s string) []int {
	var cuts []int
	for i := 0; i+1 < len(s); i++ {
		if (s[i] == '.' || s[i] == '?' || s[i] == '!') && s[i+1] == ' ' && i+2 < len(s) {
			cuts = append(cuts, i+2)
		}
	}
	return cuts
}

// spaceCuts: after every space or zero-width space (Thai text often marks
// word or phrase breaks with U+200B).
func spaceCuts(s string) []int {
	var cuts []int
	for i, r := range s {
		if r == ' ' || r == '​' {
			if c := i + utf8.RuneLen(r); c < len(s) {
				cuts = append(cuts, c)
			}
		}
	}
	return cuts
}

// wordBoundaries maps the segmenter's answer ("w1 w2 w3") back onto the
// original run: the byte offsets where each word ends. False when the answer
// does not spell the run (then the caller cuts by length instead).
func wordBoundaries(run, segmented string) ([]int, bool) {
	var bounds []int
	pos := 0
	for _, w := range strings.Fields(segmented) {
		if strings.HasPrefix(run[pos:], w) {
			pos += len(w)
		} else {
			// Tolerate a few characters the segmenter dropped (none today).
			j := strings.Index(run[pos:], w)
			if j < 0 || j > 16 {
				return nil, false
			}
			pos += j + len(w)
		}
		bounds = append(bounds, pos)
	}
	if len(bounds) == 0 {
		return nil, false
	}
	if pos < len(run) {
		bounds = append(bounds, len(run))
	}
	return bounds, true
}

// wordBoundariesWindowed segments a run longer than one window: each window
// keeps every boundary except its last word (which the window may have cut),
// and the next window starts at the last kept boundary.
func wordBoundariesWindowed(ctx context.Context, run string, seg Segmenter) ([]int, error) {
	var bounds []int
	start := 0
	for start < len(run) {
		end := advanceRunes(run, start, segWindowRunes)
		final := end >= len(run)
		piece := run[start:end]
		out, err := seg.Segment(ctx, []string{piece})
		if err != nil {
			return nil, fmt.Errorf("chunker: word segmentation: %w", err)
		}
		if len(out) != 1 {
			return nil, nil
		}
		b, ok := wordBoundaries(piece, out[0])
		if !ok {
			return nil, nil
		}
		if !final && len(b) > 1 {
			b = b[:len(b)-1] // the last word may continue past the window
		}
		for _, x := range b {
			bounds = append(bounds, start+x)
		}
		next := start + b[len(b)-1]
		if next <= start {
			return nil, nil
		}
		start = next
	}
	return bounds, nil
}

func boundariesToSpans(base int, bounds []int) []span {
	out := make([]span, 0, len(bounds))
	prev := 0
	for _, b := range bounds {
		if b > prev {
			out = append(out, span{base + prev, base + b})
		}
		prev = b
	}
	return out
}

// advanceRunes returns the byte offset n runes after from (or len(s)).
func advanceRunes(s string, from, n int) int {
	i := from
	for k := 0; k < n && i < len(s); k++ {
		_, size := utf8.DecodeRuneInString(s[i:])
		i += size
	}
	return i
}

// hardSplit cuts a span into pieces of at most size runes — the last resort
// for a single "word" longer than a chunk. It never cuts inside a character
// and never right before a combining mark, so a Thai vowel or tone mark stays
// with its consonant.
func hardSplit(text string, sp span, size int) []span {
	var out []span
	start := sp.start
	for start < sp.end {
		end := advanceRunes(text[:sp.end], start, size)
		if end < sp.end {
			// Step back while the next rune is a combining mark.
			for end > start {
				r, _ := utf8.DecodeRuneInString(text[end:])
				if !unicode.In(r, unicode.Mn, unicode.Me, unicode.Mc) {
					break
				}
				_, back := utf8.DecodeLastRuneInString(text[start:end])
				end -= back
			}
			if end == start { // a run of marks longer than a chunk: cut anyway
				end = advanceRunes(text[:sp.end], start, size)
			}
		}
		out = append(out, span{start, end})
		start = end
	}
	return out
}

// ---------------------------------------------------------------------------
// Packing
// ---------------------------------------------------------------------------

// packUnits greedily packs contiguous units into chunks of at most size
// runes; each new chunk starts with the previous chunk's trailing units
// totalling at most overlap runes.
func packUnits(text string, units []span, size, overlap int) []span {
	runes := func(s span) int { return utf8.RuneCountInString(text[s.start:s.end]) }
	var chunks []span
	var cur []span
	curLen := 0
	for _, u := range units {
		ul := runes(u)
		if curLen+ul > size && len(cur) > 0 {
			chunks = append(chunks, span{cur[0].start, cur[len(cur)-1].end})
			k, carried := len(cur), 0
			for k > 0 {
				n := runes(cur[k-1])
				if carried+n > overlap {
					break
				}
				carried += n
				k--
			}
			cur = append([]span(nil), cur[k:]...)
			curLen = carried
			for len(cur) > 0 && curLen+ul > size {
				curLen -= runes(cur[0])
				cur = cur[1:]
			}
		}
		cur = append(cur, u)
		curLen += ul
	}
	if len(cur) > 0 {
		chunks = append(chunks, span{cur[0].start, cur[len(cur)-1].end})
	}
	return chunks
}
