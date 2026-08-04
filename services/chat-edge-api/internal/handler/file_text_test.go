package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// fakeFileTextSource reproduces FileService.Get's contract: tenant- and
// user-scoped, and a file belonging to another user in the same tenant
// comes back as ErrFileNotFound (never a 403 — the caller must not be
// able to tell "not yours" from "doesn't exist").
type fakeFileTextSource struct {
	row *model.ConversationFile
}

func (f *fakeFileTextSource) Get(
	_ context.Context, tenantID, userID, fileID uuid.UUID,
) (*model.ConversationFile, error) {
	if f.row == nil || f.row.ID != fileID ||
		f.row.TenantID != tenantID || f.row.UserID != userID {
		return nil, service.ErrFileNotFound
	}
	return f.row, nil
}

func textRow(status string, text *string) *model.ConversationFile {
	return &model.ConversationFile{
		ID:               uuid.New(),
		TenantID:         uuid.New(),
		UserID:           uuid.New(),
		OriginalFilename: "scan.pdf",
		ContentType:      "application/pdf",
		ExtractionStatus: status,
		ExtractedText:    text,
	}
}

func getText(row *model.ConversationFile, tenantID, userID uuid.UUID) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	writeFileText(context.Background(), rec, row.ID.String(),
		tenantID, userID, &fakeFileTextSource{row: row})
	return rec
}

func decodeText(t *testing.T, rec *httptest.ResponseRecorder) fileTextResponse {
	t.Helper()
	var got fileTextResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode body %q: %v", rec.Body.String(), err)
	}
	return got
}

func TestFileText_OwnerGetsCompletedText(t *testing.T) {
	text := "--- Page 1 of 2 ---\n[OCR] สวัสดีครับ\n"
	row := textRow("completed", &text)

	rec := getText(row, row.TenantID, row.UserID)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (body %q)", rec.Code, rec.Body.String())
	}
	got := decodeText(t, rec)
	if got.Text != text {
		t.Fatalf("text = %q, want %q", got.Text, text)
	}
	if got.ExtractionStatus != "completed" {
		t.Fatalf("extraction_status = %q, want completed", got.ExtractionStatus)
	}
	if !got.OCR {
		t.Fatal("ocr = false, want true — the body carries the [OCR] page marker")
	}
	if got.Truncated {
		t.Fatal("truncated = true on a short document")
	}
}

// The whole point of the flag: a client must not present a capped
// extraction as the complete document.
func TestFileText_ReportsTruncation(t *testing.T) {
	text := strings.Repeat("ก", 100) +
		"\n\n[Content truncated — showing first 50000 characters of 91234 total]"
	row := textRow("completed", &text)

	got := decodeText(t, getText(row, row.TenantID, row.UserID))

	if !got.Truncated {
		t.Fatal("truncated = false, want true")
	}
}

// While extraction is still running the client must get the status and
// NOT an empty text it could mistake for "the document is blank".
func TestFileText_ProcessingReturnsStatusWithoutText(t *testing.T) {
	for _, status := range []string{"pending", "processing"} {
		t.Run(status, func(t *testing.T) {
			// A half-written row would still be wrong to serve, so the
			// guard is on status even when text is present.
			partial := "half of a page"
			row := textRow(status, &partial)

			rec := getText(row, row.TenantID, row.UserID)

			if rec.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200", rec.Code)
			}
			got := decodeText(t, rec)
			if got.ExtractionStatus != status {
				t.Fatalf("extraction_status = %q, want %q", got.ExtractionStatus, status)
			}
			if got.Text != "" {
				t.Fatalf("text = %q, want empty while %s", got.Text, status)
			}
		})
	}
}

func TestFileText_TerminalFailureCarriesNoText(t *testing.T) {
	for _, status := range []string{"failed", "unsupported"} {
		t.Run(status, func(t *testing.T) {
			row := textRow(status, nil)

			got := decodeText(t, getText(row, row.TenantID, row.UserID))

			if got.ExtractionStatus != status {
				t.Fatalf("extraction_status = %q, want %q", got.ExtractionStatus, status)
			}
			if got.Text != "" {
				t.Fatalf("text = %q, want empty", got.Text)
			}
		})
	}
}

// Someone else's file — including another user inside the same tenant —
// must 404 with no document content in the response.
func TestFileText_ForeignFileIs404(t *testing.T) {
	row := textRow("completed", ptr("CONFIDENTIAL PAYROLL"))

	sameTenantOtherUser := getText(row, row.TenantID, uuid.New())
	otherTenant := getText(row, uuid.New(), row.UserID)

	for name, rec := range map[string]*httptest.ResponseRecorder{
		"same tenant, other user": sameTenantOtherUser,
		"other tenant":            otherTenant,
	} {
		if rec.Code != http.StatusNotFound {
			t.Fatalf("%s: status = %d, want 404", name, rec.Code)
		}
		if strings.Contains(rec.Body.String(), "CONFIDENTIAL") {
			t.Fatalf("%s: response leaked document text: %s", name, rec.Body.String())
		}
	}
}

func TestFileText_MalformedIDIs400(t *testing.T) {
	rec := httptest.NewRecorder()
	writeFileText(context.Background(), rec, "not-a-uuid",
		uuid.New(), uuid.New(), &fakeFileTextSource{})

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rec.Code)
	}
}

// Guard for the web client: GET /conversations/{id}/files is polled every
// 1.5s during extraction, so its rows must stay metadata-only. If someone
// later folds the document body into that response, this fails.
func TestFileListRowStaysMetadataOnly(t *testing.T) {
	text := "the entire document body"
	row := textRow("completed", &text)
	row.ConversationID = uuid.New()

	raw, err := json.Marshal(FileToWire(row))
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var fields map[string]any
	if err := json.Unmarshal(raw, &fields); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	for _, banned := range []string{"text", "extracted_text"} {
		if _, present := fields[banned]; present {
			t.Fatalf("file list row gained a %q field — that response is polled "+
				"every 1.5s; extracted text belongs on /files/{id}/text", banned)
		}
	}
	if strings.Contains(string(raw), text) {
		t.Fatalf("file list row leaked the document body: %s", raw)
	}
	if fields["extraction_status"] != "completed" {
		t.Fatalf("extraction_status missing from list row: %s", raw)
	}
}

func ptr(s string) *string { return &s }
