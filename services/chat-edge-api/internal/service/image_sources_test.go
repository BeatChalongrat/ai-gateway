package service

import (
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
)

type imageSourceFiles struct {
	fakeImageSaver
	path        string
	deny        bool
	cleaned     int
	owner, conv uuid.UUID
}

func (f *imageSourceFiles) OpenConversationFileCopy(_ context.Context, _, user, conv, _ uuid.UUID) (string, string, func(), error) {
	if f.deny || user != f.owner || conv != f.conv {
		return "", "", nil, fmt.Errorf("denied")
	}
	return f.path, "reference.png", func() { f.cleaned++ }, nil
}
func TestImageEditConversationScopeAndRoundTrip(t *testing.T) {
	raw := []byte("\x89PNG\r\n\x1a\nreference")
	path := filepath.Join(t.TempDir(), "reference.png")
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	ec := ToolExecContext{UserID: uuid.New(), ConversationID: uuid.New(), MessageID: uuid.New()}
	files := &imageSourceFiles{path: path, owner: ec.UserID, conv: ec.ConversationID}
	gen := &fakeImageGen{resp: &GatewayImagesResponse{Model: "test-image", Data: []GatewayImageDatum{{B64JSON: base64.StdEncoding.EncodeToString(raw)}}}}
	e := newImageExecutor(gen, files)
	id := uuid.NewString()
	result := e.execGenerateImage(context.Background(), fmt.Sprintf(`{"prompt":"keep title","source_file_ids":[%q,%q]}`, id, id), ec)
	if !strings.Contains(result, "file_id=") || len(gen.gotReq.SourceImages) != 1 || files.cleaned != 1 {
		t.Fatalf("incomplete image edit: %s %+v", result, gen.gotReq)
	}
	if !strings.Contains(gen.gotReq.SourceImages[0], base64.StdEncoding.EncodeToString(raw)) {
		t.Fatal("reference changed")
	}
	files.deny = true
	gen.gotReq = GatewayImagesRequest{}
	result = e.execGenerateImage(context.Background(), fmt.Sprintf(`{"prompt":"edit","source_file_ids":[%q]}`, id), ec)
	if gen.gotReq.Prompt != "" || !strings.Contains(result, "conversation") {
		t.Fatal("denied source reached provider")
	}
}
func TestDirectImageReferenceDeniedBeforeGeneration(t *testing.T) {
	files := &imageSourceFiles{deny: true}
	gen := &fakeImageGen{}
	svc := &MessageService{toolExec: newImageExecutor(gen, files)}
	result := svc.generateImageForMessage(context.Background(), SendInput{FileIDs: []uuid.UUID{uuid.New()}}, uuid.New(), uuid.New())
	if result.Err == nil || gen.gotReq.Prompt != "" {
		t.Fatal("direct image path bypassed file ownership")
	}
}
func TestReadArtifactRejectsOversizeInsteadOfTruncating(t *testing.T) {
	path := filepath.Join(t.TempDir(), "clip.mp4")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	err = f.Truncate(maxArtifactBytes + 1)
	f.Close()
	if err != nil {
		t.Fatal(err)
	}
	if _, err = ReadArtifact(path); err == nil {
		t.Fatal("oversized MP4 was silently truncated")
	}
	if ArtifactContentType(path) != "video/mp4" {
		t.Fatal("wrong video MIME")
	}
}
