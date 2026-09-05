package handler

import (
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

func TestFileToWireIncludesRelativeDownloadURL(t *testing.T) {
	id := uuid.New()
	convID := uuid.New()
	msgID := uuid.New()
	wire := FileToWire(&model.ConversationFile{
		ID:               id,
		ConversationID:   convID,
		MessageID:        &msgID,
		OriginalFilename: "generated-image.png",
		ContentType:      "image/png",
		FileSizeBytes:    1234,
		StoragePath:      `C:\secret\internal\generated-image.png`,
		ExtractionStatus: "completed",
		CreatedAt:        time.Date(2026, 6, 12, 1, 2, 3, 0, time.UTC),
		IsGenerated:      true,
		Version:          1,
	})

	wantURL := "/files/" + id.String() + "/download"
	if wire.DownloadURL != wantURL {
		t.Fatalf("download_url = %q, want %q", wire.DownloadURL, wantURL)
	}
	if wire.DownloadURL == "" || wire.DownloadURL[0] != '/' {
		t.Fatalf("download_url must be relative, got %q", wire.DownloadURL)
	}
	if wire.OriginalFilename != "generated-image.png" ||
		wire.ContentType != "image/png" ||
		wire.FileSizeBytes != 1234 ||
		!wire.IsGenerated {
		t.Fatalf("file metadata not preserved: %+v", wire)
	}
}

// FIX67 — a generated image carries its producing model in inspection_json;
// FileToWire surfaces it as generated_by (for the "Made by …" attribution).
func TestFileToWireSurfacesGeneratedBy(t *testing.T) {
	insp := `{"generated_by":"mock-image-1"}`
	wire := FileToWire(&model.ConversationFile{
		ID:               uuid.New(),
		ConversationID:   uuid.New(),
		OriginalFilename: "cat.png",
		ContentType:      "image/png",
		ExtractionStatus: "completed",
		CreatedAt:        time.Now(),
		IsGenerated:      true,
		Version:          1,
		InspectionJSON:   &insp,
	})
	if wire.GeneratedBy != "mock-image-1" {
		t.Fatalf("generated_by = %q, want %q", wire.GeneratedBy, "mock-image-1")
	}

	// A non-generated file (or one without the metadata) exposes nothing.
	plain := FileToWire(&model.ConversationFile{
		ID: uuid.New(), ConversationID: uuid.New(), OriginalFilename: "doc.pdf",
		ContentType: "application/pdf", ExtractionStatus: "completed", CreatedAt: time.Now(),
	})
	if plain.GeneratedBy != "" {
		t.Fatalf("generated_by should be empty for a plain file, got %q", plain.GeneratedBy)
	}
}
