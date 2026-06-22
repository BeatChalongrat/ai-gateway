package handler

import (
	"testing"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/service"
)

// Sprint 2A.5 — model_kind derivation for dedicated media models.
func TestDeriveModelKind(t *testing.T) {
	cases := []struct {
		name string
		caps *service.GatewayModelCapabilities
		want string
	}{
		{"video", &service.GatewayModelCapabilities{VideoGeneration: true}, "video"},
		{"audio", &service.GatewayModelCapabilities{AudioGeneration: true}, "audio"},
		{"tts", &service.GatewayModelCapabilities{Tts: true}, "tts"},
		{"chat", &service.GatewayModelCapabilities{Tools: true}, "chat"},
		{"nil", nil, "chat"},
	}
	for _, c := range cases {
		if got := deriveModelKind(c.caps); got != c.want {
			t.Errorf("%s: deriveModelKind = %q, want %q", c.name, got, c.want)
		}
	}
}
