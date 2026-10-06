package agentmarket

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
)

// AM11 — the five packages as they ship, sent to an English screen and to a
// Thai one. The English is the packages' own (their manifests' "en"); this
// reads the real files, so a package whose manifest gains a Thai string with
// no English fails here as well as in the Python lane
// (backend/agent_market/tests/test_manifest_english.py).

var quotedLiteral = regexp.MustCompile(`“[^”]+”`)

// what in a manifest is data and stays as written on every screen
func manifestData(path string) bool {
	return path == "install.defaults" || strings.HasPrefix(path, "install.defaults.") || path == "sample" || strings.HasSuffix(path, ".sample")
}

func manifestStrings(v any, path string, visit func(path, s string)) {
	switch x := v.(type) {
	case string:
		visit(path, x)
	case []any:
		for _, e := range x {
			manifestStrings(e, path, visit)
		}
	case map[string]any:
		for k, e := range x {
			p := k
			if path != "" {
				p = path + "." + k
			}
			manifestStrings(e, p, visit)
		}
	}
}

func TestTheShippedPackagesReadInEnglishOnAnEnglishScreen(t *testing.T) {
	files, _ := filepath.Glob(filepath.Join("..", "..", "..", "..", "backend", "agent_market", "packages", "*", "agent.json"))
	if len(files) != 5 {
		t.Fatalf("the five packages' manifests: found %d", len(files))
	}
	for _, f := range files {
		pkg := filepath.Base(filepath.Dir(f))
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var original, th, en map[string]any
		if err := json.Unmarshal(raw, &original); err != nil {
			t.Fatalf("%s: %v", pkg, err)
		}
		if _, has := original["en"]; !has {
			t.Errorf("%s: the manifest carries no English", pkg)
			continue
		}
		if err := json.Unmarshal(ManifestIn(Thai, raw), &th); err != nil {
			t.Fatalf("%s (Thai): %v", pkg, err)
		}
		if err := json.Unmarshal(ManifestIn(English, raw), &en); err != nil {
			t.Fatalf("%s (English): %v", pkg, err)
		}
		// a Thai screen gets the manifest exactly as the package wrote it, without the English map
		delete(original, "en")
		if !reflect.DeepEqual(th, original) {
			t.Errorf("%s: a Thai screen's manifest is not the package's own", pkg)
		}
		if _, has := en["en"]; has {
			t.Errorf("%s: the English map was sent to a client", pkg)
		}
		// an English screen: no Thai left in what is words; data is as written
		left := 0
		manifestStrings(en, "", func(path, s string) {
			if manifestData(path) {
				return
			}
			if thaiLetters(quotedLiteral.ReplaceAllString(s, "")) {
				left++
				if left <= 3 {
					t.Errorf("%s %s: Thai on an English screen: %q", pkg, path, s)
				}
			}
		})
		// the data is the same on both screens (a policy's categories, a sample file's path)
		for _, path := range []string{"install"} {
			a, _ := th[path].(map[string]any)
			b, _ := en[path].(map[string]any)
			if !reflect.DeepEqual(a["defaults"], b["defaults"]) {
				t.Errorf("%s: an install's starting values differ between the two screens", pkg)
			}
		}
		// what the server itself reads from it for a person
		m, err := ParseManifestIn(English, raw)
		if err != nil {
			t.Fatalf("%s: %v", pkg, err)
		}
		if thaiLetters(m.Summary) || thaiLetters(m.Greeting.Role) || m.ID == "" || m.NameIn(English, m.Name) != m.NameEN {
			t.Errorf("%s: the manifest as the server reads it for an English screen: %q / %q", pkg, m.Greeting.Role, m.NameIn(English, m.Name))
		}
		if mt, err := ParseManifestIn(Thai, raw); err != nil || !thaiLetters(mt.Summary) {
			t.Errorf("%s: the manifest as the server reads it for a Thai screen: %v", pkg, err)
		}
	}
}
