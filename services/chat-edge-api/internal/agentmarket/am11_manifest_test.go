package agentmarket

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

// AM11 — what a package's manifest says to a person is read in the screen's
// language. The English is the package's own, kept in its manifest under
// "en" (Thai string → English); the server replaces, it never translates.

// the test environment's manifest, carrying the English of three of its strings
var manifestWithEnglish = json.RawMessage(strings.Replace(testManifest, `"cannot":"อ่านอย่างเดียว"}`,
	`"cannot":"อ่านอย่างเดียว","en":{"อ่านอย่างเดียว":"Reads only; it changes nothing","สวัสดีครับ":"Hello","รายงาน":"Report"}}`, 1))

func manifestFields(t *testing.T, raw []byte) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("not a manifest: %v\n%s", err, raw)
	}
	return m
}

func TestAManifestIsSentInTheScreensLanguage(t *testing.T) {
	en := manifestFields(t, ManifestIn(English, manifestWithEnglish))
	th := manifestFields(t, ManifestIn(Thai, manifestWithEnglish))
	for l, m := range map[Lang]map[string]any{English: en, Thai: th} {
		if _, has := m["en"]; has {
			t.Errorf("%s: the manifest's English map left the server", l)
		}
		// what is not one of the strings is untouched: ids, formats, flags
		if m["id"] != "sran.financial-report" || m["python_package"] != "financial_report" || m["version"] != "1.0.0" {
			t.Errorf("%s: the manifest's own values changed: %v", l, m)
		}
	}
	if en["cannot"] != "Reads only; it changes nothing" || en["greeting"].(map[string]any)["text"] != "Hello" {
		t.Errorf("English screen: %v / %v", en["cannot"], en["greeting"])
	}
	outs := en["outputs"].([]any)
	if outs[0].(map[string]any)["label"] != "Report" || outs[0].(map[string]any)["format"] != "docx" {
		t.Errorf("English screen, a string inside a list: %v", outs[0])
	}
	// a string the package gave no English for stays as the package wrote it
	if en["name"] != "รายงานการเงิน" || outs[1].(map[string]any)["label"] != "สไลด์" {
		t.Errorf("a string with no English must stay: %v / %v", en["name"], outs[1])
	}
	if th["cannot"] != "อ่านอย่างเดียว" || th["greeting"].(map[string]any)["text"] != "สวัสดีครับ" {
		t.Errorf("Thai screen: %v / %v", th["cannot"], th["greeting"])
	}
	// a manifest with no English is sent as it is, byte for byte, in both languages
	plain := json.RawMessage(testManifest)
	if !bytes.Equal(ManifestIn(English, plain), plain) || !bytes.Equal(ManifestIn(Thai, plain), plain) {
		t.Error("a manifest with no English map was rewritten")
	}
	// the server's own reading of a manifest for a person follows the screen too
	m, err := ParseManifestIn(English, manifestWithEnglish)
	if err != nil || m.Cannot != "Reads only; it changes nothing" || m.Outputs[0].Label != "Report" || m.ID != "sran.financial-report" {
		t.Errorf("ParseManifestIn(English): %v %+v", err, m.Outputs)
	}
	if m, _ := ParseManifestIn(Thai, manifestWithEnglish); m.Cannot != "อ่านอย่างเดียว" {
		t.Errorf("ParseManifestIn(Thai): %q", m.Cannot)
	}
}

// The language is the write's: the same entry is English in a write for an English screen and Thai in a
// plain write, and the map of English never appears in either.
func TestACatalogueEntrysManifestFollowsTheWrite(t *testing.T) {
	e := catalogEntry{Slug: "sran.financial-report", Name: "รายงานการเงิน", Manifest: shownManifest(manifestWithEnglish)}
	inEn, err := MarshalIn(English, e)
	if err != nil {
		t.Fatal(err)
	}
	plain, err := json.Marshal(e)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(inEn), `"cannot":"Reads only; it changes nothing"`) || strings.Contains(string(inEn), `"en":`) {
		t.Errorf("a write for an English screen: %s", inEn)
	}
	if !strings.Contains(string(plain), `"cannot":"อ่านอย่างเดียว"`) || strings.Contains(string(plain), `"en":`) || strings.Contains(string(plain), "Reads only") {
		t.Errorf("a plain write: %s", plain)
	}
	if b, _ := json.Marshal(catalogEntry{}); !strings.Contains(string(b), `"manifest":null`) {
		t.Errorf("an entry with no manifest: %s", b)
	}
}

// A package's string the server puts into a card or a sentence has its English side read from the
// package's own English, and those words are ones an English item's reader shows.
func TestAPackagesStringInACardIsItsOwnEnglish(t *testing.T) {
	AddPackageEnglish(json.RawMessage(`{"en":{"บัญชีแยกประเภทของเดือนที่จะทำรายงาน":"General ledger for the reporting month","จำเป็น (หรืองบทดลอง)":"Required (or a trial balance)"}}`))
	label := LabelFromPackage("บัญชีแยกประเภทของเดือนที่จะทำรายงาน", NoText)
	if label.In(Thai) != "บัญชีแยกประเภทของเดือนที่จะทำรายงาน" || label.In(English) != "General ledger for the reporting month" {
		t.Errorf("a label: %q / %q", label.In(Thai), label.In(English))
	}
	note := FromPackage("จำเป็น (หรืองบทดลอง)", NoText)
	if note.In(English) != "Required (or a trial balance)" || note.In(Thai) != "จำเป็น (หรืองบทดลอง)" {
		t.Errorf("a sentence: %q / %q", note.In(Thai), note.In(English))
	}
	// a string the package gave no English for reads as before in both
	other := LabelFromPackage("กันยายน 2569", NoText)
	if other.In(Thai) != "กันยายน 2569" || other.In(English) != "กันยายน 2569" {
		t.Errorf("a label with no English: %q / %q", other.In(Thai), other.In(English))
	}
	// an item written in English with the package's words is shown by an English item's reader
	if got := ShownIn(English, "General ledger for the reporting month"); got != "General ledger for the reporting month" {
		t.Errorf("the reader withheld a package's English: %q", got)
	}
}
