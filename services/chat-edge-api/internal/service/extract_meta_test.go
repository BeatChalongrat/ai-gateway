package service

import "testing"

// The extractor's meta header is the seam between a python script baked
// into the sandbox IMAGE and a Go binary deployed separately. The two
// can be at different versions at any moment, so every shape the older
// or newer side can produce has to be survivable.

func TestSplitExtractMeta_TextLayerOnlyHasNoHeader(t *testing.T) {
	// An older sandbox image, or any non-PDF branch, emits raw text.
	// It must pass through untouched with a zero meta — NOT be mistaken
	// for a malformed header and dropped.
	const body = "--- Page 1 ---\nhello world"

	text, meta := splitExtractMeta(body)

	if text != body {
		t.Fatalf("text mangled: %q", text)
	}
	if meta.UsedOCR() {
		t.Fatal("no header must mean no OCR")
	}
	if meta.Mode != "" || meta.Pages != 0 {
		t.Fatalf("expected zero meta, got %+v", meta)
	}
}

func TestSplitExtractMeta_ParsesOCRHeaderAndStripsIt(t *testing.T) {
	const out = `#SRAN-EXTRACT-META {"mode":"ocr","pages":3,"ocr_pages":3,` +
		`"ocr_avg_confidence":41.2,"ocr_truncated":false,"ocr_error":null}` +
		"\n--- Page 1 ---\n[OCR] สวัสดี"

	text, meta := splitExtractMeta(out)

	// The header must never reach the model.
	if want := "--- Page 1 ---\n[OCR] สวัสดี"; text != want {
		t.Fatalf("header not stripped:\n got %q\nwant %q", text, want)
	}
	if meta.Mode != "ocr" || meta.Pages != 3 || meta.OCRPages != 3 {
		t.Fatalf("meta not parsed: %+v", meta)
	}
	if !meta.UsedOCR() {
		t.Fatal("UsedOCR should be true")
	}
	if meta.OCRAvgConfidence == nil || *meta.OCRAvgConfidence != 41.2 {
		t.Fatalf("confidence not parsed: %+v", meta.OCRAvgConfidence)
	}
}

func TestSplitExtractMeta_MixedModeKeepsConfidence(t *testing.T) {
	const out = `#SRAN-EXTRACT-META {"mode":"mixed","pages":10,"ocr_pages":2,` +
		`"ocr_avg_confidence":88.5,"ocr_truncated":true,"ocr_error":null}` +
		"\ntext body"

	_, meta := splitExtractMeta(out)

	if meta.Mode != "mixed" {
		t.Fatalf("mode = %q", meta.Mode)
	}
	if !meta.OCRTruncated {
		t.Fatal("truncation flag lost — the user would never learn pages were skipped")
	}
	if meta.OCRAvgConfidence == nil || *meta.OCRAvgConfidence != 88.5 {
		t.Fatalf("confidence = %+v", meta.OCRAvgConfidence)
	}
}

func TestSplitExtractMeta_MalformedHeaderKeepsTheDocument(t *testing.T) {
	// Losing the metadata is acceptable; losing the user's document
	// because a JSON field changed shape is not.
	const out = "#SRAN-EXTRACT-META {this is not json}\nthe actual text"

	text, meta := splitExtractMeta(out)

	if text != "the actual text" {
		t.Fatalf("document lost: %q", text)
	}
	if meta.UsedOCR() {
		t.Fatal("unparseable meta must degrade to zero, not to a truthy claim")
	}
}

func TestSplitExtractMeta_HeaderWithoutBody(t *testing.T) {
	text, meta := splitExtractMeta("#SRAN-EXTRACT-META {\"mode\":\"ocr\"}")

	if text != "" {
		t.Fatalf("expected empty text, got %q", text)
	}
	if meta.UsedOCR() {
		t.Fatal("no body means nothing was extracted")
	}
}

func TestSplitExtractMeta_TextThatMerelyMentionsThePrefix(t *testing.T) {
	// The marker is only meaningful at position 0. A document that
	// happens to quote it mid-body must not be truncated.
	const body = "page one\n#SRAN-EXTRACT-META {\"mode\":\"ocr\"}\npage two"

	text, meta := splitExtractMeta(body)

	if text != body {
		t.Fatalf("body truncated at a mid-document marker: %q", text)
	}
	if meta.Mode != "" {
		t.Fatalf("expected zero meta, got %+v", meta)
	}
}
