package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
)

// AM6 — knowledge in ask mode. The chart of accounts, the cost-centre list and
// the expense policy live in the person's knowledge base, and they are what
// someone wants when asking about a figure. They never become the source of a
// number in a deliverable: a knowledge base named with a message is read only
// when the message is a question the agent answers (a reply), never when it
// starts or feeds a round.

const kbPassage = "## Retrieved knowledge base passages\n[1] [Source: นโยบายค่าเดินทาง.pdf]\nวงเงินค่าเดินทางในประเทศ 3,500 บาทต่อวัน"

// fakeKnowledge is the person's knowledge base: what was asked of it, and by whom.
type fakeKnowledge struct {
	mu    sync.Mutex
	calls []struct {
		tenant, user, kb uuid.UUID
		query            string
	}
}

func (f *fakeKnowledge) Context(_ context.Context, tenant, user, kb uuid.UUID, query string, _ int) (string, []KnowledgeSource, string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, struct {
		tenant, user, kb uuid.UUID
		query            string
	}{tenant, user, kb, query})
	return kbPassage, []KnowledgeSource{{Title: "นโยบายค่าเดินทาง.pdf", Part: 3}}, "นโยบายและผังบัญชี", nil
}

func (f *fakeKnowledge) asked() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

func (e *testEnv) withKnowledge() *fakeKnowledge {
	f := &fakeKnowledge{}
	e.api.SetKnowledge(f)
	return f
}

// jobKnowledge is what a reply job carries of a knowledge base (nil = nothing).
func (e *testEnv) jobKnowledge(id uuid.UUID) map[string]any {
	e.t.Helper()
	job, err := e.store.RunByID(context.Background(), id)
	if err != nil {
		e.t.Fatal(err)
	}
	var p map[string]any
	_ = json.Unmarshal(job.Params, &p)
	k, _ := p["knowledge"].(map[string]any)
	return k
}

func TestAQuestionInAskModeCarriesTheKnowledgeItFound(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	f := e.withKnowledge()
	in := e.install()
	kb := uuid.New()
	// no round yet: a question is answered from the knowledge base alone
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "รหัสบัญชีค่าเดินทางคืออะไร?",
		"knowledge_base_id": kb.String(), "client_id": "k-1"})
	if code != 200 || body["route"] != "reply" {
		t.Fatalf("a question: %d %v", code, body)
	}
	k := e.jobKnowledge(uuid.MustParse(body["run_id"].(string)))
	if k == nil || k["base"] != "นโยบายและผังบัญชี" || k["text"] != kbPassage {
		t.Fatalf("the reply job carries what was found: %v", k)
	}
	src, _ := k["sources"].([]any)
	if len(src) != 1 || src[0].(map[string]any)["title"] != "นโยบายค่าเดินทาง.pdf" {
		t.Fatalf("its sources: %v", k["sources"])
	}
	if f.asked() != 1 || f.calls[0].kb != kb || f.calls[0].user != e.user.UserID || f.calls[0].tenant != e.user.TenantUUID ||
		f.calls[0].query != "รหัสบัญชีค่าเดินทางคืออะไร?" {
		t.Fatalf("read as the signed-in person, with the question: %+v", f.calls)
	}
	// after a finished round: a question about it, with the knowledge base
	e.finishedRun(in, "2026-09", "ปิดงบกันยายน")
	code, body = e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "ค่าเดินทาง 12,000 บาท เกินวงเงินไหม?",
		"knowledge_base_id": kb.String(), "client_id": "k-2"})
	if code != 200 || body["route"] != "reply" {
		t.Fatalf("a question about the round: %d %v", code, body)
	}
	job, _ := e.store.RunByID(context.Background(), uuid.MustParse(body["run_id"].(string)))
	if job.ParentRunID == nil || e.jobKnowledge(job.ID)["base"] != "นโยบายและผังบัญชี" {
		t.Fatalf("the reply is about the round, with the knowledge: %+v", job)
	}
	// without a knowledge base named, nothing is read
	code, body = e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "รายได้รวมเท่าไร?", "client_id": "k-3"})
	if code != 200 || body["route"] != "reply" || e.jobKnowledge(uuid.MustParse(body["run_id"].(string))) != nil || f.asked() != 2 {
		t.Fatalf("no knowledge base: %d %v, read %d times", code, body, f.asked())
	}
}

// A control plane with no knowledge reader (the chat's knowledge is off, or an
// older chat-edge): a question is a reply as it always was.
func TestWithoutAKnowledgeReaderAQuestionIsAnsweredAsBefore(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "รหัสบัญชีค่าเดินทางคืออะไร?",
		"knowledge_base_id": uuid.NewString(), "client_id": "k-1"})
	if code != 200 || body["route"] != "reply" || e.jobKnowledge(uuid.MustParse(body["run_id"].(string))) != nil {
		t.Fatalf("%d %v", code, body)
	}
}

// Knowledge never feeds a round: a message that starts one, resumes one, adds
// a file to one or answers its card does not read the knowledge base at all,
// and nothing of it is on the round — its row is what its steps and its
// compute key are made of.
func TestKnowledgeNeverFeedsARound(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	f := e.withKnowledge()
	in := e.install()
	kb := uuid.New()
	clean := func(where string, id uuid.UUID) {
		t.Helper()
		run, err := e.store.RunByID(ctx, id)
		if err != nil {
			t.Fatal(err)
		}
		b, _ := json.Marshal(run)
		for _, s := range []string{kb.String(), "knowledge", "วงเงินค่าเดินทาง", "นโยบายค่าเดินทาง"} {
			if strings.Contains(string(b), s) {
				t.Fatalf("%s: the round carries %q: %s", where, s, b)
			}
		}
		if f.asked() != 0 {
			t.Fatalf("%s: the knowledge base was read (%d)", where, f.asked())
		}
	}
	send := func(body map[string]any) map[string]any {
		t.Helper()
		body["knowledge_base_id"] = kb.String()
		code, out := e.do("POST", "/installs/"+in.ID.String()+"/messages", body)
		if code != 200 {
			t.Fatalf("message: %d %v", code, out)
		}
		return out
	}
	// a file starts a round: the knowledge base is not used, and the thread says so
	out := send(map[string]any{"text": "ปิดงบกันยายน ใช้ผังบัญชีในฐานความรู้", "file_ids": []string{e.stagedFile(in, "GL_09.xlsx").ID.String()},
		"client_id": "r-1"})
	if out["route"] != "new_run" {
		t.Fatalf("a file starts a round: %v", out)
	}
	first := uuid.MustParse(out["run_id"].(string))
	clean("a new round", first)
	told := lastOfKind(e.threadItems(in, ""), "notice")
	if told == nil || !strings.Contains(told["payload"].(map[string]any)["text"].(string), "คลังความรู้") ||
		!strings.Contains(out["notice"].(string), "คลังความรู้") {
		t.Fatalf("the person is told the knowledge base was not used with files: %v / %v", told, out["notice"])
	}
	// the round is working: a file sent now is asked about, for this round
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'running' WHERE id = $1`, first); err != nil {
		t.Fatal(err)
	}
	out = send(map[string]any{"text": "เพิ่มไฟล์นี้", "file_ids": []string{e.stagedFile(in, "GL_09b.xlsx").ID.String()}, "client_id": "r-2"})
	if out["route"] != "late_file" {
		t.Fatalf("a late file: %v", out)
	}
	clean("a late file", first)
	// the round waits on a question: a sentence answers it
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', awaiting_since = now() WHERE id = $1`, first); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_run_clarifications SET status = 'answered' WHERE run_id = $1`, first); err != nil {
		t.Fatal(err)
	}
	run, _ := e.store.RunByID(ctx, first)
	e.openCard(run, true)
	out = send(map[string]any{"text": "ตัดออกตามนโยบายค่าเดินทางในฐานความรู้", "client_id": "r-3"})
	if out["route"] != "answer_text" {
		t.Fatalf("an answer to a card: %v", out)
	}
	clean("an answer to a card", first)
	// a round waiting for files is resumed by one
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'awaiting_input', awaiting_since = now() WHERE id = $1`, first); err != nil {
		t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_run_clarifications SET status = 'answered' WHERE run_id = $1`, first); err != nil {
		t.Fatal(err)
	}
	out = send(map[string]any{"text": "ไฟล์ที่ขอ", "file_ids": []string{e.stagedFile(in, "TB_09.xlsx").ID.String()}, "client_id": "r-4"})
	if out["route"] != "resumed" {
		t.Fatalf("a round resumed: %v", out)
	}
	clean("a resumed round", first)
	// a round that failed: "ทำรอบใหม่" starts the next one
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'failed', finished_at = now() WHERE id = $1`, first); err != nil {
		t.Fatal(err)
	}
	out = send(map[string]any{"text": "ทำรอบใหม่ตามผังบัญชีในฐานความรู้", "client_id": "r-5"})
	if out["route"] != "new_run" {
		t.Fatalf("a new round typed: %v", out)
	}
	clean("a round started by a sentence", uuid.MustParse(out["run_id"].(string)))
}
