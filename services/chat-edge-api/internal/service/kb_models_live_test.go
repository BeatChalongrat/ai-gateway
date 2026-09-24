package service

// FIX132 B — against a REAL kb-models (bge-m3 / bge-reranker-v2-m3 /
// PyThaiNLP). Runs only when KB_MODELS_TEST_URL is set, e.g. a throwaway
//
//	docker run -d --name fix132-kbm-lane -p 127.0.0.1:18091:8090 \
//	  -v ai-gateway_kb_models:/models:ro -e KB_DTYPE=bf16 -e HF_HUB_OFFLINE=1 ai-gateway-kb-models:dev
//	KB_MODELS_TEST_URL=http://127.0.0.1:18091 go test -run FIX132_Live ./internal/service/
//
// It checks the wire contract the fake stands in for, then runs the real
// pipeline (upload → worker → hybrid search) on Thai and English text.

import (
	"context"
	"math"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/repository"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/storage"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/testdb"
)

func liveKBModels(t *testing.T) *KBModelsClient {
	t.Helper()
	url := os.Getenv("KB_MODELS_TEST_URL")
	if url == "" {
		t.Skip("KB_MODELS_TEST_URL not set (a real kb-models)")
	}
	c, err := NewKBModelsClient(url)
	if err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Minute)
	for {
		h, err := c.Health(context.Background())
		if err == nil {
			t.Logf("kb-models ready: %s@%s dim=%d rerank=%v", h.Models.Embed.Repo, h.Models.Embed.Revision[:8], h.Models.Embed.Dim, h.Models.Rerank.Enabled)
			return c
		}
		if !IsKBModelsLoading(err) || time.Now().After(deadline) {
			t.Fatalf("kb-models not ready: %v", err)
		}
		time.Sleep(3 * time.Second)
	}
}

func cosine(a, b []float32) float64 {
	var d float64
	for i := range a {
		d += float64(a[i]) * float64(b[i])
	}
	return d
}

func TestFIX132_LiveKBModelsContract(t *testing.T) {
	c := liveKBModels(t)
	ctx := context.Background()
	texts := []string{
		"ราคาห้องพักรวมอาหารเช้า คืนละ 1,200 บาท",
		"อัตราค่าห้องพักต่อคืนรวมอาหารเช้าแล้ว",
		"Employees get ten days of annual leave per year.",
	}
	got, err := c.Embed(ctx, texts, true, KBPriorityIndex)
	if err != nil {
		t.Fatal(err)
	}
	for i, e := range got {
		var n float64
		for _, x := range e.Dense {
			n += float64(x) * float64(x)
		}
		if len(e.Dense) != KBEmbedDim || math.Abs(math.Sqrt(n)-1) > 1e-3 || e.Sparse == nil || len(e.Sparse.Indices) == 0 {
			t.Fatalf("text %d: dim=%d norm=%.4f sparse=%v", i, len(e.Dense), math.Sqrt(n), e.Sparse)
		}
	}
	thai, thaiParaphrase, english := got[0].Dense, got[1].Dense, got[2].Dense
	if cosine(thai, thaiParaphrase) <= cosine(thai, english) {
		t.Fatalf("a Thai paraphrase must be closer than an unrelated English sentence: %.3f vs %.3f",
			cosine(thai, thaiParaphrase), cosine(thai, english))
	}
	t.Logf("cosine(thai, paraphrase)=%.3f cosine(thai, english)=%.3f", cosine(thai, thaiParaphrase), cosine(thai, english))

	seg, err := c.Segment(ctx, []string{"ราคาห้องพักรวมอาหารเช้าคืนละหนึ่งพันสองร้อยบาท"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.ReplaceAll(seg[0], " ", "") != "ราคาห้องพักรวมอาหารเช้าคืนละหนึ่งพันสองร้อยบาท" || strings.Count(seg[0], " ") < 4 {
		t.Fatalf("segment must split into words and keep every character: %q", seg[0])
	}
	t.Logf("segment: %s", seg[0])

	if scores, err := c.Rerank(ctx, "ค่าห้องพักคืนละเท่าไร", []string{texts[2], texts[0]}); err == nil {
		if scores[1] <= scores[0] {
			t.Fatalf("rerank: the hotel passage must score higher: %v", scores)
		}
		t.Logf("rerank scores: %v", scores)
	} else if IsKBModelsUnavailable(err) {
		t.Logf("reranker not loaded (KB_RERANK_ENABLED off): %v", err)
	} else {
		t.Fatal(err)
	}
}

// A 3,000-character Thai paragraph with no spaces, split by the REAL
// segmenter: every cut is at a PyThaiNLP word boundary, no character lost.
func TestFIX132_LiveChunkerCutsThaiBetweenWords(t *testing.T) {
	c := liveKBModels(t)
	base := "ระบบบริหารจัดการความรู้ขององค์กรช่วยให้พนักงานค้นหาข้อมูลนโยบายการลาและสวัสดิการได้รวดเร็วขึ้นโดยไม่ต้องสอบถามฝ่ายทรัพยากรบุคคล"
	var b strings.Builder
	for b.Len() < 3000*3 {
		b.WriteString(base)
	}
	text := b.String()
	norm, spans, err := chunkSpans(context.Background(), text, 1000, 150, c)
	if err != nil {
		t.Fatal(err)
	}
	seg, err := c.Segment(context.Background(), []string{text[:0] + text})
	if err != nil {
		t.Fatal(err)
	}
	bounds := map[int]bool{0: true, len(norm): true}
	pos := 0
	for _, w := range strings.Fields(seg[0]) {
		pos += len(w)
		bounds[pos] = true
	}
	if pos != len(norm) {
		t.Fatalf("segmenter output does not spell the text (%d vs %d bytes)", pos, len(norm))
	}
	for i, s := range spans {
		if !bounds[s.start] || !bounds[s.end] {
			t.Fatalf("chunk %d cut inside a word", i)
		}
	}
	t.Logf("%d runes → %d chunks, all cut between words", len([]rune(norm)), len(spans))
}

// The real pipeline: upload → background worker → hybrid search.
func TestFIX132_LivePipelineThaiAndEnglish(t *testing.T) {
	c := liveKBModels(t)
	pool := testdb.Fresh(t)
	store, _ := storage.NewLocalBackend(t.TempDir())
	repo := repository.NewKnowledgeRepo(pool)
	svc := NewKnowledgeService(repo, c, stubExtractor{store}, store, KnowledgeServiceKnobs{})
	w := NewKnowledgeWorker(svc, KnowledgeWorkerConfig{})
	tenant, user := uuid.New(), uuid.New()
	kb, err := svc.CreateKB(context.Background(), CreateKBInput{TenantID: tenant, UserID: user, Name: "คู่มือโรงแรม"})
	if err != nil {
		t.Fatal(err)
	}
	h := &kbHarness{pool: pool, repo: repo, models: c, store: store, svc: svc, w: w, tenant: tenant, user: user, kb: kb}
	hotel := h.upload(t, "ราคาห้องพัก.txt", "text/plain", "โรงแรมริมทะเล: ห้องสแตนดาร์ดคืนละ 1,200 บาท รวมอาหารเช้าสำหรับสองท่าน ห้องดีลักซ์คืนละ 1,800 บาท เช็คอินได้ตั้งแต่บ่ายสองโมง")
	leave := h.upload(t, "leave-policy.txt", "text/plain", "Annual leave policy. Every employee gets ten days of paid annual leave per year; unused days carry over up to five days.")
	it := h.upload(t, "it.txt", "text/plain", "IT support: reset your password on the portal. The help desk answers within one working day.")
	start := time.Now()
	h.drain(t)
	t.Logf("indexed 3 documents in %v", time.Since(start))
	for _, d := range []uuid.UUID{hotel.ID, leave.ID, it.ID} {
		if got := h.doc(t, d); got.Status != "indexed" {
			t.Fatalf("%s: %s %v", got.OriginalFilename, got.Status, got.ErrorMessage)
		}
	}
	rag := NewRAGService(repo, c, DefaultRAGConfig())
	cases := []struct {
		q    string
		want uuid.UUID
	}{
		{"ห้องพักคืนละเท่าไร รวมอาหารเช้าไหม", hotel.ID},
		{"How many days of annual leave do I get?", leave.ID},
		{"ลืมรหัสผ่าน ต้องทำอย่างไร", it.ID},
	}
	for _, tc := range cases {
		res, err := rag.Search(context.Background(), tenant, kb.ID, tc.q, "", 0)
		if err != nil {
			t.Fatal(err)
		}
		if len(res.Chunks) == 0 || res.Chunks[0].DocumentID != tc.want {
			t.Fatalf("%q: want %s first, got %+v", tc.q, tc.want, res.Chunks)
		}
		t.Logf("%q → %s score=%.3f (cos %.3f lex %.3f) embed=%dms sql=%dms", tc.q, res.Chunks[0].Filename,
			res.Chunks[0].Score, res.Chunks[0].Cosine, res.Chunks[0].Lexical, res.Timing.Embed, res.Timing.SQL)
	}
	off, err := rag.Search(context.Background(), tenant, kb.ID, "วิธีทำต้มยำกุ้ง", "", 0)
	if err != nil {
		t.Fatal(err)
	}
	top := 0.0
	if len(off.Chunks) > 0 {
		top = off.Chunks[0].Score
	}
	t.Logf("off-topic question: %d passages kept (top %.3f), %d below KB_MIN_SCORE", len(off.Chunks), top, off.BelowFloor)
}
