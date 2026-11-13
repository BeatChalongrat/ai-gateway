package service

import (
	"context"
	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/model"
	"github.com/google/uuid"
	"strings"
	"testing"
)

type unconfirmedVideo struct{}

func (unconfirmedVideo) SubmitVideoJob(context.Context, GatewayCallContext, GatewayVideoSubmitRequest) (*GatewayVideoSubmitResponse, error) {
	return &GatewayVideoSubmitResponse{JobID: "pending", Status: "pending_confirm", RequiresConfirmation: true, EstimatedCostUSD: "$2.00"}, nil
}
func (unconfirmedVideo) PollVideoJob(context.Context, GatewayCallContext, string) (*GatewayVideoStatusResponse, error) {
	panic("must not poll unconfirmed job")
}
func (unconfirmedVideo) FetchVideoOutput(context.Context, GatewayCallContext, string) ([]byte, error) {
	panic("must not fetch unconfirmed job")
}
func (unconfirmedVideo) SaveGeneratedVideo(context.Context, GeneratedVideoInput) (*model.ConversationFile, error) {
	panic("must not save unconfirmed job")
}
func TestVideoToolStopsAtPremiumConfirmation(t *testing.T) {
	e := NewToolExecutor(nil)
	e.SetVideoGeneration(unconfirmedVideo{}, unconfirmedVideo{}, "configured-video")
	result := e.execGenerateVideo(context.Background(), `{"prompt":"demo"}`, ToolExecContext{MessageID: uuid.New()})
	if !strings.Contains(result, "NOT been dispatched") || !strings.Contains(result, "$2.00") {
		t.Fatalf("misleading confirmation result: %s", result)
	}
}
