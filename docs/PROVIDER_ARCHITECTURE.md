# Provider Adapter Architecture

The gateway routes LLM requests through provider adapters that translate between the gateway's canonical format and each upstream provider's API.

## Pipeline

```
Request
  |
  v
[Routing]  RoutingService selects provider + model
  |         Uses model_registry for name → provider resolution
  v
[Dispatch]  ProviderDispatcher looks up adapter by provider_name
  |
  v
[Adapter]   Adapter builds provider-specific request
  |          Calls upstream via httpx with retry + timeout
  v
[Parse]     Shared parsers normalize response → ProviderResponse
  |
  v
[Return]    Orchestrator consumes ProviderResponse
```

## Supported Providers

| Provider | Adapter | Base URL | Auth Header | Format |
|----------|---------|----------|-------------|--------|
| OpenAI | `OpenAIAdapter` | `https://api.openai.com/v1` | `Authorization: Bearer` | OpenAI Chat Completions |
| Anthropic | `AnthropicAdapter` | `https://api.anthropic.com/v1` | `x-api-key` + `anthropic-version` | Anthropic Messages API |
| OpenRouter | `OpenRouterAdapter` | `https://openrouter.ai/api/v1` | `Authorization: Bearer` | OpenAI-compatible |

## Canonical Contracts

### ProviderResponse

Every adapter returns a `ProviderResponse`:

```python
@dataclass(frozen=True)
class ProviderResponse:
    content: str              # Assistant message text
    model: str                # Model name from provider
    finish_reason: str        # "stop", "length", "content_filter"
    usage: ProviderUsage      # Token counts (or None)
    raw: dict                 # Full raw JSON for debugging
```

### ProviderUsage

```python
@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
```

### ProviderStreamChunk (streaming)

```python
@dataclass(frozen=True)
class ProviderStreamChunk:
    delta: str                # Incremental text
    finish_reason: str | None # Set on final chunk
    model: str                # Usually set on first chunk
```

## Module Map

```
app/
  domain/
    providers.py          ProviderResponse, ProviderUsage, ProviderStreamChunk

  services/
    providers/
      base.py             BaseProviderAdapter (ABC)
      http_client.py      create_provider_client() — shared httpx factory
      parsers.py          parse_openai_response(), parse_anthropic_response()
      error_handler.py    map_provider_error(), call_with_retry()
      model_registry.py   resolve_provider(), list_models()
      openai_adapter.py   OpenAIAdapter (chat + stream_chat)
      anthropic_adapter.py AnthropicAdapter (chat only)
      openrouter_adapter.py OpenRouterAdapter (chat only)

    provider_dispatcher.py  Routes to adapter by provider_name
    routing_service.py      Model → provider routing logic
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (empty) | OpenAI API key |
| `ANTHROPIC_API_KEY` | (empty) | Anthropic API key |
| `OPENROUTER_API_KEY` | (empty) | OpenRouter API key |
| `PROVIDER_TIMEOUT_CONNECT` | `5.0` | TCP connect timeout (seconds) |
| `PROVIDER_TIMEOUT_READ` | `30.0` | Read/response timeout (seconds) |
| `PROVIDER_MAX_RETRIES` | `2` | Retries on transient failures (429, 5xx, timeout) |

## Error Mapping

| Upstream Error | Domain Exception | HTTP Status |
|----------------|-----------------|-------------|
| Timeout (connect/read) | `ProviderTimeoutError` | 504 |
| Connection refused | `ProviderUnavailableError` | 503 |
| HTTP 429 | `RateLimitExceededError` | 429 |
| HTTP 502/503 | `ProviderUnavailableError` | 503 |
| HTTP 500 | `ProviderError` | 502 |
| HTTP 4xx (400, 401, etc.) | `UpstreamBadResponseError` | 502 |

### Retry Behavior

`call_with_retry` wraps each provider HTTP call:
- **Retryable:** 429, 500, 502, 503, timeouts, connection errors
- **Not retried:** 400, 401, 403, 404 (client errors from provider)
- **Backoff:** Exponential — `0.5s * 2^attempt`
- **Max retries:** Configurable via `PROVIDER_MAX_RETRIES` (default 2)

## Anthropic-Specific Translation

The Anthropic Messages API differs from OpenAI:

| Aspect | OpenAI | Anthropic |
|--------|--------|-----------|
| System message | In `messages` array | Top-level `system` parameter |
| Response content | `choices[0].message.content` (string) | `content` (list of blocks) |
| Finish reason | `finish_reason` | `stop_reason` (mapped: `end_turn`→`stop`, `max_tokens`→`length`) |
| Token fields | `prompt_tokens`, `completion_tokens` | `input_tokens`, `output_tokens` |
| Required params | model, messages | model, messages, max_tokens |

## Streaming

OpenAI adapter supports streaming via `stream_chat()`:
- Uses Server-Sent Events (SSE) format
- Yields `ProviderStreamChunk` instances
- Parses `data: {...}` lines, skips `data: [DONE]`

Anthropic and OpenRouter streaming are scaffolded (base class raises `NotImplementedError`).

End-to-end SSE to the client (FastAPI `StreamingResponse`) is a future enhancement.

## Model Registry

`model_registry.py` maps model names to providers:
- **Exact match:** `gpt-4o` → `openai`, `claude-3-5-sonnet-20241022` → `anthropic`
- **Prefix match:** `gpt-*` → `openai`, `claude-*` → `anthropic`, `openai/*` → `openrouter`
- **Unknown:** Falls back to `openai` in the routing service

## Adding a New Provider

1. Create `app/services/providers/your_adapter.py`
2. Subclass `BaseProviderAdapter`, set `provider_name`
3. Implement `chat()` → call upstream, return `ProviderResponse`
4. If the provider uses OpenAI format, reuse `parse_openai_response()`
5. Otherwise, add a parser to `parsers.py`
6. Register in `ProviderDispatcher.__init__`
7. Add prefix/model entries to `model_registry.py`
8. Add config for the API key in `config.py`
9. Write tests with `respx` mocks
