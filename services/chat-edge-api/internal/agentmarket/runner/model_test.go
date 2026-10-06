package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

func TestGuardNumbersRefusesInventedFigures(t *testing.T) {
	q := map[string]string{"revenue": "12,480,300", "revenue_m": "12.48 ล้าน", "net_margin": "22.0%", "rev_pct": "8.2%"}
	if err := GuardNumbers("รายได้ 12,480,300 บาท (12.48 ล้าน) อัตรากำไร 22.0% เพิ่มขึ้น +8.2% ใน 3 เรื่อง งวดปี 2569", q, nil); err != nil {
		t.Fatalf("quoted numbers must pass: %v", err)
	}
	for _, bad := range []string{"รายได้ 12,480,301 บาท", "กำไร 99.9%", "ยอด 1,000,000", "12.5 ล้าน"} {
		if err := GuardNumbers(bad, q, nil); err == nil {
			t.Fatalf("%q must be refused", bad)
		}
	}
}

// Account codes shown to the model are identifiers, not figures.
func TestGuardNumbersAcceptsAccountCodesFromTheData(t *testing.T) {
	q := map[string]string{"acct_5300-01_change": "+182,400"}
	ids := []string{"5300-01", "510000"}
	for _, ok := range []string{"ค่าใช้จ่ายบัญชี 5300-01 เพิ่มขึ้น +182,400 บาท", "บัญชี 510000 ยอดสูงสุด", "รหัส 510000"} {
		if err := GuardNumbers(ok, q, ids); err != nil {
			t.Fatalf("%q must pass: %v", ok, err)
		}
	}
	for _, bad := range []string{"บัญชี 5999-01 เพิ่มขึ้น", "รายได้ 510000 บาท", "บัญชี 5300-01 เพิ่มขึ้น 182,401 บาท"} {
		if err := GuardNumbers(bad, q, ids); err == nil {
			t.Fatalf("%q must be refused", bad)
		}
	}
}

func readyServer(t *testing.T, body func() string) *httptest.Server {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body())) }))
	t.Cleanup(s.Close)
	return s
}

func TestYieldWaitsWhileAnyChatTurnIsActive(t *testing.T) {
	var state atomic.Value
	state.Store(`{"checks":{"turn_queue":{"active":1,"waiting":0,"max_active":12},"event_loop_lag_ms":0.1}}`)
	s := readyServer(t, func() string { return state.Load().(string) })
	m := &ModelClient{YieldURL: s.URL, YieldMaxWait: 10 * time.Second, HTTP: http.DefaultClient}
	cases := map[string]bool{
		`{"checks":{"turn_queue":{"active":0,"waiting":0,"max_active":12},"event_loop_lag_ms":1}}`:   false,
		`{"checks":{"turn_queue":{"active":1,"waiting":0,"max_active":12},"event_loop_lag_ms":1}}`:   true,
		`{"checks":{"turn_queue":{"active":0,"waiting":2,"max_active":12},"event_loop_lag_ms":1}}`:   true,
		`{"checks":{"turn_queue":{"active":0,"waiting":0,"max_active":12},"event_loop_lag_ms":900}}`: true,
	}
	for body, want := range cases {
		state.Store(body)
		busy, ok := m.chatBusy(context.Background())
		if !ok || busy != want {
			t.Fatalf("%s: busy=%v ok=%v, want %v", body, busy, ok, want)
		}
	}
	// A busy backend holds the call until chat is idle.
	state.Store(`{"checks":{"turn_queue":{"active":1,"waiting":0,"max_active":12}}}`)
	go func() {
		time.Sleep(2500 * time.Millisecond)
		state.Store(`{"checks":{"turn_queue":{"active":0,"waiting":0,"max_active":12}}}`)
	}()
	start := time.Now()
	m.yield(context.Background())
	if waited := time.Since(start); waited < 2*time.Second {
		t.Fatalf("yield returned after %s while a chat turn was active", waited)
	}
}

func fakeGateway(t *testing.T, handler func(model string, n int) (int, string)) (*httptest.Server, *int32) {
	var calls int32
	var mu sync.Mutex
	counts := map[string]int{}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		var req struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		mu.Lock()
		counts[req.Model]++
		n := counts[req.Model]
		mu.Unlock()
		code, content := handler(req.Model, n)
		if code != 200 {
			w.WriteHeader(code)
			_, _ = w.Write([]byte(`{"error":"x"}`))
			return
		}
		b, _ := json.Marshal(map[string]any{"model": req.Model, "choices": []any{map[string]any{
			"message": map[string]any{"content": content}, "finish_reason": "stop"}}, "usage": map[string]any{"total_tokens": 10}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	return s, &calls
}

func TestCompleteFallsBackAndNeverReturnsEmpty(t *testing.T) {
	s, _ := fakeGateway(t, func(model string, n int) (int, string) {
		if model == "primary" {
			return 502, ""
		}
		return 200, `{"ok":true}`
	})
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "primary", Fallback: "fallback", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	c, tried, err := m.Complete(context.Background(), "s", "u", nil)
	if err != nil || c.Model != "fallback" || len(tried) != 1 {
		t.Fatalf("fallback: %+v %v %v", c, tried, err)
	}
	s2, _ := fakeGateway(t, func(model string, n int) (int, string) { return 200, "   " })
	m2 := &ModelClient{BaseURL: s2.URL, APIKey: "k", Primary: "a", Fallback: "b", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	if _, _, err := m2.Complete(context.Background(), "s", "u", nil); err != ErrNoModel {
		t.Fatalf("an empty answer from every model must be ErrNoModel, got %v", err)
	}
}

func TestCompleteRetriesOnceWithTheReasonThenMovesOn(t *testing.T) {
	s, calls := fakeGateway(t, func(model string, n int) (int, string) {
		if model == "primary" {
			return 200, "made-up 99"
		}
		return 200, "clean"
	})
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "primary", Fallback: "fallback", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	c, tried, err := m.Complete(context.Background(), "s", "u", func(content string) error {
		if strings.Contains(content, "99") {
			return fmt.Errorf("invented number")
		}
		return nil
	})
	if err != nil || c.Model != "fallback" || atomic.LoadInt32(calls) != 3 || len(tried) != 2 {
		t.Fatalf("want 2 tries on primary then fallback: model=%s calls=%d tried=%v err=%v", c.Model, *calls, tried, err)
	}
}

func TestOnlyOneAgentModelCallInFlight(t *testing.T) {
	var inflight, peak int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&inflight, 1)
		for {
			p := atomic.LoadInt32(&peak)
			if n <= p || atomic.CompareAndSwapInt32(&peak, p, n) {
				break
			}
		}
		time.Sleep(150 * time.Millisecond)
		atomic.AddInt32(&inflight, -1)
		_, _ = w.Write([]byte(`{"model":"m","choices":[{"message":{"content":"x"},"finish_reason":"stop"}]}`))
	}))
	defer s.Close()
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "m", Timeout: 5 * time.Second, HTTP: http.DefaultClient, Concurrency: 1}
	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); _, _, _ = m.Complete(context.Background(), "s", "u", nil) }()
	}
	wg.Wait()
	if peak != 1 {
		t.Fatalf("agent model calls in flight peaked at %d, want 1", peak)
	}
}

func engineSrc(t *testing.T) string {
	return filepath.Join(repoRoot(t), "backend", "agent_market")
}

func TestEngineZipIsDeterministicAndChecksummed(t *testing.T) {
	src := engineSrc(t)
	a, err := BuildZip(src)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := BuildZip(src)
	if string(a) != string(b) {
		t.Fatal("two builds of the same source must be byte-identical")
	}
	p := filepath.Join(t.TempDir(), "e.zip")
	_ = os.WriteFile(p, a, 0o644)
	e, err := LoadEngine(p, "")
	if err != nil {
		t.Fatal(err)
	}
	pkg := e.Packages["sran.financial-report"]
	if pkg == nil || pkg.Version != "1.7.1" || len(pkg.Checksum) != 64 || pkg.Instructions == "" || len(pkg.SelfTest) == 0 {
		t.Fatalf("package not loaded: %+v", pkg)
	}
	if _, err := e.PackageFile(pkg, "sample-data/GL_2569-09.xlsx"); err != nil {
		t.Fatalf("sample data ships in the zip: %v", err)
	}
	if strings.Contains(string(a), "evals/fixtures/") || strings.Contains(string(a), "evals/nasty/") || strings.Contains(string(a), "agent_market/tests/") {
		t.Fatal("tests and eval fixtures stay out of the runtime zip")
	}
	// A changed package file changes the package checksum, not the engine's.
	tmp := t.TempDir()
	copyTree(t, src, tmp)
	f := filepath.Join(tmp, "packages", "financial_report", "AGENT.md")
	body, _ := os.ReadFile(f)
	_ = os.WriteFile(f, append(body, []byte("\n")...), 0o644)
	c, _ := BuildZip(tmp)
	p2 := filepath.Join(t.TempDir(), "e2.zip")
	_ = os.WriteFile(p2, c, 0o644)
	e2, _ := LoadEngine(p2, "")
	if e2.Packages["sran.financial-report"].Checksum == pkg.Checksum || e2.Checksum != e.Checksum {
		t.Fatal("package checksum must follow its files; engine checksum must not")
	}
	// AM2: the build (what a released version is pinned to) follows every file, packages included
	e3, _ := LoadEngine(p, "")
	if len(e.Build) != 64 || e3.Build != e.Build || e2.Build == e.Build {
		t.Fatal("the build hash is the same for the same zip and differs when a package changes")
	}
}

func copyTree(t *testing.T, src, dst string) {
	t.Helper()
	_ = filepath.Walk(src, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, p)
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		return os.WriteFile(target, b, 0o644)
	})
}

// costGateway answers the n-th call with replies[n] (content, cost, cached).
func costGateway(t *testing.T, replies []struct {
	content string
	cost    float64
	cached  bool
}) *httptest.Server {
	var n int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		i := int(atomic.AddInt32(&n, 1)) - 1
		if i >= len(replies) {
			i = len(replies) - 1
		}
		rp := replies[i]
		b, _ := json.Marshal(map[string]any{"model": "primary", "cached": rp.cached, "choices": []any{map[string]any{
			"message": map[string]any{"content": rp.content}, "finish_reason": "stop"}},
			"usage": map[string]any{"total_tokens": 100, "estimated_cost_usd": rp.cost}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	return s
}

// The agent's budget counts what the platform paid: every attempt a model
// answered (a rejected one too), and nothing for a gateway cache hit — the
// gateway replays the original response, original cost included.
func TestBudgetCountsPaidAttemptsAndNoCacheHits(t *testing.T) {
	type rp = struct {
		content string
		cost    float64
		cached  bool
	}
	reject99 := func(content string) error {
		if strings.Contains(content, "99") {
			return fmt.Errorf("invented number")
		}
		return nil
	}
	s := costGateway(t, []rp{{"made-up 99", 0.001, false}, {"clean", 0.002, true}})
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "primary", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	c, _, err := m.Complete(context.Background(), "s", "u", reject99)
	if err != nil || c.CostUSD != 0.001 {
		t.Fatalf("rejected paid attempt counts, cached answer is free: cost=%v err=%v", c.CostUSD, err)
	}
	s2 := costGateway(t, []rp{{"made-up 99", 0.001, false}, {"still 99", 0.003, false}})
	m2 := &ModelClient{BaseURL: s2.URL, APIKey: "k", Primary: "primary", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	c2, _, err := m2.Complete(context.Background(), "s", "u", reject99)
	if err != ErrNoModel || c2.CostUSD != 0.004 {
		t.Fatalf("when every answer is refused the spend is still reported: cost=%v err=%v", c2.CostUSD, err)
	}
}

// AM1b walkthrough: the self-test's narrative fell back to rules because every
// model wrote the sign in words ("ต่ำกว่างบ 6.8%" for -6.8%) or quoted a
// variance with its own sign. The figure is the table's; the direction is prose.
func TestGuardNumbersAcceptsTheSignInWords(t *testing.T) {
	q := map[string]string{"net_profit_var_budget_pct": "-6.8%", "revenue_var_prior_m": "0.95 ล้าน", "cogs_var": "(545,400)",
		"net_margin_change_points": "-1.54 จุด"}
	for _, ok := range []string{"กำไรสุทธิต่ำกว่างบ 6.8%", "รายได้เพิ่มขึ้น 0.95 ล้าน", "ต่างจากงวดก่อน -0.95 ล้าน", "ลดลง 545,400 บาท",
		"อัตรากำไรสุทธิลดลง 1.54 จุด", "เปลี่ยน -1.54 จุด"} {
		if err := GuardNumbers(ok, q, nil); err != nil {
			t.Fatalf("%q must pass: %v", ok, err)
		}
	}
	for _, bad := range []string{"ต่ำกว่างบ 6.9%", "เพิ่มขึ้น 0.96 ล้าน", "ลดลง 545,401 บาท", "ลดลง 1.55 จุด"} {
		if err := GuardNumbers(bad, q, nil); err == nil {
			t.Fatalf("%q must be refused", bad)
		}
	}
}

// AM3: a person's typed words about an invoice or an expense claim (an
// extraction agent: narrative "rules", no text model declared) go only to a
// model inside the organisation — never to the external fallback.
func TestExtractionAgentsSendTypedWordsOnlyInsideTheOrganisation(t *testing.T) {
	var mu sync.Mutex
	seen := map[string]int{}
	s, _ := fakeGateway(t, func(model string, n int) (int, string) {
		mu.Lock()
		seen[model]++
		mu.Unlock()
		if model == "qwen" {
			return 502, ""
		}
		return 200, `{"choice": 1}`
	})
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "qwen", Fallback: "deepseek", SelfHosted: map[string]bool{"qwen": true},
		Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	invoice := &Package{Manifest: am.Manifest{Narrative: "rules"}}
	invoice.Manifest.Capabilities.Required = []string{"document.extract.e2"}
	invoice.Manifest.Capabilities.Optional = []string{"model.vision"}
	fin := &Package{}
	fin.Manifest.Capabilities.Required = []string{"model.text"}
	rcInv := &runCtx{r: &Runner{Model: m}, pkg: invoice}
	rcFin := &runCtx{r: &Runner{Model: m}, pkg: fin}
	if got := rcInv.textModels(); len(got) != 1 || got[0] != "qwen" {
		t.Fatalf("the invoice agent's text models: %v", got)
	}
	// AM4: the outside fallback only while the organisation lets documents leave
	if got := rcFin.textModels(); len(got) != 1 || got[0] != "qwen" {
		t.Fatalf("documents stay inside: the financial report uses the in-house model only: %v", got)
	}
	rcFinOut := &runCtx{r: &Runner{Model: m}, pkg: fin, docsMayLeave: true}
	if got := rcFinOut.textModels(); len(got) != 2 || got[1] != "deepseek" {
		t.Fatalf("documents may leave: its primary then its fallback: %v", got)
	}
	q := am.Question{Key: "k", Question: "ผู้ซื้อไม่ตรง", Options: []am.Option{{Label: "ใช้ตามเอกสาร"}, {Label: "ตัดออก"}}}
	if _, ok := rcInv.classify(context.Background(), q, "ใช้ได้"); ok {
		t.Fatal("the self-hosted model failed: no answer, and the external one is never asked")
	}
	if seen["deepseek"] != 0 || seen["qwen"] == 0 {
		t.Fatalf("calls: %v", seen)
	}
	// no self-hosted model at all: nothing is sent anywhere
	m2 := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "deepseek", Fallback: "gpt", Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	rc2 := &runCtx{r: &Runner{Model: m2}, pkg: invoice}
	before := seen["deepseek"]
	if _, ok := rc2.classify(context.Background(), q, "ใช้ได้"); ok || seen["deepseek"] != before {
		t.Fatalf("an extraction agent with no self-hosted model sends nothing: %v", seen)
	}
}

// AM4: every call tells the gateway never to answer with another model, and
// an answer from a model other than the one asked for is not used.
func TestAModelCallNeverTakesAnotherModelsAnswer(t *testing.T) {
	var header string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		header = r.Header.Get(FallbackOptOutHeader)
		b, _ := json.Marshal(map[string]any{"model": "deepseek-v4-flash", "choices": []any{map[string]any{
			"message": map[string]any{"content": `{"ok":true}`}, "finish_reason": "stop"}}, "usage": map[string]any{"total_tokens": 10}})
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.Close)
	m := &ModelClient{BaseURL: s.URL, APIKey: "k", Primary: "qwen3.8-27b-fp8", SelfHosted: map[string]bool{"qwen3.8-27b-fp8": true},
		Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	if _, _, err := m.CompleteWith(context.Background(), []string{"qwen3.8-27b-fp8"}, "s", "u", nil); err == nil {
		t.Fatal("an answer by another model must not be used")
	}
	if header != FallbackOptOutValue {
		t.Fatalf("the request opts out of the gateway's rescue: %q", header)
	}
	if !sameModel("sranai/qwen3.8-27b-fp8", "qwen3.8-27b-fp8") || sameModel("deepseek-v4-flash", "qwen3.8-27b-fp8") {
		t.Fatal("sameModel")
	}
}

// AM4: the round's choice picks the vision model, as the organisation allows.
func TestTheRoundsChoiceReadsTheScansAsTheOrganisationAllows(t *testing.T) {
	m := &ModelClient{BaseURL: "http://x", APIKey: "k", Primary: "qwen", VisionModel: "qwen", SelfHosted: map[string]bool{"qwen": true}}
	m.catalog.models = []am.CatalogModel{{ID: "qwen", SelfHosted: true, Vision: true}, {ID: "gpt-vision", Vision: true}, {ID: "deepseek"}}
	m.catalog.fetched = time.Now()
	pkg := &Package{}
	pkg.Manifest.Capabilities.Optional = []string{"model.vision"}
	choose := func(c string, leave bool) (string, bool) {
		rc := &runCtx{r: &Runner{Model: m}, pkg: pkg, docsMayLeave: leave}
		rc.run.ModelChoice = &c
		return rc.visionModel()
	}
	if got, not := choose(am.ModelAuto, false); got != "qwen" || not {
		t.Fatalf("auto: %s %v", got, not)
	}
	// AM11b: an outside model while documents stay inside is not in the menu; a pick of it from before is
	// no pick — the round runs as "อัตโนมัติ", and nothing is said afterwards about a model "not used"
	if got, not := choose("gpt-vision", false); got != "qwen" || not {
		t.Fatalf("a stale pick of an outside model runs as auto, and is not reported as swapped: %s %v", got, not)
	}
	if got, not := choose("gpt-vision", true); got != "gpt-vision" || not {
		t.Fatalf("allowed: %s %v", got, not)
	}
	if got, not := choose("deepseek", true); got != "qwen" || not {
		t.Fatalf("a model that cannot read images is not given scans, and a pick of it is no pick: %s %v", got, not)
	}
}
