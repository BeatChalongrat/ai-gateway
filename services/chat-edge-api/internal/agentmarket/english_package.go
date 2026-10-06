package agentmarket

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"sync"
)

// ── What a package's manifest says, in English (AM11) ────────────────────
//
// A package's manifest holds the words a person reads before a run exists —
// what the agent is, what it needs and gives back, what it cannot do, its
// settings, its guide. Its English is in the manifest itself, under "en": a
// flat map from each Thai string, exactly as the manifest has it, to its
// English. It is the server's own catalogue idea (english.go) for words the
// server does not own: found by the Thai string, so nothing that reads a
// manifest in Thai changes, and a package that has no "en" is shown in Thai
// in both languages, as every package was before.
//
// Two things are done with it:
//
//   - a manifest sent to a client is sent in the request's language
//     (shownManifest): every string that has an English is replaced by it,
//     and "en" itself never leaves the server;
//   - a package's string the server puts into a sentence or a card
//     (FromPackage, LabelFromPackage) has its English side read from here,
//     and its words join what an English item's reader takes as written for
//     a person (AddEnglishWords).
//
// The engine's own words — a check's question, a figure's label, a period's
// name, a document — are not in a manifest and are not here: they are Thai.

var packageEnglish struct {
	mu   sync.RWMutex
	by   map[string]string
	seen map[[32]byte]bool
}

// AddPackageEnglish learns the English a manifest carries for its own
// strings. A manifest already learnt costs one hash.
func AddPackageEnglish(manifest json.RawMessage) {
	if !bytes.Contains(manifest, []byte(`"en"`)) {
		return
	}
	sum := sha256.Sum256(manifest)
	packageEnglish.mu.RLock()
	known := packageEnglish.seen[sum]
	packageEnglish.mu.RUnlock()
	if known {
		return
	}
	var m struct {
		En map[string]string `json:"en"`
	}
	_ = json.Unmarshal(manifest, &m)
	words := make([]string, 0, len(m.En))
	packageEnglish.mu.Lock()
	if packageEnglish.by == nil {
		packageEnglish.by, packageEnglish.seen = map[string]string{}, map[[32]byte]bool{}
	}
	packageEnglish.seen[sum] = true
	for th, en := range m.En {
		if th != "" && en != "" {
			packageEnglish.by[th] = en
			words = append(words, en)
		}
	}
	packageEnglish.mu.Unlock()
	AddEnglishWords(words...)
}

// englishOfPackage is the English a package gives for one of its own
// strings; the string itself when it gives none.
func englishOfPackage(s string) string {
	packageEnglish.mu.RLock()
	defer packageEnglish.mu.RUnlock()
	if e, ok := packageEnglish.by[s]; ok {
		return e
	}
	return s
}

// LearnPackageEnglish learns every registered package's English (the
// control plane, at start: it reads a thread before it reads a definition).
func (s *Store) LearnPackageEnglish(ctx context.Context) error {
	rows, err := s.Pool.Query(ctx, `SELECT manifest FROM agent_market.agent_definitions`)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var raw json.RawMessage
		if err := rows.Scan(&raw); err != nil {
			return err
		}
		AddPackageEnglish(raw)
	}
	return rows.Err()
}

// ManifestIn is a manifest as a screen in l reads it: every string the
// manifest has an English for is that English on an English screen, and the
// "en" map is taken out in both. A manifest with no "en" is returned as it
// is. Keys, numbers and everything that is not one of those strings are
// untouched.
func ManifestIn(l Lang, raw json.RawMessage) json.RawMessage {
	if !bytes.Contains(raw, []byte(`"en"`)) {
		return raw
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var m map[string]any
	if err := dec.Decode(&m); err != nil {
		return raw
	}
	en, _ := m["en"].(map[string]any)
	if _, has := m["en"]; !has {
		return raw
	}
	delete(m, "en")
	if l == English && len(en) > 0 {
		for k, v := range m {
			m[k] = withEnglish(v, en)
		}
	}
	out, err := json.Marshal(m)
	if err != nil {
		return raw
	}
	return out
}

func withEnglish(v any, en map[string]any) any {
	switch x := v.(type) {
	case string:
		if e, ok := en[x].(string); ok && e != "" {
			return e
		}
	case []any:
		for i := range x {
			x[i] = withEnglish(x[i], en)
		}
	case map[string]any:
		for k := range x {
			x[k] = withEnglish(x[k], en)
		}
	}
	return v
}

// ParseManifestIn is ParseManifest of the manifest as a screen in l reads it:
// what the server itself builds from a manifest's words for a person (the
// data sheet) is built from these.
func ParseManifestIn(l Lang, raw json.RawMessage) (Manifest, error) {
	return ParseManifest(ManifestIn(l, raw))
}

// shownManifest is a manifest on its way to a client: written in the
// language of the write it is part of (MarshalIn), Thai by a writer that was
// never taught one.
type shownManifest json.RawMessage

func (m shownManifest) MarshalJSON() ([]byte, error) {
	if len(m) == 0 {
		return []byte("null"), nil
	}
	return ManifestIn(marshalLang(), json.RawMessage(m)), nil
}
