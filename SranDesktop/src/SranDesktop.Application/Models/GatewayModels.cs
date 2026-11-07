using System.Text.Json;
using System.Text.Json.Serialization;

namespace SranDesktop.Application.Models;

/// <summary>POST body for the gateway's OpenAI-compatible /v1/chat/completions.</summary>
public sealed class GatewayCompletionRequest
{
    [JsonPropertyName("model")]
    public required string Model { get; init; }

    [JsonPropertyName("messages")]
    public required List<GatewayMessage> Messages { get; init; }

    [JsonPropertyName("stream")]
    public bool Stream { get; init; } = true;

    /// <summary>Output-token cap; the gateway accepts 1–128000.</summary>
    [JsonPropertyName("max_tokens")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public int? MaxTokens { get; init; }

    [JsonPropertyName("tools")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<GatewayToolDefinition>? Tools { get; init; }

    [JsonPropertyName("tool_choice")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? ToolChoice { get; init; }

    /// <summary>Reasoning request (web parity — chat-edge attaches
    /// {mode:"auto", show_summary:true} on every turn and the gateway
    /// no-ops it for models that can't reason). Null = field omitted.</summary>
    [JsonPropertyName("reasoning")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public GatewayReasoningConfig? Reasoning { get; init; }
}

/// <summary>Mirror of the gateway's ReasoningConfig request block.</summary>
public sealed class GatewayReasoningConfig
{
    [JsonPropertyName("mode")]
    public string Mode { get; init; } = "auto";

    [JsonPropertyName("show_summary")]
    public bool ShowSummary { get; init; } = true;
}

public sealed class GatewayMessage
{
    [JsonPropertyName("role")]
    public required string Role { get; init; }

    /// <summary>Plain string, or an array of OpenAI content blocks (multimodal:
    /// {type:"text"} / {type:"image_url"}). Serialized verbatim either way.</summary>
    [JsonPropertyName("content")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public object? Content { get; init; }

    /// <summary>Only on assistant messages that invoked tools.</summary>
    [JsonPropertyName("tool_calls")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<GatewayToolCall>? ToolCalls { get; init; }

    /// <summary>Only on role:"tool" messages.</summary>
    [JsonPropertyName("tool_call_id")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? ToolCallId { get; init; }

    /// <summary>Only on role:"tool" messages.</summary>
    [JsonPropertyName("name")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Name { get; init; }
}

public sealed class GatewayToolDefinition
{
    [JsonPropertyName("type")]
    public string Type { get; init; } = "function";

    [JsonPropertyName("function")]
    public required GatewayFunctionDef Function { get; init; }
}

public sealed class GatewayFunctionDef
{
    [JsonPropertyName("name")]
    public required string Name { get; init; }

    [JsonPropertyName("description")]
    public required string Description { get; init; }

    [JsonPropertyName("parameters")]
    public required JsonElement Parameters { get; init; }
}

public sealed class GatewayToolCall
{
    [JsonPropertyName("id")]
    public required string Id { get; init; }

    [JsonPropertyName("type")]
    public string Type { get; init; } = "function";

    [JsonPropertyName("function")]
    public required GatewayToolCallFunction Function { get; init; }
}

public sealed class GatewayToolCallFunction
{
    [JsonPropertyName("name")]
    public required string Name { get; init; }

    [JsonPropertyName("arguments")]
    public required string Arguments { get; init; }
}

/// <summary>Result of one local tool execution.</summary>
public sealed record ToolResult(bool Success, string Content, string? Error = null, string? ImageDataUri = null)
{
    private const int MaxContentChars = 50_000;

    public static ToolResult Ok(string content) => new(true, content);
    public static ToolResult Fail(string error) => new(false, string.Empty, error);

    /// <summary>Success result carrying an image (e.g. a screenshot) for vision analysis.</summary>
    public static ToolResult OkWithImage(string content, string imageDataUri) =>
        new(true, content, null, imageDataUri);

    /// <summary>Truncated so a huge file read / command output can't blow the LLM context.</summary>
    public string TruncatedContent => Content.Length > MaxContentChars
        ? Content[..MaxContentChars] + "\n\n[...truncated to 50K chars]"
        : Content;
}

public sealed record ToolConsentRequest(
    string ToolCallId,
    string ToolName,
    string Description,
    Domain.Enums.ToolCategory Category,
    string ArgumentsJson,
    string Scope);

public sealed record ToolConsentResponse(bool Denied, bool AllowOnce, bool AllowAlways)
{
    public static ToolConsentResponse Deny { get; } = new(true, false, false);
    public static ToolConsentResponse Once { get; } = new(false, true, false);
    public static ToolConsentResponse Always { get; } = new(false, false, true);
}
