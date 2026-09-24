// Tests for the knowledge-base chunker (FIX132 B): sizes in runes, recursive
// boundaries, Thai split only between words (kb-models /v1/segment), valid
// UTF-8 always, and overlap.
package service

import (
	"context"
	"errors"
	"math/rand"
	"strings"
	"testing"
	"unicode"
	"unicode/utf8"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/kbfake"
)

// thaiWords is a small dictionary; texts built from it have known word
// boundaries (the test segmenter matches it greedily, longest first).
var thaiWords = []string{
	"ประเทศไทย", "กรุงเทพมหานคร", "โรงพยาบาล", "มหาวิทยาลัย", "นักศึกษา", "ความรู้",
	"การศึกษา", "เศรษฐกิจ", "สุขภาพ", "ท่องเที่ยว", "อาหาร", "ภาษา", "วัฒนธรรม",
	"ประชาชน", "รัฐบาล", "นโยบาล", "เทคโนโลยี", "ข้อมูล", "ระบบ", "บริการ", "ลูกค้า",
	"ห้องพัก", "ราคา", "สัญญา", "เงื่อนไข", "ผู้ใช้", "ใช้งาน", "พนักงาน", "บริษัท", "ของ",
	"และ", "ที่", "ใน", "ให้", "ได้", "มี", "เป็น", "จะ", "กับ", "เพื่อ", "โดย", "ว่า",
}

// dictSegmenter is an in-process Segmenter over thaiWords (same algorithm as
// kbfake's /v1/segment).
type dictSegmenter struct {
	calls int
	err   error
}

func (d *dictSegmenter) Segment(_ context.Context, texts []string) ([]string, error) {
	d.calls++
	if d.err != nil {
		return nil, d.err
	}
	out := make([]string, len(texts))
	for i, t := range texts {
		out[i] = strings.Join(kbfake.Tokenize(t, thaiWords), " ")
	}
	return out, nil
}

// thaiRun builds an unbroken Thai run (no spaces) of at least n runes and
// returns it with the byte offsets of every true word boundary.
func thaiRun(n int, seed int64) (string, map[int]bool) {
	rng := rand.New(rand.NewSource(seed))
	var b strings.Builder
	for utf8.RuneCountInString(b.String()) < n {
		b.WriteString(thaiWords[rng.Intn(len(thaiWords))])
	}
	s := b.String()
	bounds := map[int]bool{0: true, len(s): true}
	pos := 0
	for _, w := range kbfake.Tokenize(s, thaiWords) {
		pos += len(w)
		bounds[pos] = true
	}
	return s, bounds
}

func TestFIX132_ChunkThaiWithoutSpacesCutsOnlyBetweenWords(t *testing.T) {
	text, bounds := thaiRun(3000, 1)
	if strings.ContainsAny(text, " \n") {
		t.Fatal("the test paragraph must have no spaces")
	}
	seg := &dictSegmenter{}
	norm, spans, err := chunkSpans(context.Background(), text, 1000, 150, seg)
	if err != nil {
		t.Fatal(err)
	}
	if norm != text {
		t.Fatal("normalisation must not change a clean paragraph")
	}
	if seg.calls == 0 {
		t.Fatal("an unbroken run longer than a chunk must be segmented into words")
	}
	if len(spans) < 4 {
		t.Fatalf("3,000+ runes at 1,000 per chunk → at least 4 chunks with overlap, got %d", len(spans))
	}
	covered := 0
	for i, s := range spans {
		if !bounds[s.start] || !bounds[s.end] {
			t.Fatalf("chunk %d [%d,%d) is cut inside a word: …%q|%q…", i, s.start, s.end,
				lastRunes(text[:s.start], 6), firstRunes(text[s.start:], 6))
		}
		if n := utf8.RuneCountInString(text[s.start:s.end]); n > 1000 {
			t.Fatalf("chunk %d has %d runes > 1000", i, n)
		}
		if i > 0 {
			ov := utf8.RuneCountInString(text[s.start:spans[i-1].end])
			if s.start >= spans[i-1].end || ov > 150 {
				t.Fatalf("chunk %d must overlap the previous by 1..150 runes, got start=%d prevEnd=%d (%d runes)", i, s.start, spans[i-1].end, ov)
			}
		}
		if s.end > covered {
			covered = s.end
		}
	}
	if spans[0].start != 0 || covered != len(text) {
		t.Fatal("the chunks must cover the whole text")
	}

	// The same through the real kb-models client (kbfake's /v1/segment).
	fake := kbfake.New(thaiWords...)
	defer fake.Close()
	client, _ := NewKBModelsClient(fake.URL)
	chunks, err := ChunkTextRunes(context.Background(), text, 1000, 150, client)
	if err != nil {
		t.Fatal(err)
	}
	for i, c := range chunks {
		start := strings.Index(text, c)
		if start < 0 || !bounds[start] || !bounds[start+len(c)] {
			t.Fatalf("client-segmented chunk %d is not cut between words", i)
		}
	}
	if fake.SegmentCalls.Load() == 0 {
		t.Fatal("kb-models /v1/segment was not asked")
	}
}

func TestFIX132_ChunkMixedThaiEnglish(t *testing.T) {
	run, bounds := thaiRun(1500, 7)
	eng := strings.Repeat("The refund policy applies to every booking made online. ", 12)
	text := "Hotel handbook (คู่มือโรงแรม)\n\n" + eng + "\n\n" + "ราคาห้องพัก ห้องละ 1200 บาท ต่อคืน รวมอาหารเช้า\n" + run + "\n\nContact: help@example.com"
	seg := &dictSegmenter{}
	norm, spans, err := chunkSpans(context.Background(), text, 400, 60, seg)
	if err != nil {
		t.Fatal(err)
	}
	runStart := strings.Index(norm, run)
	for i, s := range spans {
		c := norm[s.start:s.end]
		if n := utf8.RuneCountInString(c); n > 400 {
			t.Fatalf("chunk %d: %d runes", i, n)
		}
		if !utf8.ValidString(c) {
			t.Fatalf("chunk %d is not valid UTF-8", i)
		}
		for _, edge := range []int{s.start, s.end} {
			if edge == 0 || edge == len(norm) {
				continue
			}
			if edge > runStart && edge < runStart+len(run) {
				if !bounds[edge-runStart] {
					t.Fatalf("chunk %d cuts a Thai word at %d", i, edge)
				}
				continue
			}
			// Outside the unbroken run every cut is at a space or line break.
			prev, _ := utf8.DecodeLastRuneInString(norm[:edge])
			if prev != ' ' && prev != '\n' {
				t.Fatalf("chunk %d cuts an English word or Thai phrase: …%q|%q…", i, lastRunes(norm[:edge], 8), firstRunes(norm[edge:], 8))
			}
		}
	}
	joined := strings.Join(ChunkText(text, 400, 60), " ")
	for _, want := range []string{"help@example.com", "1200", "refund policy"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("%q lost", want)
		}
	}
}

// Old code cut an over-long run by BYTE offset: a Thai character is 3 bytes,
// so a cut could land inside one and produce invalid UTF-8.
func TestFIX132_ChunkAlwaysValidUTF8(t *testing.T) {
	rng := rand.New(rand.NewSource(42))
	alphabet := []rune("กขคงจฉชซญฎฐดตถทนบปผพฟภมยรลวศษสหอฮะาำิีึืุูเแโใไ่้๊๋็์ั😀é")
	for trial := 0; trial < 50; trial++ {
		var b strings.Builder
		n := 600 + rng.Intn(3000)
		for i := 0; i < n; i++ {
			b.WriteRune(alphabet[rng.Intn(len(alphabet))])
		}
		text := b.String()
		for _, size := range []int{100, 333, 512, 1000} {
			chunks := ChunkText(text, size, size/5)
			if len(chunks) == 0 {
				t.Fatal("no chunks")
			}
			for i, c := range chunks {
				if !utf8.ValidString(c) {
					t.Fatalf("trial %d size %d chunk %d is invalid UTF-8", trial, size, i)
				}
				if utf8.RuneCountInString(c) > size {
					t.Fatalf("trial %d size %d chunk %d has %d runes", trial, size, i, utf8.RuneCountInString(c))
				}
				if r, _ := utf8.DecodeRuneInString(c); i > 0 && unicode.In(r, unicode.Mn, unicode.Me) && utf8.RuneCountInString(c) < size {
					t.Fatalf("trial %d chunk %d starts with a combining mark %U", trial, i, r)
				}
			}
		}
	}
	// Invalid bytes and NULs from an extractor never reach a chunk.
	for _, c := range ChunkText("ราคา\x00ห้อง\xff\xfeพัก", 100, 10) {
		if !utf8.ValidString(c) || strings.ContainsRune(c, 0) {
			t.Fatalf("bad chunk %q", c)
		}
	}
}

// Old code counted bytes: 900 Thai characters (2,700 bytes) became several
// 512-byte chunks.
func TestFIX132_ChunkCountsRunesNotBytes(t *testing.T) {
	text := strings.TrimSpace(strings.Repeat("ราคาห้องพัก ", 75)) // 75*11 + 74 = 899 runes, 2,600+ bytes
	if n := utf8.RuneCountInString(text); n > 1000 || len(text) < 2000 {
		t.Fatalf("fixture: %d runes, %d bytes", n, len(text))
	}
	if got := ChunkText(text, 1000, 150); len(got) != 1 {
		t.Fatalf("%d runes fit one 1000-rune chunk, got %d chunks", utf8.RuneCountInString(text), len(got))
	}
}

func TestFIX132_ChunkOverlapCarriesTrailingSentences(t *testing.T) {
	var b strings.Builder
	for i := 0; i < 40; i++ {
		b.WriteString("Sentence number ")
		b.WriteString(strings.Repeat("x", i%7+1))
		b.WriteString(" ends here. ")
	}
	norm, spans, err := chunkSpans(context.Background(), b.String(), 200, 50, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) < 3 {
		t.Fatalf("want several chunks, got %d", len(spans))
	}
	for i := 1; i < len(spans); i++ {
		prev, cur := spans[i-1], spans[i]
		if cur.start >= prev.end {
			t.Fatalf("chunk %d does not overlap chunk %d", i, i-1)
		}
		shared := norm[cur.start:prev.end]
		if n := utf8.RuneCountInString(shared); n == 0 || n > 50 {
			t.Fatalf("overlap %d runes, want 1..50", n)
		}
		if !strings.HasSuffix(norm[prev.start:prev.end], shared) || !strings.HasPrefix(norm[cur.start:cur.end], shared) {
			t.Fatal("the overlap is the previous chunk's tail and this chunk's head")
		}
		if !strings.HasPrefix(strings.TrimSpace(norm[cur.start:cur.end]), "Sentence number") {
			t.Fatalf("overlap carries whole sentences, chunk %d starts %q", i, firstRunes(norm[cur.start:], 20))
		}
	}
}

func TestFIX132_ChunkSegmenterFailureIsReturned(t *testing.T) {
	text, _ := thaiRun(2500, 3)
	want := errors.New("kb-models down")
	if _, err := ChunkTextRunes(context.Background(), text, 1000, 150, &dictSegmenter{err: want}); !errors.Is(err, want) {
		t.Fatalf("a segmenter failure must reach the indexer (to retry), got %v", err)
	}
	// Text that needs no word split never asks the segmenter.
	seg := &dictSegmenter{err: want}
	if _, err := ChunkTextRunes(context.Background(), "short text. another sentence.", 1000, 150, seg); err != nil || seg.calls != 0 {
		t.Fatalf("no segmentation needed: err=%v calls=%d", err, seg.calls)
	}
}

func TestFIX132_ChunkVeryLongRunIsSegmentedInWindows(t *testing.T) {
	text, bounds := thaiRun(20000, 11)
	seg := &dictSegmenter{}
	_, spans, err := chunkSpans(context.Background(), text, 1000, 150, seg)
	if err != nil {
		t.Fatal(err)
	}
	if seg.calls < 3 {
		t.Fatalf("a 20,000-rune run is segmented in %d-rune windows, got %d calls", segWindowRunes, seg.calls)
	}
	for i, s := range spans {
		if !bounds[s.start] || !bounds[s.end] {
			t.Fatalf("chunk %d cut inside a word", i)
		}
	}
}

func TestFIX132_EffectiveChunkingTreatsLegacyAsDefaults(t *testing.T) {
	cases := []struct{ size, overlap, ws, wo int }{
		{512, 50, 1000, 150}, // migration 011's defaults (bytes) → today's
		{0, 0, 1000, 150},
		{800, 100, 800, 100},
		{512, 60, 512, 60}, // an explicit choice stays
		{20, 5, 100, 5},
		{99999, 99999, 4000, 1333},
	}
	for _, c := range cases {
		s, o := EffectiveChunking(c.size, c.overlap, DefaultKBChunkSize, DefaultKBChunkOverlap)
		if s != c.ws || o != c.wo {
			t.Errorf("EffectiveChunking(%d,%d) = %d,%d want %d,%d", c.size, c.overlap, s, o, c.ws, c.wo)
		}
	}
}

func TestChunkText_EdgeCases(t *testing.T) {
	tests := []struct {
		name             string
		text             string
		size, overlap    int
		wantMin, wantMax int
	}{
		{"empty", "", 512, 50, 0, 0},
		{"whitespace only", "   \n\n   \t  ", 512, 50, 0, 0},
		{"short", "Hello world", 512, 50, 1, 1},
		{"exact-size paragraph", strings.Repeat("a", 512), 512, 50, 1, 1},
		{"two paragraphs fit one chunk", strings.Repeat("a", 300) + "\n\n" + strings.Repeat("b", 300), 1000, 50, 1, 1},
		{"two paragraphs over the size", strings.Repeat("a", 300) + "\n\n" + strings.Repeat("b", 300), 512, 50, 2, 2},
		{"long paragraph splits on sentences", strings.Repeat("This is a sentence. ", 100), 512, 50, 4, 6},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			chunks := ChunkText(tt.text, tt.size, tt.overlap)
			if len(chunks) < tt.wantMin || len(chunks) > tt.wantMax {
				t.Errorf("got %d chunks, want %d-%d", len(chunks), tt.wantMin, tt.wantMax)
			}
			for i, c := range chunks {
				if strings.TrimSpace(c) == "" {
					t.Errorf("chunk %d is empty/whitespace", i)
				}
				if utf8.RuneCountInString(c) > tt.size {
					t.Errorf("chunk %d over the size", i)
				}
			}
		})
	}
}

func TestChunkText_LongRunWithoutSegmenterIsCutByLength(t *testing.T) {
	chunks := ChunkText(strings.Repeat("x", 2000), 400, 50)
	if len(chunks) < 5 {
		t.Errorf("expected ≥5 chunks for 2000/400, got %d", len(chunks))
	}
	for i, c := range chunks {
		if c == "" || utf8.RuneCountInString(c) > 400 {
			t.Errorf("chunk %d: %d runes", i, utf8.RuneCountInString(c))
		}
	}
}

func firstRunes(s string, n int) string {
	return string([]rune(s)[:min(n, utf8.RuneCountInString(s))])
}

func lastRunes(s string, n int) string {
	r := []rune(s)
	return string(r[max(0, len(r)-n):])
}
