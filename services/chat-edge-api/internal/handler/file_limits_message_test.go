package handler

// Phase 7 — the 413 body must state the cap that is ACTUALLY enforced.
// It used to say "max 25 MB" while the service enforced
// MAX_FILE_SIZE_BYTES (200 MiB by default) and the quota message said
// "100 MB" against a 1 GiB limit, so users were told about limits that
// did not exist.

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

func TestHumanBytes(t *testing.T) {
	cases := []struct {
		in   int64
		want string
	}{
		{209715200, "200 MB"},
		{1073741824, "1 GB"},
		{26214400, "25 MB"},
		{52428800, "50 MB"},
		{1536, "1.5 KB"},
		{512, "512 B"},
	}
	for _, c := range cases {
		if got := humanBytes(c.in); got != c.want {
			t.Errorf("humanBytes(%d) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestUploadError_TooLarge_UsesConfiguredLimit(t *testing.T) {
	h := NewFileHandler(service.NewFileServiceForTest(service.Knobs{
		MaxFileSizeBytes: 209715200,  // 200 MiB
		MaxConvFileTotal: 1073741824, // 1 GiB
	}))

	rec := httptest.NewRecorder()
	h.writeUploadError(rec, service.ErrFileTooLarge)
	body := rec.Body.String()
	if !strings.Contains(body, "200 MB") {
		t.Fatalf("413 body must state the configured cap, got %s", body)
	}
	if strings.Contains(body, "25 MB") {
		t.Fatalf("413 body still hard-codes the stale 25 MB limit: %s", body)
	}

	rec = httptest.NewRecorder()
	h.writeUploadError(rec, service.ErrFileConvQuotaExceeded)
	body = rec.Body.String()
	if !strings.Contains(body, "1 GB") || strings.Contains(body, "100 MB") {
		t.Fatalf("quota body must state the configured total, got %s", body)
	}
}

func TestUploadError_LimitTracksADifferentConfig(t *testing.T) {
	// A deployment that lowers the cap must see ITS number, proving the
	// message is derived rather than hard-coded.
	h := NewFileHandler(service.NewFileServiceForTest(service.Knobs{
		MaxFileSizeBytes: 52428800, // 50 MiB
		MaxConvFileTotal: 209715200,
	}))
	rec := httptest.NewRecorder()
	h.writeUploadError(rec, service.ErrFileTooLarge)

	var env map[string]map[string]string
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("decode error envelope: %v", err)
	}
	if msg := env["error"]["message"]; !strings.Contains(msg, "50 MB") {
		t.Fatalf("message = %q, want it to mention 50 MB", msg)
	}
}
