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

// GatewayMessage mirrors OpenAI's chat-completion message shape.
// Content is `any` so it can carry either a plain string (legacy
// path) OR the vision-format content-block array:
//
//	[{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}},
//	 {"type":"text","text":"What's in this image?"}]
//
// The Go layer only fan-outs raw JSON; downstream code that touches
// Content (response decoder, change-log writer) reads it as a string
// via assertion because assistant replies never carry image blocks.
//
// Tool-call fields (S1):
//   - ToolCalls — populated on assistant responses when the model
//     wants to invoke a function. Echoed back unchanged on the
//     follow-up assistant turn so the chain stays auditable.
//   - ToolCallID + Name — set on role="tool" messages we send back
//     to the gateway carrying the result of a tool execution.
type GatewayMessage struct {
	Role       string             `json:"role"`
	Content    any                `json:"content"`
	ToolCalls  []GatewayToolCall  `json:"tool_calls,omitempty"`
	ToolCallID string             `json:"tool_call_id,omitempty"`
	Name       string             `json:"name,omitempty"`
}

// GatewayToolCall mirrors OpenAI's tool_calls[] response shape. The
// arguments field is a JSON-encoded string (NOT a parsed object) —
// the provider returns it verbatim and the chat-edge tool executor
// parses it before dispatch.
type GatewayToolCall struct {
	ID       string                  `json:"id"`
	Type     string                  `json:"type"`
	Function GatewayToolCallFunction `json:"function"`
}

type GatewayToolCallFunction struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

// GatewayTool is one entry in the request's tools[]. Mirrors OpenAI's
// function-calling spec; the backend gateway forwards tools[] +
// tool_choice verbatim to the provider adapter (see
// backend/app/schemas/public.py).
type GatewayTool struct {
	Type     string              `json:"type"`
	Function GatewayToolFunction `json:"function"`
}

type GatewayToolFunction struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}

// GatewayContentBlock is one element of a vision-format content
// array. Used by the message builder when image attachments are
// present on a user turn.
type GatewayContentBlock struct {
	Type     string             `json:"type"`
	Text     string             `json:"text,omitempty"`
	ImageURL *GatewayImageURL   `json:"image_url,omitempty"`
}

type GatewayImageURL struct {
	URL    string `json:"url"`
	Detail string `json:"detail,omitempty"`
}

// GatewayReasoningConfig mirrors the Gateway's public
// ``ReasoningConfig`` (backend/app/schemas/public.py — T1). Provider-
// neutral on the wire; the Gateway adapter translates ``mode`` into
// the provider-native shape (Anthropic ``thinking`` block, OpenAI
// ``reasoning_effort``, Gemini ``thinkingConfig.thinkingLevel``,
// DeepSeek / Groq R1 ``reasoning_content`` parsing).
//
// Mode values: ``off`` / ``auto`` / ``low`` / ``medium`` / ``high``.
// ``ShowSummary`` controls whether reasoning text is streamed back —
// the model still thinks (better answers) either way; only the
// SSE emit / response field is gated.
type GatewayReasoningConfig struct {
	Mode        string `json:"mode"`
	ShowSummary bool   `json:"show_summary"`
}

type GatewayChatRequest struct {
	Model         string           `json:"model"`
	Messages      []GatewayMessage `json:"messages"`
	Stream        bool             `json:"stream"`
	StreamOptions *struct {
		IncludeUsage bool `json:"include_usage"`
	} `json:"stream_options,omitempty"`
	// S1 — tool calling. Omitted when no tools are registered for
	// the conversation (e.g. memory_enabled=false) so we don't pay
	// the provider's tool-routing tax unnecessarily.
	Tools      []GatewayTool `json:"tools,omitempty"`
	ToolChoice any           `json:"tool_choice,omitempty"`
	// S3 — bounded sampling for internal extraction calls
	// (ConversationStateService). Both omitempty so the main chat
	// flow keeps using the gateway / provider defaults — only the
	// extractor sets them today.
	MaxTokens   int     `json:"max_tokens,omitempty"`
	Temperature float64 `json:"temperature,omitempty"`
	// T4 — reasoning / thinking mode. Omitted when the caller (a
	// conversation with reasoning_enabled=false OR a non-reasoning
	// internal call like title generation) didn't opt in. Gateway
	// silently ignores reasoning on non-capable models, so it is
	// safe to forward whenever the user-facing toggle is on.
	Reasoning *GatewayReasoningConfig `json:"reasoning,omitempty"`
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
	// T4 — Gateway surfaces the reasoning-token subdivision under
	// ``usage.reasoning_tokens`` on both the trailing
	// stream_options.include_usage chunk AND the dedicated final
	// frame that fires when reasoning happened. 0 / absent for
	// non-reasoning calls.
	ReasoningTokens int `json:"reasoning_tokens,omitempty"`
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
	// S1 — tool-call deltas. OpenAI ships tool calls in stream as
	// an incremental array of partial entries (each with an index +
	// optional id / function name / arguments-fragment). The
	// MessageService accumulator stitches these into complete
	// GatewayToolCall values before dispatching.
	ToolCallDeltas []StreamToolCallDelta
	// T4 — reasoning / thinking. The Gateway emits two new SSE
	// frame shapes alongside the OpenAI-format chunks:
	//   * ``{"type":"reasoning_summary_delta","text":"..."}``
	//   * ``{"type":"analysis_status","status":"started|completed"}``
	// ReasoningDelta carries the visible reasoning text fragment;
	// AnalysisStatus carries the started/completed phase marker.
	// Each frame yields a StreamEvent with ONLY the relevant field
	// populated — the pump distinguishes by checking which is set.
	ReasoningDelta string
	AnalysisStatus string
}

// StreamToolCallDelta is one fragment from a tool_calls[] stream
// chunk. Index pins the entry across chunks; ID / Name / Arguments
// fields are individually optional and arrive piecemeal.
type StreamToolCallDelta struct {
	Index            int
	ID               string
	Type             string
	FunctionName     string
	FunctionArgsFrag string
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
//
// The delta shape carries either content (plain text) or tool_calls
// (partial function invocation). Both are optional per chunk — most
// content chunks have no tool_calls, and tool-call chunks usually
// have no content. parseStreamChunk reads both and lets the caller
// decide whether to forward as a content delta or accumulate as a
// tool-call delta.
//
// T4 adds two reasoning-channel shapes the Gateway emits alongside
// the OpenAI chunks: ``reasoning_summary_delta`` (carries
// ``text``) and ``analysis_status`` (carries ``status``). Both have
// ``type`` at the top level rather than nesting under ``choices``,
// so we sniff for that key first and short-circuit before falling
// through to the OpenAI-shape decoder.
type streamChunk struct {
	Choices []struct {
		Delta struct {
			Content   string `json:"content"`
			ToolCalls []struct {
				Index    int    `json:"index"`
				ID       string `json:"id,omitempty"`
				Type     string `json:"type,omitempty"`
				Function struct {
					Name      string `json:"name,omitempty"`
					Arguments string `json:"arguments,omitempty"`
				} `json:"function"`
			} `json:"tool_calls,omitempty"`
		} `json:"delta"`
		FinishReason *string `json:"finish_reason"`
	} `json:"choices"`
	Usage *GatewayChatUsage `json:"usage"`
}

// reasoningEnvelope is the minimal shape we need to detect a
// Gateway T2/T3 reasoning frame. ``Type`` is the discriminator.
// We unmarshal everything first into this small struct, then
// re-unmarshal into the typed shape if it's an OpenAI chunk —
// cheaper than a full ``map[string]any`` round-trip and keeps the
// hot path allocation-light.
type reasoningEnvelope struct {
	Type   string `json:"type"`
	Text   string `json:"text"`
	Status string `json:"status"`
	// Usage hint — the Gateway's final SSE frame includes a
	// ``usage`` block when reasoning happened so the chat-edge can
	// surface reasoning_tokens to the message_complete event. The
	// usage shape on this frame is `{reasoning_tokens: N}` only;
	// full prompt/completion counts come on the trailing
	// stream_options.include_usage chunk via the OpenAI-shape
	// branch and are merged at the pump.
	Usage *struct {
		ReasoningTokens int `json:"reasoning_tokens"`
	} `json:"usage,omitempty"`
}

func parseStreamChunk(raw string) (*StreamEvent, error) {
	// Reasoning-channel frames (T2/T3) carry ``type`` at the top
	// level. Sniff first so we never accidentally swallow a
	// reasoning frame into the OpenAI-shape decoder.
	var env reasoningEnvelope
	if err := json.Unmarshal([]byte(raw), &env); err == nil && env.Type != "" {
		switch env.Type {
		case "reasoning_summary_delta":
			return &StreamEvent{ReasoningDelta: env.Text}, nil
		case "analysis_status":
			return &StreamEvent{AnalysisStatus: env.Status}, nil
		// Gateway's final chat.completion.chunk frame ALSO has
		// ``object: "chat.completion.chunk"`` but no top-level
		// ``type`` — it falls through to the OpenAI branch below.
		// Unknown ``type`` values fall through too so future
		// Gateway-side frame additions don't crash the pump.
		}
	}

	var c streamChunk
	if err := json.Unmarshal([]byte(raw), &c); err != nil {
		return nil, err
	}
	ev := &StreamEvent{Usage: c.Usage}
	if len(c.Choices) > 0 {
		choice := c.Choices[0]
		ev.Delta = choice.Delta.Content
		if choice.FinishReason != nil {
			ev.FinishReason = *choice.FinishReason
		}
		if len(choice.Delta.ToolCalls) > 0 {
			ev.ToolCallDeltas = make([]StreamToolCallDelta, 0, len(choice.Delta.ToolCalls))
			for _, tc := range choice.Delta.ToolCalls {
				ev.ToolCallDeltas = append(ev.ToolCallDeltas, StreamToolCallDelta{
					Index:            tc.Index,
					ID:               tc.ID,
					Type:             tc.Type,
					FunctionName:     tc.Function.Name,
					FunctionArgsFrag: tc.Function.Arguments,
				})
			}
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
