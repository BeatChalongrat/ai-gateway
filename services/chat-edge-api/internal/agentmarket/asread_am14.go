package agentmarket

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
)

// AM14 — what a row keeps in one language and a person reads in another.
//
// Two things about an installed agent are written once and then only read: its name
// (agent_installs.title, set from the screen it was installed on) and its greeting (a thread item, written
// in the language of the request that made it). Neither is the work of a run — a run is written in its own
// language and stays as it was written (lang.go). They are the agent introducing itself, and they were
// frozen by accident: an agent installed from a Thai screen, or before the app had English, greeted in Thai
// and was listed under a Thai name on an English screen for ever, in the first place anyone looks. The
// header beside it, read from the manifest each time, was English.
//
// So both are read as the screen reads them, from the agent's own manifest: the name from the newest
// registered version of the agent (an agent's name is the agent's, whichever version an install is on),
// the greeting from the version the install is on.

type agentNames struct{ thai, english, version string }

var packageNames = struct {
	mu sync.RWMutex
	by map[string]agentNames
}{by: map[string]agentNames{}}

// learnAgentName keeps the names a manifest gives its agent, by slug; the newest version's win.
func learnAgentName(manifest json.RawMessage) {
	var m struct {
		ID        string `json:"id"`
		Version   string `json:"version"`
		Name      string `json:"name"`
		NameEN    string `json:"name_en"`
		ShortName string `json:"short_name"`
	}
	if json.Unmarshal(manifest, &m) != nil || m.ID == "" {
		return
	}
	n := agentNames{thai: strings.TrimSpace(m.ShortName), english: strings.TrimSpace(m.NameEN), version: m.Version}
	if n.thai == "" {
		n.thai = strings.TrimSpace(m.Name)
	}
	packageNames.mu.Lock()
	defer packageNames.mu.Unlock()
	if cur, ok := packageNames.by[m.ID]; ok && versionLess(n.version, cur.version) {
		return
	}
	packageNames.by[m.ID] = n
}

// AgentNameIn is the name of the agent slug on a screen in l. kept is what an install's row holds — the
// name as it was on the day and the screen of the install; it is what a person is shown only when the
// agent's package is not known here, or gives no name in that language.
func AgentNameIn(l Lang, slug, kept string) string {
	packageNames.mu.RLock()
	n, ok := packageNames.by[slug]
	packageNames.mu.RUnlock()
	if !ok {
		return kept
	}
	if l == English {
		if n.english != "" {
			return n.english
		}
		if e := englishOfPackage(n.thai); e != "" && e != n.thai {
			return e
		}
		return kept
	}
	if n.thai != "" {
		return n.thai
	}
	return kept
}

// AsRead is the install as a screen in l is shown it: under the agent's name of today, in that language.
// It is applied where an install is written to a person — by hand at each place, and not by giving the
// type a JSON writer of its own: a response that EMBEDS an install would inherit that writer and lose
// every field of its own (the list of installs does; the first version of this did exactly that).
func (in Install) AsRead(l Lang) Install {
	in.Title = AgentNameIn(l, in.Slug, in.Title)
	return in
}

// agentTitle is an agent's name in a payload built where the screen's language is not at hand (a map put
// together for a run's page): it is written in the language of the write it ends up in.
type agentTitle struct{ slug, kept string }

func (t agentTitle) MarshalJSON() ([]byte, error) {
	return json.Marshal(AgentNameIn(marshalLang(), t.slug, t.kept))
}

// the parts of a greeting that are the package's words; everything else in the item is a fact of the day
// it was written (the version it greeted as, the self-test it points to)
var greetingWords = []string{"text", "role", "tagline", "cannot", "needs", "produces"}

// greetingsAsRead gives every greeting among items the words of the install's own version of the agent, in
// the language of the screen that asked. An item that cannot be rebuilt is left as it was written.
func (a *API) greetingsAsRead(ctx context.Context, l Lang, in Install, items []ThreadItem) []ThreadItem {
	var fresh map[string]any
	for i := range items {
		if items[i].Kind != "greeting" {
			continue
		}
		if fresh == nil {
			def, err := a.store.DefinitionByID(ctx, in.DefinitionID)
			if err != nil {
				return items
			}
			m, err := ParseManifest(def.Manifest)
			if err != nil {
				return items
			}
			b, err := MarshalIn(l, GreetingFor(m))
			if err != nil {
				return items
			}
			if json.Unmarshal(ForClient("greeting", stampLang(l, b)), &fresh) != nil || fresh == nil {
				return items
			}
		}
		var kept map[string]any
		if json.Unmarshal(items[i].Payload, &kept) != nil || kept == nil {
			continue
		}
		for _, k := range greetingWords {
			if v, ok := fresh[k]; ok {
				kept[k] = v
			} else {
				delete(kept, k)
			}
		}
		if l == English {
			kept["lang"] = string(English)
		} else {
			delete(kept, "lang")
		}
		if b, err := json.Marshal(kept); err == nil {
			items[i].Payload = b
		}
	}
	return items
}
