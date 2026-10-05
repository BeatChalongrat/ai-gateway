package agentmarket

import (
	"encoding/json"
	"testing"
)

// AM10 F: the document-language setting offered "en" and no document ever
// followed it. The agents' documents are Thai; nothing else is kept.
func TestTheDocumentLanguageSettingKeepsOnlyWhatDocumentsAre(t *testing.T) {
	if !validSettingValue("language", json.RawMessage(`"th"`)) {
		t.Fatal("th is what the documents are")
	}
	for _, v := range []string{`"en"`, `"ja"`, `""`, `1`, `null`} {
		if validSettingValue("language", json.RawMessage(v)) {
			t.Errorf("language %s was kept, though no document would follow it", v)
		}
	}
}
