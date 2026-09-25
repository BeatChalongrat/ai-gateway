# Public API Reference

The Secure AI Gateway provides an OpenAI-compatible API surface for routing LLM requests through DLP/policy enforcement.

## Base URL

```
https://localhost:8080
```

`https` since FIX133 (Caddy front door with a local CA — see `docs/ops/tls.md`);
scripts on the box may use the plain loopback door `http://127.0.0.1:28080` instead.

## Authentication

All `/v1/chat/completions` requests require a Bearer token:

```
Authorization: Bearer gw_your_api_key_here
```

API keys are created via the admin API (`POST /admin/api-keys`). Each key is bound to a tenant and project.

The `/v1/models` and `/health` endpoints do not require authentication.

## Endpoints

### POST /v1/chat/completions

Send a chat completion request. The gateway routes to the appropriate provider, applies DLP scanning, and returns a normalized response.

**Request:**

```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is the capital of France?"}
  ],
  "temperature": 0.7,
  "max_tokens": 1000,
  "top_p": 0.9,
  "stream": false
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `model` | string | No | `"auto"` | Model name or `"auto"` for gateway routing |
| `messages` | array | **Yes** | — | Message array (min 1). Roles: `system`, `user`, `assistant` |
| `temperature` | float | No | Provider default | 0.0 – 2.0 |
| `max_tokens` | int | No | Provider default | 1 – 128000 |
| `top_p` | float | No | Provider default | 0.0 – 1.0 |
| `stream` | bool | No | `false` | Enable Server-Sent Events streaming |
| `metadata` | object | No | `{}` | Gateway extensions (e.g. `{"use_case": "coding"}`) |

**Response (non-streaming):**

```json
{
  "id": "req_abc123def456",
  "object": "chat.completion",
  "created": 1712345678,
  "model": "gpt-4o-mini",
  "provider": "openai",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "The capital of France is Paris."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 10,
    "total_tokens": 35,
    "estimated_cost_usd": 0.000035
  },
  "policy_action": "allow",
  "policy_reasons": []
}
```

| Field | Description |
|-------|-------------|
| `id` | Unique request ID (also in `X-Request-Id` header) |
| `object` | Always `"chat.completion"` |
| `created` | Unix timestamp |
| `model` | Model used (as reported by provider) |
| `provider` | Provider name: `openai`, `anthropic`, `openrouter` |
| `choices` | Completion choices (currently always 1) |
| `usage` | Token counts and estimated cost |
| `policy_action` | DLP result: `allow`, `mask`, `redact`, `block` |
| `policy_reasons` | Human-readable DLP policy explanations |

**Response (streaming):**

When `stream: true`, the response is `text/event-stream` with Server-Sent Events:

```
data: {"id":"req_abc","object":"chat.completion.chunk","created":1712345678,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"The"},"finish_reason":null}]}

data: {"id":"req_abc","object":"chat.completion.chunk","created":1712345678,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":" capital"},"finish_reason":null}]}

data: {"id":"req_abc","object":"chat.completion.chunk","created":1712345678,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Streaming is currently supported for OpenAI models. Anthropic and OpenRouter streaming falls back to non-streaming.

---

### GET /v1/models

List available models. No authentication required.

**Response:**

```json
{
  "data": [
    {"id": "auto", "provider": "gateway"},
    {"id": "gpt-4o", "provider": "openai"},
    {"id": "gpt-4o-mini", "provider": "openai"},
    {"id": "claude-3-5-sonnet-20241022", "provider": "anthropic"},
    {"id": "claude-3-opus-20240229", "provider": "anthropic"}
  ]
}
```

The `auto` model lets the gateway choose the best provider based on request metadata.

---

### GET /health

Health check endpoint. No authentication required.

```json
{"status": "ok", "service": "secure-ai-gateway", "version": "0.1.0"}
```

---

## Model Selection

| Model | Provider | Notes |
|-------|----------|-------|
| `auto` | Gateway decides | Default. Uses `gpt-4o-mini` for general, `claude-3-5-sonnet` for coding/reasoning |
| `gpt-4o` | OpenAI | |
| `gpt-4o-mini` | OpenAI | Cost-efficient |
| `claude-3-5-sonnet-20241022` | Anthropic | |
| `claude-3-opus-20240229` | Anthropic | |
| `openai/gpt-4o` | OpenRouter | Prefix-based routing |
| `meta-llama/llama-3-70b` | OpenRouter | Prefix-based routing |

Use `metadata.use_case` for smarter auto-routing:

```json
{"model": "auto", "metadata": {"use_case": "coding"}}
```

---

## Error Responses

All errors use a unified envelope:

```json
{
  "error": {
    "code": "authentication_error",
    "message": "Missing or invalid authorization header",
    "details": null
  },
  "request_id": "abc-123-def"
}
```

| Status | Code | When |
|--------|------|------|
| 401 | `authentication_error` | Missing/invalid/expired API key |
| 403 | `dlp_violation` | DLP policy blocked the request (e.g. secrets detected) |
| 404 | `not_found` | Unknown endpoint or resource |
| 422 | `validation_error` | Invalid request parameters |
| 429 | `rate_limit_exceeded` | Provider rate limit hit |
| 502 | `provider_error` | Provider returned an error |
| 503 | `provider_unavailable` | Provider is unreachable |
| 504 | `provider_timeout` | Provider didn't respond in time |
| 500 | `internal_error` | Unexpected server error |

---

## DLP / Policy Behavior

The gateway scans request and response content for sensitive data:

- **PII (email, phone, SSN):** Masked by default (`****...last4`)
- **Secrets (API keys, tokens, private keys):** Blocked (403)
- **Policy profiles:** Configurable per-project via admin API

When content is masked, the `policy_action` field is `"mask"` and `policy_reasons` explains what was found.

---

## Request Tracing

Every request gets a unique `X-Request-Id` header (auto-generated or pass your own). This ID appears in:
- Response `X-Request-Id` header
- Error response `request_id` field
- Internal audit logs

---

## Known Limitations

- Streaming is OpenAI-only; Anthropic/OpenRouter fall back to non-streaming
- No pagination on admin list endpoints
- Admin endpoints have no authentication (RBAC deferred to Phase 9)
- `n > 1` choices not supported (always returns 1 choice)
- Function/tool calling not yet supported in the gateway schema
- Response-side DLP scanning does not apply during streaming
