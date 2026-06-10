package service

import (
	"context"
	"encoding/base64"
	"strings"
	"testing"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
)

// --- fakes ------------------------------------------------------------------

type fakeImageGen struct {
	resp   *GatewayImagesResponse
	err    error
	gotReq GatewayImagesRequest
}

func (f *fakeImageGen) GenerateImages(
	_ context.Context, _ GatewayCallContext, req GatewayImagesRequest,
) (*GatewayImagesResponse, error) {
	f.gotReq = req
	return f.resp, f.err
}

type fakeImageSaver struct {
	gotData  []byte
	gotMsgID uuid.UUID
	err      error
	called   bool
}

func (f *fakeImageSaver) SaveGeneratedImage(
	_ context.Context, in GeneratedImageInput,
) (*model.ConversationFile, error) {
	f.called = true
	f.gotData = in.Data
	f.gotMsgID = in.MessageID
	if f.err != nil {
		return nil, f.err
	}
	return &model.ConversationFile{ID: uuid.New()}, nil
}

func newImageExecutor(gen imageGenerator, saver generatedImageSaver) *ToolExecutor {
	e := NewToolExecutor(nil)
	e.SetImageGeneration(gen, saver, "gpt-image-1")
	return e
}

func imageCall(args string) GatewayToolCall {
	return GatewayToolCall{
		Function: GatewayToolCallFunction{Name: "generate_image", Arguments: args},
	}
}

// --- tests ------------------------------------------------------------------

func TestGenerateImageToolDef(t *testing.T) {
	def := generateImageToolDef()
	if def.Type != "function" || def.Function.Name != "generate_image" {
		t.Fatalf("unexpected tool def: %+v", def)
	}
	props, ok := def.Function.Parameters["properties"].(map[string]any)
	if !ok {
		t.Fatal("parameters.properties is not a map")
	}
	if _, ok := props["prompt"]; !ok {
		t.Fatal("missing prompt property")
	}
	req, ok := def.Function.Parameters["required"].([]string)
	if !ok || len(req) != 1 || req[0] != "prompt" {
		t.Fatalf("expected required=[prompt], got %v", def.Function.Parameters["required"])
	}
}

func TestExecGenerateImageSuccess(t *testing.T) {
	png := []byte("\x89PNG fake image bytes")
	gen := &fakeImageGen{resp: &GatewayImagesResponse{
		Data: []GatewayImageDatum{{B64JSON: base64.StdEncoding.EncodeToString(png)}},
	}}
	saver := &fakeImageSaver{}
	e := newImageExecutor(gen, saver)
	msgID := uuid.New()

	out := e.Execute(context.Background(),
		imageCall(`{"prompt":"a cat","size":"1024x1536","quality":"high"}`),
		ToolExecContext{
			TenantID: uuid.New(), UserID: uuid.New(),
			ConversationID: uuid.New(), MessageID: msgID,
		},
	)

	if gen.gotReq.Model != "gpt-image-1" || gen.gotReq.Prompt != "a cat" ||
		gen.gotReq.Size != "1024x1536" || gen.gotReq.Quality != "high" {
		t.Fatalf("unexpected gateway request: %+v", gen.gotReq)
	}
	if !saver.called {
		t.Fatal("SaveGeneratedImage was not called")
	}
	if string(saver.gotData) != string(png) {
		t.Fatalf("saver got wrong bytes: %q", saver.gotData)
	}
	if saver.gotMsgID != msgID {
		t.Fatalf("saver got message id %s, want %s", saver.gotMsgID, msgID)
	}
	if !strings.Contains(out, "attached") {
		t.Fatalf("expected success message mentioning the attachment, got %q", out)
	}
}

func TestExecGenerateImageDefaultsSizeAndQuality(t *testing.T) {
	gen := &fakeImageGen{resp: &GatewayImagesResponse{
		Data: []GatewayImageDatum{{B64JSON: base64.StdEncoding.EncodeToString([]byte("x"))}},
	}}
	e := newImageExecutor(gen, &fakeImageSaver{})
	e.Execute(context.Background(), imageCall(`{"prompt":"a dog"}`),
		ToolExecContext{MessageID: uuid.New()})
	if gen.gotReq.Size != "1024x1024" || gen.gotReq.Quality != "auto" {
		t.Fatalf("expected default size/quality, got %+v", gen.gotReq)
	}
}

func TestExecGenerateImageGatewayError(t *testing.T) {
	gen := &fakeImageGen{err: &GatewayUnavailableError{Status: 502, Body: "boom"}}
	saver := &fakeImageSaver{}
	e := newImageExecutor(gen, saver)
	out := e.Execute(context.Background(), imageCall(`{"prompt":"x"}`),
		ToolExecContext{MessageID: uuid.New()})
	if saver.called {
		t.Fatal("saver should not be called when the gateway errors")
	}
	if !strings.Contains(out, "couldn't generate") {
		t.Fatalf("expected graceful error message, got %q", out)
	}
}

func TestExecGenerateImageSaveError(t *testing.T) {
	gen := &fakeImageGen{resp: &GatewayImagesResponse{
		Data: []GatewayImageDatum{{B64JSON: base64.StdEncoding.EncodeToString([]byte("x"))}},
	}}
	saver := &fakeImageSaver{err: ErrFileEmpty}
	e := newImageExecutor(gen, saver)
	out := e.Execute(context.Background(), imageCall(`{"prompt":"x"}`),
		ToolExecContext{MessageID: uuid.New()})
	if !strings.Contains(out, "couldn't save") {
		t.Fatalf("expected save-failure message, got %q", out)
	}
}

func TestExecGenerateImageNoMessageID(t *testing.T) {
	// Non-streaming path leaves MessageID nil → degrade gracefully, and
	// never call the gateway (nothing to attach the result to).
	gen := &fakeImageGen{}
	e := newImageExecutor(gen, &fakeImageSaver{})
	out := e.Execute(context.Background(), imageCall(`{"prompt":"x"}`), ToolExecContext{})
	if !strings.Contains(out, "streaming") {
		t.Fatalf("expected streaming-only message, got %q", out)
	}
	if gen.gotReq.Prompt != "" {
		t.Fatal("gateway should not be called when MessageID is nil")
	}
}
