package service

import "testing"

// The classification here decides whether the endpoint spends a
// container at all, so it has to agree with kind_for() in
// scripts/preview_data.py. When one moves, move both.
func TestPreviewDataKind_ContentTypeFirst(t *testing.T) {
	cases := []struct {
		contentType string
		filename    string
		want        string
	}{
		{"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "a.xlsx", "sheet"},
		{"application/vnd.ms-excel.sheet.macroEnabled.12", "a.xlsm", "sheet"},
		{"text/csv", "a.csv", "sheet"},
		{"text/tab-separated-values", "a.tsv", "sheet"},
		{"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "a.docx", "document"},
		{"application/vnd.openxmlformats-officedocument.presentationml.presentation", "a.pptx", "slides"},
		// Parameters are not part of the type.
		{"text/csv; charset=utf-8", "a.csv", "sheet"},
		{"TEXT/CSV", "a.csv", "sheet"},
	}
	for _, c := range cases {
		if got := PreviewDataKind(c.contentType, c.filename); got != c.want {
			t.Errorf("PreviewDataKind(%q, %q) = %q, want %q",
				c.contentType, c.filename, got, c.want)
		}
	}
}

func TestPreviewDataKind_FallsBackToTheExtension(t *testing.T) {
	// Uploads regularly arrive as octet-stream when the browser could
	// not sniff a type; the filename is then the only signal there is.
	for _, name := range []string{"report.xlsx", "REPORT.XLSX", "notes.docx", "deck.pptx", "rows.csv"} {
		if PreviewDataKind("application/octet-stream", name) == "" {
			t.Errorf("PreviewDataKind(octet-stream, %q) = \"\", want a structured kind", name)
		}
	}
}

func TestPreviewDataKind_TypesWithNoStructuredView(t *testing.T) {
	cases := [][2]string{
		{"application/pdf", "scan.pdf"},
		{"image/png", "shot.png"},
		{"application/zip", "bundle.zip"},
		{"text/plain", "notes.txt"},
		{"", ""},
		{"", "noextension"},
	}
	for _, c := range cases {
		if got := PreviewDataKind(c[0], c[1]); got != "" {
			t.Errorf("PreviewDataKind(%q, %q) = %q, want \"\" so the client keeps the flat text",
				c[0], c[1], got)
		}
	}
}

func TestPreviewDataRequest_ClampsBeforeSpendingAContainer(t *testing.T) {
	cases := []struct {
		in, want PreviewDataRequest
	}{
		// Nothing specified → the defaults.
		{PreviewDataRequest{}, PreviewDataRequest{Sheet: 0, Offset: 0, Limit: PreviewDefaultRows}},
		// An absurd window is cut to the cap, not refused.
		{PreviewDataRequest{Limit: 100000}, PreviewDataRequest{Limit: PreviewMaxRows}},
		{PreviewDataRequest{Offset: 99_999_999}, PreviewDataRequest{Offset: PreviewMaxOffset, Limit: PreviewDefaultRows}},
		{PreviewDataRequest{Sheet: 9999}, PreviewDataRequest{Sheet: PreviewMaxSheet, Limit: PreviewDefaultRows}},
		// Negatives cannot walk backwards out of the file.
		{PreviewDataRequest{Sheet: -3, Offset: -10, Limit: -1},
			PreviewDataRequest{Sheet: 0, Offset: 0, Limit: PreviewDefaultRows}},
		// A legitimate window passes through untouched.
		{PreviewDataRequest{Sheet: 2, Offset: 400, Limit: 100},
			PreviewDataRequest{Sheet: 2, Offset: 400, Limit: 100}},
	}
	for _, c := range cases {
		if got := c.in.clamp(); got != c.want {
			t.Errorf("%+v.clamp() = %+v, want %+v", c.in, got, c.want)
		}
	}
}
