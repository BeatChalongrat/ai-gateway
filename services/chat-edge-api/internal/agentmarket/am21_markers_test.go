package agentmarket

import "testing"

// AM21: a bracketed build or card marker, or the product's name, is found in a text that goes on a
// deliverable, and taken out of one that must still be used.
func TestAMarkerOrTheProductsNameIsFoundInAText(t *testing.T) {
	cases := map[string]string{
		"[AM1b] บริษัท ตัวอย่าง จำกัด":        "[AM1b]",
		"รายงาน [FIX122] ประจำเดือน":          "[FIX122]",
		"Agent Market · bank reconciliation":  "Agent Market",
		"made by SRAN":                        "SRAN",
		"บริษัท สยามตัวอย่างการค้า จำกัด":     "",
		"Sran Chat is not the product's mark": "", // (a word that only begins the same)
		"transrand":                           "",
	}
	for text, want := range cases {
		if got := MarkerIn(text); got != want {
			t.Errorf("MarkerIn(%q) = %q, want %q", text, got, want)
		}
	}
}

func TestMarkersAreStrippedAndTheSpacesTidied(t *testing.T) {
	if got := StripMarkers("[AM1b] บริษัท ตัวอย่าง จำกัด"); got != "บริษัท ตัวอย่าง จำกัด" {
		t.Fatalf("got %q", got)
	}
	if got := StripMarkers("บริษัท [AM-21] ทดสอบ [FIX1] จำกัด"); got != "บริษัท ทดสอบ จำกัด" {
		t.Fatalf("got %q", got)
	}
	if got := StripMarkers("บริษัท ทดสอบ จำกัด"); got != "บริษัท ทดสอบ จำกัด" {
		t.Fatalf("got %q", got)
	}
}
