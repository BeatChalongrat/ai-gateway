package agentmarket

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

// AM5 phase B — the model menu on the owner's desktop offered
// "อัตโนมัติ (แนะนำ) · qwen3.8-27b-fp8 · ≈0.02 บาท · 21 วินาที", a locked
// "เร็วและประหยัด", and "แม่นที่สุด · qwen3.8-27b-fp8 · ≈0.02 บาท · 21 วินาที":
// two of three rows the identical model at the identical price, and the only
// different one disabled. A row is a choice only when it resolves to a model
// no other row does.

func pickerEnv(t *testing.T, rm RunnerModels) (*testEnv, string) {
	t.Helper()
	e := newEnv(t)
	if e == nil {
		return nil, ""
	}
	ctx := context.Background()
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.financial-report", Version: "1.0.0", Name: "รายงานการเงิน",
		Manifest: json.RawMessage(textManifest), Checksum: "pkgsum", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	caps := fullCaps()
	caps["model.text"] = Binding{Capability: "model.text", Impl: "gateway", Version: rm.Primary + "|" + rm.Fallback}
	detail, _ := json.Marshal(rm)
	caps["platform.models"] = Binding{Capability: "platform.models", Impl: "gateway", Detail: detail}
	e.heartbeat(caps)
	_, _ = e.store.Pool.Exec(ctx, `INSERT INTO agent_market.org_settings (org_id) VALUES ('default') ON CONFLICT DO NOTHING`)
	code, body := e.do("POST", "/installs", map[string]any{"slug": "sran.financial-report"})
	if code != 200 {
		t.Fatalf("install: %d %v", code, body)
	}
	return e, body["install"].(map[string]any)["id"].(string)
}

func optionModels(t *testing.T, p map[string]any) []string {
	t.Helper()
	var out []string
	seen := map[string]bool{}
	for _, o := range p["options"].([]any) {
		m := o.(map[string]any)["model"].(string)
		if seen[m] {
			t.Fatalf("two rows resolve to the same model %q: %v", m, p["options"])
		}
		seen[m] = true
		out = append(out, m)
	}
	return out
}

func customOf(p map[string]any, id string) map[string]any {
	list, _ := p["custom"].([]any)
	for _, c := range list {
		if cm := c.(map[string]any); cm["id"] == id {
			return cm
		}
	}
	return nil
}

func TestModelPickerNeverShowsTheSameModelTwice(t *testing.T) {
	// the live system: one model in the organisation, the runner's second model outside it
	e, id := pickerEnv(t, RunnerModels{Primary: "qwen", Fallback: "deepseek", Vision: "qwen", SelfHosted: map[string]bool{"qwen": true},
		Catalog: []CatalogModel{{ID: "qwen", SelfHosted: true, Vision: true, InPrice: 0.1, OutPrice: 0.1}, {ID: "deepseek", InPrice: 0.3, OutPrice: 0.5}}})
	if e == nil {
		return
	}
	code, p := e.do("GET", "/installs/"+id+"/models", nil)
	if code != 200 {
		t.Fatalf("picker: %d %v", code, p)
	}
	models := optionModels(t, p)
	if len(models) != 2 || models[0] != "qwen" || models[1] != "deepseek" {
		t.Fatalf("อัตโนมัติ (qwen) and the locked outside model, nothing padded: %v", p["options"])
	}
	opts := p["options"].([]any)
	if first := opts[0].(map[string]any); first["id"] != "auto" || first["label"] != "อัตโนมัติ (แนะนำ)" {
		t.Fatalf("auto first: %v", first)
	}
	if locked := opts[1].(map[string]any); locked["disabled"] != true || !strings.Contains(locked["reason"].(string), "นอกองค์กร") {
		t.Fatalf("the outside model stays locked with its reason: %v", locked)
	}
	if p["single_in_house"] != true || p["notice"] != "ตอนนี้องค์กรมีโมเดลในองค์กรตัวเดียว" {
		t.Fatalf("one in-house model is said in one line: single=%v notice=%v", p["single_in_house"], p["notice"])
	}
	// the model "อัตโนมัติ" resolves to may still be named outright (an install
	// that chose "แม่นที่สุด" before AM5): allowed, and shown as the one row it is
	code, p = e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "qwen"})
	if code != 200 || p["choice"] != "auto" {
		t.Fatalf("the same model by name is the auto row: %d choice=%v", code, p["choice"])
	}
	optionModels(t, p)
}

func TestModelPickerShowsInHouseModelsThatDiffer(t *testing.T) {
	e, id := pickerEnv(t, RunnerModels{Primary: "qwen", Fallback: "deepseek", Vision: "qwen", SelfHosted: map[string]bool{"qwen": true},
		Catalog: []CatalogModel{{ID: "qwen", SelfHosted: true, Vision: true}, {ID: "typhoon", SelfHosted: true}, {ID: "deepseek", InPrice: 0.3, OutPrice: 0.5}}})
	if e == nil {
		return
	}
	code, p := e.do("GET", "/installs/"+id+"/models", nil)
	if code != 200 {
		t.Fatalf("picker: %d %v", code, p)
	}
	models := optionModels(t, p)
	if strings.Join(models, ",") != "qwen,deepseek,typhoon" && strings.Join(models, ",") != "qwen,typhoon,deepseek" {
		t.Fatalf("each in-house model once, then the locked outside one: %v", models)
	}
	if p["single_in_house"] == true || p["notice"] != "" {
		t.Fatalf("two in-house models: no single-model line: %v %v", p["single_in_house"], p["notice"])
	}
	for _, o := range p["options"].([]any) {
		om := o.(map[string]any)
		if om["model"] == "typhoon" && (om["disabled"] == true || om["self_hosted"] != true || om["label"] == "") {
			t.Fatalf("an in-house model is a row a person can pick: %v", om)
		}
	}
	// an organisation with many models of its own: the menu stays short, the rest is in "เลือกโมเดลเอง"
	many := RunnerModels{Primary: "qwen", Vision: "qwen", SelfHosted: map[string]bool{"qwen": true}, Catalog: []CatalogModel{{ID: "qwen", SelfHosted: true}}}
	for _, id := range []string{"m1", "m2", "m3", "m4", "m5", "m6"} {
		many.Catalog = append(many.Catalog, CatalogModel{ID: id, SelfHosted: true})
	}
	short := e.store.Picker(context.Background(), e.api.cfg, Install{Slug: "sran.financial-report"}, mustManifest(t, textManifest), OrgSettings{}, RunnerModelsFrom(capsOf(many)))
	if len(short.Options) != 1+MaxOwnRows || len(short.Custom) != 7 || short.SingleInHouse {
		t.Fatalf("a short menu, everything in the picker: %d rows, %d in the picker", len(short.Options), len(short.Custom))
	}
	// the organisation's own list with the same model under two outcomes is still one row
	if code, body := e.do("PUT", "/admin/org", map[string]any{"agent_models": []map[string]any{
		{"model": "qwen", "outcome": "accurate"}, {"model": "typhoon", "outcome": "fast"}, {"model": "typhoon", "outcome": "accurate"}}}); code != 200 {
		t.Fatalf("admin: %d %v", code, body)
	}
	_, p = e.do("GET", "/installs/"+id+"/models", nil)
	if models := optionModels(t, p); strings.Join(models, ",") != "qwen,typhoon" {
		t.Fatalf("a curated list is collapsed the same way: %v", models)
	}
}

func TestChooseYourOwnModelKeepsOutsideModelsLocked(t *testing.T) {
	e, id := pickerEnv(t, RunnerModels{Primary: "qwen", Fallback: "deepseek", Vision: "qwen", SelfHosted: map[string]bool{"qwen": true},
		Catalog: []CatalogModel{{ID: "qwen", SelfHosted: true, Vision: true}, {ID: "typhoon", SelfHosted: true}, {ID: "deepseek"}, {ID: "gpt-4o", Vision: true}}})
	if e == nil {
		return
	}
	_, p := e.do("GET", "/installs/"+id+"/models", nil)
	// "เลือกโมเดลเอง" opens the chat's picker; each of its rows is allowed or locked by this list
	for _, want := range []struct {
		id      string
		allowed bool
	}{{"qwen", true}, {"typhoon", true}, {"deepseek", false}, {"gpt-4o", false}} {
		c := customOf(p, want.id)
		if c == nil || c["allowed"] != want.allowed {
			t.Fatalf("%s allowed=%v: %v", want.id, want.allowed, c)
		}
		if !want.allowed && !strings.Contains(c["reason"].(string), "นอกองค์กร") {
			t.Fatalf("a locked model says why: %v", c)
		}
	}
	if code, body := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "gpt-4o"}); code != http.StatusConflict {
		t.Fatalf("an outside model stays locked: %d %v", code, body)
	}
	if code, body := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "not-a-model"}); code != http.StatusConflict {
		t.Fatalf("a model the runner does not have is refused: %d %v", code, body)
	}
	// the organisation curated its own short list: a model picked by hand is still allowed when it is in-house
	if code, body := e.do("PUT", "/admin/org", map[string]any{"agent_models": []map[string]any{{"model": "qwen", "outcome": "accurate"}}}); code != 200 {
		t.Fatalf("admin: %d %v", code, body)
	}
	code, p := e.do("PUT", "/installs/"+id+"/model", map[string]any{"choice": "typhoon"})
	if code != 200 || p["choice"] != "typhoon" {
		t.Fatalf("an in-house model chosen by hand: %d %v", code, p)
	}
	// and the menu names it, once
	if models := optionModels(t, p); strings.Join(models, ",") != "qwen,typhoon" {
		t.Fatalf("the chosen model is a row of the menu: %v", models)
	}
	if code, body := e.do("POST", "/installs/"+id+"/messages", map[string]any{"text": "ปิดงบ", "model_choice": "deepseek", "client_id": "m"}); code != http.StatusConflict {
		t.Fatalf("nor for one round: %d %v", code, body)
	}
	// once the organisation lets documents leave, the outside models open
	if code, body := e.do("PUT", "/admin/org", map[string]any{"documents_may_leave": true}); code != 200 {
		t.Fatalf("admin: %d %v", code, body)
	}
	_, p = e.do("GET", "/installs/"+id+"/models", nil)
	if c := customOf(p, "gpt-4o"); c == nil || c["allowed"] != true {
		t.Fatalf("documents may leave: outside models are allowed: %v", c)
	}
}

func mustManifest(t *testing.T, raw string) Manifest {
	t.Helper()
	m, err := ParseManifest(json.RawMessage(raw))
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func capsOf(rm RunnerModels) json.RawMessage {
	d, _ := json.Marshal(rm)
	b, _ := json.Marshal(map[string]Binding{"platform.models": {Capability: "platform.models", Impl: "gateway", Detail: d}})
	return b
}
