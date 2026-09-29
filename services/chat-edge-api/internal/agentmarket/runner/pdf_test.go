package runner

import (
	"archive/zip"
	"bytes"
	"strings"
	"testing"

	am "github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/agentmarket"
)

func testDocx(t *testing.T, body string) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	w, _ := zw.Create("word/document.xml")
	_, _ = w.Write([]byte(`<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>` +
		body + `</w:body></w:document>`))
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

const (
	thWater   = "น้ำ"             // น้ำ
	thRevenue = "รายได้จากการขาย" // รายได้จากการขาย
	thCost    = "ค่าใช้จ่าย"      // ค่าใช้จ่าย
)

func TestThaiKeyIgnoresWhatAPdfTextLayerChangesAndNothingElse(t *testing.T) {
	// pdftotext gives sara am back as nikhahit + sara aa, with the tone mark on either side,
	// and puts spaces around shifted marks
	for _, got := range []string{"น้ํา", "นํ้า", "น ้ำ", thWater + "\u200b"} {
		if thaiKey(got) != thaiKey(thWater) {
			t.Fatalf("%q must compare equal to %q", got, thWater)
		}
	}
	// a reordered pair (broken shaping) is not the same text
	if thaiKey("นำ้") == thaiKey(thWater) {
		t.Fatal("a mark after sara am is a different string")
	}
}

func TestDocxThaiParagraphsInOrder(t *testing.T) {
	docx := testDocx(t, `<w:p><w:r><w:t>`+thRevenue+`</w:t></w:r></w:p>`+
		`<w:p><w:r><w:t>12,480,300</w:t></w:r></w:p>`+
		`<w:tbl><w:tr><w:tc><w:p><w:r><w:t>`+thCost[:9]+`</w:t></w:r><w:r><w:br/><w:t>`+thCost[9:]+`</w:t></w:r></w:p></w:tc></w:tr></w:tbl>`)
	got := docxThaiParagraphs(docx)
	if len(got) != 2 || got[0] != thRevenue || thaiKey(got[1]) != thaiKey(thCost) {
		t.Fatalf("paragraphs: %q", got)
	}
}

func TestPdfGate(t *testing.T) {
	docx := testDocx(t, `<w:p><w:r><w:t>`+thRevenue+`</w:t></w:r></w:p><w:p><w:r><w:t>`+thCost+`</w:t></w:r></w:p>`)
	good := CheckResult{Pages: 2, RenderedPages: 2, PageInk: []float64{0.03, 0.01},
		RawText: "รายได้ จากการขาย\n12,480,300\n" + thCost + "\n"}
	for _, c := range pdfGate(docx, good) {
		if !c.OK {
			t.Fatalf("a faithful PDF passes: %+v", c)
		}
	}
	cases := map[string]CheckResult{
		"blank page":        {Pages: 2, RenderedPages: 2, PageInk: []float64{0.03, 0}, RawText: good.RawText},
		"page not rendered": {Pages: 2, RenderedPages: 1, PageInk: []float64{0.03}, RawText: good.RawText},
		"reordered Thai": {Pages: 2, RenderedPages: 2, PageInk: []float64{0.03, 0.01},
			RawText: thRevenue + "\n" + "คา่ใช้จ่าย"},
		"Thai missing": {Pages: 2, RenderedPages: 2, PageInk: []float64{0.03, 0.01}, RawText: thRevenue},
	}
	for name, chk := range cases {
		failed := 0
		for _, c := range pdfGate(docx, chk) {
			if !c.OK {
				failed++
				if strings.Contains(c.Detail.String(), "[]") || !am.ReadsForPerson(c.Detail.String()) {
					t.Fatalf("%s: detail reads as Thai, not internals: %s", name, c.Detail)
				}
			}
		}
		if failed == 0 {
			t.Fatalf("%s must fail the gate", name)
		}
	}
}
