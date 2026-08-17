using System.Text;
using System.Text.Json;
using SranDesktop.Application.Events;

namespace SranDesktop.Application.Models;

/// <summary>
/// Accumulates OpenAI-format SSE chunks into typed agent events.
/// Tool-call arguments may arrive as streamed fragments OR as a single
/// complete frame (the gateway's DLP layer buffers arguments before release)
/// — both paths land in the same accumulator.
/// </summary>
public sealed class GatewayChunkParser
{
    /// <summary>
    /// Context-window signatures for gateways WITHOUT the typed
    /// context_length_exceeded error (they wrap the provider 400 as
    /// upstream_bad_response with the original body in error.details).
    /// Mirrors CONTEXT_LENGTH_SIGNATURES in the gateway's provider
    /// error handler; scanned case-insensitively.
    /// </summary>
    internal static readonly string[] ContextLengthSignatures =
    {
        "context_length_exceeded",
        "maximum context length",
        "prompt is too long",
        "exceeds the maximum number of tokens",
    };

    private readonly Dictionary<int, ToolCallAccumulator> _toolCalls = new();

    /// <summary>Feed one SSE data payload; returns zero or more events.</summary>
    public IReadOnlyList<AgentStreamEvent> Parse(string data)
    {
        if (data.Trim() == "[DONE]")
        {
            // Some providers end with a bare [DONE] and never send a
            // finish_reason — don't drop tool calls already accumulated.
            if (_toolCalls.Values.Any(a => a.Name is not null))
                return new AgentStreamEvent[] { new AgentToolCallsEvent(DrainToolCalls()), new AgentStreamDoneEvent() };
            return new AgentStreamEvent[] { new AgentStreamDoneEvent() };
        }

        var events = new List<AgentStreamEvent>();
        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(data);
        }
        catch (JsonException)
        {
            return events; // malformed keep-alive / partial frame — skip
        }

        using (doc)
        {
            var root = doc.RootElement;

            // Error envelope instead of a chunk
            if (root.TryGetProperty("error", out var error))
            {
                var message = error.ValueKind == JsonValueKind.Object
                              && error.TryGetProperty("message", out var msgEl)
                    ? msgEl.GetString() ?? "Gateway error"
                    : error.ToString();
                events.Add(new AgentErrorEvent(message, ExtractErrorCode(error)));
                return events;
            }

            if (!root.TryGetProperty("choices", out var choices)
                || choices.ValueKind != JsonValueKind.Array
                || choices.GetArrayLength() == 0)
            {
                // Typed metadata frames ride the same stream without choices.
                // fallback_info = the router answered with a different model:
                // {"type":"fallback_info","fallback_info":{"requested_model",
                //  "actual_model","reason","attempts":[...]}}. Any other type
                // keeps being ignored, exactly as before.
                if (root.TryGetProperty("type", out var typeEl)
                    && typeEl.ValueKind == JsonValueKind.String)
                {
                    switch (typeEl.GetString())
                    {
                        case "fallback_info"
                            when root.TryGetProperty("fallback_info", out var fallback)
                                 && fallback.ValueKind == JsonValueKind.Object:
                        {
                            var requested = GetStringProperty(fallback, "requested_model");
                            var actual = GetStringProperty(fallback, "actual_model");
                            if (!string.IsNullOrEmpty(requested) && !string.IsNullOrEmpty(actual))
                                events.Add(new AgentFallbackEvent(
                                    requested!, actual!, GetStringProperty(fallback, "reason")));
                            break;
                        }
                        // Reasoning parity: the gateway's provider-neutral
                        // reasoning frames. Field names per public_chat.py —
                        // the summary fragment rides in "text", the bracket
                        // in "status".
                        case "reasoning_summary_delta"
                            when GetStringProperty(root, "text") is { Length: > 0 } text:
                            events.Add(new AgentReasoningDeltaEvent(text));
                            break;
                        case "analysis_status"
                            when GetStringProperty(root, "status") is { Length: > 0 } status:
                            events.Add(new AgentAnalysisStatusEvent(status));
                            break;
                        // Any other type keeps being ignored, exactly as before.
                    }
                }
                return events;
            }

            var choice = choices[0];

            if (choice.TryGetProperty("delta", out var delta))
            {
                if (delta.TryGetProperty("content", out var contentEl)
                    && contentEl.ValueKind == JsonValueKind.String
                    && contentEl.GetString() is { Length: > 0 } content)
                {
                    events.Add(new AgentContentDeltaEvent(content));
                }

                if (delta.TryGetProperty("tool_calls", out var toolCallsEl)
                    && toolCallsEl.ValueKind == JsonValueKind.Array)
                {
                    AccumulateToolCalls(toolCallsEl);
                }
            }

            if (choice.TryGetProperty("finish_reason", out var frEl)
                && frEl.ValueKind == JsonValueKind.String
                && frEl.GetString() is { } finishReason)
            {
                if (finishReason == "tool_calls" || (_toolCalls.Count > 0 && finishReason == "stop"))
                {
                    // Some providers finish tool turns with "stop"; if calls
                    // accumulated, they take precedence over plain completion.
                    events.Add(new AgentToolCallsEvent(DrainToolCalls()));
                }
                else if (finishReason is "stop" or "length")
                {
                    events.Add(new AgentStreamDoneEvent());
                }
            }
        }

        return events;
    }

    /// <summary>
    /// Extracts error.code, normalizing an old gateway's generic
    /// upstream_bad_response to context_length_exceeded when the wrapped
    /// provider details carry a context-window signature.
    /// </summary>
    private static string? ExtractErrorCode(JsonElement error)
    {
        if (error.ValueKind != JsonValueKind.Object
            || !error.TryGetProperty("code", out var codeEl)
            || codeEl.ValueKind != JsonValueKind.String)
            return null;

        var code = codeEl.GetString();
        if (code == "upstream_bad_response"
            && error.TryGetProperty("details", out var details))
        {
            var raw = details.GetRawText();
            if (ContextLengthSignatures.Any(sig =>
                    raw.Contains(sig, StringComparison.OrdinalIgnoreCase)))
                return "context_length_exceeded";
        }
        return code;
    }

    private static string? GetStringProperty(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    public void Reset() => _toolCalls.Clear();

    /// <summary>Drains any tool calls still accumulated (stream ended without a
    /// finish_reason). Returns an empty list when nothing is pending.</summary>
    public IReadOnlyList<GatewayToolCall> DrainRemaining() => DrainToolCalls();

    private void AccumulateToolCalls(JsonElement toolCallsEl)
    {
        foreach (var tc in toolCallsEl.EnumerateArray())
        {
            var index = tc.TryGetProperty("index", out var idxEl) && idxEl.ValueKind == JsonValueKind.Number
                ? idxEl.GetInt32()
                : 0;

            if (!_toolCalls.TryGetValue(index, out var acc))
            {
                acc = new ToolCallAccumulator();
                _toolCalls[index] = acc;
            }

            if (tc.TryGetProperty("id", out var idEl) && idEl.ValueKind == JsonValueKind.String)
                acc.Id = idEl.GetString();

            if (tc.TryGetProperty("function", out var fn) && fn.ValueKind == JsonValueKind.Object)
            {
                if (fn.TryGetProperty("name", out var nameEl) && nameEl.ValueKind == JsonValueKind.String)
                    acc.Name = nameEl.GetString();
                if (fn.TryGetProperty("arguments", out var argsEl) && argsEl.ValueKind == JsonValueKind.String)
                    acc.Arguments.Append(argsEl.GetString());
            }
        }
    }

    private List<GatewayToolCall> DrainToolCalls()
    {
        var calls = _toolCalls
            .OrderBy(kv => kv.Key)
            .Where(kv => kv.Value.Name is not null)
            .Select(kv => new GatewayToolCall
            {
                Id = kv.Value.Id ?? $"call_{Guid.NewGuid():N}",
                Function = new GatewayToolCallFunction
                {
                    Name = kv.Value.Name!,
                    Arguments = kv.Value.Arguments.Length > 0 ? kv.Value.Arguments.ToString() : "{}"
                }
            })
            .ToList();
        _toolCalls.Clear();
        return calls;
    }

    private sealed class ToolCallAccumulator
    {
        public string? Id;
        public string? Name;
        public readonly StringBuilder Arguments = new();
    }
}
