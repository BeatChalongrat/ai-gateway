package service

// FIX132 B — the knowledge base's model client: kb-models on this machine,
// never a host off it.

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/kbfake"
)

func TestFIX132_KBModelsURLGuard(t *testing.T) {
	ok := []string{
		"http://kb-models:8090", "http://kb-models:8090/", "https://kb_models", "http://localhost:18091",
		"http://127.0.0.1:8090", "http://[::1]:8090", "http://10.0.3.7:8090", "http://172.20.0.5:8090",
		"http://172.31.255.1", "http://192.168.1.10:8090", "http://host.docker.internal:18091",
	}
	for _, u := range ok {
		if _, err := ValidateKBModelsURL(u); err != nil {
			t.Errorf("%s must be accepted: %v", u, err)
		}
	}
	refused := []string{
		"https://api.openai.com/v1", "http://kb-models.example.com:8090", "http://8.8.8.8:8090",
		"http://172.32.0.1:8090", "http://100.64.0.1", "http://169.254.169.254", "http://[2001:db8::1]:80",
		"ftp://kb-models", "http://user:pw@kb-models:8090", "kb-models:8090", "http://",
	}
	for _, u := range refused {
		if _, err := ValidateKBModelsURL(u); err == nil {
			t.Errorf("%s must be refused", u)
		}
	}
	if _, err := NewKBModelsClient("https://api.openai.com/v1"); err == nil {
		t.Fatal("a client for a public host must not be built")
	}
	if c, err := NewKBModelsClient(""); err != nil || c.BaseURL() != KBModelsDefaultURL {
		t.Fatalf("empty URL → the compose default, got %v %v", c, err)
	}
}

func TestFIX132_KBModelsDialRefusesPublicAddresses(t *testing.T) {
	// The check runs on the resolved address, so a service name that DNS
	// points at the internet is refused at connect time too.
	for _, a := range []string{"8.8.8.8:443", "1.1.1.1:80", "[2606:4700::1111]:443", "100.64.0.1:80"} {
		if err := internalOnlyDial("tcp", a, nil); err == nil {
			t.Errorf("dial to %s must be refused", a)
		}
	}
	for _, a := range []string{"127.0.0.1:8090", "[::1]:8090", "172.18.0.4:8090", "10.1.2.3:8090", "192.168.65.254:8090"} {
		if err := internalOnlyDial("tcp", a, nil); err != nil {
			t.Errorf("dial to %s must be allowed: %v", a, err)
		}
	}
}

func TestFIX132_KBModelsEmbedBatchesWithSparseAndPriority(t *testing.T) {
	fake := kbfake.New("สวัสดี", "ครับ")
	defer fake.Close()
	c, err := NewKBModelsClient(fake.URL)
	if err != nil {
		t.Fatal(err)
	}
	texts := make([]string, 40)
	for i := range texts {
		texts[i] = "สวัสดีครับ passage " + strings.Repeat("x", i+1)
	}
	got, err := c.Embed(context.Background(), texts, true, KBPriorityIndex)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 40 {
		t.Fatalf("want 40 embeddings, got %d", len(got))
	}
	if want := []int{16, 16, 8}; !equalInts(fake.BatchSizes, want) {
		t.Fatalf("batches of at most 16, got %v", fake.BatchSizes)
	}
	for _, p := range fake.Priorities {
		if p != "index" {
			t.Fatalf("indexing is priority index, got %v", fake.Priorities)
		}
	}
	for i, e := range got {
		if len(e.Dense) != KBEmbedDim {
			t.Fatalf("embedding %d has %d dims", i, len(e.Dense))
		}
		if e.Sparse == nil || len(e.Sparse.Indices) == 0 || len(e.Sparse.Indices) != len(e.Sparse.Values) {
			t.Fatalf("embedding %d: sparse weights missing: %+v", i, e.Sparse)
		}
	}
	// In order: the i-th result is the i-th text's.
	want := kbfake.Dense(fake.WordsOf(texts[33]))
	for k := range want {
		if want[k] != got[33].Dense[k] {
			t.Fatal("results out of order")
		}
	}

	// A question is priority query.
	if _, err := c.Embed(context.Background(), []string{"คำถาม"}, true, KBPriorityQuery); err != nil {
		t.Fatal(err)
	}
	if last := fake.Priorities[len(fake.Priorities)-1]; last != "query" {
		t.Fatalf("a question must be priority query, got %s", last)
	}
}

func TestFIX132_KBModelsRefusesWrongDimension(t *testing.T) {
	// A model swap that changes the dimension must fail loudly, not write
	// rows the column will reject halfway through a document.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"data": []map[string]any{{"index": 0, "embedding": make([]float32, 1536)}}})
	}))
	defer srv.Close()
	c, _ := NewKBModelsClient(srv.URL)
	if _, err := c.Embed(context.Background(), []string{"x"}, false, KBPriorityIndex); err == nil || !strings.Contains(err.Error(), "dimension 1536") {
		t.Fatalf("want a dimension error, got %v", err)
	}
}

func TestFIX132_KBModelsErrorClasses(t *testing.T) {
	fake := kbfake.New()
	c, _ := NewKBModelsClient(fake.URL)

	fake.SetStatus("loading")
	_, err := c.Embed(context.Background(), []string{"x"}, false, KBPriorityIndex)
	if !IsKBModelsLoading(err) || !IsKBModelsUnavailable(err) {
		t.Fatalf("503 models loading → loading + unavailable, got %v", err)
	}
	if _, herr := c.Health(context.Background()); !errors.Is(herr, ErrKBModelsLoading) {
		t.Fatalf("health loading → ErrKBModelsLoading, got %v", herr)
	}
	fake.SetStatus("failed")
	if _, herr := c.Health(context.Background()); herr == nil || IsKBModelsLoading(herr) || !IsKBModelsUnavailable(herr) {
		t.Fatalf("health failed → unavailable, not loading, got %v", herr)
	}
	fake.SetStatus("ready")
	fake.Fail(1, 500)
	if _, err := c.Segment(context.Background(), []string{"x"}); !IsKBModelsUnavailable(err) || IsKBModelsLoading(err) {
		t.Fatalf("500 → unavailable, got %v", err)
	}
	fake.Fail(1, 400)
	if _, err := c.Rerank(context.Background(), "q", []string{"d"}); err == nil || IsKBModelsUnavailable(err) {
		t.Fatalf("400 is about the input, not the service, got %v", err)
	}
	fake.Close()
	if _, err := c.Embed(context.Background(), []string{"x"}, false, KBPriorityIndex); !IsKBModelsUnavailable(err) {
		t.Fatalf("connection refused → unavailable, got %v", err)
	}
	// A caller that gave up is not a verdict on kb-models.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := c.Embed(ctx, []string{"x"}, false, KBPriorityIndex); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled caller → context.Canceled, got %v", err)
	}

	var none *KBModelsClient
	if _, err := none.Embed(context.Background(), []string{"x"}, false, KBPriorityIndex); !errors.Is(err, ErrKBModelsNotConfigured) || !IsKBModelsUnavailable(err) {
		t.Fatalf("nil client → not configured, got %v", err)
	}
}

func TestFIX132_KBModelsRerankAndSegment(t *testing.T) {
	fake := kbfake.New("ราคา", "ห้อง", "พัก")
	defer fake.Close()
	c, _ := NewKBModelsClient(fake.URL)
	scores, err := c.Rerank(context.Background(), "ราคาห้องพัก", []string{"อาหารเช้า", "ราคาห้องพัก 1200 บาท", "ห้องประชุม"})
	if err != nil {
		t.Fatal(err)
	}
	if len(scores) != 3 || !(scores[1] > scores[2] && scores[2] > scores[0]) {
		t.Fatalf("scores come back in the documents' order, got %v", scores)
	}
	seg, err := c.Segment(context.Background(), []string{"ราคาห้องพัก", "hello world"})
	if err != nil {
		t.Fatal(err)
	}
	if seg[0] != "ราคา ห้อง พัก" || seg[1] != "hello world" {
		t.Fatalf("segment: %q", seg)
	}
}

// The knowledge-base path names no OpenAI key, model or endpoint any more:
// the models are local (FIX132 B). This reads the source, so a message like
// "set OPENAI_API_KEY" coming back into a KB file fails here.
func TestFIX132_KBPathHasNoOpenAI(t *testing.T) {
	files := []string{
		"knowledge_service.go", "rag_service.go", "knowledge_availability.go",
		"kb_models_client.go", "chunker.go", "knowledge_worker.go",
		"../handler/knowledge.go", "../repository/knowledge_repo.go",
	}
	re := regexp.MustCompile(`(?i)openai|text-embedding-3|api\.openai\.com`)
	for _, f := range files {
		b, err := os.ReadFile(filepath.FromSlash(f))
		if err != nil {
			if os.IsNotExist(err) && f == "knowledge_worker.go" {
				continue
			}
			t.Fatal(err)
		}
		for i, line := range strings.Split(string(b), "\n") {
			if re.MatchString(line) && !strings.Contains(line, "FIX132") && !strings.Contains(line, "until FIX132") {
				t.Errorf("%s:%d names OpenAI in the knowledge-base path: %s", f, i+1, strings.TrimSpace(line))
			}
		}
	}
}

func equalInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
