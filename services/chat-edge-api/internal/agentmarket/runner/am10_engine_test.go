package runner

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

// AM10: the engine reads each package's own icon file, checks it, and the
// version it registers carries it — a package with a bad or missing icon is
// not loaded at all.
func TestTheEngineReadsEachPackagesIconIntoItsReleasedManifest(t *testing.T) {
	e, err := LoadEngine("", engineSrc(t))
	if err != nil {
		t.Fatal(err)
	}
	for slug, p := range e.Packages {
		if p.IconSVG == "" || !strings.HasPrefix(strings.TrimSpace(p.IconSVG), "<svg") {
			t.Errorf("%s: no icon read from its icon_file", slug)
			continue
		}
		rel, err := p.ReleaseManifest()
		if err != nil {
			t.Fatal(err)
		}
		if l := am.LookOf(rel); l.IconSVG != strings.TrimSpace(p.IconSVG) || l.Accent == nil {
			t.Errorf("%s: the released manifest does not carry the look", slug)
		}
		var raw map[string]json.RawMessage
		_ = json.Unmarshal(p.RawManifest, &raw)
		if _, ok := raw["icon_svg"]; ok {
			t.Errorf("%s: agent.json carries icon_svg itself", slug)
		}
	}
}

func TestAPackageWhoseIconIsMissingOrUnsafeIsNotLoaded(t *testing.T) {
	src := engineSrc(t)
	for name, svg := range map[string]string{"missing": "", "script": `<svg viewBox="0 0 24 24"><script>1</script></svg>`} {
		dst := filepath.Join(t.TempDir(), "agent_market")
		copyTree(t, src, dst)
		icon := filepath.Join(dst, "packages", "financial_report", "icon.svg")
		if svg == "" {
			_ = os.Remove(icon)
		} else {
			_ = os.WriteFile(icon, []byte(svg), 0o644)
		}
		if _, err := LoadEngine("", dst); err == nil || !strings.Contains(err.Error(), "icon") {
			t.Errorf("%s: loaded (%v)", name, err)
		}
	}
}
