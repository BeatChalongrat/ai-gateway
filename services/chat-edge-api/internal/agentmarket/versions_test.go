package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestVersionOrderAndDiff(t *testing.T) {
	if !versionLess("1.9.0", "1.10.0") || versionLess("1.10.0", "1.9.0") || versionLess("1.1.0", "1.1.0") || !versionLess("1.0", "1.0.1") {
		t.Fatal("dotted numeric order")
	}
	from := Definition{Version: "1.0.0", Manifest: json.RawMessage(testManifest), Instructions: "line a\nline b\nline c"}
	var m map[string]any
	_ = json.Unmarshal([]byte(testManifest), &m)
	m["version"] = "1.1.0"
	m["outputs"] = []any{map[string]any{"format": "docx", "label": "รายงาน", "default": true}, map[string]any{"format": "pdf", "label": "PDF"}}
	caps := m["capabilities"].(map[string]any)
	caps["optional"] = []any{"deliverable.pdf"}
	m["install"].(map[string]any)["defaults"] = map[string]any{"formats": []any{"docx"}}
	b, _ := json.Marshal(m)
	to := Definition{Version: "1.1.0", Manifest: b, Instructions: "line a\nline B\nline c\nline d"}
	got := map[string][]Change{}
	for _, c := range VersionDiff(from, to) {
		got[c.What] = append(got[c.What], c)
	}
	if len(got["manifest"]) != 1 || got["manifest"][0].Key != "outputs" {
		t.Fatalf("manifest changes: %+v", got["manifest"])
	}
	capText := ""
	for _, c := range got["capability"] {
		capText += c.Key + ":" + c.Before + ">" + c.After + " "
	}
	if !strings.Contains(capText, "optional:deliverable.report.pdf>") || !strings.Contains(capText, "optional:>deliverable.pdf") {
		t.Fatalf("capability changes: %s", capText)
	}
	if len(got["setting"]) != 1 || got["setting"][0].Key != "formats" {
		t.Fatalf("setting changes: %+v", got["setting"])
	}
	var removed, added []string
	for _, c := range got["instructions"] {
		if c.Before != "" {
			removed = append(removed, c.Before)
		}
		if c.After != "" {
			added = append(added, c.After)
		}
	}
	if strings.Join(removed, "|") != "line b" || strings.Join(added, "|") != "line B|line d" {
		t.Fatalf("AGENT.md diff: -%v +%v", removed, added)
	}
}

func TestUpgradeShowsTheDiffRunsASelfTestAndRollsBack(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	in := e.install() // pinned to 1.0.0 (seedDefinition)
	if code, body := e.do("GET", "/installs/"+in.ID.String()+"/upgrade", nil); code != 200 || body["available"] != nil {
		t.Fatalf("nothing newer yet: %d %v", code, body)
	}
	var m map[string]any
	_ = json.Unmarshal([]byte(testManifest), &m)
	m["version"] = "1.1.0"
	m["summary"] = "รุ่นใหม่"
	b, _ := json.Marshal(m)
	next, err := e.store.InsertDefinition(ctx, Definition{Slug: "sran.financial-report", Version: "1.1.0", Name: "รายงานการเงิน",
		Manifest: b, Instructions: "new", Checksum: "pkgsum2", EngineChecksum: "engsum2"})
	if err != nil {
		t.Fatal(err)
	}
	code, info := e.do("GET", "/installs/"+in.ID.String()+"/upgrade", nil)
	if code != 200 || info["available"].(map[string]any)["version"] != "1.1.0" || len(info["changes"].([]any)) == 0 {
		t.Fatalf("upgrade info: %d %v", code, info)
	}
	code, up := e.do("POST", "/installs/"+in.ID.String()+"/upgrade", map[string]any{"definition_id": next.ID})
	if code != 202 {
		t.Fatalf("start upgrade: %d %v", code, up)
	}
	run := up["run"].(map[string]any)
	if run["kind"] != KindSelfTest || run["definition_version"] != "1.1.0" {
		t.Fatalf("the upgrade is a self-test of the new version: %v", run)
	}
	// the install stays where it is until the runner has seen that self-test pass
	if cur, _ := e.store.GetInstall(ctx, in.TenantID, in.UserID, in.ID); cur.DefinitionID != in.DefinitionID {
		t.Fatal("an upgrade switches only after its self-test")
	}
	if code, _ := e.do("POST", "/installs/"+in.ID.String()+"/rollback", nil); code != 409 {
		t.Fatalf("nothing to roll back to yet: %d", code)
	}
	// what the runner does when the self-test passed
	if _, err := e.store.SwitchInstall(ctx, in.ID, next, nil); err != nil {
		t.Fatal(err)
	}
	code, rb := e.do("POST", "/installs/"+in.ID.String()+"/rollback", nil)
	if code != 200 || rb["install"].(map[string]any)["definition_version"] != "1.0.0" {
		t.Fatalf("rollback: %d %v", code, rb)
	}
	back, _ := e.store.GetInstall(ctx, in.TenantID, in.UserID, in.ID)
	if back.PreviousDefinitionID == nil || *back.PreviousDefinitionID != next.ID {
		t.Fatal("after a rollback the newer version is the one to go forward to again")
	}
	if code, _ := e.do("POST", "/installs/"+in.ID.String()+"/upgrade", map[string]any{"definition_id": uuid.New()}); code != 400 {
		t.Fatalf("an unknown version: %d", code)
	}
}
