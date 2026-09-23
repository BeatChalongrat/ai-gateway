package handler

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"syscall"
	"testing"
)

// FIX130 C3.4 — a name the storage refuses is a 4xx the client can explain
// (and will not retry), never a 500.
func TestFIX130_StorageNameTooLongIsA422NotA500(t *testing.T) {
	rec := httptest.NewRecorder()
	(&FileHandler{}).writeUploadError(rec, fmt.Errorf("storage upload: local storage rename: %w", syscall.ENAMETOOLONG))
	if rec.Code != http.StatusUnprocessableEntity || !strings.Contains(rec.Body.String(), "FILENAME_TOO_LONG") {
		t.Fatalf("want 422 FILENAME_TOO_LONG, got %d %s", rec.Code, rec.Body.String())
	}
}
