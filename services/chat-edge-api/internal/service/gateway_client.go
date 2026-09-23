// HTTP client for the upstream Gateway API.
//
// Authentication (FIX 1 / Option B — chat decoupled from gw_ keys):
//  1. Service JWT (auth.IssueServiceJWT) — minted fresh per request, 60s TTL,
//     iss="chat-edge-api" / aud="gateway-api", signed with SERVICE_JWT_SECRET.
//     The gateway validates it and resolves the END USER from the X-User-ID /
//     X-Tenant-ID headers we send (billing that user). This is the credential
//     for the chat path — so revoking/rotating any developer gw_ key never
//     affects the chat UI.
//  2. “GATEWAY_API_KEY“ — LEGACY fallback only when SERVICE_JWT_SECRET is
//     unset (a config error, since it's required).
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
	"log/slog"
	"mime/multipart"
	"net"
	"net/http"
	"net/textproto"
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
	// timedOut (FIX130 C3.2) — chat-edge gave up waiting (streamOpenTimeout)
	// rather than the gateway refusing: Unwrap then says
	// context.DeadlineExceeded, so the stream classifier files it as
	// provider_timeout / timeout like any other deadline.
	timedOut bool
}

func (e *GatewayUnavailableError) Error() string {
	return fmt.Sprintf("gateway unavailable: status=%d body=%s", e.Status, truncate(e.Body, 200))
}

// Unwrap exposes the deadline behind a stream-open timeout; nil otherwise.
func (e *GatewayUnavailableError) Unwrap() error {
	if e.timedOut {
		return context.DeadlineExceeded
	}
	return nil
}

// backendErrorCode (FIX130 C3.2) — the code a gateway error body is known
// by. The backend's own stream guards (bounded_sse: stream_timeout; the
// request hard timeout: upstream_timeout) write {"error":{"type":…}} with
// NO "code", and every reader here took only "code": the frame became
// INTERNAL_ERROR with an English sentence, and the pre-stream 504 became
// PROVIDER_UNAVAILABLE. A timeout of the backend is the catalogue's
// provider_timeout (retryable); any other code-less body is known by its
// type. "" when the body carries neither.
func backendErrorCode(code, typ string) string {
	if code != "" {
		return code
	}
	switch typ {
	case "stream_timeout", "upstream_timeout":
		return CodeProviderTimeout
	}
	return typ
}

// normalize fills Code from Type (backendErrorCode) and marks a timeout
// retryable when the backend did not say.
func (e *StreamProviderError) normalize() {
	code := backendErrorCode(e.Code, e.Type)
	if e.Code == "" && code == CodeProviderTimeout && e.Retryable == nil {
		yes := true
		e.Retryable = &yes
	}
	e.Code = code
}

// Envelope is the gateway's typed error body — {"error":{"code","message",
// "retryable"}} — when the response carried one. The code is what the
// clients localise on; Retryable is nil when the gateway did not say.
// Before FIX47 the handler forwarded only Error() (status + raw body) as
// "gateway unavailable", so a 400 "no model can use tools" reached the
// person as a provider outage with the raw JSON as the message.
type GatewayErrorEnvelope struct {
	Code      string
	Message   string
	Retryable *bool
}

func (e *GatewayUnavailableError) Envelope() (GatewayErrorEnvelope, bool) {
	var body struct {
		Error struct {
			Code      string `json:"code"`
			Type      string `json:"type"`
			Message   string `json:"message"`
			Retryable *bool  `json:"retryable"`
		} `json:"error"`
	}
	if err := json.Unmarshal([]byte(e.Body), &body); err != nil {
		return GatewayErrorEnvelope{}, false
	}
	code := backendErrorCode(body.Error.Code, body.Error.Type)
	if code == "" {
		return GatewayErrorEnvelope{}, false
	}
	retryable := body.Error.Retryable
	if body.Error.Code == "" && code == CodeProviderTimeout && retryable == nil {
		yes := true
		retryable = &yes
	}
	return GatewayErrorEnvelope{Code: code, Message: body.Error.Message, Retryable: retryable}, true
}

// GatewayClient is the typed HTTP client used by every chat-edge
// service that needs DLP / LLM access.
type GatewayClient struct {
	BaseURL          string
	APIKey           string // optional; preferred over service JWT
	ServiceJWTSecret string
	ServiceJWTTTL    time.Duration
	// InternalServiceSecret, when set, is forwarded as
	// ``X-Internal-Service-Secret`` so the gateway trusts our
	// ``X-User-ID`` for per-end-user rate limiting. Empty → not sent.
	InternalServiceSecret string

	http *http.Client
}

// newGatewayTransport builds the pooled transport for the chat-edge →
// gateway hop (Batch A). Previously this client used
// http.DefaultTransport, whose MaxIdleConnsPerHost=2 meant every
// concurrent request past the 2nd paid a fresh TCP(+TLS) handshake and
// its connection was discarded on return — measurable TTFB churn on the
// hottest internal hop. Values mirror the gateway proxy's transport
// (services/gateway/internal/proxy/proxy.go), which is tuned for the
// same SSE-heavy traffic.
func newGatewayTransport() *http.Transport {
	return &http.Transport{
		DialContext: (&net.Dialer{
			Timeout:   5 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		ForceAttemptHTTP2:   true,
		MaxIdleConns:        100,
		MaxIdleConnsPerHost: 100,
		IdleConnTimeout:     90 * time.Second,
		TLSHandshakeTimeout: 10 * time.Second,
		// Time to response HEADERS only — the streamed body is
		// unbounded (an SSE generation may run for many minutes).
		// The gateway sends SSE headers immediately, so a slow
		// first-token wait does not trip this.
		ResponseHeaderTimeout: 120 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
	}
}

func NewGatewayClient(
	baseURL, apiKey, serviceJWTSecret string,
	serviceJWTTTL time.Duration,
	internalServiceSecret string,
) *GatewayClient {
	return &GatewayClient{
		BaseURL:               strings.TrimRight(baseURL, "/"),
		APIKey:                apiKey,
		ServiceJWTSecret:      serviceJWTSecret,
		ServiceJWTTTL:         serviceJWTTTL,
		InternalServiceSecret: internalServiceSecret,
		http: &http.Client{
			Transport: newGatewayTransport(),
			// No whole-request deadline by default: Client.Timeout
			// covers the entire body read, so ANY value here severs
			// streams that outlive it (the old 5-minute value cut
			// long generations mid-SSE). Failure modes are bounded
			// instead by the transport's dial/TLS/response-header
			// timeouts plus per-request context cancellation.
			// Operators who want a hard ceiling for non-stream
			// traffic can set GATEWAY_CLIENT_TIMEOUT (see
			// SetTotalTimeout / config.go).
			Timeout: 0,
		},
	}
}

// SetTotalTimeout applies a whole-request deadline (headers + full
// body). 0 keeps the default unbounded behavior. Streaming callers
// should leave this at 0 — a total deadline severs any SSE generation
// that outlives it.
func (g *GatewayClient) SetTotalTimeout(d time.Duration) {
	g.http.Timeout = d
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
	Role       string            `json:"role"`
	Content    any               `json:"content"`
	ToolCalls  []GatewayToolCall `json:"tool_calls,omitempty"`
	ToolCallID string            `json:"tool_call_id,omitempty"`
	Name       string            `json:"name,omitempty"`
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
	Type     string           `json:"type"`
	Text     string           `json:"text,omitempty"`
	ImageURL *GatewayImageURL `json:"image_url,omitempty"`
}

type GatewayImageURL struct {
	URL    string `json:"url"`
	Detail string `json:"detail,omitempty"`
}

// GatewayReasoningConfig mirrors the Gateway's public
// “ReasoningConfig“ (backend/app/schemas/public.py — T1). Provider-
// neutral on the wire; the Gateway adapter translates “mode“ into
// the provider-native shape (Anthropic “thinking“ block, OpenAI
// “reasoning_effort“, Gemini “thinkingConfig.thinkingLevel“,
// DeepSeek / Groq R1 “reasoning_content“ parsing).
//
// Mode values: “off“ / “auto“ / “low“ / “medium“ / “high“.
// “ShowSummary“ controls whether reasoning text is streamed back —
// the model still thinks (better answers) either way; only the
// SSE emit / response field is gated.
type GatewayReasoningConfig struct {
	Mode        string `json:"mode"`
	ShowSummary bool   `json:"show_summary"`
}

// RoutingHints mirrors the Gateway's public “RoutingHints“
// (backend/app/schemas/public.py). Sent ONLY alongside model="auto":
// chat-edge describes what the turn NEEDS (capabilities + complexity)
// and the gateway's provider-agnostic smart router picks the best model
// from whatever providers currently hold a valid key. This replaces the
// old client-side hardcode (which resolved "auto" -> a concrete model
// and bypassed gateway routing). Every field is omitempty so an explicit
// model pick sends no hints and the gateway's explicit path is unchanged.
type RoutingHints struct {
	NeedsVision    bool `json:"needs_vision,omitempty"`
	NeedsReasoning bool `json:"needs_reasoning,omitempty"`
	// HasTools/ToolCount tell the gateway this turn ships tool
	// definitions, so it must not elect a tools-blind (or budget-tier)
	// model — a model that ignores the tool set answers in prose and
	// the memory/search calls never happen.
	HasTools      bool   `json:"has_tools,omitempty"`
	ToolCount     int    `json:"tool_count,omitempty"`
	HasCode       bool   `json:"has_code,omitempty"`
	HasFiles      bool   `json:"has_files,omitempty"`
	Complexity    string `json:"complexity,omitempty"` // "low" | "medium" | "high"
	ConvTurnCount int    `json:"conv_turn_count,omitempty"`
	Language      string `json:"language,omitempty"` // "th" | "en" | "zh" | ...
	// Tier (FIX35 Part C) — cost ceiling the Auto router may spend up to:
	// "" (tenant / deployment default) | low | medium | high | xhigh | max.
	// Only meaningful with model="auto"; the gateway ignores it otherwise.
	Tier string `json:"tier,omitempty"`
	// MinContextTokens (FIX102) — what this compiled request needs a model
	// to hold: prompt plus the output reserve. HARD: the router never picks
	// a model whose catalogue window is smaller, because that model would
	// refuse it.
	MinContextTokens int `json:"min_context_tokens,omitempty"`
	// PreferredContextTokens (FIX102) — the window this conversation is
	// budgeted against (the model that answered its last turn). SOFT: the
	// router keeps the turn on a model at least that large when the tier
	// ceiling leaves one, so the next turn's budget does not collapse.
	PreferredContextTokens int `json:"preferred_context_tokens,omitempty"`
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
	// Provider-agnostic smart routing. Non-nil ONLY when the user
	// picked "Auto" (Model is then the "auto" sentinel). The gateway
	// reads these to select a concrete model from key-valid providers.
	RoutingHints *RoutingHints `json:"routing_hints,omitempty"`
	// OpenAI Structured-Outputs passthrough (e.g. title generation asks
	// for a json_schema so a small model has nowhere to preamble).
	// Omitted for the main chat flow so existing calls stay identical;
	// forwarded verbatim by the backend openai_adapter when present.
	ResponseFormat any `json:"response_format,omitempty"`
	// Metadata (FIX122) is the gateway's own extension dict. Omitted by
	// every caller but title generation, which uses allowed_providers
	// to keep a failed title call from walking the fallback chain.
	Metadata map[string]any `json:"metadata,omitempty"`
	// NewestMessageOverBudget (FIX105) is never sent. The compiler sets it
	// when the newest message alone is larger than the whole messages
	// budget, so a context_length_exceeded refusal can be named for what it
	// is: this message is too long, not this conversation.
	NewestMessageOverBudget bool `json:"-"`
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
	// Phase 4A.2.2 — internal DLP canonical envelope on managed WebChat
	// non-stream responses (off-schema; public callers never send it).
	// Parsed into a CanonicalArtifact by the consumer; never the display.
	XSranDLP *CanonicalArtifact `json:"x_sran_dlp,omitempty"`
	// PolicyAction (FIX16 §1.5) — the DLP verdict the gateway attaches to a
	// managed completion ("allow"|"mask"|"block"|…). Empty on public/Direct
	// paths. Surfaced so an unattended cloud run's audit row can record what
	// the policy did (DLP runs regardless; this only reports it).
	PolicyAction string `json:"policy_action,omitempty"`
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
	// FIX119 — why the model stopped (the gateway's closed set), the
	// provider's own request id and the gateway's request id, all from
	// the chunk that carried them; "" elsewhere.
	StopReason        string
	ProviderRequestID string
	GatewayRequestID  string
	// FIX35 Part B — the model that is actually answering, as the gateway
	// names it: from a typed ``model_resolved`` frame (a meta-router route
	// such as auto / openrouter/auto, announced before the first content
	// frame) or from the ``model`` field every content chunk carries.
	// Empty on frames that don't name one.
	ServedModel string
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
	// B1 — provider error frame. The Gateway emits
	//   data: {"error":{"code":"...","message":"...","details":{...},"attempts":[...]}}
	// when a stream fails terminally (pre-stream or mid-stream). The
	// pump must FORWARD this to the frontend, never swallow it — a
	// swallowed error frame is the blank-screen bug.
	ProviderError *StreamProviderError
	// B2 — fallback notice frame. The Gateway emits
	//   data: {"type":"fallback_info","fallback_info":{...}}
	// before the answering model's content when a fallback rescued
	// the request, so the client can show which model actually replied.
	FallbackInfo map[string]any
	// FIX50 P1-C — input-masking notice. The Gateway emits
	//   data: {"type":"dlp_notice","dlp_notice":{"direction":"input","findings":N,"by_detector":{...}}}
	// before any content when DLP transformed the question. Counts
	// only, never values — the user learns their question was altered,
	// not what was hidden.
	DlpNotice map[string]any
	// Reasoning downgrade notice. The Gateway emits
	//   data: {"type":"reasoning_notice","model":"...","reasoning_downgraded":true,"reason":"..."}
	// as its FIRST frame when reasoning was requested but the resolved
	// model can't reason — the answer arrives without thinking. Pure
	// information (not an error): forwarded so the clients can tell the
	// user instead of silently serving less than they asked for.
	ReasoningNotice *ReasoningNotice
	// Phase 4A.2.2 — internal DLP canonical artifact frames, carried on
	// named SSE events (``event: dlp.canonical_*``). These are consumed
	// by the chat-edge canonical state machine and are NEVER forwarded to
	// the browser, appended to display content, or counted as tokens.
	CanonicalCapability  *CanonicalCapabilityFrame
	CanonicalFinal       *CanonicalArtifact
	CanonicalUnavailable *CanonicalUnavailableFrame
}

// ReasoningNotice is the payload of a Gateway “reasoning_notice“
// frame: the request asked for reasoning but routing/policy resolved
// to a model that can't reason. Model is the model that answered;
// Reason is the Gateway's human-readable explanation (English — the
// clients render their own localized line and may show this as
// detail).
type ReasoningNotice struct {
	Model  string `json:"model,omitempty"`
	Reason string `json:"reason,omitempty"`
}

// StreamProviderError is the payload of a Gateway “error“ SSE frame
// (B1). “Attempts“ is populated on the “all_models_failed“ case so
// the client can show which models were tried and why each failed.
type StreamProviderError struct {
	Code string `json:"code"`
	// Type is the backend's own discriminator on its code-less frames
	// (FIX130: bounded_sse writes {"error":{"type":"stream_timeout"}}).
	// parseStreamChunk folds it into Code; nothing downstream reads it.
	Type     string         `json:"type,omitempty"`
	Message  string         `json:"message"`
	Details  map[string]any `json:"details,omitempty"`
	Attempts []any          `json:"attempts,omitempty"`
	// Retryable is the gateway's verdict on whether the same request can
	// succeed again (app.core.error_catalog); nil when it did not say.
	Retryable *bool `json:"retryable,omitempty"`
	// FIX119 — the ids the backend put on the frame: its own request id
	// and the turn id it was handed. Forwarded on the terminal error's
	// details so the person's card and the logs name the same thing.
	RequestID string `json:"request_id,omitempty"`
	TurnID    string `json:"turn_id,omitempty"`
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

	// FIX130 C3.2 — the call gets streamOpenTimeout to answer (headers),
	// not the transport's 120 s per hop: a turn whose gateway never
	// answers now fails with provider_timeout at 90 s instead of spinning
	// (NIGHT2 F2: 241 s). The deadline covers the headers only — the timer
	// is stopped once they arrive, and the context lives on with the body
	// until StreamReader.Close.
	octx, ocancel := context.WithCancel(ctx)
	timer := time.AfterFunc(streamOpenTimeout, ocancel)
	resp, err := g.do(octx, callCtx, req)
	if !timer.Stop() {
		ocancel()
		if resp != nil {
			resp.Body.Close()
		}
		slog.Warn("gateway_stream_open_timeout", "run_id", callCtx.RequestID,
			"conversation_id", callCtx.ConversationID.String(), "after_ms", streamOpenTimeout.Milliseconds())
		return nil, streamOpenTimeoutError()
	}
	if err != nil {
		ocancel()
		return nil, err
	}
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		resp.Body.Close()
		ocancel()
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}
	// FIX119 — the backend answers with its own request id (the
	// request_logs key). One log line joins it to the run id we sent, so
	// a grep for either finds the other. The backend can carry TWO
	// X-Request-Id values (its middleware echoes ours as well); the
	// backend's own starts with "req_".
	gatewayRequestID := ""
	for _, v := range resp.Header.Values("X-Request-Id") {
		if strings.HasPrefix(v, "req_") {
			gatewayRequestID = v
			break
		}
	}
	slog.Info("gateway_stream_opened",
		"run_id", callCtx.RequestID, "conversation_id", callCtx.ConversationID.String(),
		"gateway_request_id", gatewayRequestID, "gateway_provider", resp.Header.Get("X-Gateway-Provider"),
		"gateway_model", resp.Header.Get("X-Gateway-Model"))
	return &StreamReader{body: resp.Body, scanner: bufio.NewScanner(resp.Body), GatewayRequestID: gatewayRequestID, cancel: ocancel}, nil
}

// streamOpenTimeout (FIX130 C3.2) is how long a streaming completion may
// wait for the gateway's response headers. The gateway answers with SSE
// headers as soon as it has routed the request (a slow first token is
// AFTER the headers and does not count); its own pre-stream bound is
// 120 s. A var so tests can shorten it; the web client's matching guard
// (STREAM_OPEN.sendMs) sits just above it.
var streamOpenTimeout = 90 * time.Second

// streamOpenTimeoutError is the gateway-shaped error for a call that got
// no answer in time: a 504 carrying the catalogue's provider_timeout,
// so every pre-stream path (send, regenerate, edit, the early-open frame)
// answers with the code the clients localise, and unwrapping to
// context.DeadlineExceeded for the in-stream classifier.
func streamOpenTimeoutError() *GatewayUnavailableError {
	return &GatewayUnavailableError{
		Status:   http.StatusGatewayTimeout,
		Body:     `{"error":{"code":"provider_timeout","message":"The AI service did not answer in time. Please try again.","retryable":true}}`,
		timedOut: true,
	}
}

// StreamReader wraps the gateway's SSE body. “Next“ blocks until
// the next event, returns “io.EOF“ when the stream finishes.
type StreamReader struct {
	body    io.ReadCloser
	scanner *bufio.Scanner
	// cancel releases the request context (FIX130); nil in tests that
	// build a reader by hand.
	cancel context.CancelFunc
	// GatewayRequestID (FIX119) — the backend's own id for the call this
	// stream answers ("" when the response carried none).
	GatewayRequestID string
}

func (s *StreamReader) Close() error {
	if s.cancel != nil {
		defer s.cancel()
	}
	return s.body.Close()
}

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
	// Track the most recent ``event:`` name so the following ``data:``
	// line can be dispatched to the canonical consumer. A blank line ends
	// an SSE record and resets the pending event name.
	eventName := ""
	for s.scanner.Scan() {
		line := strings.TrimRight(s.scanner.Text(), "\r")
		if line == "" {
			eventName = ""
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		if strings.HasPrefix(line, "event: ") {
			eventName = strings.TrimPrefix(line, "event: ")
			continue
		}
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		payload := strings.TrimPrefix(line, "data: ")
		if payload == "[DONE]" {
			return &StreamEvent{Done: true}, nil
		}
		ev, err := parseStreamChunk(payload, eventName)
		eventName = ""
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
// the OpenAI chunks: “reasoning_summary_delta“ (carries
// “text“) and “analysis_status“ (carries “status“). Both have
// “type“ at the top level rather than nesting under “choices“,
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
		// FIX119 — the gateway's closed-set stop reason, beside the
		// OpenAI-shaped finish_reason, on the final chunk.
		StopReason *string `json:"stop_reason"`
	} `json:"choices"`
	Usage *GatewayChatUsage `json:"usage"`
	// FIX119 — the gateway's own request id (its request_logs key) rides
	// every chunk as the OpenAI "id"; the provider's request id rides the
	// final chunk when the provider sent one.
	ID                string `json:"id,omitempty"`
	ProviderRequestID string `json:"provider_request_id,omitempty"`
	// FIX35 Part B — every chunk names the model that is actually
	// answering (the gateway resolves a meta-router id to the provider-
	// reported model before the first content frame).
	Model string `json:"model,omitempty"`
}

// reasoningEnvelope is the minimal shape we need to detect a
// Gateway T2/T3 reasoning frame. “Type“ is the discriminator.
// We unmarshal everything first into this small struct, then
// re-unmarshal into the typed shape if it's an OpenAI chunk —
// cheaper than a full “map[string]any“ round-trip and keeps the
// hot path allocation-light.
type reasoningEnvelope struct {
	Type   string `json:"type"`
	Text   string `json:"text"`
	Status string `json:"status"`
	// reasoning_notice fields — Model/Reason are only read when Type
	// is "reasoning_notice" (the terminal chunk also carries "model"
	// but no top-level "type", so it never reaches that branch).
	Model  string `json:"model"`
	Reason string `json:"reason"`
	// B1 / B2 — error + fallback frames carried on the same top-level
	// envelope so we sniff them before the OpenAI-shape decoder.
	Error        *StreamProviderError `json:"error"`
	FallbackInfo map[string]any       `json:"fallback_info"`
	DlpNotice    map[string]any       `json:"dlp_notice"`
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

func parseStreamChunk(raw string, eventName string) (*StreamEvent, error) {
	// Phase 4A.2.2 — internal DLP canonical events are carried on named
	// SSE events. Parse them into typed canonical fields so the consumer
	// state machine sees them; a malformed canonical payload yields an
	// empty (non-forwarded, non-content) event rather than aborting.
	switch eventName {
	case "dlp.canonical_capability":
		var f CanonicalCapabilityFrame
		if json.Unmarshal([]byte(raw), &f) == nil {
			return &StreamEvent{CanonicalCapability: &f}, nil
		}
		return &StreamEvent{}, nil
	case "dlp.canonical_final":
		var a CanonicalArtifact
		if json.Unmarshal([]byte(raw), &a) == nil {
			return &StreamEvent{CanonicalFinal: &a}, nil
		}
		return &StreamEvent{}, nil
	case "dlp.canonical_unavailable":
		var u CanonicalUnavailableFrame
		if json.Unmarshal([]byte(raw), &u) == nil {
			return &StreamEvent{CanonicalUnavailable: &u}, nil
		}
		return &StreamEvent{}, nil
	}

	// Reasoning-channel frames (T2/T3) carry ``type`` at the top
	// level. Sniff first so we never accidentally swallow a
	// reasoning frame into the OpenAI-shape decoder.
	var env reasoningEnvelope
	if err := json.Unmarshal([]byte(raw), &env); err == nil {
		// B1: provider error frame — forward as a typed error event so
		// the handler surfaces it instead of the client hanging on a
		// truncated stream.
		if env.Error != nil {
			env.Error.normalize() // FIX130 — a code-less frame gets its code
			return &StreamEvent{ProviderError: env.Error}, nil
		}
		// B2: fallback notice frame.
		if env.Type == "fallback_info" {
			return &StreamEvent{FallbackInfo: env.FallbackInfo}, nil
		}
		// FIX50 P1-C — input-masking notice.
		if env.Type == "dlp_notice" {
			return &StreamEvent{DlpNotice: env.DlpNotice}, nil
		}
		switch env.Type {
		case "reasoning_summary_delta":
			return &StreamEvent{ReasoningDelta: env.Text}, nil
		case "analysis_status":
			return &StreamEvent{AnalysisStatus: env.Status}, nil
		case "reasoning_notice":
			return &StreamEvent{ReasoningNotice: &ReasoningNotice{
				Model:  env.Model,
				Reason: env.Reason,
			}}, nil
		case "model_resolved":
			// FIX35 Part B — the gateway's router picked ``model`` for
			// this turn (auto / openrouter/auto routes); announced before
			// the first content frame.
			return &StreamEvent{ServedModel: env.Model}, nil
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
	ev := &StreamEvent{Usage: c.Usage, ServedModel: c.Model, ProviderRequestID: c.ProviderRequestID, GatewayRequestID: c.ID}
	if len(c.Choices) > 0 {
		choice := c.Choices[0]
		ev.Delta = choice.Delta.Content
		if choice.FinishReason != nil {
			ev.FinishReason = *choice.FinishReason
		}
		if choice.StopReason != nil {
			ev.StopReason = *choice.StopReason
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
	// Prove we're a trusted internal caller so the gateway keys its rate
	// limiter per END-USER (X-User-ID) rather than per shared API key.
	if g.InternalServiceSecret != "" {
		httpReq.Header.Set("X-Internal-Service-Secret", g.InternalServiceSecret)
	}
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

// bearer picks the credential for a gateway call.
//
// FIX 1 / Option B — the chat path authenticates as a trusted internal
// SERVICE (a short-lived, audience-bound service JWT), NOT a user-facing
// “gw_“ API key. The gateway validates the service JWT and bills the end
// user from the “X-User-ID“ / “X-Tenant-ID“ headers we already send. This
// DECOUPLES chat from any “gw_“ key: rotating/revoking a developer key can
// never break the chat UI. “SERVICE_JWT_SECRET“ is required, so this branch
// is always taken; “GATEWAY_API_KEY“ is a legacy fallback only for the
// (config-invalid) case where no service secret is set.
func (g *GatewayClient) bearer() (string, error) {
	if g.ServiceJWTSecret != "" {
		tok, err := auth.IssueServiceJWT(g.ServiceJWTSecret, g.ServiceJWTTTL)
		if err != nil {
			return "", fmt.Errorf("gateway: mint service jwt: %w", err)
		}
		return tok, nil
	}
	// Legacy fallback: only when SERVICE_JWT_SECRET is unset (should not
	// happen — it's required). A static gateway API key.
	if g.APIKey != "" {
		return g.APIKey, nil
	}
	return "", errors.New("gateway: no credential configured (set SERVICE_JWT_SECRET)")
}

// ---------------------------------------------------------------------------
// Image generation (Phase B) — POST /images/generations
// ---------------------------------------------------------------------------

// GatewayImagesRequest mirrors the gateway's public ImageGenerationRequest
// (backend/app/schemas/public.py). Only the fields chat-edge sets are
// included; the gateway applies defaults for the rest.
type GatewayImagesRequest struct {
	Model   string `json:"model"`
	Prompt  string `json:"prompt"`
	N       int    `json:"n,omitempty"`
	Size    string `json:"size,omitempty"`
	Quality string `json:"quality,omitempty"`
}

// GatewayImageDatum is one entry in the images response. gpt-image-1
// returns B64JSON (no URL); DALL-E returns a URL. RevisedPrompt is
// dall-e-3 only.
type GatewayImageDatum struct {
	URL           string `json:"url,omitempty"`
	B64JSON       string `json:"b64_json,omitempty"`
	RevisedPrompt string `json:"revised_prompt,omitempty"`
}

type GatewayImagesResponse struct {
	Created  int                 `json:"created"`
	Model    string              `json:"model"`
	Provider string              `json:"provider"`
	Data     []GatewayImageDatum `json:"data"`
	Usage    *struct {
		CostUSD float64 `json:"cost_usd"`
	} `json:"usage"`
}

// GenerateImages issues a non-streaming image-generation call to the
// gateway's /images/generations endpoint. Mirrors do()'s auth + audit
// headers but targets a different path (do() is hardwired to
// /chat/completions). Returns GatewayUnavailableError on any non-2xx so
// the caller maps cleanly.
func (g *GatewayClient) GenerateImages(
	ctx context.Context,
	callCtx GatewayCallContext,
	req GatewayImagesRequest,
) (*GatewayImagesResponse, error) {
	buf, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("gateway: marshal image request: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost,
		g.BaseURL+"/images/generations",
		bytes.NewReader(buf),
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build image request: %w", err)
	}

	bearer, err := g.bearer()
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", "Bearer "+bearer)
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")
	httpReq.Header.Set("X-Tenant-ID", callCtx.TenantID.String())
	httpReq.Header.Set("X-User-ID", callCtx.UserID.String())
	// Prove we're a trusted internal caller so the gateway keys its rate
	// limiter per END-USER (X-User-ID) rather than per shared API key.
	if g.InternalServiceSecret != "" {
		httpReq.Header.Set("X-Internal-Service-Secret", g.InternalServiceSecret)
	}
	if callCtx.ConversationID != uuid.Nil {
		httpReq.Header.Set("X-Conversation-ID", callCtx.ConversationID.String())
	}
	if callCtx.RequestID != "" {
		httpReq.Header.Set("X-Request-Id", callCtx.RequestID)
	}

	start := time.Now()
	slog.Info("chat_edge_to_gateway_started",
		"request_id", callCtx.RequestID,
		"conversation_id", callCtx.ConversationID.String(),
		"model", req.Model,
		"provider", "gateway",
		"endpoint", "/images/generations",
	)
	resp, err := g.http.Do(httpReq)
	latencyMs := time.Since(start).Milliseconds()
	if err != nil {
		slog.Error("gateway_image_request_failed",
			"request_id", callCtx.RequestID,
			"conversation_id", callCtx.ConversationID.String(),
			"model", req.Model,
			"endpoint", "/images/generations",
			"latency_ms", latencyMs,
			"elapsed_ms", latencyMs,
			"error_type", "transport",
			"error_message", truncate(err.Error(), 200),
		)
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		slog.Error("gateway_image_request_failed",
			"request_id", callCtx.RequestID,
			"conversation_id", callCtx.ConversationID.String(),
			"model", req.Model,
			"endpoint", "/images/generations",
			"status_code", resp.StatusCode,
			"latency_ms", latencyMs,
			"elapsed_ms", latencyMs,
			"error_type", "http_status",
		)
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}
	slog.Info("gateway_image_request_succeeded",
		"request_id", callCtx.RequestID,
		"conversation_id", callCtx.ConversationID.String(),
		"model", req.Model,
		"endpoint", "/images/generations",
		"status_code", resp.StatusCode,
		"latency_ms", latencyMs,
	)
	slog.Info("gateway_image_response_received",
		"request_id", callCtx.RequestID,
		"conversation_id", callCtx.ConversationID.String(),
		"model", req.Model,
		"provider", "gateway",
		"endpoint", "/images/generations",
		"status_code", resp.StatusCode,
		"elapsed_ms", latencyMs,
	)

	out := &GatewayImagesResponse{}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return nil, fmt.Errorf("gateway: decode image response: %w", err)
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// Async video generation (backend /internal/v1/jobs/*)
// ---------------------------------------------------------------------------

// rootURL strips the trailing “/v1“ from BaseURL so we can reach the
// backend's ROOT-level internal routes (“/internal/v1/jobs/...“), which
// are NOT under the public “/v1“ prefix.
func (g *GatewayClient) rootURL() string {
	return strings.TrimSuffix(g.BaseURL, "/v1")
}

type GatewayVideoSubmitRequest struct {
	Model          string `json:"model"`
	Prompt         string `json:"prompt"`
	DurationSec    int    `json:"duration_sec"`
	ConversationID string `json:"conversation_id,omitempty"`
	MessageID      string `json:"message_id,omitempty"`
	IdempotencyKey string `json:"idempotency_key,omitempty"`
}

type GatewayVideoSubmitResponse struct {
	JobID                string `json:"job_id"`
	Status               string `json:"status"`
	EstimatedCostUSD     string `json:"estimated_cost_usd"`
	RequiresConfirmation bool   `json:"requires_confirmation"`
}

type GatewayVideoStatusResponse struct {
	JobID        string `json:"job_id"`
	Status       string `json:"status"`
	ProgressPct  *int   `json:"progress_pct"`
	OutputURL    string `json:"output_url"`
	ErrorCode    string `json:"error_code"`
	ErrorMessage string `json:"error_message"`
}

func (g *GatewayClient) internalHeaders(httpReq *http.Request, callCtx GatewayCallContext) error {
	bearer, err := g.bearer()
	if err != nil {
		return err
	}
	httpReq.Header.Set("Authorization", "Bearer "+bearer)
	httpReq.Header.Set("Accept", "application/json")
	httpReq.Header.Set("X-Tenant-ID", callCtx.TenantID.String())
	httpReq.Header.Set("X-User-ID", callCtx.UserID.String())
	if g.InternalServiceSecret != "" {
		httpReq.Header.Set("X-Internal-Service-Secret", g.InternalServiceSecret)
	}
	if callCtx.ConversationID != uuid.Nil {
		httpReq.Header.Set("X-Conversation-ID", callCtx.ConversationID.String())
	}
	if callCtx.RequestID != "" {
		httpReq.Header.Set("X-Request-Id", callCtx.RequestID)
	}
	return nil
}

// ProxyPublicMedia forwards a direct-dispatch media request from web-chat to
// the gateway's PUBLIC media route as a TRUSTED internal caller carrying the
// end-user's identity (the same header set as SubmitVideoJob / the tool
// path). “subpath“ is BaseURL-relative, e.g. "/videos/generations" →
// {BaseURL}/videos/generations. Returns the upstream status + raw body so
// the caller can relay the gateway's response (and error envelope) verbatim.
func (g *GatewayClient) ProxyPublicMedia(
	ctx context.Context,
	callCtx GatewayCallContext,
	method, subpath string,
	body []byte,
) (int, []byte, error) {
	var rdr io.Reader
	if len(body) > 0 {
		rdr = bytes.NewReader(body)
	}
	httpReq, err := http.NewRequestWithContext(ctx, method, g.BaseURL+subpath, rdr)
	if err != nil {
		return 0, nil, fmt.Errorf("gateway: build media proxy request: %w", err)
	}
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return 0, nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := g.http.Do(httpReq)
	if err != nil {
		return 0, nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 8*1024*1024))
	if err != nil {
		return 0, nil, fmt.Errorf("gateway: read media proxy response: %w", err)
	}
	return resp.StatusCode, respBody, nil
}

// ProxyPublicMediaOutput opens a streaming GET against the gateway's PUBLIC
// media route (“/jobs/{id}/output“) as a trusted internal caller carrying
// the end-user's identity. Unlike ProxyPublicMedia this does NOT buffer the
// body — finished artifacts are videos/audio that can run to tens of MB —
// the caller relays resp.Body and MUST close it.
func (g *GatewayClient) ProxyPublicMediaOutput(
	ctx context.Context,
	callCtx GatewayCallContext,
	subpath string,
) (*http.Response, error) {
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, g.BaseURL+subpath, nil)
	if err != nil {
		return nil, fmt.Errorf("gateway: build media output request: %w", err)
	}
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return nil, err
	}
	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	return resp, nil
}

// SubmitVideoJob enqueues an async video-generation job on the backend and
// returns the job id. The provider runs out-of-band (job loop); poll with
// PollVideoJob until terminal.
func (g *GatewayClient) SubmitVideoJob(
	ctx context.Context, callCtx GatewayCallContext, req GatewayVideoSubmitRequest,
) (*GatewayVideoSubmitResponse, error) {
	buf, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("gateway: marshal video submit: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost, g.rootURL()+"/internal/v1/jobs/video", bytes.NewReader(buf),
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build video submit: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return nil, err
	}
	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}
	out := &GatewayVideoSubmitResponse{}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return nil, fmt.Errorf("gateway: decode video submit: %w", err)
	}
	return out, nil
}

// PollVideoJob reads the current status of a submitted job.
func (g *GatewayClient) PollVideoJob(
	ctx context.Context, callCtx GatewayCallContext, jobID string,
) (*GatewayVideoStatusResponse, error) {
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodGet, g.rootURL()+"/internal/v1/jobs/"+jobID, nil,
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build video poll: %w", err)
	}
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return nil, err
	}
	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}
	out := &GatewayVideoStatusResponse{}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return nil, fmt.Errorf("gateway: decode video poll: %w", err)
	}
	return out, nil
}

// FetchVideoOutput downloads the finished artifact bytes from the backend
// output endpoint (the backend stores them; chat-edge persists the
// ConversationFile on its own side).
func (g *GatewayClient) FetchVideoOutput(
	ctx context.Context, callCtx GatewayCallContext, jobID string,
) ([]byte, error) {
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodGet, g.rootURL()+"/internal/v1/jobs/"+jobID+"/output", nil,
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build video output: %w", err)
	}
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return nil, err
	}
	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}
	// Cap the read at 200 MiB (the chat-edge per-file ceiling) so a runaway
	// response can't exhaust memory.
	return io.ReadAll(io.LimitReader(resp.Body, 200*1024*1024))
}

// GatewayModelInfo is one entry from the Gateway's GET /v1/models.
// The Gateway returns a minimal shape (id + provider, plus an optional
// reasoning block for reasoning-capable models); chat-edge enriches it
// with display metadata before serving to the frontend.
type GatewayModelInfo struct {
	ID           string                      `json:"id"`
	Provider     string                      `json:"provider"`
	Reasoning    *GatewayReasoningCapability `json:"reasoning,omitempty"`
	Capabilities *GatewayModelCapabilities   `json:"capabilities,omitempty"`
	// Display data from model_configs (models-catalog step 1). Zero
	// means "gateway didn't send it" — the Gateway omits unknown
	// values, so chat-edge falls back to its curated metadata.
	ContextWindow    int     `json:"context_window,omitempty"`
	InputPricePer1M  float64 `json:"input_price_per_1m,omitempty"`
	OutputPricePer1M float64 `json:"output_price_per_1m,omitempty"`
	// CostTier (FIX35 Part C) — the gateway's price band for the picker
	// badge and the Auto router's tier ceiling: low | medium | high | xhigh | max.
	CostTier string `json:"cost_tier,omitempty"`
	// ShowInChat (FIX47 Part C) — the catalogue's second axis: offered in
	// the chat picker. nil (older gateway) means shown.
	ShowInChat *bool `json:"show_in_chat,omitempty"`
	// SelfHosted (FIX65) — the operator's own box; the picker groups it
	// apart from the curated vendor list. Absent means vendor.
	SelfHosted bool `json:"self_hosted,omitempty"`
}

// GatewayModelCapabilities mirrors the Gateway's ModelCapabilities block
// (Phase A). Only TRUE flags are sent, so a false bool means "absent".
// image_generation marks a DEDICATED image model (gpt-image-1) — not a
// chat model; image_generation_via_tools marks a chat model that can emit
// images by calling the generate_image tool.
type GatewayModelCapabilities struct {
	Streaming               bool `json:"streaming"`
	Vision                  bool `json:"vision"`
	Tools                   bool `json:"tools"`
	Reasoning               bool `json:"reasoning"`
	ImageGeneration         bool `json:"image_generation"`
	ImageGenerationViaTools bool `json:"image_generation_via_tools"`
	// VideoGeneration marks a DEDICATED video model (model_kind=video);
	// VideoGenerationViaTools marks a chat model that can call generate_video
	// (the 🎬 composer button). Kept distinct so model_kind disambiguates.
	VideoGeneration         bool `json:"video_generation"`
	VideoGenerationViaTools bool `json:"video_generation_via_tools"`
	// Sprint 2A.5 — dedicated media-model capabilities (music / TTS).
	AudioGeneration bool `json:"audio_generation"`
	Tts             bool `json:"tts"`
}

// GatewayReasoningCapability mirrors the Gateway's ReasoningCapability
// block. Present only on models the Gateway flags supports_reasoning.
type GatewayReasoningCapability struct {
	Supported        bool     `json:"supported"`
	Modes            []string `json:"modes"`
	DefaultMode      string   `json:"default_mode"`
	SummarySupported bool     `json:"summary_supported"`
}

// ListModels fetches the Gateway's active model catalog (GET
// /v1/models). This is the dynamic source of truth for the chat model
// selector: models the operator discovers (by adding a provider key)
// appear here without a chat-edge redeploy. Callers should fall back to
// the static config catalog on error.
func (g *GatewayClient) ListModels(ctx context.Context) ([]GatewayModelInfo, error) {
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodGet, g.BaseURL+"/models", nil,
	)
	if err != nil {
		return nil, fmt.Errorf("gateway models: build request: %w", err)
	}
	bearer, err := g.bearer()
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", "Bearer "+bearer)
	httpReq.Header.Set("Accept", "application/json")

	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("gateway models: status %d", resp.StatusCode)
	}
	var out struct {
		Data []GatewayModelInfo `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("gateway models: decode: %w", err)
	}
	return out.Data, nil
}

// ---------------------------------------------------------------------------
// Web search (gateway /v1/search)
// ---------------------------------------------------------------------------

type GatewaySearchRequest struct {
	Query      string `json:"query"`
	MaxResults int    `json:"max_results,omitempty"`
}

// GatewaySearchResult is one normalized hit. Mirrors the gateway's
// SearchResult.to_dict() shape (title/url/snippet/domain/published_date).
type GatewaySearchResult struct {
	Title         string `json:"title"`
	URL           string `json:"url"`
	Snippet       string `json:"snippet"`
	Domain        string `json:"domain"`
	PublishedDate string `json:"published_date,omitempty"`
}

type GatewaySearchResponse struct {
	Query     string                `json:"query"`
	Provider  string                `json:"provider"`
	LatencyMs int                   `json:"latency_ms"`
	Results   []GatewaySearchResult `json:"results"`
}

// GatewaySearchConfig mirrors GET /v1/search/config: whether the
// caller's tenant should be offered the web_search tool.
type GatewaySearchConfig struct {
	Enabled       bool `json:"enabled"`
	SearchEnabled bool `json:"search_enabled"`
	ProviderCount int  `json:"provider_count"`
}

// Search issues a non-streaming web-search call to the gateway's
// /search endpoint. Same auth + audit headers as GenerateImages so the
// gateway bills the trusted end-user. Returns GatewayUnavailableError
// on any non-2xx (carrying the status + body so the tool can map
// "not enabled" / "insufficient_credits" to a graceful model message).
func (g *GatewayClient) Search(
	ctx context.Context,
	callCtx GatewayCallContext,
	req GatewaySearchRequest,
) (*GatewaySearchResponse, error) {
	buf, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("gateway: marshal search request: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost, g.BaseURL+"/search", bytes.NewReader(buf),
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build search request: %w", err)
	}
	bearer, err := g.bearer()
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", "Bearer "+bearer)
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")
	httpReq.Header.Set("X-Tenant-ID", callCtx.TenantID.String())
	httpReq.Header.Set("X-User-ID", callCtx.UserID.String())
	if g.InternalServiceSecret != "" {
		httpReq.Header.Set("X-Internal-Service-Secret", g.InternalServiceSecret)
	}
	if callCtx.ConversationID != uuid.Nil {
		httpReq.Header.Set("X-Conversation-ID", callCtx.ConversationID.String())
	}
	if callCtx.RequestID != "" {
		httpReq.Header.Set("X-Request-Id", callCtx.RequestID)
	}

	start := time.Now()
	resp, err := g.http.Do(httpReq)
	latencyMs := time.Since(start).Milliseconds()
	if err != nil {
		slog.Error("gateway_search_request_failed",
			"request_id", callCtx.RequestID,
			"conversation_id", callCtx.ConversationID.String(),
			"latency_ms", latencyMs,
			"error_type", "transport",
			"error_message", truncate(err.Error(), 200),
		)
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		slog.Warn("gateway_search_request_failed",
			"request_id", callCtx.RequestID,
			"conversation_id", callCtx.ConversationID.String(),
			"status_code", resp.StatusCode,
			"latency_ms", latencyMs,
		)
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}
	out := &GatewaySearchResponse{}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return nil, fmt.Errorf("gateway: decode search response: %w", err)
	}
	slog.Info("gateway_search_request_succeeded",
		"request_id", callCtx.RequestID,
		"conversation_id", callCtx.ConversationID.String(),
		"provider", out.Provider,
		"results_count", len(out.Results),
		"latency_ms", latencyMs,
	)
	return out, nil
}

// SearchConfig fetches GET /v1/search/config for the caller's tenant.
// chat-edge uses this to decide whether to offer the web_search tool.
func (g *GatewayClient) SearchConfig(
	ctx context.Context, callCtx GatewayCallContext,
) (*GatewaySearchConfig, error) {
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodGet, g.BaseURL+"/search/config", nil,
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build search-config request: %w", err)
	}
	bearer, err := g.bearer()
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", "Bearer "+bearer)
	httpReq.Header.Set("Accept", "application/json")
	httpReq.Header.Set("X-Tenant-ID", callCtx.TenantID.String())
	httpReq.Header.Set("X-User-ID", callCtx.UserID.String())
	if g.InternalServiceSecret != "" {
		httpReq.Header.Set("X-Internal-Service-Secret", g.InternalServiceSecret)
	}
	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("gateway search-config: status %d", resp.StatusCode)
	}
	out := &GatewaySearchConfig{}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return nil, fmt.Errorf("gateway: decode search-config: %w", err)
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// Embeddings (gateway /v1/embeddings) — FIX 2
//
// chat-edge no longer calls OpenAI /v1/embeddings directly (that bypassed
// DLP). The gateway fronts it behind the raw-egress gate
// (scan_embedding_text: credential-block + per-tenant PII + audit), then
// calls OpenAI. Same trusted-internal auth + identity headers as the media
// proxy so the gateway attributes/audits the egress to the end user.
// ---------------------------------------------------------------------------

// GenerateEmbedding requests a single embedding vector from the gateway.
// The text egresses RAW by design (a masked embedding is useless) but the
// gateway's DLP gate blocks the credential/secret class and honors the
// tenant's kb_pii_egress policy — a 403 (raw_egress_blocked) surfaces as a
// GatewayUnavailableError the indexer records on the document.
func (g *GatewayClient) GenerateEmbedding(
	ctx context.Context, callCtx GatewayCallContext, text string,
) ([]float32, error) {
	buf, err := json.Marshal(map[string]any{"input": text})
	if err != nil {
		return nil, fmt.Errorf("gateway: marshal embedding request: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost, g.BaseURL+"/embeddings", bytes.NewReader(buf),
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build embedding request: %w", err)
	}
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(body)}
	}
	var out struct {
		Data []struct {
			Embedding []float32 `json:"embedding"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("gateway: decode embedding response: %w", err)
	}
	if len(out.Data) == 0 || len(out.Data[0].Embedding) == 0 {
		return nil, errors.New("gateway: embedding response empty")
	}
	return out.Data[0].Embedding, nil
}

// ---------------------------------------------------------------------------
// Voice (gateway /v1/voice/{transcribe,synthesize}) — FIX 2
//
// chat-edge no longer calls OpenAI Whisper/TTS directly. The gateway fronts
// both behind the raw-egress gate (scan_voice_text for TTS text;
// assert_stt_egress_allowed for STT audio) before OpenAI.
// ---------------------------------------------------------------------------

// VoiceTranscribe forwards audio bytes to the gateway STT route and returns
// the transcript. The multipart field names match the gateway route
// (“audio“ file + optional “language“).
func (g *GatewayClient) VoiceTranscribe(
	ctx context.Context, callCtx GatewayCallContext,
	audio []byte, filename, contentType, language string,
) (string, error) {
	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	part, err := mw.CreatePart(textproto.MIMEHeader{
		"Content-Disposition": []string{
			fmt.Sprintf(`form-data; name="audio"; filename=%q`, filename),
		},
		"Content-Type": []string{contentType},
	})
	if err != nil {
		return "", fmt.Errorf("gateway: build voice form: %w", err)
	}
	if _, err := part.Write(audio); err != nil {
		return "", fmt.Errorf("gateway: write voice audio: %w", err)
	}
	if language != "" {
		_ = mw.WriteField("language", language)
	}
	if err := mw.Close(); err != nil {
		return "", fmt.Errorf("gateway: close voice form: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost, g.BaseURL+"/voice/transcribe", &body,
	)
	if err != nil {
		return "", fmt.Errorf("gateway: build voice request: %w", err)
	}
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return "", err
	}
	httpReq.Header.Set("Content-Type", mw.FormDataContentType())

	resp, err := g.http.Do(httpReq)
	if err != nil {
		return "", &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		return "", &GatewayUnavailableError{Status: resp.StatusCode, Body: string(b)}
	}
	var out struct {
		Text string `json:"text"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return "", fmt.Errorf("gateway: decode voice response: %w", err)
	}
	return out.Text, nil
}

// VoiceSynthesize forwards TTS text to the gateway and returns the raw mp3
// bytes. Returns the upstream status + body inside GatewayUnavailableError on
// any non-2xx (so a DLP 403 on the text surfaces distinctly).
func (g *GatewayClient) VoiceSynthesize(
	ctx context.Context, callCtx GatewayCallContext,
	text, voice string, speed float64,
) ([]byte, error) {
	buf, err := json.Marshal(map[string]any{
		"text": text, "voice": voice, "speed": speed,
	})
	if err != nil {
		return nil, fmt.Errorf("gateway: marshal voice synth: %w", err)
	}
	httpReq, err := http.NewRequestWithContext(
		ctx, http.MethodPost, g.BaseURL+"/voice/synthesize", bytes.NewReader(buf),
	)
	if err != nil {
		return nil, fmt.Errorf("gateway: build voice synth request: %w", err)
	}
	if err := g.internalHeaders(httpReq, callCtx); err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := g.http.Do(httpReq)
	if err != nil {
		return nil, &GatewayUnavailableError{Status: 0, Body: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024))
		return nil, &GatewayUnavailableError{Status: resp.StatusCode, Body: string(b)}
	}
	return io.ReadAll(io.LimitReader(resp.Body, 32*1024*1024))
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
