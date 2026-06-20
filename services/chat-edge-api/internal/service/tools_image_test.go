package service

import (
	"context"
	"encoding/base64"
	"strings"
	"testing"
	"time"

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
	gotData      []byte
	gotMsgID     uuid.UUID
	gotRequestID string
	err          error
	called       bool
}

func (f *fakeImageSaver) SaveGeneratedImage(
	_ context.Context, in GeneratedImageInput,
) (*model.ConversationFile, error) {
	f.called = true
	f.gotData = in.Data
	f.gotMsgID = in.MessageID
	f.gotRequestID = in.RequestID
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

func TestDirectImageGenerationUsesImageEndpointAndSavesAttachment(t *testing.T) {
	png := []byte("\x89PNG direct image bytes")
	gen := &fakeImageGen{resp: &GatewayImagesResponse{
		Data: []GatewayImageDatum{{B64JSON: base64.StdEncoding.EncodeToString(png)}},
	}}
	saver := &fakeImageSaver{}
	exec := newImageExecutor(gen, saver)
	svc := &MessageService{toolExec: exec}
	asstID := uuid.New()

	out := svc.generateImageForMessage(context.Background(), SendInput{
		TenantID:  uuid.New(),
		UserID:    uuid.New(),
		Content:   "สร้างรูปแมว",
		RequestID: "req-image-test",
	}, uuid.New(), asstID)

	if gen.gotReq.Model != "gpt-image-1" {
		t.Fatalf("expected image model gpt-image-1, got %q", gen.gotReq.Model)
	}
	if gen.gotReq.Prompt != "สร้างรูปแมว" {
		t.Fatalf("expected original prompt, got %q", gen.gotReq.Prompt)
	}
	if gen.gotReq.Size != "1024x1024" || gen.gotReq.Quality != "auto" {
		t.Fatalf("unexpected defaults: %+v", gen.gotReq)
	}
	if !saver.called || saver.gotMsgID != asstID || saver.gotRequestID != "req-image-test" ||
		string(saver.gotData) != string(png) {
		t.Fatalf("image was not saved on assistant message: called=%v msg=%s", saver.called, saver.gotMsgID)
	}
	if out.Err != nil {
		t.Fatalf("expected success, got error %+v", out.Err)
	}
	if !strings.Contains(out.Message, "สร้างรูป") {
		t.Fatalf("expected Thai success acknowledgement, got %q", out.Message)
	}
}

func TestMapImageGenerationError(t *testing.T) {
	cases := []struct {
		name string
		err  error
		code string
	}{
		{
			name: "invalid request",
			err:  &GatewayUnavailableError{Status: 502, Body: `{"error":{"code":"upstream_bad_response","message":"bad","details":{"status":400,"body":{"error":{"code":"invalid_request_error","message":"invalid size"}}}}}`},
			code: "image_invalid_request",
		},
		{
			name: "auth",
			err:  &GatewayUnavailableError{Status: 502, Body: `{"error":{"code":"provider_error","message":"credentials","details":{"status":401,"body":{"error":{"code":"invalid_api_key","message":"bad key"}}}}}`},
			code: "image_auth_error",
		},
		{
			name: "permission",
			err:  &GatewayUnavailableError{Status: 502, Body: `{"error":{"code":"provider_error","message":"permission","details":{"status":403}}}`},
			code: "image_auth_error",
		},
		{
			name: "rate limited",
			err:  &GatewayUnavailableError{Status: 429, Body: `{"error":{"code":"rate_limit_exceeded","message":"rate limit","details":{"status":429,"body":{"error":{"code":"insufficient_quota"}}}}}`},
			code: "image_rate_limited",
		},
		{
			name: "timeout",
			err:  &GatewayUnavailableError{Status: 504, Body: `{"error":{"code":"provider_timeout","message":"timed out"}}`},
			code: "image_timeout",
		},
		{
			name: "provider unavailable",
			err:  &GatewayUnavailableError{Status: 503, Body: `{"error":{"code":"provider_unavailable","message":"down","details":{"status":503}}}`},
			code: "image_provider_unavailable",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := mapImageGenerationError(tc.err)
			if got.Code != tc.code {
				t.Fatalf("code = %q, want %q (%+v)", got.Code, tc.code, got)
			}
			if got.Code == "provider_error" || got.Code == "all_models_failed" {
				t.Fatalf("image error collapsed to generic provider code: %+v", got)
			}
		})
	}
}

func TestDirectImageGenerationContextTimeout(t *testing.T) {
	gen := &fakeImageGen{err: context.DeadlineExceeded}
	svc := &MessageService{
		toolExec:               newImageExecutor(gen, &fakeImageSaver{}),
		imageGenerationTimeout: time.Second,
	}
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()

	out := svc.generateImageForMessage(ctx, SendInput{
		TenantID: uuid.New(),
		UserID:   uuid.New(),
		Content:  "draw a cat",
	}, uuid.New(), uuid.New())

	if out.Err == nil || out.Err.Code != "image_timeout" {
		t.Fatalf("expected image_timeout, got %+v", out.Err)
	}
}

func TestDirectImageGenerationContextCancel(t *testing.T) {
	gen := &fakeImageGen{err: context.Canceled}
	svc := &MessageService{
		toolExec:               newImageExecutor(gen, &fakeImageSaver{}),
		imageGenerationTimeout: time.Second,
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	out := svc.generateImageForMessage(ctx, SendInput{
		TenantID: uuid.New(),
		UserID:   uuid.New(),
		Content:  "draw a cat",
	}, uuid.New(), uuid.New())

	if out.Err == nil || out.Err.Code != "image_cancelled" {
		t.Fatalf("expected image_cancelled, got %+v", out.Err)
	}
}

func TestNewMessageServiceDefaultImageTimeout(t *testing.T) {
	svc := NewMessageService(nil, nil, nil, nil, nil, 0)
	if svc.imageGenerationTimeout != 180*time.Second {
		t.Fatalf("image timeout = %s, want 180s", svc.imageGenerationTimeout)
	}
	svc.SetImageGenerationTimeout(2 * time.Second)
	if svc.imageGenerationTimeout != 2*time.Second {
		t.Fatalf("image timeout setter failed: %s", svc.imageGenerationTimeout)
	}
}
