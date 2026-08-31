package service

import (
	"context"
	"errors"
	"testing"
)

// Sprint 1 — media-gen file caps. Verifies the raised size limit and the
// extended MIME allow-list (video/audio). The size + type gates in
// FileService.Upload run BEFORE any DB/storage access, so the rejection
// paths are exercisable with a knobs-only service; the full persistence
// path (50 MiB success) is covered by the integration harness.

func newSizedService() *FileService {
	return &FileService{knobs: Knobs{
		MaxFileSizeBytes: 200 * 1024 * 1024,  // 200 MiB
		MaxConvFileTotal: 1024 * 1024 * 1024, // 1 GiB
	}}
}

func TestAllowedContentTypes_IncludesVideoAudio(t *testing.T) {
	for _, ct := range []string{
		"video/mp4", "video/webm", "video/quicktime",
		"audio/mpeg", "audio/wav", "audio/mp4", "audio/webm",
	} {
		if !allowedContentTypes[ct] {
			t.Errorf("expected content type %q to be allowed", ct)
		}
	}
}

func TestUpload_RejectsOversizeVideo(t *testing.T) {
	s := newSizedService()
	_, err := s.Upload(context.Background(), UploadInput{
		ContentType:  "video/mp4",
		OriginalName: "big.mp4",
		Size:         250 * 1024 * 1024, // > 200 MiB
	})
	if !errors.Is(err, ErrFileTooLarge) {
		t.Fatalf("expected ErrFileTooLarge, got %v", err)
	}
}

func TestUpload_RejectsDisallowedType(t *testing.T) {
	s := newSizedService()
	_, err := s.Upload(context.Background(), UploadInput{
		ContentType:  "application/octet-stream",
		OriginalName: "evil.exe",
		Size:         1024,
	})
	if !errors.Is(err, ErrFileTypeNotAllowed) {
		t.Fatalf("expected ErrFileTypeNotAllowed, got %v", err)
	}
}

func TestUpload_FiftyMBVideoWithinGates(t *testing.T) {
	s := newSizedService()
	const fiftyMiB = int64(50 * 1024 * 1024)
	if fiftyMiB > s.knobs.MaxFileSizeBytes {
		t.Fatalf("50 MiB should be within MaxFileSizeBytes=%d", s.knobs.MaxFileSizeBytes)
	}
	if !allowedContentTypes["video/mp4"] {
		t.Fatal("video/mp4 must be allowed for the 50 MiB upload to pass")
	}
}
