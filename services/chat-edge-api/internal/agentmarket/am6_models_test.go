package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// AM6 — the agent's model menu is the chat's. The rows AM4 and AM5 built
// (outcomes, cost and time per row, "one in-house model", "choose your own")
// are gone: the server lists every model that can do the agent's job under its
// own id, says of each whether the organisation's policy allows it, and says
// once why the others are blocked or not listed.

const scanManifest = `{"schema_version":"agent.market.v1","id":"sran.invoice-processing","version":"1.0.0","name":"ใบกำกับภาษี",
"short_name":"ใบกำกับภาษี","python_package":"invoice_processing","greeting":{"text":"สวัสดีครับ"},
"inputs":[{"role":"invoice","label":"ใบกำกับภาษี","required":true,"accept":[".pdf"]}],
"outputs":[{"format":"xlsx","label":"ทะเบียน","default":true}],"narrative":"rules",
"capabilities":{"required":["spreadsheet.read.xlsx"],"optional":["model.vision"]},
"install":{"defaults":{"formats":["xlsx"]}},"permissions":{"default":"read_only"},"cannot":"อ่านอย่างเดียว"}`

var menuModels = RunnerModels{Primary: "qwen", Fallback: "deepseek", Vision: "qwen", SelfHosted: map[string]bool{"qwen": true},
	Catalog: []CatalogModel{{ID: "typhoon", SelfHosted: true}, {ID: "qwen", SelfHosted: true, Vision: true},
		{ID: "gpt-4o", Vision: true, Recipients: []string{"openrouter:openai", "openrouter:microsoft"}},
		{ID: "deepseek", InPrice: 0.3, OutPrice: 0.5, Recipients: []string{"deepseek"}}}}

// menuEnv installs an agent with the given manifest next to a runner that has menuModels.
func menuEnv(t *testing.T, slug, manifest string) (*testEnv, string) {
	t.Helper()
	e := newEnv(t)
	if e == nil {
		return nil, ""
	}
	ctx := context.Background()
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: slug, Version: "1.0.0", Name: "agent ทดสอบ",
		Manifest: json.RawMessage(manifest), Checksum: "pkgsum", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	caps := fullCaps()
	caps["model.text"] = Binding{Capability: "model.text", Impl: "gateway", Version: "qwen|deepseek"}
	detail, _ := json.Marshal(menuModels)
	caps["platform.models"] = Binding{Capability: "platform.models", Impl: "gateway", Detail: detail}
	e.heartbeat(caps)
	_, _ = e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id) VALUES ('default') ON CONFLICT DO NOTHING`)
	code, body := e.do("POST", "/installs", map[string]any{"slug": slug})
	if code != 200 {
		t.Fatalf("install: %d %v", code, body)
	}
	return e, body["install"].(map[string]any)["id"].(string)
}

// menuOf reads the menu's models as "id=allowed", in the order they were sent.
func menuOf(t *testing.T, p map[string]any) string {
	t.Helper()
	list, ok := p["models"].([]any)
	if !ok {
		t.Fatalf("the menu has no list of models: %v", p)
	}
	var out []string
	for _, x := range list {
		m := x.(map[string]any)
		if len(m) != 2 {
			t.Fatalf("a model of the menu is its id and whether it is allowed, nothing else: %v", m)
		}
		allowed := "blocked"
		if m["allowed"] == true {
			allowed = "allowed"
		}
		out = append(out, m["id"].(string)+"="+allowed)
	}
	return strings.Join(out, " ")
}

func TestModelMenuListsEveryModelThatCanDoTheJob(t *testing.T) {
	e, id := menuEnv(t, "sran.financial-report", textManifest)
	if e == nil {
		return
	}
	code, p := e.do("GET", "/installs/"+id+"/models", nil)
	if code != 200 || p["uses"] != "text" || p["choice"] != "auto" || p["auto_model"] != "qwen" {
		t.Fatalf("menu: %d %v", code, p)
	}
	// every model can write; AM11b: the two outside the organisation are not in the menu while this
	// agent's data stays inside — the menu lists what can run, by id, in the order of the ids
	if got := menuOf(t, p); got != "qwen=allowed typhoon=allowed" || p["outside_absent"] != true {
		t.Fatalf("models: %s (outside_absent %v)", got, p["outside_absent"])
	}
	if why, _ := p["blocked_reason"].(string); !strings.Contains(why, "นอกองค์กร") || !ReadsForPerson(why) {
		t.Fatalf("why the outside models are not listed, in one line: %q", p["blocked_reason"])
	}
	if p["hidden_reason"] != "" {
		t.Fatalf("every model can write, so none is hidden: %q", p["hidden_reason"])
	}
	if p["reads"] == "" || p["note"] == "" {
		t.Fatalf("what the model does for this agent: %v", p)
	}
	// what the owner cancelled is not sent at all
	for _, k := range []string{"options", "custom", "single_in_house", "notice", "locked_reason", "documents_may_leave"} {
		if _, ok := p[k]; ok {
			t.Errorf("%q is still sent: %v", k, p[k])
		}
	}
	// the organisation lets documents leave, and so does this agent's own switch (AM16: off for every
	// agent until an admin turns it on): nothing is blocked, and nothing says so
	if code, body := e.do("PUT", "/admin/org", map[string]any{"documents_may_leave": true}); code != 200 {
		t.Fatalf("admin: %d %v", code, body)
	}
	if code, body := e.do("PUT", "/admin/egress/sran.financial-report", map[string]any{"documents_may_leave": true}); code != 200 {
		t.Fatalf("admin, the agent's own switch: %d %v", code, body)
	}
	// AM16: both switches on and nobody named is nobody — the menu is as short as before, and says why
	_, p = e.do("GET", "/installs/"+id+"/models", nil)
	if got := menuOf(t, p); got != "qwen=allowed typhoon=allowed" || !strings.Contains(p["blocked_reason"].(string), "ยังไม่ได้ระบุผู้ให้บริการ") {
		t.Fatalf("may leave, nobody named: %s, reason %q", got, p["blocked_reason"])
	}
	// Microsoft named (it runs gpt-4o behind the router) and DeepSeek: for the organisation, then the agent
	named := map[string]any{"recipients": []string{"deepseek", "openrouter:microsoft"}}
	if code, body := e.do("PUT", "/admin/egress/recipients", named); code != 200 {
		t.Fatalf("admin, the organisation's list: %d %v", code, body)
	}
	if code, body := e.do("PUT", "/admin/egress/sran.financial-report", named); code != 200 {
		t.Fatalf("admin, the agent's own list: %d %v", code, body)
	}
	_, p = e.do("GET", "/installs/"+id+"/models", nil)
	if got := menuOf(t, p); got != "deepseek=allowed gpt-4o=allowed qwen=allowed typhoon=allowed" || p["blocked_reason"] != "" {
		t.Fatalf("documents may leave: %s, reason %q", got, p["blocked_reason"])
	}
}

func TestModelMenuOfAVisionAgentListsOnlyModelsThatReadImages(t *testing.T) {
	e, id := menuEnv(t, "sran.invoice-processing", scanManifest)
	if e == nil {
		return
	}
	code, p := e.do("GET", "/installs/"+id+"/models", nil)
	if code != 200 || p["uses"] != "vision" || p["auto_model"] != "qwen" {
		t.Fatalf("menu: %d %v", code, p)
	}
	if got := menuOf(t, p); got != "qwen=allowed" || p["outside_absent"] != true {
		t.Fatalf("only the models that read images and may be given this agent's scans: %s (outside_absent %v)", got, p["outside_absent"])
	}
	if p["hidden_reason"] != "agent นี้ต้องใช้โมเดลที่อ่านภาพได้" {
		t.Fatalf("why the others are not listed: %q", p["hidden_reason"])
	}
	// a model that cannot read images is refused, with that reason
	code, body := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "typhoon"})
	if c, m := errMsg(body); code != http.StatusConflict || c != "MODEL_NOT_ALLOWED" || !strings.Contains(m, "อ่านภาพได้") {
		t.Fatalf("a model that cannot do the job: %d %v", code, body)
	}
}

func TestAModelIsChosenByItsIdWhenThePolicyAllowsIt(t *testing.T) {
	e, id := menuEnv(t, "sran.financial-report", textManifest)
	if e == nil {
		return
	}
	ctx := context.Background()
	code, p := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "typhoon"})
	if code != 200 || p["choice"] != "typhoon" || p["auto_model"] != "qwen" {
		t.Fatalf("an allowed model: %d %v", code, p)
	}
	// the model "อัตโนมัติ" resolves to is a model of the list like any other
	code, p = e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "qwen"})
	if code != 200 || p["choice"] != "qwen" {
		t.Fatalf("the model auto resolves to, by its id: %d %v", code, p)
	}
	for _, c := range []struct{ choice, says string }{{"gpt-4o", "นอกองค์กร"}, {"not-a-model", "ไม่อยู่ในรายการ"}} {
		code, body := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": c.choice})
		code2, msg := errMsg(body)
		if code != http.StatusConflict || code2 != "MODEL_NOT_ALLOWED" || !strings.Contains(msg, c.says) || !ReadsForPerson(msg) {
			t.Fatalf("%s: %d %v", c.choice, code, body)
		}
	}
	in, _ := e.store.GetInstall(ctx, e.user.TenantUUID, e.user.UserID, uuid.MustParse(id))
	if in.ModelChoice != "qwen" {
		t.Fatalf("a refused choice changes nothing: %q", in.ModelChoice)
	}
	if code, p := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "auto"}); code != 200 || p["choice"] != "auto" {
		t.Fatalf("back to auto: %d %v", code, p)
	}
}

// A message names no model: a round runs with the install's choice. A client
// of before AM6 that still sends model_choice is not refused — the field is
// not read.
func TestAMessageNamesNoModel(t *testing.T) {
	e, id := menuEnv(t, "sran.financial-report", textManifest)
	if e == nil {
		return
	}
	ctx := context.Background()
	if code, p := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "typhoon"}); code != 200 {
		t.Fatalf("choose: %d %v", code, p)
	}
	install := uuid.MustParse(id)
	f, err := e.store.CreateFile(ctx, e.store.Pool, File{ID: uuid.New(), TenantID: e.user.TenantUUID, UserID: e.user.UserID, InstallID: install,
		Name: "GL.xlsx", Size: 10, SHA256: "abc", StorageKey: "agent-market/x", Status: "staged"})
	if err != nil {
		t.Fatal(err)
	}
	code, body := e.do("POST", "/installs/"+id+"/messages", map[string]any{"text": "ปิดงบกันยายน", "file_ids": []string{f.ID.String()},
		"model_choice": "deepseek", "client_id": "m-1"})
	if code != 200 || body["route"] != "new_run" {
		t.Fatalf("a message with a model named by an old client: %d %v", code, body)
	}
	run, err := e.store.RunByID(ctx, uuid.MustParse(body["run_id"].(string)))
	if err != nil {
		t.Fatal(err)
	}
	if run.ModelChoice == nil || *run.ModelChoice != "typhoon" {
		t.Fatalf("the round runs with the install's choice: %v", run.ModelChoice)
	}
}

func TestTheOrganisationKeepsNoListOfModels(t *testing.T) {
	e, id := menuEnv(t, "sran.financial-report", textManifest)
	if e == nil {
		return
	}
	// an admin page of before AM6 may still send its list: it is not read, and it changes nothing
	code, body := e.do("PUT", "/admin/org", map[string]any{"agent_models": []map[string]any{{"model": "typhoon", "outcome": "fast"}}})
	if code != 200 {
		t.Fatalf("admin: %d %v", code, body)
	}
	if _, ok := body["org"].(map[string]any)["agent_models"]; ok {
		t.Fatalf("the organisation's settings carry no list of models: %v", body["org"])
	}
	_, p := e.do("GET", "/installs/"+id+"/models", nil)
	if got := menuOf(t, p); got != "qwen=allowed typhoon=allowed" {
		t.Fatalf("the menu is the runner's models that can run, whatever was sent: %s", got)
	}
	if code, _ := e.do("GET", "/admin/models", nil); code != http.StatusNotFound && code != http.StatusMethodNotAllowed {
		t.Fatalf("GET /admin/models is gone: %d", code)
	}
}

// An agent that sends nothing to a model has no menu.
func TestAnAgentWithoutAModelHasNoMenu(t *testing.T) {
	e, id := menuEnv(t, "sran.financial-report", testManifest)
	if e == nil {
		return
	}
	code, p := e.do("GET", "/installs/"+id+"/models", nil)
	if code != 200 || p["uses"] != "none" || p["choice"] != "auto" || menuOf(t, p) != "" || p["blocked_reason"] != "" || p["hidden_reason"] != "" {
		t.Fatalf("menu: %d %v", code, p)
	}
}
