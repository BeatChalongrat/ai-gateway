package agentmarket

import (
	"encoding/json"
	"testing"
)

// AM10 F: the document-language setting offered "en" and no document ever
// followed it. The agents' documents are Thai; nothing else is kept.
func TestTheDocumentLanguageSettingKeepsOnlyWhatDocumentsAre(t *testing.T) {
	// AM18: the documents follow the run's language, so the install's own may be either of the two
	for _, v := range []string{`"th"`, `"en"`} {
		if !validSettingValue("language", json.RawMessage(v)) {
			t.Fatalf("%s is a language the documents are written in", v)
		}
	}
	for _, v := range []string{`"ja"`, `""`, `1`, `null`} {
		if validSettingValue("language", json.RawMessage(v)) {
			t.Errorf("language %s was kept, though no document would follow it", v)
		}
	}
}
