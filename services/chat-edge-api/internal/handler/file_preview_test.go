package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// fakePreviewSource stands in for FileService.PreviewData: it records
// the request the handler built and returns whatever the test wants.
type fakePreviewSource struct {
	tenantID uuid.UUID
	userID   uuid.UUID
	fileID   uuid.UUID
	payload  json.RawMessage
	err      error

	got service.PreviewDataRequest
}

func (f *fakePreviewSource) PreviewData(
	_ context.Context, tenantID, userID, fileID uuid.UUID,
	req service.PreviewDataRequest,
) (json.RawMessage, error) {
	f.got = req
	if f.err != nil {
		return nil, f.err
	}
	// The service's own ownership rule, reproduced: someone else's file
	// is not-found, never forbidden.
	if tenantID != f.tenantID || userID != f.userID || fileID != f.fileID {
		return nil, service.ErrFileNotFound
	}
	return f.payload, nil
}

func getPreview(t *testing.T, src *fakePreviewSource, fileID string, query string,
	tenantID, userID uuid.UUID) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/files/x/preview-data"+query, nil)
	rec := httptest.NewRecorder()
	writePreviewData(context.Background(), rec, req, fileID, tenantID, userID, src)
	return rec
}

func previewSource(payload string) *fakePreviewSource {
	return &fakePreviewSource{
		tenantID: uuid.New(),
		userID:   uuid.New(),
		fileID:   uuid.New(),
		payload:  json.RawMessage(payload),
	}
}

func TestPreviewData_OwnerGetsTheScriptsJsonVerbatim(t *testing.T) {
	body := `{"kind":"sheet","sheet":{"names":["ยอดขาย"],"rows":[["ก","1"]]}}`
	src := previewSource(body)

	rec := getPreview(t, src, src.fileID.String(), "", src.tenantID, src.userID)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (body %q)", rec.Code, rec.Body.String())
	}
	if rec.Body.String() != body {
		t.Fatalf("body = %q, want the script output unchanged %q", rec.Body.String(), body)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("content-type = %q, want application/json", ct)
	}
}

func TestPreviewData_AnotherUsersFileIs404NotForbidden(t *testing.T) {
	src := previewSource(`{"kind":"sheet"}`)

	// Right file id, wrong user — the two must be indistinguishable from
	// "no such file", or the response can be used to enumerate ids.
	rec := getPreview(t, src, src.fileID.String(), "", src.tenantID, uuid.New())

	if rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rec.Code)
	}
}

func TestPreviewData_UnsupportedTypeIs415SoTheClientCanFallBack(t *testing.T) {
	src := previewSource("")
	src.err = service.ErrPreviewUnsupported

	rec := getPreview(t, src, src.fileID.String(), "", src.tenantID, src.userID)

	if rec.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("status = %d, want 415", rec.Code)
	}
}

func TestPreviewData_NoSandboxIs503(t *testing.T) {
	src := previewSource("")
	src.err = service.ErrFileSandboxUnavailable

	rec := getPreview(t, src, src.fileID.String(), "", src.tenantID, src.userID)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", rec.Code)
	}
}

func TestPreviewData_AScriptFailureIs500NotAPartialBody(t *testing.T) {
	src := previewSource("")
	src.err = context.DeadlineExceeded

	rec := getPreview(t, src, src.fileID.String(), "", src.tenantID, src.userID)

	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", rec.Code)
	}
}

func TestPreviewData_BadFileIdIs400(t *testing.T) {
	src := previewSource(`{"kind":"sheet"}`)

	rec := getPreview(t, src, "not-a-uuid", "", src.tenantID, src.userID)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rec.Code)
	}
}

func TestPreviewData_QueryParamsReachTheService(t *testing.T) {
	src := previewSource(`{"kind":"sheet"}`)

	getPreview(t, src, src.fileID.String(), "?sheet=2&offset=400&limit=100",
		src.tenantID, src.userID)

	want := service.PreviewDataRequest{Sheet: 2, Offset: 400, Limit: 100}
	if src.got != want {
		t.Fatalf("request = %+v, want %+v", src.got, want)
	}
}

func TestPreviewData_GarbageQueryParamsFallBackInsteadOfFailing(t *testing.T) {
	src := previewSource(`{"kind":"sheet"}`)

	rec := getPreview(t, src, src.fileID.String(), "?sheet=abc&offset=&limit=%C3%A9",
		src.tenantID, src.userID)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 — a stray query string must not fail the preview", rec.Code)
	}
	if (src.got != service.PreviewDataRequest{}) {
		t.Fatalf("request = %+v, want the zero value so the service applies its defaults", src.got)
	}
}
