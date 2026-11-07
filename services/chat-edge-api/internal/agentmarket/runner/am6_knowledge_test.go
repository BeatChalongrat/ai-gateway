package runner

import (
	"context"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM6 — knowledge in ask mode, on the runner's side. A question asked with a
// knowledge base is answered with the round's computed figures and the
// passages the control plane found; the passages explain, they never replace
// a figure of the round, and a number quoted from them (a limit of the
// policy) is a quotation, not a computed figure. The answer says it came from
// the knowledge base. Nothing but the reply job reads what was found.

var kbFound = &am.KnowledgeContext{Base: "นโยบายและผังบัญชี",
	Text:    "## Retrieved knowledge base passages\n[1] [Source: นโยบายค่าเดินทาง.pdf]\nวงเงินค่าเดินทางในประเทศ 3,500 บาทต่อวัน",
	Sources: []am.KnowledgeSource{{Title: "นโยบายค่าเดินทาง.pdf", Part: 3}}}

// askedGateway answers with the given contents in turn and keeps what it was asked.
func askedGateway(t *testing.T, contents ...string) (*httptest.Server, func() []string) {
	t.Helper()
	var mu sync.Mutex
	var asked []string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		mu.Lock()
		i := len(asked)
		asked = append(asked, string(b))
		mu.Unlock()
		if i >= len(contents) {
			i = len(contents) - 1
		}
		out, _ := json.Marshal(map[string]any{"model": "primary", "choices": []any{map[string]any{
			"message": map[string]any{"content": contents[i]}, "finish_reason": "stop"}},
			"usage": map[string]any{"total_tokens": 100, "estimated_cost_usd": 0.001}})
		_, _ = w.Write(out)
	}))
	t.Cleanup(s.Close)
	return s, func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), asked...)
	}
}

// computedRound is a finished round with its computed table, as its compute step left it.
func (e *failEnv) computedRound(computed string) am.Run {
	e.t.Helper()
	ctx := context.Background()
	parent := e.round(am.KindRun, true)
	stepKey := am.StepKey(parent.ID, "compute", "am6-knowledge")
	key := am.BlobKey(parent.TenantID, parent.ID, stepKey, "computed.json")
	if err := e.r.Files.Upload(ctx, key, strings.NewReader(computed), "application/json", int64(len(computed))); err != nil {
		e.t.Fatal(err)
	}
	st, _, err := e.store.BeginStep(ctx, parent.ID, "compute", "compute", stepKey, json.RawMessage(`{}`))
	if err != nil {
		e.t.Fatal(err)
	}
	out, _ := json.Marshal(map[string]any{"computed": map[string]any{"key": key}, "computed_key": key, "summary": map[string]any{}})
	if err := e.store.FinishStep(ctx, st.ID, "succeeded", out, "", "scripts/metrics.py", "", 1); err != nil {
		e.t.Fatal(err)
	}
	_ = e.store.Finish(ctx, parent.ID, e.r.ID, am.RunSucceeded, "")
	p, _ := e.store.RunByID(ctx, parent.ID)
	return p
}

func (e *failEnv) replyCtx(t *testing.T, gateway string) (*Runner, *runCtx) {
	t.Helper()
	ctx := context.Background()
	job, _, err := e.store.CreateRun(ctx, e.store.Pool, am.Run{InstallID: e.install.ID, TenantID: e.install.TenantID,
		UserID: e.install.UserID, Kind: am.KindReply, Message: "ถาม", DefinitionVersion: "1.0.0", Checksum: "pkgsum"}, "")
	if err != nil {
		t.Fatal(err)
	}
	m := &ModelClient{Primary: "primary", SelfHosted: map[string]bool{"primary": true}, Timeout: 5 * time.Second, HTTP: http.DefaultClient}
	if gateway != "" {
		m.BaseURL, m.APIKey = gateway, "k"
	}
	r := &Runner{Store: e.store, Files: e.r.Files, Log: slog.Default(), ID: e.r.ID, Now: time.Now, CreditsPerUSD: 1000, Model: m}
	var man am.Manifest
	_ = json.Unmarshal([]byte(`{"narrative":"rules","capabilities":{"required":["model.text"]}}`), &man)
	return r, &runCtx{ctx: ctx, r: r, run: job, install: e.install, pkg: &Package{Manifest: man}, log: slog.Default()}
}

// sentKnowledge is the answer's knowledge as the thread sends it to a person.
func sentKnowledge(t *testing.T, a am.AgentText) map[string]any {
	t.Helper()
	raw, _ := json.Marshal(a)
	var sent map[string]any
	_ = json.Unmarshal(am.ForClient("agent_text", raw), &sent)
	k, _ := sent["knowledge"].(map[string]any)
	return k
}

func TestAQuestionWithKnowledgeIsAnsweredWithTheRoundsFiguresAndThePassages(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	parent := e.computedRound(`{"quotable":{"travel":"12,000.00"},"citations":[]}`)
	answer := `{"answer":"ค่าเดินทางของรอบนี้คือ 12,000.00 บาท และตามนโยบายค่าเดินทางในฐานความรู้ วงเงินในประเทศคือ 3,500 บาทต่อวัน"}`
	s, asked := askedGateway(t, answer)
	r, rc := e.replyCtx(t, s.URL)
	got := r.answerWith(context.Background(), rc, parent, "ค่าเดินทางเกินวงเงินไหม?", kbFound)
	if !strings.Contains(got.Text.String(), "3,500") || !strings.Contains(got.Text.String(), "12,000.00") {
		t.Fatalf("a number quoted from the passages is a quotation, not an invented figure: %q", got.Text)
	}
	req := asked()
	if len(req) != 1 || !strings.Contains(req[0], "วงเงินค่าเดินทางในประเทศ 3,500 บาทต่อวัน") || !strings.Contains(req[0], "12,000.00") {
		t.Fatalf("the model is asked with the round's figures and the passages: %v", req)
	}
	if !strings.Contains(req[0], "ไม่ใช่ตัวเลขของรอบนี้") {
		t.Fatalf("the model is told the passages never replace a figure of the round: %s", req[0])
	}
	k := sentKnowledge(t, got)
	src, _ := k["sources"].([]any)
	if k["base"] != "นโยบายและผังบัญชี" || len(src) != 1 || src[0].(map[string]any)["title"] != "นโยบายค่าเดินทาง.pdf" ||
		src[0].(map[string]any)["locator"] != "ส่วนที่ 3" {
		t.Fatalf("the answer says where it came from, and a person is sent it: %v", k)
	}
	if _, has := k["text"]; has {
		t.Fatalf("the passages themselves are not sent on the answer: %v", k)
	}
	// a number that is in neither the round nor the passages is refused, as before
	s, _ = askedGateway(t, `{"answer":"วงเงินคือ 4,200 บาท"}`)
	r, rc = e.replyCtx(t, s.URL)
	if got := r.answerWith(context.Background(), rc, parent, "ค่าเดินทางเกินวงเงินไหม?", kbFound); !strings.Contains(got.Text.String(), "ยังตอบคำถามนี้") ||
		got.Knowledge != nil {
		t.Fatalf("an invented number: %q %v", got.Text, got.Knowledge)
	}
	// without the knowledge base, the policy's limit is not the round's: refused
	s, _ = askedGateway(t, answer)
	r, rc = e.replyCtx(t, s.URL)
	if got := r.answerWith(context.Background(), rc, parent, "ค่าเดินทางเกินวงเงินไหม?", nil); !strings.Contains(got.Text.String(), "ยังตอบคำถามนี้") ||
		got.Knowledge != nil {
		t.Fatalf("no knowledge: %q", got.Text)
	}
}

func TestAQuestionWithNoRoundIsAnsweredFromTheKnowledgeAlone(t *testing.T) {
	e := newFailEnv(t)
	if e == nil {
		return
	}
	parent := e.computedRound(`{"quotable":{"travel":"12,000.00"},"citations":[]}`)
	s, asked := askedGateway(t, `{"answer":"วงเงินค่าเดินทางในประเทศคือ 3,500 บาทต่อวัน ตามนโยบายค่าเดินทาง"}`)
	r, rc := e.replyCtx(t, s.URL)
	got := r.answerFromKnowledge(context.Background(), rc, "วงเงินค่าเดินทางเท่าไร?", kbFound)
	if !strings.Contains(got.Text.String(), "3,500") || sentKnowledge(t, got)["base"] != "นโยบายและผังบัญชี" {
		t.Fatalf("an answer from the knowledge base: %q %v", got.Text, got.Knowledge)
	}
	if req := asked(); len(req) != 1 || !strings.Contains(req[0], "วงเงินค่าเดินทางในประเทศ 3,500") {
		t.Fatalf("asked with the passages: %v", req)
	}
	if runJSON(t, e, rc.run.ID)["credits_spent"].(float64) != 1 {
		t.Fatalf("an answer the person got is charged")
	}
	// no model inside the organisation: the sentence it always was, and where to look in the knowledge base
	r, rc = e.replyCtx(t, "")
	got = r.answerFromKnowledge(context.Background(), rc, "วงเงินค่าเดินทางเท่าไร?", kbFound)
	if !strings.Contains(got.Text.String(), "ไม่มีโมเดลในองค์กรที่ตอบคำถามได้") || !strings.Contains(got.Text.String(), "นโยบายค่าเดินทาง.pdf") ||
		!am.ReadsForPerson(got.Text.String()) || sentKnowledge(t, got)["base"] != "นโยบายและผังบัญชี" {
		t.Fatalf("no model: %q", got.Text)
	}
	got = r.answerWith(context.Background(), rc, parent, "ค่าเดินทางเกินวงเงินไหม?", kbFound)
	if !strings.Contains(got.Text.String(), "ไม่มีโมเดลในองค์กรที่ตอบคำถามได้") || !strings.Contains(got.Text.String(), "นโยบายค่าเดินทาง.pdf") ||
		!strings.Contains(got.Text.String(), "ขั้นตอนและที่มาของตัวเลข") || got.Knowledge == nil {
		t.Fatalf("no model, about a round: %q", got.Text)
	}
}

// A deliverable never sees knowledge: no code of the runner but the reply
// job's reads what a job carries of a knowledge base.
func TestOnlyTheReplyJobReadsKnowledge(t *testing.T) {
	allowed := map[string]bool{"replyJob": true, "answerWith": true, "answerFromKnowledge": true, "knowledgeGuard": true,
		"withoutModel": true}
	// AM11: what a model is told about a knowledge base stands in two constants of its own (jobs.go: a
	// prompt is not a sentence for a person, and is kept apart from the sentences that are). A constant
	// reads nothing; whoever uses one is a function, and is held to the list above like any other.
	prompts := map[string]bool{"knowledgeRules": true, "knowledgeQuestionRules": true}
	files, _ := filepath.Glob("*.go")
	readers := map[string]bool{}
	fs := token.NewFileSet()
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") || strings.HasPrefix(f, "english") {
			continue // AM11: the catalogue holds the English of sentences (some name the knowledge base); it reads nothing
		}
		file, err := parser.ParseFile(fs, f, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, d := range file.Decls {
			fn, ok := d.(*ast.FuncDecl)
			if !ok {
				ast.Inspect(d, func(n ast.Node) bool {
					if id, ok := n.(*ast.Ident); ok && prompts[id.Name] {
						return true
					}
					if names(n) {
						t.Errorf("%s declares something of knowledge outside a function of the reply job", fs.Position(n.Pos()))
					}
					return true
				})
				continue
			}
			ast.Inspect(fn, func(n ast.Node) bool {
				if names(n) {
					readers[fn.Name.Name] = true
					if !allowed[fn.Name.Name] {
						t.Errorf("%s: %s reads knowledge; only the reply job may", fs.Position(n.Pos()), fn.Name.Name)
					}
				}
				return true
			})
		}
	}
	var got []string
	for k := range readers {
		got = append(got, k)
	}
	sort.Strings(got)
	if !readers["replyJob"] {
		t.Fatalf("the reply job reads the knowledge its job carries (readers: %v)", got)
	}
	t.Logf("the functions that read knowledge: %v", got)
}

// names: the node names a knowledge base (an identifier, a field, a string).
func names(n ast.Node) bool {
	switch x := n.(type) {
	case *ast.Ident:
		return strings.Contains(strings.ToLower(x.Name), "knowledge")
	case *ast.BasicLit:
		return x.Kind == token.STRING && strings.Contains(strings.ToLower(x.Value), "knowledge")
	}
	return false
}
