package service

import (
	"context"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// FIX 2 — chat-edge voice must go THROUGH the gateway (which runs the DLP
// raw-egress gate), never OpenAI directly. These tests prove the client hits
// the gateway's whitelisted routes, carries the service JWT + end-user
// identity headers, and parses the responses.
//
// FIX132 B — knowledge-base embeddings no longer go through the gateway (or
// anywhere off this machine): they run in kb-models on the compose network
// (kb_models_client.go, kb_models_client_test.go).

func newTestCallCtx() GatewayCallContext {
	return GatewayCallContext{
		TenantID: uuid.MustParse("11111111-1111-1111-1111-111111111111"),
		UserID:   uuid.MustParse("22222222-2222-2222-2222-222222222222"),
	}
}

func assertTrustedHeaders(t *testing.T, r *http.Request) {
	t.Helper()
	if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
		t.Fatalf("missing bearer credential")
	}
	if r.Header.Get("X-Tenant-ID") != "11111111-1111-1111-1111-111111111111" {
		t.Fatalf("X-Tenant-ID = %q", r.Header.Get("X-Tenant-ID"))
	}
	if r.Header.Get("X-User-ID") != "22222222-2222-2222-2222-222222222222" {
		t.Fatalf("X-User-ID = %q", r.Header.Get("X-User-ID"))
	}
	if r.Header.Get("X-Internal-Service-Secret") != "internal-secret" {
		t.Fatalf("missing internal-service-secret header")
	}
}

func newVoiceEmbClient(base string) *GatewayClient {
	// Service JWT secret set (FIX 1) so the bearer is a service JWT; internal
	// secret set so the trusted-caller header is stamped.
	return NewGatewayClient(base, "", testSvcSecret, 60*time.Second, "internal-secret")
}

func TestVoiceTranscribe_RoutesThroughGatewayMultipart(t *testing.T) {
	var gotPath, gotAudio, gotLang string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		assertTrustedHeaders(t, r)
		mediaType, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if mediaType != "multipart/form-data" {
			t.Fatalf("content-type = %q", mediaType)
		}
		mr := multipart.NewReader(r.Body, params["boundary"])
		for {
			p, err := mr.NextPart()
			if err != nil {
				break
			}
			data, _ := io.ReadAll(p)
			switch p.FormName() {
			case "audio":
				gotAudio = string(data)
			case "language":
				gotLang = string(data)
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"text":"transcribed text"}`))
	}))
	defer srv.Close()

	g := newVoiceEmbClient(srv.URL)
	text, err := g.VoiceTranscribe(
		context.Background(), newTestCallCtx(),
		[]byte("RIFFxxxx"), "audio.webm", "audio/webm", "en",
	)
	if err != nil {
		t.Fatalf("VoiceTranscribe: %v", err)
	}
	if gotPath != "/voice/transcribe" {
		t.Fatalf("path = %q", gotPath)
	}
	if gotAudio != "RIFFxxxx" || gotLang != "en" {
		t.Fatalf("audio=%q lang=%q", gotAudio, gotLang)
	}
	if text != "transcribed text" {
		t.Fatalf("text = %q", text)
	}
}

func TestVoiceSynthesize_RoutesThroughGatewayReturnsAudio(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		assertTrustedHeaders(t, r)
		var body struct {
			Text  string  `json:"text"`
			Voice string  `json:"voice"`
			Speed float64 `json:"speed"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body.Text != "say this" || body.Voice != "alloy" {
			t.Fatalf("body = %+v", body)
		}
		w.Header().Set("Content-Type", "audio/mpeg")
		_, _ = w.Write([]byte("ID3-mp3-bytes"))
	}))
	defer srv.Close()

	g := newVoiceEmbClient(srv.URL)
	audio, err := g.VoiceSynthesize(context.Background(), newTestCallCtx(), "say this", "alloy", 1.0)
	if err != nil {
		t.Fatalf("VoiceSynthesize: %v", err)
	}
	if gotPath != "/voice/synthesize" {
		t.Fatalf("path = %q", gotPath)
	}
	if string(audio) != "ID3-mp3-bytes" {
		t.Fatalf("audio = %q", string(audio))
	}
}

// asGatewayUnavailable is a tiny errors.As helper kept local so the test
// doesn't depend on import placement in the non-test file.
func asGatewayUnavailable(err error, target **GatewayUnavailableError) bool {
	for err != nil {
		if gu, ok := err.(*GatewayUnavailableError); ok {
			*target = gu
			return true
		}
		type unwrapper interface{ Unwrap() error }
		u, ok := err.(unwrapper)
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}
