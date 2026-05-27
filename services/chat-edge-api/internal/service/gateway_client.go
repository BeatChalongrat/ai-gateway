// HTTP client for the upstream Gateway API.
//
// Authentication preference (highest → lowest):
//  1. “GATEWAY_API_KEY“ env var — sent as Bearer. Used by dev
//     builds (seeded gateway key) and prod deployments alike.
//  2. Service JWT (auth.IssueServiceJWT) — minted fresh per
//     request, 60s TTL. The current gateway-api doesn't recognise
//     this token format; it's the migration target.
//
// Every outbound request carries the per-user / per-request context
// headers so the gateway can attribute costs + audit traces back to
// the chat-edge caller.
package service

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/BeatChalongrat/ai-gateway/chat-edge-api/internal/auth"
)

// GatewayUnavailableError marks "gateway didn't answer / refused
// us" — surfaces as 502 PROVIDER_UNAVAILABLE at the chat-edge
// handler boundary. Other errors map to 500.
type GatewayUnavailableError struct {
	Status int
	Body   string
}

func (e *GatewayUnavailableError) Error() string {
	return fmt.Sprintf("gateway unavailable: status=%d body=%s", e.Status, truncate(e.Body, 200))
}

// GatewayClient is the typed HTTP client used by every chat-edge
// service that needs DLP / LLM access.
type GatewayClient struct {
	BaseURL          string
	APIKey           string // optional; preferred over service JWT
	ServiceJWTSecret string
	ServiceJWTTTL    time.Duration

	http *http.Client
}

func NewGatewayClient(
	baseURL, apiKey, serviceJWTSecret string,
	serviceJWTTTL time.Duration,
) *GatewayClient {
	return &GatewayClient{
		BaseURL:          strings.TrimRight(baseURL, "/"),
		APIKey:           apiKey,
		ServiceJWTSecret: serviceJWTSecret,
		ServiceJWTTTL:    serviceJWTTTL,
		http: &http.Client{
			// Long timeout because the LLM completion path can be
			// slow on cold starts; streaming uses no body timeout
			// at the transport level (set below per request).
			Timeout: 5 * time.Minute,
		},
	}
}

// ---------------------------------------------------------------------------
// Request / response types (OpenAI-style wire format the gateway speaks)
// ---------------------------------------------------------------------------

type GatewayMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type GatewayChatRequest struct {
	Model         string           `json:"model"`
	Messages      []GatewayMessage `json:"messages"`
	Stream        bool             `json:"stream"`
	StreamOptions *struct {
		IncludeUsage bool `json:"include_usage"`
	} `json:"stream_options,omitempty"`
}

// GatewayCallContext carries the per-request identity headers we
// forward so the gateway can attribute usage + audit traces.
type GatewayCallContext struct {
	TenantID       uuid.UUID
	UserID         uuid.UUID
	ConversationID uuid.UUID
	RequestID      string
}

// GatewayChatChoice / GatewayChatUsage / GatewayChatResponse mirror
// the non-streaming gateway response. We only decode the fields we
// use; the rest fall through as unknown JSON keys.
type GatewayChatChoice struct {
	Index   int            `json:"index"`
	Message GatewayMessage `json:"message"`
}

type GatewayChatUsage struct {
	PromptTokens     int     `json:"prompt_tokens"`
	CompletionTokens int     `json:"completion_tokens"`
	TotalTokens      int     `json:"total_tokens"`
	EstimatedCostUSD float64 `json:"estimated_cost_usd"`
}

type GatewayChatResponse struct {
	ID      string              `json:"id"`
	Model   string              `json:"model"`
	Choices []GatewayChatChoice `json:"choices"`
	Usage   *GatewayChatUsage   `json:"usage"`
}

// ---------------------------------------------------------------------------
// Non-streaming completion
// ---------------------------------------------------------------------------

// SendChatCompletion issues a non-streaming chat completion call.
// Returns the parsed response on 2xx; “GatewayUnavailableError“
// on any non-2xx so the handler can map cleanly to 502.
func (g *GatewayClient) SendChatCompletion(
	ctx context.Context,
	callCtx GatewayCallContext,
	req GatewayChatRequest,
) (*GatewayChatResponse, error) {
	req.Stream = false

	resp, err := g.do(ctx, callCtx, req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}

	out := &GatewayChatResponse{}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return nil, fmt.Errorf("gateway: decode response: %w", err)
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// Streaming completion
// ---------------------------------------------------------------------------

// StreamEvent is one parsed SSE event from the gateway. Only the
// fields the chat-edge layer consumes are exposed; “finish_reason“
// helps the message-service decide between status=completed and
// status=cancelled.
type StreamEvent struct {
	Delta        string
	FinishReason string
	Usage        *GatewayChatUsage
	// Done is true on the synthetic ``[DONE]`` sentinel event.
	Done bool
}

// SendChatCompletionStream opens a streaming completion. The
// returned “StreamReader“ yields one “StreamEvent“ per line of
// the gateway's SSE body until the channel closes. Caller MUST
// call “Close“ to release the HTTP connection even on early exit.
func (g *GatewayClient) SendChatCompletionStream(
	ctx context.Context,
	callCtx GatewayCallContext,
	req GatewayChatRequest,
) (*StreamReader, error) {
	req.Stream = true
	// Ask for usage in the trailing chunk so we can persist token
	// counts at message-completion time.
	req.StreamOptions = &struct {
		IncludeUsage bool `json:"include_usage"`
	}{IncludeUsage: true}

	resp, err := g.do(ctx, callCtx, req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		resp.Body.Close()
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}
	return &StreamReader{body: resp.Body, scanner: bufio.NewScanner(resp.Body)}, nil
}

// StreamReader wraps the gateway's SSE body. “Next“ blocks until
// the next event, returns “io.EOF“ when the stream finishes.
type StreamReader struct {
	body    io.ReadCloser
	scanner *bufio.Scanner
}

func (s *StreamReader) Close() error { return s.body.Close() }

// Next parses the next SSE event. Returns “io.EOF“ on stream
// end. Skips empty / comment lines per the SSE spec.
//
// SSE events from the gateway look like:
//
//	data: {"id":"req_...","object":"chat.completion.chunk",...}
//	data: [DONE]
//
// We only care about the JSON-payload lines and the “[DONE]“
// sentinel. Multi-line “data:“ continuations don't occur for the
// gateway's payload format so we treat each “data: …“ as a
// complete event.
func (s *StreamReader) Next() (*StreamEvent, error) {
	for s.scanner.Scan() {
		line := strings.TrimRight(s.scanner.Text(), "\r")
		if line == "" || strings.HasPrefix(line, ":") {
			continue
		}
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		payload := strings.TrimPrefix(line, "data: ")
		if payload == "[DONE]" {
			return &StreamEvent{Done: true}, nil
		}
		ev, err := parseStreamChunk(payload)
		if err != nil {
			// Malformed chunk → skip rather than abort the stream;
			// the gateway sometimes emits keep-alive comments that
			// don't decode as chat-chunk JSON.
			continue
		}
		return ev, nil
	}
	if err := s.scanner.Err(); err != nil {
		return nil, err
	}
	return nil, io.EOF
}

// parseStreamChunk decodes one chunk JSON into a typed StreamEvent.
type streamChunk struct {
	Choices []struct {
		Delta struct {
			Content string `json:"content"`
		} `json:"delta"`
		FinishReason *string `json:"finish_reason"`
	} `json:"choices"`
	Usage *GatewayChatUsage `json:"usage"`
}

func parseStreamChunk(raw string) (*StreamEvent, error) {
	var c streamChunk
	if err := json.Unmarshal([]byte(raw), &c); err != nil {
		return nil, err
	}
	ev := &StreamEvent{Usage: c.Usage}
	if len(c.Choices) > 0 {
		ev.Delta = c.Choices[0].Delta.Content
		if c.Choices[0].FinishReason != nil {
			ev.FinishReason = *c.Choices[0].FinishReason
		}
	}
	return ev, nil
}

// ---------------------------------------------------------------------------
// Internal request plumbing
// ---------------------------------------------------------------------------

// do builds + executes the HTTP request, returning the open
// “*http.Response“ so the caller can stream or fully read the body.
// Status code dispatch happens in the caller (different code paths
// for stream vs non-stream).
func (g *GatewayClient) do(
	ctx context.Context,
	callCtx GatewayCallContext,
	req GatewayChatRequest,
) (*http.Response, error) {
	buf, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("gateway: marshal request: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost,
		g.BaseURL+"/chat/completions",
		bytes.NewReader(buf),
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build request: %w", err)
	}

	bearer, err := g.bearer()
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", "Bearer "+bearer)
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "text/event-stream, application/json")
	// Per-request identity for the gateway's audit / usage
	// attribution. The gateway is free to ignore unknown headers.
	httpReq.Header.Set("X-Tenant-ID", callCtx.TenantID.String())
	httpReq.Header.Set("X-User-ID", callCtx.UserID.String())
	if callCtx.ConversationID != uuid.Nil {
		httpReq.Header.Set("X-Conversation-ID", callCtx.ConversationID.String())
	}
	if callCtx.RequestID != "" {
		httpReq.Header.Set("X-Request-Id", callCtx.RequestID)
	}

	resp, err := g.http.Do(httpReq)
	if err != nil {
		// Network error → wrap as GatewayUnavailableError so the
		// handler maps to 502 instead of generic 500.
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	return resp, nil
}

// bearer picks the credential per the auth preference order. Wraps
// the service-JWT minter so a malformed secret still returns a
// clean error instead of panic-on-empty.
func (g *GatewayClient) bearer() (string, error) {
	if g.APIKey != "" {
		return g.APIKey, nil
	}
	if g.ServiceJWTSecret == "" {
		return "", errors.New("gateway: no credential configured (set GATEWAY_API_KEY or SERVICE_JWT_SECRET)")
	}
	tok, err := auth.IssueServiceJWT(g.ServiceJWTSecret, g.ServiceJWTTTL)
	if err != nil {
		return "", fmt.Errorf("gateway: mint service jwt: %w", err)
	}
	return tok, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
