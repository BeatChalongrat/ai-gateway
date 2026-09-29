package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// AM4: rounds that do not bleed into each other, threads, the model picker,
// an admin's reasoned access to someone else's run, and a person's delete
// that leaves the organisation's accounting records.

func (e *testEnv) finishedRun(in Install, period, message string) Run {
	e.t.Helper()
	ctx := context.Background()
	r, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
		Message: message, DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
	if err != nil {
		e.t.Fatal(err)
	}
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = 'succeeded', period = $2, started_at = now() - interval '1 minute',
		finished_at = now() WHERE id = $1`, r.ID, period); err != nil {
		e.t.Fatal(err)
	}
	r, _ = e.store.RunByID(ctx, r.ID)
	return r
}

func (e *testEnv) stagedFile(in Install, name string) File {
	e.t.Helper()
	f, err := e.store.CreateFile(context.Background(), e.store.Pool, File{ID: uuid.New(), TenantID: in.TenantID, UserID: in.UserID,
		InstallID: in.ID, Name: name, Size: 10, SHA256: name, StorageKey: "agent-market/" + name, Status: "staged"})
	if err != nil {
		e.t.Fatal(err)
	}
	return f
}

func (e *testEnv) threadItems(in Install, thread string) []map[string]any {
	e.t.Helper()
	path := "/installs/" + in.ID.String() + "/thread"
	if thread != "" {
		path += "?thread=" + thread
	}
	code, body := e.do("GET", path, nil)
	if code != 200 {
		e.t.Fatalf("thread: %d %v", code, body)
	}
	var out []map[string]any
	for _, it := range body["items"].([]any) {
		out = append(out, it.(map[string]any))
	}
	return out
}

func lastOfKind(items []map[string]any, kind string) map[string]any {
	for i := len(items) - 1; i >= 0; i-- {
		if items[i]["kind"] == kind {
			return items[i]
		}
	}
	return nil
}

// Files after a finished round: never slipped into it, never silently a new
// one — a chip asks. Before AM4 they became a new run (or an "include?" card
// on the finished round whose default carried the old round's files).
func TestFilesAfterAFinishedRoundAskBeforeAnything(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	sept := e.finishedRun(in, "2026-09", "ปิดงบเดือนกันยายน")
	f := e.stagedFile(in, "GL_10.xlsx")
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{f.ID.String()},
		"client_id": "am4-files"})
	if code != 200 || body["route"] != "intent" || body["run_id"] != nil {
		t.Fatalf("files after a finished round must ask first: %d %v", code, body)
	}
	if n, _ := e.store.RunFiles(ctx, sept.ID); len(n) != 0 {
		t.Fatalf("the finished round got the new file: %v", n)
	}
	var runs int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_runs WHERE install_id = $1 AND kind = 'run'`, in.ID).Scan(&runs)
	if runs != 1 {
		t.Fatalf("no new round starts on its own: %d runs", runs)
	}
	chip := lastOfKind(e.threadItems(in, ""), "intent")
	if chip == nil {
		t.Fatal("no chip in the thread")
	}
	p := chip["payload"].(map[string]any)
	if p["status"] != "open" || p["last_round"].(map[string]any)["period_label"] != "กันยายน 2569" {
		t.Fatalf("chip: %v", p)
	}
	// [เริ่มรอบใหม่]: a round with the new file only, and not the old round's instruction
	code, body = e.do("POST", "/intents/"+chip["id"].(string), map[string]any{"choice": "new_round"})
	if code != 200 || body["run_id"] == nil {
		t.Fatalf("new round: %d %v", code, body)
	}
	nr, _ := e.store.RunByID(ctx, uuid.MustParse(body["run_id"].(string)))
	files, _ := e.store.RunFiles(ctx, nr.ID)
	if len(files) != 1 || files[0].ID != f.ID || nr.Message != "" {
		t.Fatalf("the new round carries only what was sent now: files %v message %q", files, nr.Message)
	}
	// a second click changes nothing
	if code, body := e.do("POST", "/intents/"+chip["id"].(string), map[string]any{"choice": "about_last"}); code != 200 || body["changed"] != false {
		t.Fatalf("second click: %d %v", code, body)
	}
}

func TestAnInstructionAfterAFinishedRoundAsksAQuestionDoesNot(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	e.finishedRun(in, "2026-09", "ปิดงบเดือนกันยายน")
	send := func(text, client string) map[string]any {
		code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": text, "client_id": client})
		if code != 200 {
			t.Fatalf("%q: %d %v", text, code, body)
		}
		return body
	}
	for _, q := range []string{"ทำไมยอดชำระสุทธิเป็น 0 บาท", "ขอเป็น PDF ด้วย", "รายได้เดือนนี้เท่าไร"} {
		if b := send(q, "q-"+q); b["route"] != "reply" {
			t.Fatalf("a question about the round is answered, not asked about: %q → %v", q, b["route"])
		}
	}
	if b := send("ขอบคุณครับ", "thanks"); b["route"] != "reply" {
		t.Fatalf("thanks is not an instruction: %v", b["route"])
	}
	for _, instr := range []string{"ปิดงบเดือนตุลาคม", "ทำรอบใหม่", "ลบแชทนี้แล้วเริ่มใหม่หมด"} {
		if b := send(instr, "i-"+instr); b["route"] != "intent" {
			t.Fatalf("an instruction after a finished round asks: %q → %v", instr, b["route"])
		}
	}
	chip := lastOfKind(e.threadItems(in, ""), "intent")
	if chip["payload"].(map[string]any)["fresh"] != true {
		t.Fatal("someone about to delete the chat is told why they need not")
	}
}

func TestStartRoundButtonThenFilesStartTheRound(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	e.finishedRun(in, "2026-09", "ปิดงบเดือนกันยายน")
	time.Sleep(10 * time.Millisecond)
	if code, body := e.do("POST", "/installs/"+in.ID.String()+"/rounds", map[string]any{}); code != 200 {
		t.Fatalf("start round: %d %v", code, body)
	}
	f := e.stagedFile(in, "GL_10.xlsx")
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{f.ID.String()}, "client_id": "b"})
	if code != 200 || body["route"] != "new_run" {
		t.Fatalf("after เริ่มรอบใหม่ the files start the round without asking: %d %v", code, body)
	}
	// a round in progress cannot be started over by the button
	if code, _ := e.do("POST", "/installs/"+in.ID.String()+"/rounds", map[string]any{}); code != http.StatusConflict {
		t.Fatalf("round in progress: %d", code)
	}
}

// A new round inherits settings and remembered answers only.
func TestANewRoundInheritsSettingsAndRememberedAnswersOnly(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	sept := e.finishedRun(in, "2026-09", "ปิดงบเดือนกันยายน")
	old := e.stagedFile(in, "GL_09.xlsx")
	if _, err := e.store.AttachFiles(ctx, e.store.Pool, in.TenantID, in.UserID, sept.ID, []uuid.UUID{old.ID}, "active"); err != nil {
		t.Fatal(err)
	}
	// September's answers: a convention (remembered), and "leave the October rows out" (this round's data)
	_ = e.store.MergeAnswers(ctx, e.store.Pool, sept.ID, map[string]Answer{"period_mixed": {Value: json.RawMessage(`"exclude"`), Via: "card"}})
	qs := []Question{
		{Key: "date_order:GL_09.xlsx", Code: "date_order", Header: "ลำดับวันที่", Rememberable: true},
		{Key: "period_mixed", Code: "period_mixed", Header: "รายการนอกงวด", Rememberable: true},
		{Key: "currency_mixed", Code: "currency_mixed", Header: "สกุลเงิน", Rememberable: true},
	}
	ans := map[string]Answer{
		"date_order:GL_09.xlsx": {Value: json.RawMessage(`"mdy"`), Label: "เดือน/วัน/ปี", Via: "card"},
		"period_mixed":          {Value: json.RawMessage(`"exclude"`), Label: "ตัดออก", Via: "card"},
		"currency_mixed":        {Value: json.RawMessage(`"exclude_foreign"`), Label: "ตัดออก", Via: "card"},
	}
	n, err := e.store.RememberAnswers(ctx, e.store.Pool, sept, uuid.New(), qs, ans)
	if err != nil || n != 1 {
		t.Fatalf("only the convention is remembered: %d %v", n, err)
	}
	// what an older version stored is not passed on either
	_ = e.store.SetDefault(ctx, e.store.Pool, in.ID, Default{Key: "answers.period_mixed", Value: json.RawMessage(`"exclude"`), Source: "answer"})
	defs, _ := e.store.ListDefaults(ctx, in.ID)
	engine, _ := DefaultsForEngine(defs)
	answers := engine["answers"].(map[string]json.RawMessage)
	if string(answers["date_order"]) != `"mdy"` || answers["period_mixed"] != nil || answers["currency_mixed"] != nil {
		t.Fatalf("the engine gets the convention only: %v", answers)
	}
	if engine["formats"] == nil {
		t.Fatal("the install's settings carry over")
	}
	// the new round: no file, figure, answer or instruction of September's
	f := e.stagedFile(in, "GL_10.xlsx")
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"text": "ปิดงบ", "file_ids": []string{f.ID.String()},
		"client_id": "x"})
	if code != 200 || body["route"] != "intent" {
		t.Fatalf("%d %v", code, body)
	}
	chip := lastOfKind(e.threadItems(in, ""), "intent")
	_, body = e.do("POST", "/intents/"+chip["id"].(string), map[string]any{"choice": "new_round"})
	nr, _ := e.store.RunByID(ctx, uuid.MustParse(body["run_id"].(string)))
	files, _ := e.store.RunFiles(ctx, nr.ID)
	if len(files) != 1 || files[0].Name != "GL_10.xlsx" {
		t.Fatalf("the new round has only its own files: %v", files)
	}
	var got map[string]Answer
	_ = json.Unmarshal(nr.Answers, &got)
	if len(got) != 0 || nr.Period != nil || nr.Message != "ปิดงบ" {
		t.Fatalf("no answer, period or instruction of September's: %v %v %q", got, nr.Period, nr.Message)
	}
}

func TestThreadsKeepTheirOwnRounds(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	e.finishedRun(in, "2026-09", "")
	code, body := e.do("POST", "/installs/"+in.ID.String()+"/threads", nil)
	if code != 200 {
		t.Fatalf("new thread: %d %v", code, body)
	}
	tid := body["thread"].(map[string]any)["id"].(string)
	items := e.threadItems(in, tid)
	if len(items) != 1 || items[0]["kind"] != "greeting" {
		t.Fatalf("a new thread starts with the greeting only: %v", items)
	}
	// files in the new thread start a round there, no chip (its own first round)
	f := e.stagedFile(in, "GL_10.xlsx")
	code, body = e.do("POST", "/installs/"+in.ID.String()+"/messages", map[string]any{"file_ids": []string{f.ID.String()},
		"client_id": "t1", "thread_id": tid})
	if code != 200 || body["route"] != "new_run" {
		t.Fatalf("thread's first round: %d %v", code, body)
	}
	run, _ := e.store.RunByID(ctx, uuid.MustParse(body["run_id"].(string)))
	if run.ThreadID == nil || run.ThreadID.String() != tid {
		t.Fatalf("the round belongs to the thread: %v", run.ThreadID)
	}
	if lastOfKind(e.threadItems(in, ""), "user_files") != nil {
		t.Fatal("the first thread does not show the second thread's message")
	}
	_, list := e.do("GET", "/installs/"+in.ID.String()+"/threads", nil)
	if n := len(list["threads"].([]any)); n != 2 {
		t.Fatalf("two threads listed: %d", n)
	}
	// both chats now have a September round: each is still told apart by its
	// own name (the AM4 screen tour saw two rows reading "รอบ กันยายน 2569")
	if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET period = '2026-09' WHERE id = $1`, run.ID); err != nil {
		t.Fatal(err)
	}
	_, list = e.do("GET", "/installs/"+in.ID.String()+"/threads", nil)
	titles := map[string]bool{}
	for _, x := range list["threads"].([]any) {
		title := x.(map[string]any)["title"].(string)
		if titles[title] || !strings.Contains(title, "กันยายน 2569") || !strings.HasPrefix(title, "แชท") {
			t.Fatalf("each chat named apart, with its latest round: %v", list["threads"])
		}
		titles[title] = true
	}
	// someone else's thread id is refused
	if code, _ := e.do("GET", "/installs/"+in.ID.String()+"/thread?thread="+uuid.NewString(), nil); code != http.StatusNotFound {
		t.Fatalf("unknown thread: %d", code)
	}
}

const textManifest = `{"schema_version":"agent.market.v1","id":"sran.financial-report","version":"1.0.0","name":"รายงานการเงิน",
"short_name":"ปิดงบการเงิน","python_package":"financial_report","greeting":{"text":"สวัสดีครับ"},
"inputs":[{"role":"primary","label":"GL","required":true}],"outputs":[{"format":"docx","label":"รายงาน","default":true}],
"capabilities":{"required":["spreadsheet.read.xlsx","deliverable.report.docx","model.text"]},
"install":{"defaults":{"formats":["docx"]}},"permissions":{"default":"read_only"},"cannot":"อ่านอย่างเดียว"}`

// AM6: the menu lists models by their ids (am6_models_test.go); what AM4
// decided stays — a model outside the organisation can be chosen only once
// the organisation lets documents leave, and the choice is the install's.
func TestModelPickerOffersOutsideModelsOnlyWhenDocumentsMayLeave(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.financial-report", Version: "1.0.0", Name: "รายงานการเงิน",
		Manifest: json.RawMessage(textManifest), Checksum: "pkgsum", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	caps := fullCaps()
	caps["model.text"] = Binding{Capability: "model.text", Impl: "gateway", Version: "qwen|deepseek"}
	rm, _ := json.Marshal(RunnerModels{Primary: "qwen", Fallback: "deepseek", Vision: "qwen", SelfHosted: map[string]bool{"qwen": true},
		Catalog: []CatalogModel{{ID: "qwen", SelfHosted: true, Vision: true, InPrice: 0.1, OutPrice: 0.1},
			{ID: "deepseek", InPrice: 0.3, OutPrice: 0.5}}})
	caps["platform.models"] = Binding{Capability: "platform.models", Impl: "gateway", Detail: rm}
	e.heartbeat(caps)
	_, _ = e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id) VALUES ('default') ON CONFLICT DO NOTHING`)
	code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"})
	if code != 200 {
		t.Fatalf("install: %d %v", code, body)
	}
	id := body["install"].(map[string]any)["id"].(string)
	code, p := e.do("GET", "/installs/"+id+"/models", nil)
	if code != 200 || p["choice"] != "auto" || p["uses"] != "text" {
		t.Fatalf("picker: %d %v", code, p)
	}
	if got := menuOf(t, p); got != "deepseek=blocked qwen=allowed" || !strings.Contains(p["blocked_reason"].(string), "นอกองค์กร") {
		t.Fatalf("an outside model is shown blocked, with the reason: %s %q", got, p["blocked_reason"])
	}
	if code, _ := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "deepseek"}); code != http.StatusConflict {
		t.Fatalf("an outside model cannot be picked while documents stay inside: %d", code)
	}
	// the admin lets documents leave: now it can be picked, and it is remembered for the install
	if code, body := e.do("PUT", "/admin/org", map[string]any{"documents_may_leave": true}); code != 200 {
		t.Fatalf("admin: %d %v", code, body)
	}
	code, p = e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "deepseek"})
	if code != 200 || p["choice"] != "deepseek" {
		t.Fatalf("pick: %d %v", code, p)
	}
	in, _ := e.store.GetInstall(ctx, e.user.TenantUUID, e.user.UserID, uuid.MustParse(id))
	if in.ModelChoice != "deepseek" {
		t.Fatalf("remembered per install: %q", in.ModelChoice)
	}
}

func (e *testEnv) addMember(email string) {
	e.t.Helper()
	if code, body := e.do("PUT", "/admin/members/"+email, map[string]any{"role": "member"}); code != 200 {
		e.t.Fatalf("add member: %d %v", code, body)
	}
}

func TestAnAdminSeesMetadataAndOpensContentOnlyWithAReasonTheOwnerIsTold(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	e.seedDefinition()
	e.heartbeat(fullCaps())
	_, _ = e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id) VALUES ('default') ON CONFLICT DO NOTHING`)
	_, _ = e.store.Pool.Exec(ctx, `INSERT INTO agent_market.catalog_state (org_id, slug, status) VALUES ('default', 'sran.financial-report', 'published') ON CONFLICT DO NOTHING`)
	member := "am4-member@example.test"
	e.addMember(member)
	code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"}, "X-Test-Email", member)
	if code != 200 {
		t.Fatalf("member install: %d %v", code, body)
	}
	mid := uuid.MustParse(body["install"].(map[string]any)["id"].(string))
	min, _ := e.store.InstallByID(ctx, mid)
	run := e.finishedRun(min, "2026-09", "เงินเดือนพนักงาน")
	// a member sees no one's runs
	if code, _ := e.do("GET", "/admin/runs", nil, "X-Test-Email", member); code != http.StatusForbidden {
		t.Fatalf("member: %d", code)
	}
	code, list := e.do("GET", "/admin/runs", nil)
	if code != 200 {
		t.Fatalf("admin list: %d %v", code, list)
	}
	row := list["runs"].([]any)[0].(map[string]any)
	if row["id"] != run.ID.String() || row["status"] != "succeeded" || row["period_label"] != "กันยายน 2569" {
		t.Fatalf("metadata: %v", row)
	}
	for _, k := range []string{"message", "answers", "files", "artifacts", "steps"} {
		if _, ok := row[k]; ok {
			t.Fatalf("the list carries no content (%s): %v", k, row)
		}
	}
	// content is closed until opened with a reason
	if code, _ := e.do("GET", "/admin/runs/"+run.ID.String(), nil); code != http.StatusForbidden {
		t.Fatalf("content without a reason: %d", code)
	}
	if code, _ := e.do("POST", "/admin/runs/"+run.ID.String()+"/open", map[string]any{"reason": "ดู"}); code != http.StatusBadRequest {
		t.Fatalf("a reason is required: %d", code)
	}
	reason := "ตรวจปัญหาที่ผู้ใช้แจ้งเรื่องยอดไม่ตรง"
	if code, body := e.do("POST", "/admin/runs/"+run.ID.String()+"/open", map[string]any{"reason": reason}); code != 200 {
		t.Fatalf("open: %d %v", code, body)
	}
	if code, body := e.do("GET", "/admin/runs/"+run.ID.String(), nil); code != 200 || body["run"] == nil {
		t.Fatalf("content after opening: %d %v", code, body)
	}
	// the owner is told, in their thread and on the run's page
	code, th := e.do("GET", "/installs/"+mid.String()+"/thread", nil, "X-Test-Email", member)
	if code != 200 {
		t.Fatalf("owner thread: %d", code)
	}
	told := false
	for _, it := range th["items"].([]any) {
		im := it.(map[string]any)
		if im["kind"] == "notice" && strings.Contains(im["payload"].(map[string]any)["text"].(string), reason) {
			told = true
		}
	}
	if !told {
		t.Fatal("the owner's thread says who opened the run and why")
	}
	code, rd := e.do("GET", "/runs/"+run.ID.String(), nil, "X-Test-Email", member)
	if code != 200 || len(rd["access_log"].([]any)) != 1 {
		t.Fatalf("the run page lists the opening: %d %v", code, rd["access_log"])
	}
	// the opening lasts an hour
	_, _ = e.store.Pool.Exec(ctx, `UPDATE agent_market.run_access_log SET expires_at = now() - interval '1 second'`)
	if code, _ := e.do("GET", "/admin/runs/"+run.ID.String(), nil); code != http.StatusForbidden {
		t.Fatalf("expired: %d", code)
	}
}

func TestAPersonalDeleteKeepsTheOrganisationsRecords(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	put := func(key string) {
		if err := e.api.files.Upload(ctx, key, strings.NewReader("x"), "", 1); err != nil {
			t.Fatal(err)
		}
	}
	done := e.finishedRun(in, "2026-09", "ปิดงบ")
	up := UploadKey(in.TenantID, in.ID, uuid.New(), "gl.xlsx")
	put(up)
	rid := done.ID
	if _, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: uuid.New(), TenantID: in.TenantID, UserID: in.UserID, InstallID: in.ID,
		RunID: &rid, Name: "gl.xlsx", Size: 1, SHA256: "a", StorageKey: up, Status: "active"}); err != nil {
		t.Fatal(err)
	}
	art := ArtifactKey(in.TenantID, done.ID, "report_v1.docx")
	put(art)
	if _, _, err := e.store.InsertArtifact(ctx, Artifact{RunID: done.ID, InstallID: in.ID, Format: "docx", Kind: "report",
		Name: "report_v1.docx", Version: 1, StorageKey: art, Size: 1, SHA256: "b", ProducedByStep: "k"}, in.TenantID, in.UserID); err != nil {
		t.Fatal(err)
	}
	_, _, _ = e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, nil, UserMessage{Text: "คุยเรื่องส่วนตัว"}, nil)
	_, _, _ = e.store.AddItem(ctx, e.store.Pool, in.ID, in.TenantID, in.UserID, &rid, OutputsPayload{RunID: done.ID.String()}, nil)
	p := NewAccountProvider(e.store, e.api.files)
	counts, err := p.Purge(ctx, in.UserID)
	if err != nil {
		t.Fatal(err)
	}
	if counts["org_records_kept"] != 1 {
		t.Fatalf("counts: %v", counts)
	}
	for _, k := range []string{up, art} {
		if ok, _ := e.api.files.Exists(ctx, k); !ok {
			t.Fatalf("the finished round's file is the organisation's record and stays: %s", k)
		}
	}
	var chat, defaults int
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_thread_items WHERE install_id = $1 AND kind = 'user_text'`, in.ID).Scan(&chat)
	_ = e.store.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_install_defaults WHERE install_id = $1`, in.ID).Scan(&defaults)
	if chat != 0 || defaults != 0 {
		t.Fatalf("the person's conversation and settings go: chat %d settings %d", chat, defaults)
	}
	got, _ := e.store.InstallByID(ctx, in.ID)
	if got.Status != "removed" {
		t.Fatalf("the install is no longer theirs to use: %s", got.Status)
	}
}

func TestStorageSaysWhatIsKeptAndThatItIsNotTheArchive(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	in := e.install()
	code, body := e.do("GET", "/installs/"+in.ID.String()+"/storage", nil)
	if code != 200 || body["retention_years"].(float64) != 5 || !strings.Contains(body["not_archive"].(string), "ไม่ใช่ที่เก็บเอกสารของบริษัท") {
		t.Fatalf("storage: %d %v", code, body)
	}
}

// Removing an agent stops its unfinished rounds. Before AM4 a queued or parked
// round of a removed install still ran (and was charged) once it was claimed
// or resumed — the AM4 probe watched a self-test finish, and cost credits,
// 20 s after its agent was removed; its files went to an install nobody sees.
func TestRemovingAnAgentStopsItsUnfinishedRounds(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install()
	done := e.finishedRun(in, "2026-09", "ปิดงบเดือนกันยายน")
	mk := func(status string) Run {
		r, _, err := e.store.CreateRun(ctx, e.store.Pool, Run{InstallID: in.ID, TenantID: in.TenantID, UserID: in.UserID, Kind: KindRun,
			DefinitionVersion: in.DefinitionVersion, Checksum: in.Checksum}, "")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := e.store.Pool.Exec(ctx, `UPDATE agent_market.agent_runs SET status = $2,
			awaiting_since = CASE WHEN $2 = 'awaiting_input' THEN now() END WHERE id = $1`, r.ID, status); err != nil {
			t.Fatal(err)
		}
		return r
	}
	queued, parked, running := mk("queued"), mk("awaiting_input"), mk("running")
	if code, body := e.do("DELETE", "/installs/"+in.ID.String(), nil); code != 200 {
		t.Fatalf("remove: %d %v", code, body)
	}
	for _, c := range []struct {
		run    Run
		status string
		cancel bool
	}{{queued, "cancelled", true}, {parked, "cancelled", true}, {running, "running", true}, {done, "succeeded", false}} {
		got, err := e.store.RunByID(ctx, c.run.ID)
		if err != nil {
			t.Fatal(err)
		}
		if got.Status != c.status || got.CancelRequested != c.cancel {
			t.Errorf("run %s after the agent was removed: status %s cancel %v, want %s %v", c.run.ID, got.Status, got.CancelRequested, c.status, c.cancel)
		}
	}
	// a parked round cannot be resumed afterwards (a raised budget, a late answer)
	if ok, err := e.store.Resume(ctx, e.store.Pool, parked.ID); err != nil || ok {
		t.Fatalf("resumed a round of a removed agent: %v %v", ok, err)
	}
}
