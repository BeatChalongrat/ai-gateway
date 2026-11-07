package runner

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"testing"
	"time"

	"github.com/google/uuid"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM14: the progress card names the theme a run's files were made with. The name of a built-in theme is
// the product's own word, so an English screen reads “Navy”; a name an organisation gave its own theme is
// theirs and is shown as they wrote it, on every screen.

// themeDetail is the detail of the “making the files” line of a run whose render used a theme of this name.
func themeDetail(t *testing.T, l am.Lang, name string) string {
	t.Helper()
	started := time.Date(2026, 9, 28, 3, 0, 0, 0, time.UTC)
	run := am.Run{ID: uuid.New(), Kind: am.KindRun, Status: am.RunRunning, StartedAt: &started}
	out, _ := json.Marshal(map[string]any{"theme": map[string]any{"id": "x", "name": name}, "passed": true})
	p := BuildProgress(run, []am.Step{{Name: "render", Status: "succeeded", Output: out}}, started.Add(time.Minute))
	var got struct {
		Steps []struct{ Key, Detail string } `json:"steps"`
	}
	if err := json.Unmarshal([]byte(written(t, l, p)), &got); err != nil {
		t.Fatal(err)
	}
	for _, s := range got.Steps {
		if s.Key == "render" {
			return s.Detail
		}
	}
	t.Fatal("the progress card has no line for making the files")
	return ""
}

func TestTheProgressCardNamesABuiltInThemeInTheReadersLanguage(t *testing.T) {
	// the built-in themes are the renderer's (deliverables/theme.py): every one of them, so a fourth
	// cannot be added there and left in Thai here
	src, err := os.ReadFile(filepath.Join(engineSrc(t), "deliverables", "theme.py"))
	if err != nil {
		t.Fatal(err)
	}
	names := regexp.MustCompile(`"id": "[a-z]+", "name": "([^"]+)"`).FindAllStringSubmatch(string(src), -1)
	if len(names) < 3 {
		t.Fatalf("the built-in themes were not read from the renderer: %d", len(names))
	}
	for _, m := range names {
		name := m[1]
		if got, want := themeDetail(t, am.Thai, name), "ธีม “"+name+"”"; got != want {
			t.Errorf("a Thai screen: %q, want %q", got, want)
		}
		en := themeDetail(t, am.English, name)
		if hasThai(en) {
			t.Errorf("an English screen is told the built-in theme in Thai: %q", en)
		}
	}
	if got := themeDetail(t, am.English, "กรมท่า"); got != "Theme “Navy”" {
		t.Errorf("the default theme on an English screen: %q", got)
	}
	// an organisation's own name for its theme is theirs: as written, in both languages
	if got := themeDetail(t, am.Thai, "สีของบริษัท ก"); got != "ธีม “สีของบริษัท ก”" {
		t.Errorf("an organisation's own theme on a Thai screen: %q", got)
	}
	if got := themeDetail(t, am.English, "สีของบริษัท ก"); got != "Theme “สีของบริษัท ก”" {
		t.Errorf("an organisation's own theme on an English screen: %q", got)
	}
}
