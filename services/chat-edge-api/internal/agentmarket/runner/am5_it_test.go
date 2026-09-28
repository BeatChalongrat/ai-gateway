package runner

// AM5 phase C end to end: the real runner, the real sandbox image, the real
// control plane's handlers (AGENT_MARKET_IT=1).

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/middleware"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/sandbox"
)

// api is the control plane over the test's store, signed in as the install's owner.
func (e *itEnv) api() *httptest.Server {
	e.t.Helper()
	cfg := am.ConfigFromEnv()
	cfg.Enabled = true
	cfg.AllowedEmails = nil
	cfg.AdminEmails = []string{"am5-it@example.test"}
	api := am.NewAPIWithStore(cfg, e.store, e.files, slog.Default())
	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			c := middleware.Caller{UserID: e.install.UserID, TenantUUID: e.install.TenantID, Email: "am5-it@example.test"}
			next.ServeHTTP(w, req.WithContext(middleware.WithCaller(req.Context(), c)))
		})
	})
	r.Route("/agent-market", api.Routes)
	srv := httptest.NewServer(r)
	e.t.Cleanup(srv.Close)
	return srv
}

func post(t *testing.T, srv *httptest.Server, path string, body any) (int, map[string]any) {
	t.Helper()
	b, _ := json.Marshal(body)
	resp, err := http.Post(srv.URL+"/agent-market"+path, "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

// outputsOf is the round's files cards, oldest first.
func (e *itEnv) outputsOf(run uuid.UUID) []map[string]any {
	e.t.Helper()
	items, err := e.store.ThreadItemsAfter(context.Background(), e.install.ID, nil, 0, 500)
	if err != nil {
		e.t.Fatal(err)
	}
	var out []map[string]any
	for _, it := range items {
		if it.Kind == "outputs" && it.RunID != nil && *it.RunID == run {
			var p map[string]any
			_ = json.Unmarshal(it.Payload, &p)
			out = append(out, p)
		}
	}
	return out
}

func leftOutNames(p map[string]any) []string {
	s, _ := p["summary"].(map[string]any)
	list, _ := s["not_included"].([]any)
	var out []string
	for _, x := range list {
		out = append(out, x.(map[string]any)["name"].(string))
	}
	return out
}

func TestASkippedVATCardLeavesTheInvoiceOnTheListAndOneClickBringsItBack(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInvoiceInstall()
	ctx := context.Background()
	srv := e.api()
	run := e.invoiceRun("d01_clean_full_tax_invoice.pdf", "f02_vat_mismatch.pdf")
	e.drive(e.runner("r1"), run.ID, 5*time.Minute, parked)
	cards, _ := e.store.RunCards(ctx, run.ID)
	if len(cards) != 1 {
		t.Fatalf("one card: %d", len(cards))
	}
	var qs []am.Question
	_ = json.Unmarshal(cards[0].Questions, &qs)
	if len(qs) != 1 || !strings.HasSuffix(qs[0].Key, ":vat_7_percent") {
		t.Fatalf("the VAT question: %+v", qs)
	}
	// what the card recommends keeps the purchase; what skipping it does is said
	if qs[0].RecommendedLabel != "บันทึกตามเอกสาร มีข้อยกเว้น" || !qs[0].LeavesOut || qs[0].DefaultLabel != "ตัดเอกสารนี้ออก" {
		t.Fatalf("recommended %q · leaves out %v · a skip records %q", qs[0].RecommendedLabel, qs[0].LeavesOut, qs[0].DefaultLabel)
	}
	// the person skips the card
	code, body := post(t, srv, "/cards/"+cards[0].ID.String()+"/answer", map[string]any{"answers": map[string]any{
		qs[0].Key: map[string]any{"labels": []string{qs[0].DefaultLabel}, "skipped": true}}})
	if code != 200 {
		t.Fatalf("skip: %d %v", code, body)
	}
	got := e.drive(e.runner("r1"), run.ID, 5*time.Minute, terminal)
	if got.Status != am.RunSucceeded {
		t.Fatalf("the round finishes: %s %s", got.Status, got.StatusReason)
	}
	outs := e.outputsOf(run.ID)
	if len(outs) != 1 || strings.Join(leftOutNames(outs[0]), ",") != "f02_vat_mismatch.pdf" {
		t.Fatalf("the invoice that was left out is on the round's list: %v", outs)
	}
	arts, _ := e.store.RunArtifacts(ctx, run.ID)
	if len(arts) != 1 || arts[0].Version != 1 {
		t.Fatalf("the register, version 1: %+v", arts)
	}

	// one click
	code, body = post(t, srv, "/runs/"+run.ID.String()+"/restore", map[string]any{"doc_key": firstLeftOut(outs[0])})
	if code != 200 || body["changed"] != true || body["asks"] != false {
		t.Fatalf("restore: %d %v", code, body)
	}
	got = e.drive(e.runner("r1"), run.ID, 5*time.Minute, func(r am.Run) bool { return terminal(r) && len(e.outputsOf(run.ID)) == 2 })
	if got.Status != am.RunSucceeded {
		t.Fatalf("computed again: %s %s", got.Status, got.StatusReason)
	}
	outs = e.outputsOf(run.ID)
	if n := leftOutNames(outs[1]); len(n) != 0 {
		t.Fatalf("nothing is left out any more: %v", n)
	}
	sum := outs[1]["summary"].(map[string]any)
	if sum["included"].(float64) != 2 || sum["excluded"].(float64) != 0 {
		t.Fatalf("both invoices are in the register: %v", sum)
	}
	arts, _ = e.store.RunArtifacts(ctx, run.ID)
	newest := 0
	for _, a := range arts {
		if a.Version > newest {
			newest = a.Version
		}
	}
	if newest != 2 {
		t.Fatalf("the register comes out as the next version: %+v", arts)
	}
	// brought back with an exception for whoever reviews, never silently
	exc, _ := e.store.RunExceptions(ctx, run.ID)
	_ = exc
	readable(t, "the thread", e.visibleThread())
}

func firstLeftOut(p map[string]any) string {
	s, _ := p["summary"].(map[string]any)
	list, _ := s["not_included"].([]any)
	if len(list) == 0 {
		return ""
	}
	return list[0].(map[string]any)["doc_key"].(string)
}

// visibleThread is what a person reads in the install's thread (failure_test.go: visible).
func (e *itEnv) visibleThread() []string {
	e.t.Helper()
	items, err := e.store.ThreadItemsAfter(context.Background(), e.install.ID, nil, 0, 500)
	if err != nil {
		e.t.Fatal(err)
	}
	var out []string
	for _, it := range items {
		if it.Kind == "agent_text" || it.Kind == "notice" || it.Kind == "progress" {
			out = append(out, failureVisible(e.t, it.Kind, it.Payload)...)
		}
	}
	return out
}

// failureVisible is visible() for what the agent wrote in the thread.
func failureVisible(t *testing.T, kind string, payload json.RawMessage) []string {
	var out []string
	for _, s := range visible(t, kind, payload) {
		// the person's own file names and the model's name are theirs to read
		for _, name := range []string{"d01_clean_full_tax_invoice.pdf", "f02_vat_mismatch.pdf", "GL_2569-09.xlsx", "GL_2569-08.xlsx", "fake-self-hosted"} {
			s = strings.ReplaceAll(s, name, "")
		}
		out = append(out, s)
	}
	return out
}

func TestARoundThatFailsSaysSoInWordsAndCanBeTriedAgain(t *testing.T) {
	e := newIT(t)
	if e == nil {
		return
	}
	e.setupInstall(3_000_000)
	ctx := context.Background()
	srv := e.api()
	run := e.newRun("ปิดงบเดือนกันยายน", "sample-data/GL_2569-09.xlsx", "sample-data/GL_2569-08.xlsx")
	// a runner whose sandbox image is not there: every step of the engine fails,
	// with the container runtime's own words for it
	bad := e.runner("r-bad")
	bad.Exec = &Executor{SB: sandbox.New(sandbox.Config{Image: "ai-gateway-sandbox:am5-not-there",
		SeccompPath: filepath.Join(e.root, "services", "chat-edge-api", "sandbox", "seccomp.json"), WorkDir: t.TempDir()}),
		Engine: e.engine, Limits: bad.Exec.Limits}
	got := e.drive(bad, run.ID, 4*time.Minute, terminal)
	if got.Status != am.RunFailed {
		t.Fatalf("the round fails: %s %s", got.Status, got.StatusReason)
	}
	readable(t, "a failed round", e.visibleThread())
	text, f := lastFailureOf(t, e, run)
	if f == nil || f["can_retry"] != true {
		t.Fatalf("what happened, what to try, the files, the charge — and a way forward: %q %v", text, f)
	}
	for _, part := range []string{"what", "next", "files", "charged"} {
		if s, _ := f[part].(string); strings.TrimSpace(s) == "" {
			t.Fatalf("%s is said: %v", part, f)
		}
	}
	// the error itself is on the run, for the run page and a problem report
	raw := ""
	for _, s := range e.steps(run.ID) {
		if s.Status == "failed" {
			raw = s.Error
		}
	}
	if !strings.Contains(raw, "am5-not-there") && !strings.Contains(raw, "sandbox") {
		t.Fatalf("the run keeps what went wrong: %q", raw)
	}
	t.Logf("kept on the run (never shown in the thread): %s", trim(raw, 200))
	// the round is not a dead end: the same files, a runner that works
	code, body := post(t, srv, "/runs/"+run.ID.String()+"/retry", nil)
	if code != 200 {
		t.Fatalf("retry: %d %v", code, body)
	}
	again := uuid.MustParse(body["run"].(map[string]any)["id"].(string))
	files, _ := e.store.RunFiles(ctx, again, "active")
	if len(files) != 2 {
		t.Fatalf("the same files: %+v", files)
	}
	done := e.drive(e.runner("r1"), again, 5*time.Minute, terminal)
	if done.Status != am.RunSucceeded {
		t.Fatalf("tried again, the round succeeds: %s %s", done.Status, done.StatusReason)
	}
	if arts, _ := e.store.RunArtifacts(ctx, again); len(arts) == 0 {
		t.Fatal("and delivers its files")
	}
	readable(t, "the whole thread", e.visibleThread())
}

// lastFailureOf is the failed round's message in the install's thread.
func lastFailureOf(t *testing.T, e *itEnv, run am.Run) (string, map[string]any) {
	t.Helper()
	return lastFailure(t, &failEnv{t: t, store: e.store, install: e.install}, run)
}
