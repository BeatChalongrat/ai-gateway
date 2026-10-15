package agentmarket

import (
	"encoding/json"
	"strings"
	"testing"
)

// AM21 §6.2/§6.3: the organisation's look is one of the themes; its name carries no marker; a brand
// colour that fails contrast is refused with the ratio and the theme's own stays.
func TestTheLookSettingRefusesWhatWouldHurtTheDocument(t *testing.T) {
	ok := LookPatch{ID: "teal", Company: "บริษัท ทดสอบ จำกัด"}
	if why := lookRefusal(ok); why != "" {
		t.Fatalf("a plain choice is refused: %s", why)
	}
	primary := "1B3A5C"
	if why := lookRefusal(LookPatch{ID: "navy", Company: "x", Primary: &primary}); why != "" {
		t.Fatalf("a dark brand colour is refused: %s", why)
	}
	cases := map[string]LookPatch{
		"a theme that does not exist": {ID: "pink", Company: "x"},
		"a marker in the name":        {ID: "navy", Company: "[AM1b] บริษัท ตัวอย่าง จำกัด"},
		"our name in the name":        {ID: "navy", Company: "Agent Market Co."},
		"not a colour":                {ID: "navy", Company: "x", Primary: strPtr("blue")},
		"too light to read on":        {ID: "navy", Company: "x", Primary: strPtr("FFFF99")},
	}
	for name, p := range cases {
		why := lookRefusal(p)
		if why == "" {
			t.Errorf("%s: not refused", name)
		}
		if name == "a marker in the name" && !strings.Contains(string(why), "[AM1b]") {
			t.Errorf("the marker is named: %s", why)
		}
		if name == "too light to read on" && !strings.Contains(string(why), "คอนทราสต์") {
			t.Errorf("the contrast is said: %s", why)
		}
	}
}

func TestTheLookPatchIsKeptAsTheRendererReadsIt(t *testing.T) {
	accent := "#1c8c84"
	th := LookPatch{ID: "teal", Company: " บริษัท ทดสอบ จำกัด ", Accent: &accent}.theme()
	data, _ := json.Marshal(th)
	if string(data) != `{"id":"teal","company":"บริษัท ทดสอบ จำกัด","palette":{"accent":"1C8C84"}}` {
		t.Fatalf("got %s", data)
	}
	if got := lookContrast("12264A", "FFFFFF"); got < 13 || got > 16 {
		t.Fatalf("the navy against paper: %v", got)
	}
}

func TestTheChatAppsOwnThemeSettingRefusesAMarkerToo(t *testing.T) {
	if why := themeCompanyRefusal(nil, json.RawMessage(`{"id":"navy","company":"[AM1b] บริษัท ตัวอย่าง จำกัด"}`)); why == "" {
		t.Fatal("a marker typed into the chat app's theme setting must be refused")
	}
	if why := themeCompanyRefusal(nil, json.RawMessage(`{"id":"navy","company":"บริษัท ตัวอย่าง จำกัด"}`)); why != "" {
		t.Fatalf("a plain name refused: %s", why)
	}
}

func strPtr(s string) *string { return &s }
