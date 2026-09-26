package agentmarket

import (
	"bytes"
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"testing"

	"github.com/google/uuid"
)

func testSpec() TemplateSpec {
	var s TemplateSpec
	_ = json.Unmarshal([]byte(`{"fields": {"company": {"label": "x"}, "total_thb": {"label": "y"}},
		"lists": {"lines": {"label": "z", "columns": {"description": "a", "amount": "b"}}}}`), &s)
	return s
}

func testSlots() []TemplateSlot {
	no := "formula_cell"
	return []TemplateSlot{
		{Key: "CompanyName", Kind: "name", Label: "CompanyName", Fillable: true},
		{Key: "GrandTotal", Kind: "name", Label: "GrandTotal", Fillable: false, Reason: &no},
		{Key: "LinesTable", Kind: "table", Label: "LinesTable", Fillable: true, Columns: []string{"A", "B"}},
	}
}

func TestTemplateMappingIsCheckedAgainstTheTemplateAndTheAgent(t *testing.T) {
	ok := map[string]string{"CompanyName": "company", "LinesTable": "lines", "LinesTable.A": "lines.description",
		"LinesTable.B": "lines.amount"}
	if msg := CheckTemplateMapping(testSlots(), ok, testSpec()); msg != "" {
		t.Fatalf("a good mapping: %s", msg)
	}
	for name, m := range map[string]map[string]string{
		"nothing chosen":             {},
		"a place that is not there":  {"Nowhere": "company"},
		"a formula cell":             {"GrandTotal": "total_thb"},
		"a field the agent lacks":    {"CompanyName": "salary"},
		"a single value into a list": {"LinesTable": "company"},
		"a list into a single cell":  {"CompanyName": "lines"},
		"a column the range lacks":   {"LinesTable": "lines", "LinesTable.Z": "lines.amount"},
		"a column from another list": {"LinesTable": "lines", "LinesTable.A": "items.description"},
		"a column the list lacks":    {"LinesTable": "lines", "LinesTable.A": "lines.salary"},
	} {
		if CheckTemplateMapping(testSlots(), m, testSpec()) == "" {
			t.Fatalf("%s must be refused", name)
		}
	}
}

const invoiceTemplatesManifest = `{"schema_version":"agent.market.v1","id":"sran.invoice-processing","version":"1.0.0","name":"ใบกำกับภาษี",
"python_package":"invoice_processing","greeting":{"text":"สวัสดีครับ"},
"inputs":[{"role":"documents","label":"เอกสาร","required":true,"accept":[".pdf"]}],
"outputs":[{"format":"xlsx","label":"ทะเบียน","default":true}],"capabilities":{"required":["spreadsheet.read.xlsx"]},
"install":{"defaults":{"formats":["xlsx"]}},"permissions":{"default":"read_only"},"cannot":"อ่านอย่างเดียว",
"templates":{"fields":{"company":{"label":"บริษัท"},"total_thb":{"label":"ยอดรวม"}},
"lists":{"lines":{"label":"รายการ","columns":{"description":"รายละเอียด","amount":"จำนวนเงิน"}}}}}`

func (e *testEnv) postTemplate(in Install, path string) (int, map[string]any) {
	e.t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		e.t.Fatal(err)
	}
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, _ := mw.CreateFormFile("file", filepath.Base(path))
	_, _ = fw.Write(data)
	_ = mw.Close()
	req, _ := http.NewRequest("POST", e.srv.URL+"/agent-market/installs/"+in.ID.String()+"/template", &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

func TestTemplateUploadSaveVersionAndRemove(t *testing.T) {
	e := newEnv(t)
	if e == nil {
		return
	}
	ctx := context.Background()
	root, _ := filepath.Abs("../../../..")
	tplDir := filepath.Join(root, "backend", "agent_market", "fixtures", "templates")
	fin := e.install()
	if code, _ := e.postTemplate(fin, filepath.Join(tplDir, "xlsx_named_ranges.xlsx")); code != http.StatusConflict {
		t.Fatalf("an agent without a templates block takes no template: %d", code)
	}
	if _, err := e.store.UpsertDefinition(ctx, Definition{Slug: "sran.invoice-processing", Version: "1.0.0", Name: "ใบกำกับภาษี",
		Manifest: json.RawMessage(invoiceTemplatesManifest), Checksum: "inv", EngineChecksum: "engsum"}); err != nil {
		t.Fatal(err)
	}
	_, body := e.do("POST", "/installs", map[string]any{"slug": "sran.invoice-processing"})
	inv, _ := e.store.GetInstall(ctx, e.user.TenantUUID, e.user.UserID, uuid.MustParse(body["install"].(map[string]any)["id"].(string)))
	if code, _ := e.postTemplate(inv, filepath.Join(tplDir, "expected.json")); code != http.StatusUnsupportedMediaType {
		t.Fatalf("a template is a Word, Excel or PowerPoint file: %d", code)
	}
	code, up := e.postTemplate(inv, filepath.Join(tplDir, "xlsx_named_ranges.xlsx"))
	if code != http.StatusAccepted || up["job"] == nil || up["kind"] != "xlsx" {
		t.Fatalf("upload: %d %v", code, up)
	}
	fileID := up["file"].(map[string]any)["id"].(string)
	slots := testSlots()
	save := func(fid string, mapping map[string]string) (int, map[string]any) {
		return e.do("PUT", "/installs/"+inv.ID.String()+"/template", map[string]any{"file_id": fid, "slots": slots, "mapping": mapping})
	}
	if code, _ := save(fileID, map[string]string{"GrandTotal": "total_thb"}); code != http.StatusBadRequest {
		t.Fatalf("a formula cell is refused: %d", code)
	}
	code, got := save(fileID, map[string]string{"CompanyName": "company", "LinesTable": "lines", "LinesTable.A": "lines.description"})
	if code != 200 || got["template"].(map[string]any)["version"].(float64) != 1 {
		t.Fatalf("save: %d %v", code, got)
	}
	if _, again := save(fileID, map[string]string{"CompanyName": "company"}); again["template"].(map[string]any)["version"].(float64) != 1 {
		t.Fatal("the same file re-mapped keeps its version")
	}
	_, up2 := e.postTemplate(inv, filepath.Join(tplDir, "xlsx_no_markers.xlsx"))
	if _, v2 := save(up2["file"].(map[string]any)["id"].(string), map[string]string{"CompanyName": "company"}); v2["template"].(map[string]any)["version"].(float64) != 2 {
		t.Fatal("a new file is the next version")
	}
	if code, cur := e.do("GET", "/installs/"+inv.ID.String()+"/template", nil); code != 200 || cur["template"] == nil {
		t.Fatalf("get: %d", code)
	}
	if code, _ := e.do("DELETE", "/installs/"+inv.ID.String()+"/template", nil); code != 200 {
		t.Fatalf("remove: %d", code)
	}
	if code, _ := e.do("GET", "/installs/"+inv.ID.String()+"/template", nil); code != http.StatusNotFound {
		t.Fatalf("removed: %d", code)
	}
}
